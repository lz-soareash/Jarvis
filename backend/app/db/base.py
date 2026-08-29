from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base declarativa dos modelos de domínio do JARVIS.

    A persistência é SQLite nesta fase; os modelos usam apenas SQLAlchemy 2.x,
    permitindo migrar para PostgreSQL futuramente apenas trocando a URL.
    """