"""Gerenciamento de contexto do Local LLM — Fase 11.3 (#1).

Camada determinística de *context budget*: antes de enviar o prompt ao
`create_chat_completion` do llama.cpp, mede (via tokenizer real quando
disponível, senão heurística) o custo em tokens de cada componente e trunca de
forma inteligente:

- preserva sempre o system prompt essencial;
- preserva a mensagem atual do usuário;
- preserva tool declarations necessárias;
- reserva espaço para a resposta do modelo (saída);
- trunca primeiro mensagens antigas de menor prioridade;
- nunca trunca a mensagem atual;
- nunca remove instruções críticas de segurança/permissões (parte do system).

Retorna metadados de uso (tokens usados / limite, flag de truncamento) para
registro na Central de Operações.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.schemas.ai import ToolDeclaration

# Reserva de tokens reservados para a resposta do modelo (saída).
OUTPUT_RESERVE_RATIO = 0.25
# Fração mínima do system prompt que jamais é truncada (essência).
SYSTEM_MIN_KEEP_RATIO = 0.9
# Tamanho máximo, em caracteres, que uma única mensagem antiga pode ter antes
# de ser cortada (proteção contra tool results gigantes).
_MAX_OLD_MESSAGE_CHARS = 1500
# Margem de segurança (tokens) para sobreposição de template do chat + defs.
SAFETY_MARGIN_TOKENS = 96


def _estimate_chars_per_token() -> float:
    # Heurística ~4 chars/token (conservadora p/ Qwen).
    return 4.0


def estimate_input_tokens(text: str, tokenizer=None) -> int:
    """Conta tokens de um texto — via tokenizer real quando possível."""
    if tokenizer is not None:
        try:
            ids = tokenizer(text)
            if hasattr(ids, "__iter__"):
                return len(list(ids))
            return int(ids)
        except Exception:  # noqa: BLE001 — fallback
            pass
    # Fallback heurístico
    return max(1, int(len(text) / _estimate_chars_per_token()))


def _serialize_tool_def_tokens(tools: list[ToolDeclaration] | None, tokenizer=None) -> int:
    if not tools:
        return 0
    body = json.dumps(
        [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters or {},
                },
            }
            for t in tools
        ],
        ensure_ascii=False,
    )
    return estimate_input_tokens(body, tokenizer)


def _system_tokens(system: str, tokenizer=None) -> int:
    if not system:
        return 0
    return estimate_input_tokens(system, tokenizer)


@dataclass
class ContextFit:
    """Resultado da aplicação do context budget sobre as mensagens."""

    messages: list[dict] = field(default_factory=list)
    # Perfil de uso (para observabilidade).
    input_tokens: int = 0
    used_tokens: int = 0
    limit_tokens: int = 0
    truncated: bool = False
    dropped_old_messages: int = 0
    profile: dict = field(default_factory=dict)


def fit_context(
    messages: list[dict],
    *,
    system: str | None = None,
    tools: list[ToolDeclaration] | None = None,
    n_ctx: int,
    max_tokens_out: int,
    tokenizer=None,
    clip_current: bool = False,
) -> ContextFit:
    """Trunca `messages` para caber em `n_ctx`, preservando invariantes.

    `messages` está na forma serializada do llama.cpp (list[dict] com
    `role`/`content`). O system já está mesclado no topo caso tenha sido
    injetado; aqui tratamos de forma que o primeiro bloco system seja
    preservado intacto.
    """
    reserve = max(1, int(n_ctx * OUTPUT_RESERVE_RATIO))
    budget = n_ctx - reserve
    if budget <= 0:
        # Janela minúscula — devolve tudo mesmo assim (o motor decidirá).
        return ContextFit(messages=list(messages), input_tokens=0, used_tokens=0, limit_tokens=n_ctx)

    # Separa o system (se houver) do restante do histórico.
    has_system = system is not None
    sys_tokens = _system_tokens(system or "", tokenizer)
    tool_tokens = _serialize_tool_def_tokens(tools, tokenizer)

    # Conta o custo das mensagens restantes.
    def _msg_tokens(m: dict) -> int:
        return estimate_input_tokens(m.get("content") or "", tokenizer) + 4  # overhead de role

    # Sistema + ferramentas são custo fixo (no prompt que o motor efetivamente
    # monta, as tools são injetadas junto). O system é preservado integralmente
    # até o limite SYSTSTEM_MIN_KEEP_RATIO; tools são necessárias.
    fixed = sys_tokens + tool_tokens
    available = budget - fixed
    if available < 0:
        # System + tools sozinhos já estouram: trunca ferramentas por último e
        # deixa o system íntegro na medida do possível.
        available = max(0, int(budget - sys_tokens * SYSTEM_MIN_KEEP_RATIO))

    # Separa a mensagem atual (a mais recente) — sempre preservada.
    history = list(messages)
    current: list[dict] = []
    if history:
        current = [history[-1]]
        history = history[:-1]

    # Atribui prioridade: mensagens do usuário/tool recentes > antigas.
    # Trunca do início (mais antigas) para frente.
    kept: list[dict] = []
    used = 0
    # Preserva as mensagens de histórico mais recentes primeiro.
    reverse = list(reversed(history))
    dropped = 0
    for m in reverse:
        if used >= available:
            dropped += 1
            continue
        mt = _msg_tokens(m)
        # Corta mensagens antigas muito longas (tool results gigantes).
        content = m.get("content") or ""
        if len(content) > _MAX_OLD_MESSAGE_CHARS and used + mt > available:
            clipped = content[:_MAX_OLD_MESSAGE_CHARS]
            m = {**m, "content": clipped}
            mt = _msg_tokens(m)
        if used + mt > available:
            dropped += 1
            continue
        kept.append(m)
        used += mt

    kept = list(reversed(kept))
    clipped_current = False

    # Re-adiciona a mensagem atual (sempre preservada). Se `clip_current` e a
    # janela estourar mesmo depois de truncar o histórico, corta o conteúdo
    # corrente para garantir que o prompt final caiba no n_ctx real.
    current_kept = list(current)
    if clip_current and current_kept:
        margin = SAFETY_MARGIN_TOKENS
        limit = n_ctx - reserve - margin
        # Espaço restante depois de system + tools + histórico preservado.
        desired = max(0, limit - sys_tokens - tool_tokens - used)
        content = current_kept[-1].get("content") or ""
        cur_tokens = estimate_input_tokens(content, tokenizer) + 4
        if cur_tokens > desired:
            # Corta iterativamente até caber (heurística de caracteres).
            max_chars = max(120, int(len(content) * (desired / max(1, cur_tokens))))
            if max_chars < len(content):
                current_kept[-1] = {**current_kept[-1], "content": content[:max_chars]}
                clipped_current = True

    final = kept + current_kept
    total_used = (
        used
        + sys_tokens
        + tool_tokens
        + sum(_msg_tokens(c) for c in current_kept)
        + reserve
    )

    truncated = (dropped > 0) or clipped_current
    profile = {
        "system_tokens": sys_tokens,
        "tool_tokens": tool_tokens,
        "history_tokens": used,
        "current_tokens": sum(_msg_tokens(c) for c in current),
        "reserved_output": reserve,
        "dropped_messages": dropped,
    }

    return ContextFit(
        messages=final,
        input_tokens=sys_tokens + tool_tokens + used + sum(_msg_tokens(c) for c in current),
        used_tokens=sys_tokens + tool_tokens + used + sum(_msg_tokens(c) for c in current),
        limit_tokens=n_ctx,
        truncated=truncated,
        dropped_old_messages=dropped,
        profile=profile,
    )
