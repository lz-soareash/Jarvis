"""LocalLLMProvider (Fase 11 — extensão): motor linguístico local generativo.

Usa `llama-cpp-python` (llama.cpp/GGML) para rodar um modelo quantizado (GGUF)
100% no CPU — sem depender de API externa, chave ou internet. O Qwen2.5-3B
Instruct Q4_K_M é o modelo de referência inicial; a arquitetura permite trocar
o arquivo GGUF sem reescrever AI Core/AI Router/memória/tools, pois o provider
implementa o mesmo contrato `AIProvider`.

- `generate/stream/analyze` → chat generativo local.
- `embed` → modelo de embedding local separado (memória semântica offline).
- `tools` → o modelo emite tool calls no formato nativo do Qwen
  (`<tool_call>{json}</tool_call>` em texto); este provider parseia para o
  `ToolCall` que o AI Core/Tool Engine já entendem e valida (nunca executa
  chamada não registrada).

O modelo é carregado uma única vez (lazy, singleton) e compartilhado entre
chamadas, via `asyncio.to_thread` (llama.cpp é bloqueante).
"""

import asyncio
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import AsyncIterator, Callable

from app.core.config import REPO_ROOT, settings
from app.schemas.ai import (
    AIMessage,
    AIProviderStatus,
    AIResponse,
    ToolCall,
    ToolDeclaration,
)

from .base import AIProvider, AIProviderError

logger = logging.getLogger("jarvis.local_llm")

DEFAULT_MODEL_REL = Path(".local_models") / "qwen2.5-3b-instruct-q4_k_m.gguf"
DEFAULT_EMBED_REL = (
    Path(".local_models") / "embeddings" / "nomic-embed-text-v1.5.Q8_0.gguf"
)

# Formato nativo de tool call do Qwen: <tool_call>{...json...}</tool_call>
_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


def _default_model_path() -> str:
    return str(REPO_ROOT / DEFAULT_MODEL_REL)


def _default_embed_path() -> str:
    return str(REPO_ROOT / DEFAULT_EMBED_REL)


class _LlamaOwners:
    """Cache singleton thread-safe do modelo LLM e do modelo de embedding.

    O `llama-cpp-python` mantém estado nativo numa instância única; recriá-lo a
    cada chamada seria inviável (carrega GBs da memória). Guardamos os objetos
    aqui, criando-os uma única vez (lazy). Modelos são carregados em thread
    dedicada para não bloquear o event loop.
    """

    def __init__(self) -> None:
        self._llm = None
        self._embed = None
        self._lock = threading.Lock()

    def _load(self, make: Callable):
        # Cria a instância numa thread separada — o carregamento de pesos é
        # pesado e o llama.cpp pode emitir logs/block no processo.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(make).result()

    def get_llm(self, model_path: str, n_ctx: int, n_threads: int):
        if self._llm is not None:
            return self._llm
        with self._lock:
            if self._llm is None:
                from llama_cpp import Llama

                self._llm = self._load(
                    lambda: Llama(
                        model_path=model_path,
                        n_ctx=n_ctx,
                        n_threads=n_threads,
                        verbose=False,
                    )
                )
        return self._llm

    def get_embed(self, embed_path: str, n_ctx: int, n_threads: int):
        if self._embed is not None:
            return self._embed
        with self._lock:
            if self._embed is None:
                from llama_cpp import Llama

                self._embed = self._load(
                    lambda: Llama(
                        model_path=embed_path,
                        n_ctx=n_ctx,
                        n_threads=n_threads,
                        verbose=False,
                        embedding=True,
                    )
                )
        return self._embed

    def reset(self) -> None:
        with self._lock:
            self._llm = None
            self._embed = None


_shared = _LlamaOwners()


