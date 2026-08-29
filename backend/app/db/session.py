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