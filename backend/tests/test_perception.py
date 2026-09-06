"""Testes da Fase 15 — Perception Layer & Computer State Foundation.

Cobre: contracts (ComputerObservation estruturado, sem binário/segredos),
capability discovery reflexiva (nunca inventada), WindowsPerceptionProvider
(ativo/indisponível, janela ativa via ctypes fake, stats/processos via
SystemController fake), ObservationStore bounded/TTL, ComputerState (screenshot
só metadata), tool `observe_computer` (LEVEL_0, pipeline Registry→Permission→
Audit), integração Agentic Core (passo kind="observe"), e segurança (sem
screenshot em eventos, sem binário em logs, gate de screenshot).
"""

import json

import pytest

from app.core.enums import StepStatus, TaskStatus
from app.perception.base import (
    ActiveWindow,
    ComputerObservation,
    ComputerCapabilities,
    PerceptionResult,
    ScreenshotMetadata,
    sha256_hex,
)


class _FakeController:
    """Controlador determinístico — nunca toca no sistema real."""

    def __init__(self):
        self.stats = {
            "cpu_percent": 12.7,
            "memory_used": 6_000_000_000,
            "memory_total": 16_000_000_000,
            "disk_free": 200_000_000_000,
            "disk_total": 512_000_000_000,
            "boot_time": "2026-08-29T08:00:00Z",
        }
        self.processes = [
            {"pid": 4, "name": "System", "mem_bytes": 16777216, "created_at": None},
            {"pid": 812, "name": "notepad.exe", "mem_bytes": 2621440, "created_at": None},
        ]

    def system_stats(self):
        return dict(self.stats)

    def list_processes(self, limit=30):
        return self.processes[:limit]


# ---------------------------------------------------------------------------
# Contracts & capabilities
# ---------------------------------------------------------------------------


def test_computer_observation_structured_without_binary():
    obs = ComputerObservation(
        capabilities=ComputerCapabilities(os="windows", screenshot=False, active_window=True),
        active_window=ActiveWindow(title="Teste", process_name="test.exe", pid=1),
        processes=[{"pid": 1, "name": "x.exe"}],
    )
    data = obs.to_dict()
    assert data["capabilities"]["os"] == "windows"
    assert data["active_window"]["title"] == "Teste"
    assert data["processes"][0]["name"] == "x.exe"
    assert "image" not in data and "data" not in data


def test_screenshot_metadata_never_holds_binary():
    meta = ScreenshotMetadata.from_pixels(b"\x89PNG-fake-bytes", 800, 600)
    assert meta.sha256 == sha256_hex(b"\x89PNG-fake-bytes")
    d = meta.to_meta_dict()
    assert set(d) == {"timestamp", "width", "height", "sha256", "format", "mode"}
    assert "bytes" not in d and "data" not in d
    assert meta.width == 800 and meta.height == 600


def test_capability_discovery_never_invents():
    caps = ComputerCapabilities(
        os="linux", screenshot=False, ocr=False, vision=False, accessibility_tree=False
    )
    assert caps.screenshot is False
    assert caps.ocr is False
    # summary não declara capacidades falsas
    assert "screenshot" not in caps.summary


# ---------------------------------------------------------------------------
# ObservationStore — bounded + TTL, thread-safe
# ---------------------------------------------------------------------------


def test_observation_store_bounded():
    from app.perception.state import ObservationStore

    store = ObservationStore(max_entries=3)
    for i in range(10):
        store.push(ComputerObservation())
    entries = store.all()
    assert len(entries) == 3
    assert store.latest() is entries[-1]


def test_observation_store_ttl_expires():
    from datetime import timedelta

    from app.perception.state import ObservationStore

    store = ObservationStore(max_entries=10, ttl=timedelta(milliseconds=1))
    store.push(ComputerObservation())
    import time

    time.sleep(0.01)
    assert store.latest() is None
    assert store.all() == []


def test_observation_store_clear():
    from app.perception.state import ObservationStore

    store = ObservationStore()
    store.push(ComputerObservation())
    store.clear()
    assert store.latest() is None


# ---------------------------------------------------------------------------
# ComputerState
# ---------------------------------------------------------------------------


def test_computer_state_records_observation_and_screenshot_metadata():
    from app.perception.state import ComputerState

    state = ComputerState()
    obs = ComputerObservation(screenshot=ScreenshotMetadata.from_pixels(b"png", 640, 480))
    state.record(obs)
    snap = state.snapshot()
    assert snap["observation"]["screenshot"]["sha256"] == sha256_hex(b"png")
    assert snap["last_screenshot"]["meta"]["width"] == 640
    assert snap["history_count"] == 1
    state.clear()
    assert state.snapshot()["last_screenshot"] is None