def _parse_tool_calls(text: str) -> list[ToolCall]:
    """Extrai tool calls no formato nativo `<tool_call>{...}</tool_call>`."""
    calls: list[ToolCall] = []
    for block in _TOOL_CALL_RE.findall(text):
        block = block.strip()
        try:
            payload = json.loads(block)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        name = payload.get("name") or payload.get("function") or ""
        args = payload.get("arguments") or payload.get("args") or {}
        if not isinstance(args, dict):
            try:
                args = json.loads(args) if isinstance(args, str) else {}
            except (ValueError, TypeError):
                args = {}
        if name:
            calls.append(ToolCall(name=name, arguments=args))
    return calls


def _serialize_messages(
    history: list[AIMessage], system: str | None, tools: list[ToolDeclaration] | None
) -> tuple[list[dict], list[dict] | None]:
    """Converte AIMessage p/ o formato do llama-cpp-python (chat completion)."""
    messages: list[dict] = []
    for m in history:
        if m.role == "tool":
            messages.append({"role": "tool", "content": m.content})
        elif m.tool_calls:
            # Replicamos o tool call proposto em texto (formato Qwen) e, se
            # houver conteúdo, adicionamos uma mensagem assistant de texto.
            tc_text = "".join(
                f'<tool_call>{{"name": "{c.name}", "arguments": '
                f"{json.dumps(c.arguments, ensure_ascii=False)}}}</tool_call>"
                for c in m.tool_calls
            )
            messages.append({"role": "assistant", "content": tc_text})
            if m.content:
                messages.append({"role": "assistant", "content": m.content})
        else:
            content = m.content or ""
            if content:
                messages.append({"role": m.role, "content": content})
    if system:
        messages = [{"role": "system", "content": system}] + messages

    tool_defs = None
    if tools:
        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters or {},
                },
            }
            for t in tools
        ]
    return messages, tool_defs


def _apply_fit(
    chat_messages: list[dict],
    *,
    system: str | None,
    tools: list[ToolDeclaration] | None,
    tool_defs: list[dict] | None,
    n_ctx: int,
    max_tokens_out: int,
    tokenizer_fn,
    provider: str,
    model: str,
):
    """Aplica o context budget nas mensagens serializadas (thread separada).

    Usa o tokenizer real do llama.cpp para medir; no fallback (tokenizer sem
    método tokenize) a heurística do módulo de contexto é usada. Retorna
    (messages, tool_defs, fit) — registrando truncamento via logger.
    """
    from app.ai import context as ctx

    # `_serialize_messages` já embute o system no topo de chat_messages.
    # Separa o system para contagem correta (sem dupla contagem) e re-insere
    # intacto ao final.
    sys_content = None
    body = list(chat_messages)
    if system is not None and body and body[0].get("role") == "system":
        sys_content = body[0].get("content")
        body = body[1:]

    tokenizer = None
    try:
        llm = tokenizer_fn()
        if hasattr(llm, "tokenize"):
            tokenizer = llm.tokenize
    except Exception:  # noqa: BLE001
        tokenizer = None

    fit = ctx.fit_context(
        body,
        system=sys_content,
        tools=tools,
        n_ctx=n_ctx,
        max_tokens_out=max_tokens_out,
        tokenizer=tokenizer,
        clip_current=True,
    )

    final = fit.messages
    if sys_content is not None:
        final = [{"role": "system", "content": sys_content}] + final

    if fit.truncated:
        logger.warning(
            "Contexto truncado (%s): %d/%d tokens, %d mensagens antigas descartadas "
            "(profile=%s)",
            model,
            fit.used_tokens,
            fit.limit_tokens,
            fit.dropped_old_messages,
            ctx.json.dumps(fit.profile, ensure_ascii=False),
        )
    return final, (tool_defs if tools else None), fit


