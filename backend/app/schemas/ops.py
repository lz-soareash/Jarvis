"""Schemas da Central de Operações (Fase 11) e observabilidade."""

from datetime import datetime

from app.schemas.base import APIModel


class ExecutionEventOut(APIModel):
    id: int
    session_id: str | None = None
    event_type: str
    provider: str | None = None
    model: str | None = None
    status: str | None = None
    latency_ms: int | None = None
    meta: dict | None = None
    created_at: datetime


class OpsProvider(APIModel):
    name: str
    model: str | None = None
    configured: bool
    status: str  # ok | unconfigured


class OpsProviderLive(OpsProvider):
    detail: str | None = None
    code: str | None = None


class OpsAICore(APIModel):
    active_provider: str | None = None
    active_model: str | None = None
    fallback_active: bool = False
    router_ready: bool = False


class OpsOverview(APIModel):
    ai_core: OpsAICore
    providers: list[OpsProvider]
    memory: dict  # total, by_kind, recent
    tasks: dict  # pending_approvals, recent_executions
    tools: dict  # total, by_permission
    atlas: dict  # enabled, configured, base_url, detail
    system: dict  # db, version
    recent_events: list[ExecutionEventOut]