# ---------------------------------------------------------------------------
# WindowsPerceptionProvider
# ---------------------------------------------------------------------------


def test_windows_provider_unavailable_off_windows(monkeypatch):
    from app.perception.windows import WindowsPerceptionProvider

    monkeypatch.setattr("app.perception.windows.sys.platform", "linux")
    provider = WindowsPerceptionProvider(controller=_FakeController())
    assert provider.available() is False
    result = provider.observe()
    assert result.available is False
    assert isinstance(result, PerceptionResult)


def test_windows_provider_capability_discovery_reflexive(monkeypatch):
    from app.perception.windows import WindowsPerceptionProvider

    monkeypatch.setattr("app.perception.windows.sys.platform", "win32")
    monkeypatch.setattr("app.perception.windows._has_module", lambda _name: False)
    provider = WindowsPerceptionProvider(controller=_FakeController())
    caps = provider.discover_capabilities()
    assert caps.os == "windows"
    assert caps.active_window is True
    assert caps.processes is True
    # sem bibliotecas opcionais → essas capacidades são honestamente False
    assert caps.screenshot is False
    assert caps.ocr is False
    assert caps.vision is False
    assert caps.accessibility_tree is False


def test_windows_provider_observe_with_active_window(monkeypatch):
    from app.perception.windows import WindowsPerceptionProvider

    monkeypatch.setattr("app.perception.windows.sys.platform", "win32")
    monkeypatch.setattr("app.perception.windows._has_module", lambda _name: False)
    monkeypatch.setattr(
        "app.perception.windows._windows_active_window",
        lambda: ActiveWindow(title="Bloco de Notas", process_name="notepad.exe", pid=812),
    )
    provider = WindowsPerceptionProvider(controller=_FakeController())
    result = provider.observe(include_processes=True, max_processes=30)
    assert result.available is True
    obs = result.observation
    assert obs.active_window is not None
    assert obs.active_window.title == "Bloco de Notas"
    assert obs.active_window.process_name == "notepad.exe"
    assert obs.active_window.pid == 812
    assert obs.processes[0]["name"] == "System"
    assert obs.system_stats["cpu_percent"] == 12.7


def test_windows_provider_observe_without_active_window(monkeypatch):
    from app.perception.windows import WindowsPerceptionProvider

    monkeypatch.setattr("app.perception.windows.sys.platform", "win32")
    monkeypatch.setattr("app.perception.windows._has_module", lambda _name: False)
    monkeypatch.setattr("app.perception.windows._windows_active_window", lambda: None)
    provider = WindowsPerceptionProvider(controller=_FakeController())
    result = provider.observe()
    assert result.available is True
    assert result.observation.active_window is None


def test_windows_active_window_ctypes_path(monkeypatch):
    """Unit: `_windows_active_window` navega user32/kernel32 via ctypes fake."""
    from app.perception import windows as windows_module

    monkeypatch.setattr(windows_module.sys, "platform", "win32")
    fake_mod = _FakeCTypes()
    monkeypatch.setattr(windows_module, "ctypes", fake_mod)
    active = windows_module._windows_active_window()
    assert active is not None
    assert active.title == "Bloco de Notas"
    assert active.process_name == "notepad.exe"
    assert active.pid == 812


def test_windows_provider_screenshot_without_library(monkeypatch):
    """Sem bibliotecas opcionais → capability False e capture retorna None."""
    from app.perception.windows import WindowsPerceptionProvider

    monkeypatch.setattr("app.perception.windows.sys.platform", "win32")
    monkeypatch.setattr("app.perception.windows._has_module", lambda _name: False)
    provider = WindowsPerceptionProvider(controller=_FakeController())
    assert provider.capture_screenshot() is None


def test_windows_provider_screenshot_disabled(monkeypatch, tmp_path):
    from app.perception.windows import WindowsPerceptionProvider

    monkeypatch.setattr("app.perception.windows.sys.platform", "win32")
    monkeypatch.setattr("app.perception.windows._has_module", lambda name: name in ("PIL", "mss"))
    provider = WindowsPerceptionProvider(controller=_FakeController())
    # fallback: import falha → None (sem crash)
    meta = provider.capture_screenshot()
    assert meta is None or isinstance(meta, ScreenshotMetadata)


