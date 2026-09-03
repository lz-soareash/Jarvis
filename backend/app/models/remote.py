"""Modelos de identidade remota (Fase 12.2).

Entidades:
- `Device`: identidade de um dispositivo (id estável, emitido pelo servidor ou
  configurado no bootstrap do root device). `PENDING` → `ACTIVE` → `REVOKED`.
- `Credential`: token de autenticação de um device. Apenas o hash derivado
  (`sha256:<hex>`) é persistido — nunca o valor bruto. Revogação e expiração
  por linha; múltiplas credenciais por device (rotação).
- `PairingRequest`: pedido de pairing (código curto salvo apenas derivado,
  single-use, TTL, teto de tentativas). Relaciona-se ao `Device` apenas após
  sucesso (`device_id` nulo até lá).
- `RemoteSession`: sessão remota separada do device (CREATED/AUTHENTICATED/
  ACTIVE/ENDED/REVOKED). Revogar device/sessão preserva o histórico de auditoria.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    DeviceStatus,
    DeviceType,
    PairingStatus,
    RemoteCommandStatus,
    RemoteSessionStatus,
)
from app.db.base import Base
from app.models.session import ensure_utc, utcnow


def _uuid() -> str:
    return str(uuid4())


class Device(Base):
    __tablename__ = "remote_devices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="Dispositivo remoto")
    device_type: Mapped[str] = mapped_column(
        String(20), default=DeviceType.DESKTOP.value
    )
    status: Mapped[str] = mapped_column(
        String(20), default=DeviceStatus.PENDING.value, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Sessão JARVIS (tabela `sessions`) dedicada a este device remoto (Fase 12.3).
    # Usada como alvo dos `ApprovalRequest` (FK obrigatória para `sessions.id`)
    # e p/ auditoria de comandos remotos. Estável por device, não por conexão.
    jarvis_session_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )

    credentials: Mapped[list["Credential"]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
        order_by="Credential.created_at",
    )
    sessions: Mapped[list["RemoteSession"]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
        order_by="RemoteSession.created_at",
    )

    @property
    def is_active(self) -> bool:
        return self.status == DeviceStatus.ACTIVE.value


class Credential(Base):
    __tablename__ = "remote_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("remote_devices.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    device: Mapped[Device] = relationship(back_populates="credentials")

    @property
    def is_active(self) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and ensure_utc(self.expires_at) <= utcnow():
            return False
        return True


class PairingRequest(Base):
    __tablename__ = "remote_pairings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code_hash: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(
        String(20), default=PairingStatus.CREATED.value, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Device é vinculado SOMENTE após o código ser consumido com sucesso.
    device_id: Mapped[str | None] = mapped_column(
        ForeignKey("remote_devices.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_remote_pairings_status_expires", "status", "expires_at"),
    )


class RemoteSession(Base):
    __tablename__ = "remote_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("remote_devices.id"), index=True)
    credential_id: Mapped[str | None] = mapped_column(
        ForeignKey("remote_credentials.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default=RemoteSessionStatus.CREATED.value, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    transport_meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    device: Mapped[Device] = relationship(back_populates="sessions")


class RemoteCommand(Base):
    """Comando remoto (Fase 12.3) — transporte de comandos p/ o fluxo interno.

    Registro PERSISTIDO de cada comando para idempotência e rastreamento:
    `(device_id, command_id)` é UNIQUE, garantindo atomicidade a nível de banco
    (duas chegadas simultâneas do mesmo command_id não executam duas vezes).

    Estados: REGISTERED (chegou, ainda não avaliado), PENDING_APPROVAL (nível ≥ 2
    aguardando decisão), EXECUTING, EXECUTED, FAILED, EXPIRED. `expires_at`
    rejeita comandos/approvals velhos (TTL).
    """

    __tablename__ = "remote_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("remote_devices.id"), index=True)
    command_id: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(String(60), default="tool_call")
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30),
        default=RemoteCommandStatus.REGISTERED.value,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Aprovação que governa este comando (Fase 12.4): quando um comando de
    # nível ≥ 2 é pausado em PENDING_APPROVAL, este registro guarda o
    # ApprovalRequest correspondente, ligando a decisão do usuário ao comando
    # exato a ser retomado (estável, independe da conexão WebSocket).
    approval_id: Mapped[str | None] = mapped_column(
        ForeignKey("approval_requests.id"), nullable=True, index=True
    )

    __table_args__ = (
        Index("ix_remote_commands_device_cmd", "device_id", "command_id", unique=True),
    )