"""SQLite schema and persistence tests for reconciliation audit records."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.database import Base
from app.db.models import ReconciliationActionRecord


AUDIT_TABLE = "reconciliation_action_records"
CHECK_REQUEST_INDEX = "ix_reconciliation_action_records_check_request_id"
PROCESSING_TOKEN_INDEX = (
    "ix_reconciliation_action_records_processing_check_id"
)
EXPECTED_INDEX_NAMES = {
    CHECK_REQUEST_INDEX,
    PROCESSING_TOKEN_INDEX,
}
EXPECTED_COLUMNS = {
    "id",
    "check_request_id",
    "processing_check_id",
    "action",
    "outcome",
    "diagnosis_snapshot_json",
    "artifact_json_sha256",
    "artifact_markdown_sha256",
    "actor_label",
    "operator_note",
    "created_at",
}
NON_NULL_COLUMNS = {
    "id",
    "check_request_id",
    "processing_check_id",
    "action",
    "outcome",
    "diagnosis_snapshot_json",
    "actor_label",
    "created_at",
}
NULLABLE_COLUMNS = {
    "artifact_json_sha256",
    "artifact_markdown_sha256",
    "operator_note",
}
SNAPSHOT_JSON = (
    '{"kind":"diagnosis",'
    '"classification":"stale_persisted_incomplete"}'
)


@pytest.fixture()
def sqlite_db(tmp_path):
    """Use an isolated temporary SQLite database for audit schema tests."""
    database_url = (
        f"sqlite:///{tmp_path / 'processing_reconciliation_audit.db'}"
    )
    database.configure_engine(database_url)

    yield

    database.engine.dispose()


def _session_factory():
    return sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=database.engine,
    )


def _indexes_by_name(table_name: str) -> dict[str, dict]:
    indexes = inspect(database.engine).get_indexes(table_name)
    return {index["name"]: index for index in indexes if index.get("name")}


def _assert_exact_audit_indexes(indexes: dict[str, dict]) -> None:
    assert set(indexes) == EXPECTED_INDEX_NAMES

    check_request_index = indexes[CHECK_REQUEST_INDEX]
    assert not check_request_index.get("unique")
    assert list(check_request_index.get("column_names") or []) == [
        "check_request_id"
    ]

    processing_index = indexes[PROCESSING_TOKEN_INDEX]
    assert not processing_index.get("unique")
    assert list(processing_index.get("column_names") or []) == [
        "processing_check_id"
    ]


def _valid_audit_record(**overrides) -> ReconciliationActionRecord:
    payload = {
        "check_request_id": 42,
        "processing_check_id": "1782245999001",
        "action": "finalize",
        "outcome": "precondition_failed",
        "diagnosis_snapshot_json": SNAPSHOT_JSON,
        "artifact_json_sha256": None,
        "artifact_markdown_sha256": None,
        "actor_label": "internal-unauthenticated",
        "operator_note": "Missing report artifact",
    }
    payload.update(overrides)
    return ReconciliationActionRecord(**payload)


def test_audit_table_registered_in_metadata():
    import app.db.models  # noqa: F401

    assert AUDIT_TABLE in Base.metadata.tables


def test_fresh_database_creates_exact_audit_schema(sqlite_db):
    database.init_db()

    inspector = inspect(database.engine)
    assert AUDIT_TABLE in inspector.get_table_names()

    columns = {
        column["name"]: column
        for column in inspector.get_columns(AUDIT_TABLE)
    }
    assert set(columns) == EXPECTED_COLUMNS

    for column_name in NON_NULL_COLUMNS:
        assert columns[column_name]["nullable"] is False

    for column_name in NULLABLE_COLUMNS:
        assert columns[column_name]["nullable"] is True


def test_sqlalchemy_type_widths_match_model():
    import app.db.models  # noqa: F401

    table = Base.metadata.tables[AUDIT_TABLE]
    assert table.c.processing_check_id.type.length == 64
    assert table.c.action.type.length == 50
    assert table.c.outcome.type.length == 50
    assert table.c.artifact_json_sha256.type.length == 64
    assert table.c.artifact_markdown_sha256.type.length == 64
    assert table.c.actor_label.type.length == 255


def test_explicit_non_unique_audit_indexes(sqlite_db):
    database.init_db()
    _assert_exact_audit_indexes(_indexes_by_name(AUDIT_TABLE))


def test_audit_table_has_no_foreign_keys():
    import app.db.models  # noqa: F401

    table = Base.metadata.tables[AUDIT_TABLE]
    assert not table.foreign_keys


def test_actor_label_has_no_default():
    import app.db.models  # noqa: F401

    table = Base.metadata.tables[AUDIT_TABLE]
    assert table.c.actor_label.default is None
    assert table.c.actor_label.server_default is None


def test_audit_table_has_no_unique_constraints_beyond_primary_key():
    import app.db.models  # noqa: F401
    from sqlalchemy import UniqueConstraint

    table = Base.metadata.tables[AUDIT_TABLE]
    unique_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert unique_constraints == []

    for index in table.indexes:
        assert not index.unique


def test_repeated_init_db_is_idempotent_for_audit_table(sqlite_db):
    database.init_db()
    database.init_db()

    inspector = inspect(database.engine)
    assert AUDIT_TABLE in inspector.get_table_names()
    _assert_exact_audit_indexes(_indexes_by_name(AUDIT_TABLE))


def test_historical_database_gains_audit_table_without_data_loss(sqlite_db):
    historical_created_at = "2026-07-01 10:00:00"
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
                CREATE UNIQUE INDEX
                ux_check_request_records_processing_check_id
                ON check_request_records (processing_check_id)
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
                    processing_started_at,
                    processing_check_id,
                    created_at
                ) VALUES (
                    7,
                    'Historical GmbH',
                    'Austria',
                    'historical@example.com',
                    'en',
                    'pending',
                    NULL,
                    NULL,
                    NULL,
                    :created_at
                )
                """
            ),
            {"created_at": historical_created_at},
        )

    database.init_db()

    inspector = inspect(database.engine)
    assert AUDIT_TABLE in inspector.get_table_names()
    _assert_exact_audit_indexes(_indexes_by_name(AUDIT_TABLE))

    with database.engine.begin() as connection:
        row = connection.execute(
            text(
                """
                SELECT
                    id,
                    company_name,
                    country,
                    email,
                    status,
                    created_at
                FROM check_request_records
                WHERE id = 7
                """
            )
        ).one()

    assert row.id == 7
    assert row.company_name == "Historical GmbH"
    assert row.country == "Austria"
    assert row.email == "historical@example.com"
    assert row.status == "pending"
    assert str(row.created_at) == historical_created_at

    database.init_db()
    assert AUDIT_TABLE in inspect(database.engine).get_table_names()
    _assert_exact_audit_indexes(_indexes_by_name(AUDIT_TABLE))


