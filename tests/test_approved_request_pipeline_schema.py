from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.models import CheckRequestRecord, CompanyCheckRecord
from app.schemas.check_request import CheckRequestStatus


PROCESSING_INDEX = "ux_check_request_records_processing_check_id"
SOURCE_INDEX = "ux_company_check_records_source_check_request_id"


@pytest.fixture()
def sqlite_engine(tmp_path):
    """Configure an isolated temporary SQLite database."""
    database_url = f"sqlite:///{tmp_path / 'pipeline_schema.db'}"
    database.configure_engine(database_url)

    yield database.engine

    database.engine.dispose()


def _session_factory():
    return sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=database.engine,
    )


def _index_by_name(table_name: str, index_name: str) -> dict:
    indexes = inspect(database.engine).get_indexes(table_name)
    for index in indexes:
        if index.get("name") == index_name:
            return index
    raise AssertionError(f"Index {index_name} not found on {table_name}")


def _create_historical_tables() -> None:
    """Create minimal pre-pipeline table shapes without new columns/indexes."""
    with database.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE check_request_records (
                    id INTEGER PRIMARY KEY,
                    company_name VARCHAR(255) NOT NULL,
                    country VARCHAR(100) NOT NULL,
                    email VARCHAR(320) NOT NULL,
                    preferred_language VARCHAR(10) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    company_check_id VARCHAR(64),
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE company_check_records (
                    id INTEGER PRIMARY KEY,
                    check_id VARCHAR(64) NOT NULL UNIQUE,
                    company_name VARCHAR(255) NOT NULL,
                    country VARCHAR(100) NOT NULL,
                    human_review_status VARCHAR(50) DEFAULT 'pending',
                    is_locked BOOLEAN NOT NULL DEFAULT 0,
                    created_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO check_request_records (
                    id,
                    company_name,
                    country,
                    email,
                    preferred_language,
                    status,
                    company_check_id,
                    created_at
                ) VALUES (
                    1,
                    'Historical Request GmbH',
                    'Austria',
                    'history@example.com',
                    'en',
                    'pending',
                    NULL,
                    '2026-01-01 00:00:00'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO company_check_records (
                    id,
                    check_id,
                    company_name,
                    country,
                    human_review_status,
                    is_locked,
                    created_at
                ) VALUES (
                    1,
                    '1111111111',
                    'Historical Check GmbH',
                    'Austria',
                    'pending',
                    0,
                    '2026-01-01 00:00:00'
                )
                """
            )
        )


def test_processing_status_enum_value():
    assert CheckRequestStatus.processing.value == "processing"


def test_fresh_schema_contains_pipeline_columns(sqlite_engine):
    database.init_db()
    inspector = inspect(database.engine)

    request_columns = {
        column["name"]
        for column in inspector.get_columns("check_request_records")
    }
    check_columns = {
        column["name"]
        for column in inspector.get_columns("company_check_records")
    }

    assert "processing_started_at" in request_columns
    assert "processing_check_id" in request_columns
    assert "source_check_request_id" in check_columns


def test_fresh_schema_contains_unique_pipeline_indexes(sqlite_engine):
    database.init_db()

    processing_index = _index_by_name(
        "check_request_records",
        PROCESSING_INDEX,
    )
    source_index = _index_by_name(
        "company_check_records",
        SOURCE_INDEX,
    )

    assert processing_index["unique"]
    assert processing_index["column_names"] == ["processing_check_id"]
    assert source_index["unique"]
    assert source_index["column_names"] == ["source_check_request_id"]


def test_repeated_init_db_is_idempotent(sqlite_engine):
    database.init_db()
    database.init_db()

    inspector = inspect(database.engine)
    processing_indexes = [
        index
        for index in inspector.get_indexes("check_request_records")
        if index.get("name") == PROCESSING_INDEX
    ]
    source_indexes = [
        index
        for index in inspector.get_indexes("company_check_records")
        if index.get("name") == SOURCE_INDEX
    ]

    assert len(processing_indexes) == 1
    assert len(source_indexes) == 1


def test_existing_correct_pipeline_indexes_remain_valid(sqlite_engine):
    """Correct pre-existing indexes must keep init_db idempotent and successful."""
    with database.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE check_request_records (
                    id INTEGER PRIMARY KEY,
                    company_name VARCHAR(255) NOT NULL,
                    country VARCHAR(100) NOT NULL,
                    email VARCHAR(320) NOT NULL,
                    preferred_language VARCHAR(10) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    company_check_id VARCHAR(64),
                    processing_started_at TIMESTAMP,
                    processing_check_id VARCHAR(64),
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE company_check_records (
                    id INTEGER PRIMARY KEY,
                    check_id VARCHAR(64) NOT NULL UNIQUE,
                    company_name VARCHAR(255) NOT NULL,
                    country VARCHAR(100) NOT NULL,
                    human_review_status VARCHAR(50) DEFAULT 'pending',
                    is_locked BOOLEAN NOT NULL DEFAULT 0,
                    source_check_request_id INTEGER,
                    created_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                f"CREATE UNIQUE INDEX {PROCESSING_INDEX} "
                "ON check_request_records (processing_check_id)"
            )
        )
        connection.execute(
            text(
                f"CREATE UNIQUE INDEX {SOURCE_INDEX} "
                "ON company_check_records (source_check_request_id)"
            )
        )

    database.init_db()
    database.init_db()

    processing_index = _index_by_name(
        "check_request_records",
        PROCESSING_INDEX,
    )
    source_index = _index_by_name(
        "company_check_records",
        SOURCE_INDEX,
    )

    assert processing_index["unique"]
    assert processing_index["column_names"] == ["processing_check_id"]
    assert source_index["unique"]
    assert source_index["column_names"] == ["source_check_request_id"]


def test_old_schema_migration_adds_columns_and_indexes(sqlite_engine):
    _create_historical_tables()
    database.init_db()

    inspector = inspect(database.engine)
    request_columns = {
        column["name"]
        for column in inspector.get_columns("check_request_records")
    }
    check_columns = {
        column["name"]
        for column in inspector.get_columns("company_check_records")
    }

    assert "processing_started_at" in request_columns
    assert "processing_check_id" in request_columns
    assert "source_check_request_id" in check_columns
    assert _index_by_name(
        "check_request_records",
        PROCESSING_INDEX,
    )["unique"]
    assert _index_by_name(
        "company_check_records",
        SOURCE_INDEX,
    )["unique"]

    with database.engine.begin() as connection:
        request_row = connection.execute(
            text(
                "SELECT company_name, email, status "
                "FROM check_request_records WHERE id = 1"
            )
        ).one()
        check_row = connection.execute(
            text(
                "SELECT check_id, company_name, country "
                "FROM company_check_records WHERE id = 1"
            )
        ).one()

    assert request_row == (
        "Historical Request GmbH",
        "history@example.com",
        "pending",
    )
    assert check_row == (
        "1111111111",
        "Historical Check GmbH",
        "Austria",
    )


def test_processing_check_id_rejects_duplicate_non_null(sqlite_engine):
    database.init_db()
    SessionLocal = _session_factory()

    session = SessionLocal()
    try:
        session.add(
            CheckRequestRecord(
                company_name="First GmbH",
                country="Austria",
                email="first@example.com",
                preferred_language="en",
                status="pending",
                processing_check_id="2222222222",
            )
        )
        session.commit()
    finally:
        session.close()

    session = SessionLocal()
    try:
        session.add(
            CheckRequestRecord(
                company_name="Second GmbH",
                country="Austria",
                email="second@example.com",
                preferred_language="en",
                status="pending",
                processing_check_id="2222222222",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    finally:
        session.close()


def test_processing_check_id_allows_multiple_nulls(sqlite_engine):
    database.init_db()
    SessionLocal = _session_factory()

    session = SessionLocal()
    try:
        session.add_all(
            [
                CheckRequestRecord(
                    company_name="Null One GmbH",
                    country="Austria",
                    email="one@example.com",
                    preferred_language="en",
                    status="pending",
                    processing_check_id=None,
                ),
                CheckRequestRecord(
                    company_name="Null Two GmbH",
                    country="Austria",
                    email="two@example.com",
                    preferred_language="en",
                    status="pending",
                    processing_check_id=None,
                ),
            ]
        )
        session.commit()
        assert session.query(CheckRequestRecord).count() == 2
    finally:
        session.close()


def test_source_check_request_id_rejects_duplicate_non_null(sqlite_engine):
    database.init_db()
    SessionLocal = _session_factory()

    session = SessionLocal()
    try:
        session.add(
            CompanyCheckRecord(
                check_id="3333333333",
                company_name="First Check GmbH",
                country="Austria",
                source_check_request_id=42,
            )
        )
        session.commit()
    finally:
        session.close()

    session = SessionLocal()
    try:
        session.add(
            CompanyCheckRecord(
                check_id="4444444444",
                company_name="Second Check GmbH",
                country="Austria",
                source_check_request_id=42,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    finally:
        session.close()


def test_source_check_request_id_allows_multiple_nulls(sqlite_engine):
    database.init_db()
    SessionLocal = _session_factory()

    session = SessionLocal()
    try:
        session.add_all(
            [
                CompanyCheckRecord(
                    check_id="5555555555",
                    company_name="Legacy One GmbH",
                    country="Austria",
                    source_check_request_id=None,
                ),
                CompanyCheckRecord(
                    check_id="6666666666",
                    company_name="Legacy Two GmbH",
                    country="Austria",
                    source_check_request_id=None,
                ),
            ]
        )
        session.commit()
        assert session.query(CompanyCheckRecord).count() == 2
    finally:
        session.close()


@pytest.mark.parametrize(
    (
        "table_name",
        "index_name",
        "column_sql",
        "wrong_index_sql",
    ),
    [
        (
            "check_request_records",
            PROCESSING_INDEX,
            "processing_check_id VARCHAR(64)",
            (
                f"CREATE INDEX {PROCESSING_INDEX} "
                "ON check_request_records (processing_check_id)"
            ),
        ),
        (
            "check_request_records",
            PROCESSING_INDEX,
            "processing_check_id VARCHAR(64)",
            (
                f"CREATE UNIQUE INDEX {PROCESSING_INDEX} "
                "ON check_request_records (company_name)"
            ),
        ),
        (
            "company_check_records",
            SOURCE_INDEX,
            "source_check_request_id INTEGER",
            (
                f"CREATE INDEX {SOURCE_INDEX} "
                "ON company_check_records (source_check_request_id)"
            ),
        ),
        (
            "company_check_records",
            SOURCE_INDEX,
            "source_check_request_id INTEGER",
            (
                f"CREATE UNIQUE INDEX {SOURCE_INDEX} "
                "ON company_check_records (company_name)"
            ),
        ),
    ],
)
def test_malformed_existing_pipeline_index_raises(
    sqlite_engine,
    table_name,
    index_name,
    column_sql,
    wrong_index_sql,
):
    with database.engine.begin() as connection:
        if table_name == "check_request_records":
            connection.execute(
                text(
                    f"""
                    CREATE TABLE check_request_records (
                        id INTEGER PRIMARY KEY,
                        company_name VARCHAR(255) NOT NULL,
                        country VARCHAR(100) NOT NULL,
                        email VARCHAR(320) NOT NULL,
                        preferred_language VARCHAR(10) NOT NULL,
                        status VARCHAR(20) NOT NULL DEFAULT 'pending',
                        company_check_id VARCHAR(64),
                        {column_sql},
                        created_at TIMESTAMP NOT NULL
                    )
                    """
                )
            )
        else:
            connection.execute(
                text(
                    f"""
                    CREATE TABLE company_check_records (
                        id INTEGER PRIMARY KEY,
                        check_id VARCHAR(64) NOT NULL UNIQUE,
                        company_name VARCHAR(255) NOT NULL,
                        country VARCHAR(100) NOT NULL,
                        human_review_status VARCHAR(50) DEFAULT 'pending',
                        is_locked BOOLEAN NOT NULL DEFAULT 0,
                        {column_sql},
                        created_at TIMESTAMP
                    )
                    """
                )
            )
        connection.execute(text(wrong_index_sql))

    with pytest.raises(RuntimeError, match=index_name):
        database.init_db()