class LocalLLMProvider(AIProvider):
    """Provedor generativo local (chato, streaming, análise, embeddings).

    Capacidades declaradas: gera/stream/analyze sempre; `tools` e `embed`
    apenas quando os artefatos locais (modelo de chat / de embedding) existem.
    """

    name = "local"

    def __init__(
        self,
        enabled: bool | None = None,
        model_path: str | None = None,
        embed_path: str | None = None,
        n_ctx: int | None = None,
        n_threads: int | None = None,
        temperature: float | None = None,
        llama_factory: Callable[..., object] | None = None,
        embed_factory: Callable[..., object] | None = None,
    ):
        self._enabled = (
            settings.ai_local_llm_enabled if enabled is None else enabled
        )
        self._model_path = model_path or settings.ai_local_llm_model_path or _default_model_path()
        self._embed_path = embed_path or settings.ai_local_llm_embed_path or _default_embed_path()
        self._n_ctx = n_ctx or settings.ai_local_llm_n_ctx
        self._n_threads = n_threads or settings.ai_local_llm_n_threads
        self._temperature = (
            settings.ai_local_llm_temperature
            if temperature is None
            else temperature
        )
        self._llama_factory = llama_factory
        self._embed_factory = embed_factory
        self.model = os.path.basename(self._model_path)

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def _has_chat_model(self) -> bool:
        return self._enabled and os.path.exists(self._model_path)

    def _has_embed_model(self) -> bool:
        return os.path.exists(self._embed_path)

    def _get_llm(self):
        llm = self._llama_factory
        if llm is not None:
            return llm() if callable(llm) else llm
        return _shared.get_llm(
            self._model_path,
            self._n_ctx,
            self._n_threads,
        )

    def _get_embed(self):
        emb = self._embed_factory
        if emb is not None:
            return emb() if callable(emb) else emb
        return _shared.get_embed(
            self._embed_path,
            self._n_ctx,
            self._n_threads,
        )

    @property
    def is_configured(self) -> bool:
        # Nunca depende do Gemini nem de internet: só do modelo local existir.
        return self._has_chat_model()

    @property
    def capabilities(self) -> set[str]:
        caps = {"generate", "stream", "analyze"}
        if self._has_embed_model():
            caps.add("embed")
        if self._has_chat_model():
            caps.add("tools")
        return caps

    # ------------------------------------------------------------------
    # Contrato AIProvider
    # ------------------------------------------------------------------
    async def generate(
        self,
        messages: list[AIMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDeclaration] | None = None,
    ) -> AIResponse:
        llm = await asyncio.to_thread(self._get_llm)
        chat_messages, tool_defs = _serialize_messages(messages, system, tools)
        out_tokens = max_tokens or 256

        # Fase 11.3 (#1) — context budget: mede e trunca inteligentemente.
        fit = await asyncio.to_thread(
            lambda: _apply_fit(
                chat_messages,
                system=system,
                tools=tools,
                tool_defs=tool_defs,
                n_ctx=self._n_ctx,
                max_tokens_out=out_tokens,
                tokenizer_fn=lambda: llm,
                provider=self.name,
                model=self.model or os.path.basename(self._model_path),
            )
        )
        chat_messages, tool_defs, _ = fit

        def _call():
            return llm.create_chat_completion(
                messages=chat_messages,
                tools=tool_defs,
                tool_choice="auto" if tool_defs else None,
                max_tokens=out_tokens,
                temperature=self._temperature if temperature is None else temperature,
            )

        try:
            result = await asyncio.to_thread(_call)
        except Exception as exc:  # noqa: BLE001 — vira erro de provedor legível
            logger.warning("Local LLM falhou: %s", exc)
            raise AIProviderError(f"Local LLM indisponível: {exc}") from exc

        message = result["choices"][0]["message"]
        text = (message.get("content") or "").strip()
        tool_calls = _parse_tool_calls(text)
        if tool_calls:
            text = ""
        return AIResponse(
            text=text,
            provider=self.name,
            model=self.model,
            tool_calls=tool_calls,
        )

    async def stream(
        self,
        messages: list[AIMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Streaming do LLM local. Fase 11.2: itera o generator em thread
        separada para não bloquear o event loop do asyncio."""
        import queue

        llm = await asyncio.to_thread(self._get_llm)
        chat_messages, _ = _serialize_messages(messages, system, None)
        kwargs = {
            "messages": chat_messages,
            "max_tokens": max_tokens or 256,
            "temperature": self._temperature if temperature is None else temperature,
            "stream": True,
        }

        chunk_queue: queue.Queue[str | None] = queue.Queue()

        def _generate():
            """Roda em thread separada — itera o generator do llama.cpp."""
            try:
                stream = llm.create_chat_completion(**kwargs)
                for chunk in stream:
                    delta = chunk["choices"][0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        chunk_queue.put(text)
            except Exception as exc:  # noqa: BLE001
                chunk_queue.put(exc)
            finally:
                chunk_queue.put(None)  # sentinel

        # Inicia a geração em thread separada
        loop = asyncio.get_event_loop()
        thread = loop.run_in_executor(None, _generate)

        try:
            while True:
                try:
                    chunk = await asyncio.to_thread(chunk_queue.get, timeout=120)
                except Exception:
                    break
                if chunk is None:
                    break
                if isinstance(chunk, Exception):
                    raise AIProviderError(f"Local LLM indisponível: {chunk}") from chunk
                yield chunk
        finally:
            # Garante que a thread termine
            try:
                await thread
            except Exception:  # noqa: BLE001
                pass

    async def analyze(
        self, text: str, *, instruction: str | None = None
    ) -> AIResponse:
        return await self.generate(
            [AIMessage(role="user", content=text)], system=instruction
        )

    async def embed(self, text: str) -> list[float]:
        emb = await asyncio.to_thread(self._get_embed)
        def _call():
            return emb.create_embedding(text)
        result = await asyncio.to_thread(_call)
        embeddings = (result or {}).get("data") or []
        if not embeddings:
            raise AIProviderError("Embedding local vazio")
        return list(embeddings[0].get("embedding") or [])

    def tool_result_message(
        self,
        tool_calls: list[ToolCall],
        results: list,
    ) -> list[AIMessage]:
        if len(tool_calls) != len(results):
            raise ValueError("tool_calls e results devem ter o mesmo tamanho")
        messages = []

        for call, result in zip(tool_calls, results):
            tc_text = (
                f'<tool_call>{{"name": "{call.name}", "arguments": '
                f"{json.dumps(call.arguments, ensure_ascii=False)}}}</tool_call>"
            )
            messages.append(AIMessage(role="assistant", content=tc_text))
            output = getattr(result, "output", str(result))
            messages.append(AIMessage(role="tool", content=output))
        return messages

    async def health_check(self) -> AIProviderStatus:
        if not self._enabled:
            return AIProviderStatus(
                status="unconfigured",
                provider=self.name,
                model=self.model,
                detail="Modelo local desabilitado (AI_LOCAL_LLM_ENABLED=false)",
            )
        if not os.path.exists(self._model_path):
            return AIProviderStatus(
                status="unconfigured",
                provider=self.name,
                model=self.model,
                detail=f"Modelo não encontrado: {self._model_path}",
            )
        try:
            llm = await asyncio.to_thread(self._get_llm)
            _ = llm  # carregou a instância => ONLINE (chat)
            emb_ok = self._has_embed_model()
            detail = "ok" + ("" if emb_ok else " (sem modelo de embedding)")
            return AIProviderStatus(
                status="ok", provider=self.name, model=self.model, detail=detail
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Healthcheck do modelo local falhou: %s", exc)
            return AIProviderStatus(
                status="error",
                provider=self.name,
                model=self.model,
                detail=f"{type(exc).__name__}: {exc}",
            )


def reset_local_llm_shared() -> None:
    """Descartada a instância compartilhada (isolamento em testes)."""
    _shared.reset()
