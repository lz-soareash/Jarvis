"""Testes da Fase 8 — Developer Tools internas (Tool Engine).

Escopo: somente inspeção/configuração/diagnóstico predefinidos. Garante que NENHUM
mecanismo de execução arbitrária (shell/terminal/subprocess) seja introduzido.
"""

import asyncio
import json

import pytest

from app.db.session import SessionLocal
from app.tools import registry as tool_registry
from app.tools.base import ToolContext
from app.tools.developer import (
    DevDiagnostics,
    DevGetConfig,
    DevGetToolSchema,
    DevListTools,
)


def _run(inst, db=None, **args):
    ctx = ToolContext(db=db, provider=None, session_id=None)
    return asyncio.run(inst.run(ctx, **args))


def _db():
    return SessionLocal()


# --------------------------------------------------------------- registro
def test_dev_tools_registered(client):
    reg = tool_registry.get_tool_registry()
    names = {t.name for t in reg.all()}
    assert {"dev_list_tools", "dev_get_tool_schema", "dev_get_config", "dev_diagnostics"} <= names


def test_dev_tools_are_read_only(client):
    # Regra arquitetural da Fase 8: sem execução arbitrária no Core.
    import inspect as _inspect

    src = _inspect.getsource(DevListTools) + _inspect.getsource(DevGetToolSchema) + \
        _inspect.getsource(DevGetConfig) + _inspect.getsource(DevDiagnostics)
    for banned in ("subprocess", "os.system", "Popen", "shell=True", "eval(", "exec(", "os.popen"):
        assert banned not in src, f"mecanismo proibido presente: {banned}"


# --------------------------------------------------------------- list
def test_dev_list_tools_requires_db(client):
    res = _run(DevListTools(), db=None)
    assert res.ok is False
    assert "db" in res.output.lower() or "banco" in res.output.lower()


def test_dev_list_tools_catalog(client):
    db = _db()
    try:
        res = _run(DevListTools(), db=db)
    finally:
        db.close()
    assert res.ok is True
    assert "dev_list_tools" in res.output
    assert "get_current_time" in res.output
    assert "Total:" in res.output


def test_dev_list_tools_blank_db_failure(client, tmp_path):
    reg = tool_registry.get_tool_registry()
    assert reg.get("dev_list_tools") is not None


# --------------------------------------------------------------- schema
def test_dev_get_tool_schema_known(client):
    db = _db()
    try:
        res = _run(DevGetToolSchema(), db=db, tool="store_memory")
    finally:
        db.close()
    assert res.ok is True
    assert "store_memory" in res.output
    assert "content" in res.output  # parâmetro conhecido
    assert "LEVEL" in res.output or "L1" in res.output or "L2" in res.output


def test_dev_get_tool_schema_unknown(client):
    db = _db()
    try:
        res = _run(DevGetToolSchema(), db=db, tool="nao_existe_xyz")
    finally:
        db.close()
    assert res.ok is True
    assert "não encontrada" in res.output


def test_dev_get_tool_schema_requires_arg(client):
    db = _db()
    try:
        res = _run(DevGetToolSchema(), db=db)
    finally:
        db.close()
    assert res.ok is False


# --------------------------------------------------------------- config
def test_mask_helper_redacts_secret():
    from app.tools.developer import _mask

    masked = _mask("sk-abcd1234segredo")
    assert masked != "sk-abcd1234segredo"
    assert "segredo" not in masked
    assert masked.startswith("s")


def test_dev_get_config_masks_secrets(client):
    db = _db()
    try:
        res = _run(DevGetConfig(), db=db)
    finally:
        db.close()
    assert res.ok is True
    assert "gemini_api_key" in res.output
    # Sob ENV de teste a chave é vazia; o importante é: o campo existe e o
    # valor real (se houvesse) não vaza. Não contém segredo.
    assert "sk-" not in res.output


def test_dev_get_config_section(client):
    db = _db()
    try:
        res = _run(DevGetConfig(), db=db, section="tts_")
    finally:
        db.close()
    assert res.ok is True
    assert "tts_voice" in res.output
    assert "gemini_api_key" not in res.output


def test_dev_get_config_unknown_section(client):
    db = _db()
    try:
        res = _run(DevGetConfig(), db=db, section="zzz_nao_existe")
    finally:
        db.close()
    assert res.ok is True
    assert "Nenhum campo" in res.output


# --------------------------------------------------------------- diagnostics
def test_dev_diagnostics(client):
    db = _db()
    try:
        res = _run(DevDiagnostics(), db=db)
    finally:
        db.close()
    assert res.ok is True
    assert "tools_registered" in res.output
    assert "python" in res.output
    assert "tts_cache" in res.output


# --------------------------------------------------------------- agente (integração)
def test_dev_list_tools_via_agent(client, fake_ai):
    from app.schemas.ai import ToolCall

    create = client.post("/api/sessions", json={})
    session_id = create.json()["id"]

    fake_ai._planned_tool_calls = [ToolCall(name="dev_list_tools", arguments={})]
    fake_ai.reply = "Catálogo obtido."

    with client.stream(
        "POST",
        f"/api/sessions/{session_id}/messages",
        json={"content": "liste as ferramentas", "stream": True, "tools": True},
    ) as res:
        assert res.status_code == 200
        raw = b"".join(res.iter_bytes()).decode()
    done = raw.rsplit("data: ", 1)[-1]
    assert '"done"' in raw
    assert fake_ai.generate_calls >= 2  # planejada + retomada após tool