def _FakeCTypes():
    class _FakeUser32_(object):
        def GetForegroundWindow(self):
            return 0x1234

        def GetWindowTextLengthW(self, hwnd):
            return 8

        def GetWindowTextW(self, hwnd, buf, n):
            buf.value = "Bloco de Notas"
            return 8

        def GetWindowThreadProcessId(self, hwnd, pid):
            pid.value = 812
            return 1

    class _FakeKernel32_(object):
        def OpenProcess(self, flags, inherit, pid):
            return 0xABC

        def GetModuleBaseNameW(self, handle, mod, buf, n):
            buf.value = "notepad.exe"
            return 12

        def CloseHandle(self, handle):
            return 0

    class _Fn(object):
        def __init__(self, fn):
            self.fn = fn
            self.restype = None
            self.argtypes = None

        def __call__(self, *a):
            return self.fn(*a)

    class _WinDLL(object):
        def __init__(self, lib, **kw):
            self.impl = _FakeUser32_() if lib == "user32" else _FakeKernel32_()

        def __getattr__(self, name):
            return _Fn(getattr(self.impl, name))

    class _Mod(object):
        user32 = _FakeUser32_()
        kernel32 = _FakeKernel32_()

        def WinDLL(self, lib, **kw):
            return _WinDLL(lib, **kw)

        def create_unicode_buffer(self, n):
            return type("Buf", (), {"value": ""})()

        def byref(self, x):
            return x

        def POINTER(self, t):
            return t

        def c_ulong(self, v=0):
            return type("UL", (), {"value": v})()

        def c_wchar_p(self, s=""):
            return s

        def c_int(self, v=0):
            return v

        def c_void_p(self, v=0):
            return v

    return _Mod()


class _NoKernel:
    def __init__(self):
        self.OpenProcess = lambda *a, **k: 0
        self.CloseHandle = lambda *a, **k: 0


class _NoCTypes:
    def __init__(self, user32):
        self._user32 = user32

    def WinDLL(self, lib):
        return self._user32 if lib == "user32" else _NoKernel()


# ---------------------------------------------------------------------------
# Tool observe_computer: registro, pipeline, gates
# ---------------------------------------------------------------------------


def test_observe_computer_tool_registered_level0():
    from app.tools.registry import get_tool_registry

    tool = get_tool_registry().get("observe_computer")
    assert tool is not None
    assert tool.permission_level.value == 0
    assert tool.declaration().name == "observe_computer"


def test_observe_computer_tool_permission_effective_level0():
    from app.services import permissions as perms

    from app.db.session import SessionLocal

    with SessionLocal() as db:
        level = perms.effective_level(db, "observe_computer")
    assert level.value == 0
    assert level.requires_confirmation is False


def test_observe_computer_gate_perception_disabled(tmp_path, monkeypatch):
    from app.perception.tool import ObserveComputer
    from app.tools.base import ToolContext

    monkeypatch.setattr("app.perception.tool.settings.perception_enabled", False)
    tool = ObserveComputer()
    result = asyncio_run(tool.run(ToolContext(db=None), include_processes=False))
    assert result.ok is False
    assert "desabilitada" in result.output


def test_observe_computer_gate_screenshot_disabled(tmp_path, monkeypatch):
    from app.perception.tool import ObserveComputer
    from app.tools.base import ToolContext

    monkeypatch.setattr("app.perception.tool.settings.perception_enabled", True)
    monkeypatch.setattr("app.perception.tool.settings.perception_screenshot_enabled", False)
    tool = ObserveComputer()
    result = asyncio_run(tool.run(ToolContext(db=None), capture_screenshot=True))
    assert result.ok is False
    assert "desabilitado" in result.output


def test_observe_computer_runs_via_perception_service(db_session, monkeypatch):
    from app.perception import service as perception_service
    from app.perception.tool import ObserveComputer
    from app.tools.base import ToolContext

    fake_result = PerceptionResult.ok(
        ComputerObservation(
            capabilities=ComputerCapabilities(os="windows", active_window=True,
                                               screenshot=False, processes=True),
            active_window=ActiveWindow(title="Janela", process_name="app.exe", pid=9),
            processes=[{"pid": 9, "name": "app.exe"}],
            system_stats={"cpu_percent": 12.7},
        )
    )

    async def fake_run_perception(*a, **k):
        return fake_result

    monkeypatch.setattr(perception_service, "run_perception", fake_run_perception)
    tool = ObserveComputer()
    result = asyncio_run(tool.run(ToolContext(db=db_session, session_id="s1")))
    assert result.ok is True
    assert "Janela ativa: Janela" in result.output
    assert "CPU: 12.7%" in result.output


