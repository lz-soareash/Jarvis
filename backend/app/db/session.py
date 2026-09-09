from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings

from .base import Base


def _create_engine():
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    url = settings.database_url
    if url.startswith("sqlite"):
        if url == "sqlite:///:memory:":
            kwargs["poolclass"] = StaticPool
        elif url.startswith("sqlite:///"):
            db_path = url.removeprefix("sqlite:///")
            if db_path not in ("", ":memory:"):
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


engine = _create_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    """Cria as tabelas declaradas (modelos de domínio)."""
    from app import models  # noqa: F401 — registra tabelas no metadata

    Base.metadata.create_all(bind=engine)
    _enable_wal_for_file_db()
    _ensure_schema()


def _enable_wal_for_file_db() -> None:
    """Fase 12.1: avaliação SQLite — WAL para bancos em arquivo.

    WAL permite leitores concorrentes com um único escritor (útil quando o
    transporte remoto e a API escrevem no mesmo arquivo). Banco de testes
    (`:memory:`) não é afetado. Best-effort: falhas não bloqueiam o boot.
    """
    url = settings.database_url
    if not url.startswith("sqlite:") or ":memory:" in url:
        return
    try:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.commit()
    except Exception:  # pragma: no cover — journal não configurável não bloqueia
        pass

def _ensure_schema() -> None:
    """Migrações idempotentes de schema para bancos já existentes.

    `create_all` não altera tabelas já criadas; colunas adicionadas depois da
    primeira execução precisam de um ALTER TABLE manual. Vamos apenas
    adicionar colunas quando estiverem ausentes (SQLite PRAGMA + ALTER).
    """
    try:
        with engine.connect() as conn:
            cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(messages)")).fetchall()
            }
            if "metadata_json" not in cols:
                conn.execute(text("ALTER TABLE messages ADD COLUMN metadata_json TEXT"))
                conn.commit()
    except Exception:  # pragma: no cover — plataformas/estados sem suporte não bloqueiam
        pass

    _ensure_memories_schema()
    _ensure_remote_commands_schema()
    _ensure_device_bridge_schema()


def _ensure_memories_schema() -> None:
    """Fase 19.5 — colunas aditivas de `memories` para bancos já existentes.

    `project`/`confidence`/`source`/`expires_at` nascem junto com o modelo nas
    bases novas; nas bases legadas entram via ALTER idempotente (PRAGMA). Nada
    é destrutivo; falhas não bloqueiam o boot (best-effort).
    """
    additions = {
        "project": "VARCHAR(200)",
        "confidence": "VARCHAR(40) DEFAULT 'unverified'",
        "source": "VARCHAR(60)",
        "expires_at": "DATETIME",
    }
    try:
        with engine.connect() as conn:
            cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(memories)")).fetchall()
            }
            for name, ddl in additions.items():
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE memories ADD COLUMN {name} {ddl}"))
            conn.commit()
    except Exception:  # pragma: no cover — estados sem suporte não bloqueiam
        pass


def _ensure_remote_commands_schema() -> None:
    """Fase 12.4-R1 — UNIQUE em `remote_commands.approval_id` p/ bancos legados.

    Para bancos já criados antes da Fase 12.4-R1, `create_all` não reconstrói a
    tabela e o `UniqueConstraint` declarado no modelo não é aplicado. Recriamos
    a garantia (1:1 Approval↔Command) via UNIQUE INDEX idempotente. SQLite permite
    múltiplos NULLs em índice UNIQUE, então comandos sem aprovação não são
    afetados (não destrutivo). Falha não bloqueia o boot (best-effort).
    """
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_remote_commands_approval_id "
                    "ON remote_commands(approval_id)"
                )
            )
            conn.commit()
    except Exception:  # pragma: no cover — estados sem suporte não bloqueiam
        pass


def _ensure_device_bridge_schema() -> None:
    """Fase 21 — colunas aditivas do Device Bridge em `remote_devices`.

    `platform`/`client_version`/`capabilities_json` nascem junto com o modelo
    nas bases novas; nas bases legadas entram via ALTER idempotente (PRAGMA +
    ADD COLUMN). Nada é destrutivo; falhas não bloqueiam o boot (best-effort).
    """
    additions = {
        "platform": "VARCHAR(20) DEFAULT 'web'",
        "client_version": "VARCHAR(40)",
        "capabilities_json": "TEXT",
    }
    try:
        with engine.connect() as conn:
            cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(remote_devices)")).fetchall()
            }
            for name, ddl in additions.items():
                if name not in cols:
                    conn.execute(
                        text(f"ALTER TABLE remote_devices ADD COLUMN {name} {ddl}")
                    )
            conn.commit()
    except Exception:  # pragma: no cover — estados sem suporte não bloqueiam
        pass


def check_database() -> bool:
    """Healthcheck de conectividade com o banco."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()