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


def _ensure_official_website_review_columns() -> None:
    """Add official website review fields to existing company_check_records tables."""
    inspector = inspect(engine)
    if "company_check_records" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("company_check_records")}
    dialect_name = engine.dialect.name

    column_defs = {
        "official_website_review_decision": "VARCHAR(20)",
        "official_website_review_note": "TEXT",
        "official_website_review_reviewed_by": "VARCHAR(255)",
        "official_website_review_reviewed_at": "TIMESTAMP",
    }

    statements: list[str] = []
    for column_name, column_type in column_defs.items():
        if column_name in columns:
            continue
        if dialect_name == "postgresql":
            statements.append(
                f"ALTER TABLE company_check_records "
                f"ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
            )
        else:
            statements.append(
                f"ALTER TABLE company_check_records "
                f"ADD COLUMN {column_name} {column_type}"
            )

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_approved_request_pipeline_columns() -> None:
    """Add approved-request pipeline columns to existing tables."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    dialect_name = engine.dialect.name
    statements: list[str] = []

    def _append_column_statement(
        table_name: str,
        column_name: str,
        column_type: str,
        existing_columns: set[str],
    ) -> None:
        if column_name in existing_columns:
            return
        if dialect_name == "postgresql":
            statements.append(
                f"ALTER TABLE {table_name} "
                f"ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
            )
        else:
            statements.append(
                f"ALTER TABLE {table_name} "
                f"ADD COLUMN {column_name} {column_type}"
            )

    if "check_request_records" in table_names:
        request_columns = {
            column["name"]
            for column in inspector.get_columns("check_request_records")
        }
        _append_column_statement(
            "check_request_records",
            "processing_started_at",
            "TIMESTAMP",
            request_columns,
        )
        _append_column_statement(
            "check_request_records",
            "processing_check_id",
            "VARCHAR(64)",
            request_columns,
        )

    if "company_check_records" in table_names:
        check_columns = {
            column["name"]
            for column in inspector.get_columns("company_check_records")
        }
        _append_column_statement(
            "company_check_records",
            "source_check_request_id",
            "INTEGER",
            check_columns,
        )

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _validate_unique_single_column_index(
    *,
    table_name: str,
    index_name: str,
    expected_column: str,
    existing_indexes: list[dict],
) -> bool:
    """Return True when the named unique index already exists correctly."""
    matching = [
        index for index in existing_indexes if index.get("name") == index_name
    ]
    if not matching:
        return False

    index_info = matching[0]
    column_names = list(index_info.get("column_names") or [])
    if not index_info.get("unique"):
        raise RuntimeError(
            f"Schema invariant failed: index {index_name} on "
            f"{table_name} must be unique"
        )
    if column_names != [expected_column]:
        raise RuntimeError(
            f"Schema invariant failed: index {index_name} on "
            f"{table_name} must cover exactly [{expected_column}], "
            f"found {column_names}"
        )
    return True


def _require_unique_single_column_index(
    *,
    table_name: str,
    index_name: str,
    expected_column: str,
    existing_indexes: list[dict],
) -> None:
    """Raise when the named unique index is missing or malformed."""
    if not _validate_unique_single_column_index(
        table_name=table_name,
        index_name=index_name,
        expected_column=expected_column,
        existing_indexes=existing_indexes,
    ):
        raise RuntimeError(
            f"Schema invariant failed: index {index_name} on "
            f"{table_name} was expected after bootstrap"
        )


def _ensure_approved_request_pipeline_indexes() -> None:
    """Create unique nullable indexes required by the approved-request pipeline."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    statements: list[str] = []
    required_indexes = (
        (
            "check_request_records",
            "ux_check_request_records_processing_check_id",
            "processing_check_id",
        ),
        (
            "company_check_records",
            "ux_company_check_records_source_check_request_id",
            "source_check_request_id",
        ),
    )
    tables_needing_indexes: list[tuple[str, str, str]] = []

    for table_name, index_name, column_name in required_indexes:
        if table_name not in table_names:
            continue

        existing_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        if column_name not in existing_columns:
            raise RuntimeError(
                f"Schema invariant failed: column {table_name}.{column_name} "
                f"must exist before creating index {index_name}"
            )

        existing_indexes = inspector.get_indexes(table_name)
        if _validate_unique_single_column_index(
            table_name=table_name,
            index_name=index_name,
            expected_column=column_name,
            existing_indexes=existing_indexes,
        ):
            continue

        tables_needing_indexes.append((table_name, index_name, column_name))
        statements.append(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
            f"ON {table_name} ({column_name})"
        )

    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))

        post_inspector = inspect(engine)
        for table_name, index_name, column_name in tables_needing_indexes:
            _require_unique_single_column_index(
                table_name=table_name,
                index_name=index_name,
                expected_column=column_name,
                existing_indexes=post_inspector.get_indexes(table_name),
            )


def init_db() -> None:
    """Create all tables. Called on startup."""
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_company_check_lock_column()
    _ensure_source_relevance_columns()
    _ensure_official_website_review_columns()
    _ensure_approved_request_pipeline_columns()
    _ensure_approved_request_pipeline_indexes()
