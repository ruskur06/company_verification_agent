"""Database engine and session factory."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _create_engine(database_url: str):
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_engine(database_url, connect_args=connect_args, echo=False)


engine = _create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def configure_engine(database_url: str) -> None:
    """Reconfigure the global engine and session factory (used in tests)."""
    global engine, SessionLocal

    engine = _create_engine(database_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency: yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_company_check_lock_column() -> None:
    """Add is_locked to existing company_check_records tables.

    create_all() does not alter existing tables, so local/dev databases need this
    lightweight bootstrap step.
    """
    inspector = inspect(engine)
    if "company_check_records" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("company_check_records")}
    if "is_locked" in columns:
        return

    dialect_name = engine.dialect.name
    if dialect_name == "postgresql":
        statement = (
            "ALTER TABLE company_check_records "
            "ADD COLUMN IF NOT EXISTS is_locked BOOLEAN NOT NULL DEFAULT FALSE"
        )
    elif dialect_name == "sqlite":
        statement = (
            "ALTER TABLE company_check_records "
            "ADD COLUMN is_locked BOOLEAN NOT NULL DEFAULT 0"
        )
    else:
        statement = (
            "ALTER TABLE company_check_records "
            "ADD COLUMN is_locked BOOLEAN NOT NULL DEFAULT FALSE"
        )

    with engine.begin() as connection:
        connection.execute(text(statement))


def _ensure_source_relevance_columns() -> None:
    """Add relevance fields to existing source_records tables."""
    inspector = inspect(engine)
    if "source_records" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("source_records")}
    dialect_name = engine.dialect.name

    statements: list[str] = []
    if "relevance" not in columns:
        if dialect_name == "postgresql":
            statements.append(
                "ALTER TABLE source_records "
                "ADD COLUMN IF NOT EXISTS relevance VARCHAR(20) NOT NULL DEFAULT 'uncertain'"
            )
        else:
            statements.append(
                "ALTER TABLE source_records "
                "ADD COLUMN relevance VARCHAR(20) NOT NULL DEFAULT 'uncertain'"
            )

    if "relevance_score" not in columns:
        if dialect_name == "postgresql":
            statements.append(
                "ALTER TABLE source_records "
                "ADD COLUMN IF NOT EXISTS relevance_score DOUBLE PRECISION NOT NULL DEFAULT 0.0"
            )
        elif dialect_name == "sqlite":
            statements.append(
                "ALTER TABLE source_records "
                "ADD COLUMN relevance_score REAL NOT NULL DEFAULT 0.0"
            )
        else:
            statements.append(
                "ALTER TABLE source_records "
                "ADD COLUMN relevance_score DOUBLE PRECISION NOT NULL DEFAULT 0.0"
            )

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def init_db() -> None:
    """Create all tables. Called on startup."""
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_company_check_lock_column()
    _ensure_source_relevance_columns()
