"""Testes da Fase 11.3 (#1) — Context Budget (context.py)."""

from app.ai.context import fit_context, estimate_input_tokens
from app.schemas.ai import ToolDeclaration


class FakeTokenizer:
    """Tokenizer determinístico: conta tokens por aproximadamente 4 chars."""

    def __call__(self, text: str):
        import math

        n = max(1, int(len(text) / 4))
        return list(range(n))


def _msgs(*contents):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": c} for i, c in enumerate(contents)]


def test_estimate_input_tokens():
    assert estimate_input_tokens("hello world", FakeTokenizer()) >= 1
    # heurística: ~4 chars por token
    assert estimate_input_tokens("a" * 400) >= 90


def test_small_context_fits_untouched():
    msgs = _msgs("oi", "olá")
    sys = "Você é o JARVIS."
    fit = fit_context(msgs, system=sys, n_ctx=4096, max_tokens_out=256, tokenizer=FakeTokenizer())
    assert fit.truncated is False
    assert fit.messages == msgs
    assert fit.limit_tokens == 4096


def test_large_history_truncates_old_but_keeps_current():
    old = [f"mensagem antiga {i} com bastante conteúdo para encher o contexto " * 5 for i in range(200)]
    current = "ESTA É A MENSAGEM ATUAL — deve ser sempre preservada"
    msgs = _msgs(*old) + [{"role": "user", "content": current}]
    sys = "system prompt essencial"
    fit = fit_context(msgs, system=sys, n_ctx=512, max_tokens_out=64, tokenizer=FakeTokenizer())
    assert fit.truncated is True
    assert fit.dropped_old_messages >= 1
    # A mensagem atual deve estar presente intacta.
    assert any(m.get("content") == current for m in fit.messages)
    # O system deve ser preservado.
    assert fit.used_tokens <= fit.limit_tokens


def test_current_message_never_dropped():
    msgs = _msgs("a" * 200, "b" * 200, "MENSAGEM ATUAL")
    sys = "sys"
    fit = fit_context(msgs, system=sys, n_ctx=50, max_tokens_out=10, tokenizer=FakeTokenizer())
    assert any(m.get("content") == "MENSAGEM ATUAL" for m in fit.messages)
    # Mesmo que estoure, a mensagem atual é mantida (política de preservação).
    assert fit.used_tokens >= 0


def test_tool_declarations_counted_as_fixed_cost():
    tools = [
        ToolDeclaration(name="open_application", description="abre app", parameters={"type": "object"}),
        ToolDeclaration(name="open_url", description="abre url", parameters={"type": "object"}),
    ]
    msgs = _msgs("oi")
    fit = fit_context(msgs, system="sys", tools=tools, n_ctx=4096, max_tokens_out=256, tokenizer=FakeTokenizer())
    assert fit.profile["tool_tokens"] > 0


def test_empty_history():
    fit = fit_context([], system="sys", n_ctx=4096, max_tokens_out=256, tokenizer=FakeTokenizer())
    assert fit.messages == []


def test_used_tokens_within_limit_when_truncated():
    msgs = _msgs(*["x" * 300 for _ in range(100)], "atual")
    sys = "sys " * 50
    fit = fit_context(msgs, system=sys, n_ctx=1024, max_tokens_out=256, tokenizer=FakeTokenizer())
    # Reserva de saída (25%) + consumo deve caber.
    assert fit.used_tokens <= fit.limit_tokens


def test_giant_current_message_is_clipped_when_requested():
    # Mensagem atual gigante: sem clip ela sozinha estouraria a janela.
    current = "ATUAL " * 5000
    msgs = _msgs("histórico curto") + [{"role": "user", "content": current}]
    sys = "system"
    fit = fit_context(
        msgs, system=sys, n_ctx=512, max_tokens_out=64, tokenizer=FakeTokenizer(), clip_current=True
    )
    assert fit.truncated is True
    # Deve ter cortado a mensagem atual para caber no orçamento.
    kept_current = fit.messages[-1].get("content") or ""
    assert len(kept_current) < len(current)
    # Limite de modelo não estourado após o clip (ignora a reserva já contida).
    body_tokens = sum(estimate_input_tokens(m.get("content") or "", FakeTokenizer()) + 4 for m in fit.messages)
    assert body_tokens <= fit.limit_tokens


def test_giant_current_message_preserved_without_clip_flag():
    current = "ATUAL " * 5000
    msgs = _msgs("histórico") + [{"role": "user", "content": current}]
    fit = fit_context(msgs, system="sys", n_ctx=512, max_tokens_out=64, tokenizer=FakeTokenizer())
    assert (fit.messages[-1].get("content") or "") == current
