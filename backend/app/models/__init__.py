"""Modelos de persistência do domínio JARVIS (SQLAlchemy 2.x)."""

from app.models.governance import ApprovalRequest, AuditLog, ToolPolicy
from app.models.memory import Memory
from app.models.ops import ExecutionEvent
from app.models.research import KnowledgeRecord, ResearchRun
from app.models.tasks import AgentTask
from app.models.remote import (
    Credential,
    Device,
    PairingRequest,
    RemoteCommand,
    RemoteOutbox,
    RemoteSession,
)
from app.models.proactive import (
    ProactiveInboxEvent,
    ProactiveMessage,
    ProactiveSchedule,
)
from app.models.session import Message, Session, utcnow

__all__ = [
    "Message",
    "Session",
    "Memory",
    "ApprovalRequest",
    "AuditLog",
    "ToolPolicy",
    "ExecutionEvent",
    "AgentTask",
    "ResearchRun",
    "KnowledgeRecord",
    "Device",
    "Credential",
    "PairingRequest",
    "RemoteSession",
    "RemoteCommand",
    "RemoteOutbox",
    "ProactiveInboxEvent",
    "ProactiveSchedule",
    "ProactiveMessage",
    "utcnow",
]