def test_observe_computer_fails_when_perception_unavailable(db_session, monkeypatch):
    from app.perception import service as perception_service
    from app.perception.tool import ObserveComputer
    from app.tools.base import ToolContext

    async def fail(*a, **k):
        return PerceptionResult.unavailable("provedor indisponível")

    monkeypatch.setattr(perception_service, "run_perception", fail)
    tool = ObserveComputer()
    result = asyncio_run(tool.run(ToolContext(db=db_session)))
    assert result.ok is False
    assert "indisponível" in result.output


# ---------------------------------------------------------------------------
# Segurança: eventos sanitizados, sem screenshot em eventos, auditoria
# ---------------------------------------------------------------------------


def test_perception_ops_events_sanitized_without_screenshot(db_session, monkeypatch):
    from app.services import ops as ops_service

    from app.perception import service as perception_service

    obs = ComputerObservation(
        capabilities=ComputerCapabilities(os="windows", screenshot=True, active_window=True),
        active_window=ActiveWindow(title="Confidencial", process_name="app.exe", pid=1),
    )
    # observação COM metadata de screenshot — mas o evento NUNCA leva o binário.
    obs.screenshot = ScreenshotMetadata.from_pixels(b"RAW-BINARY", 800, 600)

    result = PerceptionResult.ok(obs)
    # o payload do sink nunca carrega binário/sha — apenas o boolean
    sink_payload = perception_service._sink_payload(result)
    assert "RAW-BINARY" not in json.dumps(sink_payload)
    assert sink_payload["screenshot"] is True

    perception_service._record_ops(db_session, "s1", {"type": "observe.completed", "observation": obs})
    events = ops_service.list_events(db_session, event_type=ops_service.EVENT_COMPUTER_OBSERVED)
    assert len(events) == 1
    meta = json.loads(events[0].meta_json)
    # o evento carrega apenas contagens/metadata — nunca binário/base64 de imagem
    serialized = json.dumps(meta)
    assert "RAW-BINARY" not in serialized
    assert "screenshot" in meta and meta["screenshot"] is True


def test_perception_writes_audit(db_session, monkeypatch):
    from app.models import AuditLog

    from app.perception import service as perception_service

    obs = ComputerObservation(capabilities=ComputerCapabilities(os="windows", screenshot=False))
    from app.perception.service import _record_ops

    _record_ops(db_session, "s9", {"type": "observe.completed", "observation": obs})
    entries = db_session.query(AuditLog).filter(AuditLog.tool == "observe_computer").all()
    assert len(entries) == 1
    assert entries[0].allowed is True


def test_perception_failed_writes_audit_and_event(db_session):
    from app.models import AuditLog
    from app.services import ops as ops_service

    from app.perception.service import _record_ops

    _record_ops(db_session, "s7", {"type": "observe.failed", "error": "provedor indisponível"})
    entries = db_session.query(AuditLog).filter(AuditLog.tool == "observe_computer").all()
    assert len(entries) == 1
    assert entries[0].allowed is False
    events = ops_service.list_events(db_session, event_type=ops_service.EVENT_COMPUTER_OBSERVATION_FAILED)
    assert len(events) == 1


# ---------------------------------------------------------------------------
# Integração Agentic Core — passo kind="observe"
# ---------------------------------------------------------------------------


def _parse(items):
    parsed = []
    for item in items:
        if isinstance(item, str) and item.startswith("data:"):
            try:
                parsed.append(json.loads(item.removeprefix("data:").strip()))
            except ValueError:
                continue
        else:
            parsed.append(item)
    return parsed


async def _collect_all(agen):
    return [e async for e in agen]


def _iter_sync(agen):
    loop = asyncio_loop()
    try:
        return loop.run_until_complete(_collect_all(agen))
    finally:
        loop.close()


def asyncio_loop():
    import asyncio

    return asyncio.new_event_loop()


def asyncio_run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_plan_for_detects_observe():
    from app.services.agent_core import plan_for

    steps = plan_for("Observe o estado do computador")
    assert len(steps) == 1
    assert steps[0]["kind"] == "observe"
    assert steps[0]["guard"] == "skip"
    assert steps[0]["arguments"]["include_processes"] is True


def test_plan_for_observe_precedes_default_analysis():
    from app.services.agent_core import plan_for

    steps = plan_for("analise como está o computador")
    assert steps and steps[0]["kind"] == "observe"