def test_round_trip_persistence(sqlite_db):
    database.init_db()
    session = _session_factory()()
    try:
        record = _valid_audit_record()
        session.add(record)
        session.commit()
        record_id = record.id
        assert record_id is not None
        assert record.created_at is not None

        session.expire_all()
        reloaded = session.get(ReconciliationActionRecord, record_id)
        assert reloaded is not None
        assert reloaded.check_request_id == 42
        assert reloaded.processing_check_id == "1782245999001"
        assert reloaded.action == "finalize"
        assert reloaded.outcome == "precondition_failed"
        assert reloaded.diagnosis_snapshot_json == SNAPSHOT_JSON
        assert reloaded.artifact_json_sha256 is None
        assert reloaded.artifact_markdown_sha256 is None
        assert reloaded.actor_label == "internal-unauthenticated"
        assert reloaded.operator_note == "Missing report artifact"
        assert isinstance(reloaded.created_at, datetime)
    finally:
        session.close()


def test_duplicate_audit_rows_are_permitted(sqlite_db):
    database.init_db()
    session = _session_factory()()
    try:
        session.add(_valid_audit_record())
        session.add(_valid_audit_record(operator_note="Second attempt"))
        session.commit()

        rows = (
            session.query(ReconciliationActionRecord)
            .filter(
                ReconciliationActionRecord.check_request_id == 42,
                ReconciliationActionRecord.processing_check_id
                == "1782245999001",
            )
            .all()
        )
        assert len(rows) == 2
    finally:
        session.close()


@pytest.mark.parametrize(
    "field_name",
    [
        "check_request_id",
        "processing_check_id",
        "action",
        "outcome",
        "diagnosis_snapshot_json",
        "actor_label",
    ],
)
def test_required_fields_reject_null(sqlite_db, field_name: str):
    database.init_db()
    session = _session_factory()()
    try:
        record = _valid_audit_record(**{field_name: None})
        session.add(record)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    finally:
        session.close()