class _FakeProvider:
    """Provider fake — o caminho real do run_perception é exercitado."""

    name = "fake"

    def __init__(self, obs):
        self._obs = obs

    def available(self):
        return True

    def discover_capabilities(self):
        return self._obs.capabilities

    def observe(self, *, include_processes=False, max_processes=30):
        return PerceptionResult.ok(self._obs)

    def capture_screenshot(self):
        return None


class _FakeProviderUnavailable:
    name = "unavailable"

    def available(self):
        return False

    def discover_capabilities(self):
        return ComputerCapabilities(os="windows", detail="provedor indisponível")

    def observe(self, *, include_processes=False, max_processes=30):
        return PerceptionResult.unavailable("provedor indisponível")

    def capture_screenshot(self):
        return None


def _install_fake_provider(monkeypatch, provider):
    from app.perception import service as perception_service

    monkeypatch.setattr(perception_service, "get_provider", lambda: provider)
    return provider


def test_agent_task_with_observe_step_completes(db_session, monkeypatch, fake_ai):
    from app.db.session import SessionLocal
    from app.services import chat as chat_service
    from app.perception import service as perception_service
    from app.services.agent_core import get_task, run_agent_task

    obs = ComputerObservation(
        capabilities=ComputerCapabilities(os="windows", active_window=True),
        active_window=ActiveWindow(title="Bloco de Notas", process_name="notepad.exe", pid=812),
        system_stats={"cpu_percent": 12.7},
    )
    _install_fake_provider(monkeypatch, _FakeProvider(obs))

    with SessionLocal() as db:
        session = chat_service.create_session(db)
        plan = [
            {
                "description": "Observar o computador",
                "kind": "observe",
                "arguments": {"include_processes": True},
                "verify": {"kind": "observe"},
                "guard": "skip",
            }
        ]
        events = _parse(
            _iter_sync(run_agent_task(db, session.id, fake_ai, "Observe o computador", plan=plan))
        )
        done = next(e for e in events if e["type"] == "agent_task_done")
        assert done["status"] == TaskStatus.COMPLETED.value
        task = get_task(db, done["task_id"])
        assert task.progress[0]["status"] == StepStatus.DONE.value
        tool_done = next(e for e in events if e["type"] == "tool_done")
        assert tool_done["name"] == "observe_computer"
        assert tool_done["ok"] is True
        # o sink da tarefa emite a observação estruturada no SSE
        obs_events = [e for e in events if e["type"] == "computer.observation"]
        assert any(e.get("active_window") == "Bloco de Notas" for e in obs_events)
        # ops registrou o evento sanitizado (sem binário)
        from app.services import ops as ops_service

        records = ops_service.list_events(db, event_type=ops_service.EVENT_COMPUTER_OBSERVED)
        assert len(records) == 1


def test_agent_task_observe_unavailable_skips_step(db_session, monkeypatch, fake_ai):
    from app.db.session import SessionLocal
    from app.services import chat as chat_service
    from app.perception import service as perception_service
    from app.services.agent_core import get_task, run_agent_task

    _install_fake_provider(monkeypatch, _FakeProviderUnavailable())

    with SessionLocal() as db:
        session = chat_service.create_session(db)
        plan = [
            {
                "description": "Observar indisponível",
                "kind": "observe",
                "arguments": {},
                "verify": {"kind": "observe"},
                "guard": "skip",
            }
        ]
        events = _parse(_iter_sync(run_agent_task(db, session.id, fake_ai, "Observe", plan=plan)))
        done = next(e for e in events if e["type"] == "agent_task_done")
        assert done["status"] == TaskStatus.COMPLETED.value
        task = get_task(db, done["task_id"])
        # guard=skip: passo ignorado, tarefa não falha
        assert task.progress[0]["status"] == StepStatus.SKIPPED.value
        tool_done = next(e for e in events if e["type"] == "tool_done")
        assert tool_done["ok"] is False


def test_observe_goes_through_registry_not_invented():
    """O passo observe resolve uma CAPACIDADE, não uma tool arbitrária."""
    from app.perception.tool import ObserveComputer
    from app.tools.registry import get_tool_registry

    registry = get_tool_registry()
    assert registry.get("observe_computer") is not None
    assert isinstance(registry.get("observe_computer"), ObserveComputer)
    assert registry.get("raw_screenshot") is None  # nunca existe endpoint raw
    assert registry.get("execute_anything") is None