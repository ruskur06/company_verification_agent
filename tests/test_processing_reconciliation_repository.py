"""Read-only repository tests for processing reconciliation DB inspection."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.models import (
    CheckRequestRecord,
    CompanyCheckRecord,
    ReportRecord,
    SourceRecord,
    ToolCallRecord,
)
from app.db.repositories import get_processing_reconciliation_database_inspection
from app.schemas.check_request import CheckRequestStatus
from app.schemas.processing_reconciliation import (
    ReconciliationConsistency,
)


TOKEN = "1782245999001"
STARTED_AT = datetime(2026, 7, 20, 12, 0, 0)
REQUIRED_TOOLS = (
    "web_search",
    "domain_dns_check",
    "registry_search",
    "risk_score",
)


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    database_url = (
        f"sqlite:///{tmp_path / 'processing_reconciliation_repository.db'}"
    )
    database.configure_engine(database_url)
    database.init_db()

    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=database.engine,
    )
    monkeypatch.setattr(
        "app.db.repositories.SessionLocal",
        session_factory,
    )

    yield session_factory

    database.engine.dispose()


def _insert_request(
    session_factory,
    *,
    status: str = "processing",
    company_check_id: str | None = None,
    processing_check_id: str | None = TOKEN,
    processing_started_at: datetime | None = STARTED_AT,
    company_name: str = "Inspect GmbH",
) -> int:
    session = session_factory()
    try:
        record = CheckRequestRecord(
            company_name=company_name,
            country="Austria",
            email="inspect@example.com",
            preferred_language="en",
            status=status,
            company_check_id=company_check_id,
            processing_check_id=processing_check_id,
            processing_started_at=processing_started_at,
        )
        session.add(record)
        session.commit()
        return record.id
    finally:
        session.close()


def _insert_company_check(
    session_factory,
    *,
    check_id: str = TOKEN,
    source_check_request_id: int | None = None,
    json_report_path: str | None = f"outputs/json/company_check_{TOKEN}.json",
    markdown_report_path: str | None = (
        f"outputs/reports/company_check_{TOKEN}.md"
    ),
) -> int:
    session = session_factory()
    try:
        record = CompanyCheckRecord(
            check_id=check_id,
            source_check_request_id=source_check_request_id,
            company_name="Inspect GmbH",
            country="Austria",
            json_report_path=json_report_path,
            markdown_report_path=markdown_report_path,
        )
        session.add(record)
        session.commit()
        return record.id
    finally:
        session.close()


def _insert_source(session_factory, *, check_id: str = TOKEN) -> None:
    session = session_factory()
    try:
        session.add(
            SourceRecord(
                check_id=check_id,
                title="Source",
                url="https://example.com",
                snippet="snippet",
            )
        )
        session.commit()
    finally:
        session.close()


def _insert_tool_call(
    session_factory,
    *,
    tool_name: str,
    check_id: str = TOKEN,
) -> None:
    session = session_factory()
    try:
        session.add(
            ToolCallRecord(
                check_id=check_id,
                tool_name=tool_name,
                status="completed",
            )
        )
        session.commit()
    finally:
        session.close()


def _insert_report(
    session_factory,
    *,
    check_id: str = TOKEN,
    json_path: str | None = f"outputs/json/company_check_{TOKEN}.json",
    markdown_path: str | None = (
        f"outputs/reports/company_check_{TOKEN}.md"
    ),
    json_content: str | None = '{"ok": true}',
    markdown_content: str | None = "# Report\n",
) -> int:
    session = session_factory()
    try:
        record = ReportRecord(
            check_id=check_id,
            json_path=json_path,
            markdown_path=markdown_path,
            json_content=json_content,
            markdown_content=markdown_content,
        )
        session.add(record)
        session.commit()
        return record.id
    finally:
        session.close()


@pytest.mark.parametrize(
    "invalid_id",
    [True, False, "1", 1.5, None, 0, -1],
)
def test_invalid_request_ids_raise_value_error(invalid_id):
    with pytest.raises(ValueError, match="positive integer"):
        get_processing_reconciliation_database_inspection(invalid_id)


def test_missing_request_returns_none(sqlite_db):
    assert get_processing_reconciliation_database_inspection(999999) is None


def test_non_processing_request_is_still_inspected(sqlite_db):
    request_id = _insert_request(
        sqlite_db,
        status="approved",
        processing_check_id=None,
        processing_started_at=None,
    )

    result = get_processing_reconciliation_database_inspection(request_id)

    assert result is not None
    assert result.request.request_id == request_id
    assert result.request.status is CheckRequestStatus.approved
    assert result.request.processing_check_id is None
    assert result.token_company_checks == ()
    assert result.token_report_records == ()


def test_processing_request_with_no_evidence(sqlite_db):
    request_id = _insert_request(sqlite_db)

    result = get_processing_reconciliation_database_inspection(request_id)

    assert result is not None
    assert result.request.status is CheckRequestStatus.processing
    assert result.request.processing_check_id == TOKEN
    assert result.request.processing_started_at == STARTED_AT.replace(
        tzinfo=timezone.utc
    )
    assert result.request.processing_started_at.tzinfo is not None
    assert result.database.source_record_count == 0
    assert result.database.tool_call_names == ()
    assert result.database.report_record_count == 0
    assert result.database.matching_company_check_source_request_ids == ()
    assert result.database.foreign_processing_token_request_ids == ()
    assert result.token_company_checks == ()
    assert result.token_report_records == ()
    assert (
        result.database.report_json_path_consistency
        is ReconciliationConsistency.not_checked
    )
    assert (
        result.database.report_markdown_path_consistency
        is ReconciliationConsistency.not_checked
    )
    assert (
        result.database.report_json_content_consistency
        is ReconciliationConsistency.not_checked
    )
    assert (
        result.database.report_markdown_content_consistency
        is ReconciliationConsistency.not_checked
    )


def test_no_processing_token_excludes_unrelated_evidence(sqlite_db):
    request_id = _insert_request(
        sqlite_db,
        processing_check_id=None,
        processing_started_at=None,
        status="approved",
    )
    _insert_company_check(sqlite_db, check_id="unrelated-token")
    _insert_source(sqlite_db, check_id="unrelated-token")
    _insert_tool_call(sqlite_db, tool_name="web_search", check_id="unrelated-token")
    _insert_report(sqlite_db, check_id="unrelated-token")

    result = get_processing_reconciliation_database_inspection(request_id)

    assert result is not None
    assert result.request.processing_check_id is None
    assert result.database.source_record_count == 0
    assert result.database.tool_call_names == ()
    assert result.database.report_record_count == 0
    assert result.token_company_checks == ()
    assert result.token_report_records == ()


def test_complete_evidence_bundle(sqlite_db):
    request_id = _insert_request(sqlite_db)
    company_id = _insert_company_check(
        sqlite_db,
        source_check_request_id=request_id,
    )
    _insert_source(sqlite_db)
    _insert_source(sqlite_db)
    for tool_name in REQUIRED_TOOLS:
        _insert_tool_call(sqlite_db, tool_name=tool_name)
    report_id = _insert_report(sqlite_db)

    result = get_processing_reconciliation_database_inspection(request_id)

    assert result is not None
    assert result.database.matching_company_check_source_request_ids == (
        request_id,
    )
    assert result.database.source_record_count == 2
    assert result.database.tool_call_names == REQUIRED_TOOLS
    assert result.database.report_record_count == 1
    assert result.database.orphan_source_record_count == 0
    assert result.database.orphan_tool_call_record_count == 0
    assert result.database.orphan_report_record_count == 0
    assert len(result.token_company_checks) == 1
    assert result.token_company_checks[0].record_id == company_id
    assert result.token_company_checks[0].source_check_request_id == request_id
    assert len(result.token_report_records) == 1
    assert result.token_report_records[0].record_id == report_id
    assert (
        result.token_report_records[0].json_path
        == f"outputs/json/company_check_{TOKEN}.json"
    )
    assert (
        result.token_report_records[0].markdown_path
        == f"outputs/reports/company_check_{TOKEN}.md"
    )
    assert result.token_report_records[0].json_content == '{"ok": true}'
    assert result.token_report_records[0].markdown_content == "# Report\n"


def test_foreign_positive_source_request_id_preserved(sqlite_db):
    request_id = _insert_request(sqlite_db)
    _insert_company_check(sqlite_db, source_check_request_id=99)

    result = get_processing_reconciliation_database_inspection(request_id)

    assert result is not None
    assert result.token_company_checks[0].source_check_request_id == 99
    assert result.database.matching_company_check_source_request_ids == (99,)


def test_missing_source_request_id_produces_none(sqlite_db):
    request_id = _insert_request(sqlite_db)
    _insert_company_check(sqlite_db, source_check_request_id=None)

    result = get_processing_reconciliation_database_inspection(request_id)

    assert result is not None
    assert result.token_company_checks[0].source_check_request_id is None
    assert result.database.matching_company_check_source_request_ids == (None,)


@pytest.mark.parametrize("raw_source_id", [0, -7])
def test_zero_or_negative_source_request_id_mapping(
    sqlite_db,
    raw_source_id: int,
):
    request_id = _insert_request(sqlite_db)
    _insert_company_check(sqlite_db, source_check_request_id=raw_source_id)

    result = get_processing_reconciliation_database_inspection(request_id)

    assert result is not None
    assert (
        result.token_company_checks[0].source_check_request_id
        == raw_source_id
    )
    assert result.database.matching_company_check_source_request_ids == (None,)


def test_orphan_evidence_without_company_check(sqlite_db):
    request_id = _insert_request(sqlite_db)
    _insert_source(sqlite_db)
    _insert_tool_call(sqlite_db, tool_name="web_search")
    report_id = _insert_report(sqlite_db)

    result = get_processing_reconciliation_database_inspection(request_id)

    assert result is not None
    assert result.token_company_checks == ()
    assert result.database.source_record_count == 1
    assert result.database.tool_call_names == ("web_search",)
    assert result.database.report_record_count == 1
    assert result.database.orphan_source_record_count == 1
    assert result.database.orphan_tool_call_record_count == 1
    assert result.database.orphan_report_record_count == 1
    assert result.token_report_records[0].record_id == report_id


def test_company_check_clears_orphan_counts(sqlite_db):
    request_id = _insert_request(sqlite_db)
    _insert_company_check(sqlite_db, source_check_request_id=None)
    _insert_source(sqlite_db)
    _insert_tool_call(sqlite_db, tool_name="web_search")
    _insert_report(sqlite_db)

    result = get_processing_reconciliation_database_inspection(request_id)

    assert result is not None
    assert result.database.source_record_count == 1
    assert result.database.tool_call_names == ("web_search",)
    assert result.database.report_record_count == 1
    assert result.database.orphan_source_record_count == 0
    assert result.database.orphan_tool_call_record_count == 0
    assert result.database.orphan_report_record_count == 0


def test_tool_call_names_preserve_insertion_order(sqlite_db):
    request_id = _insert_request(sqlite_db)
    names = ("risk_score", "custom_tool", "web_search", "web_search")
    for name in names:
        _insert_tool_call(sqlite_db, tool_name=name)

    result = get_processing_reconciliation_database_inspection(request_id)

    assert result is not None
    assert result.database.tool_call_names == names


def test_duplicate_reports_preserve_both_snapshots(sqlite_db):
    request_id = _insert_request(sqlite_db)
    first_id = _insert_report(sqlite_db, json_content='{"n":1}')
    second_id = _insert_report(sqlite_db, json_content='{"n":2}')

    result = get_processing_reconciliation_database_inspection(request_id)

    assert result is not None
    assert result.database.report_record_count == 2
    assert [row.record_id for row in result.token_report_records] == [
        first_id,
        second_id,
    ]
    assert result.token_report_records[0].json_content == '{"n":1}'
    assert result.token_report_records[1].json_content == '{"n":2}'


def test_foreign_request_using_same_token(sqlite_db):
    request_id = _insert_request(sqlite_db, company_name="Target GmbH")

    with database.engine.begin() as connection:
        connection.execute(
            text(
                "DROP INDEX ux_check_request_records_processing_check_id"
            )
        )

    foreign_a = _insert_request(
        sqlite_db,
        company_name="Foreign A",
        processing_check_id=TOKEN,
    )
    foreign_b = _insert_request(
        sqlite_db,
        company_name="Foreign B",
        processing_check_id=TOKEN,
    )

    result = get_processing_reconciliation_database_inspection(request_id)

    assert result is not None
    assert result.database.foreign_processing_token_request_ids == (
        foreign_a,
        foreign_b,
    )
    assert request_id not in result.database.foreign_processing_token_request_ids


def test_inspection_is_read_only(sqlite_db, monkeypatch):
    request_id = _insert_request(sqlite_db)
    session_factory = sqlite_db

    class GuardedSession:
        def __init__(self):
            self._session = session_factory()

        def commit(self):
            raise AssertionError("commit must not be called")

        def flush(self, *args, **kwargs):
            raise AssertionError("flush must not be called")

        def __getattr__(self, name):
            return getattr(self._session, name)

    monkeypatch.setattr(
        "app.db.repositories.SessionLocal",
        GuardedSession,
    )

    result = get_processing_reconciliation_database_inspection(request_id)
    assert result is not None

    session = session_factory()
    try:
        record = session.get(CheckRequestRecord, request_id)
        assert record is not None
        assert record.status == "processing"
        assert record.processing_check_id == TOKEN
        assert record.company_check_id is None
    finally:
        session.close()


def test_inspection_does_not_read_filesystem(sqlite_db, monkeypatch):
    request_id = _insert_request(sqlite_db)
    _insert_report(sqlite_db)
    monkeypatch.setattr(
        "app.db.repositories._read_text_file",
        MagicMock(side_effect=AssertionError("_read_text_file must not run")),
    )

    result = get_processing_reconciliation_database_inspection(request_id)

    assert result is not None
    assert result.database.report_record_count == 1


def test_one_repository_call_creates_exactly_one_session(
    sqlite_db,
    monkeypatch,
):
    request_id = _insert_request(sqlite_db)
    session_factory = sqlite_db
    created: list[object] = []

    def tracking_session_local():
        session = session_factory()
        created.append(session)
        return session

    monkeypatch.setattr(
        "app.db.repositories.SessionLocal",
        tracking_session_local,
    )

    result = get_processing_reconciliation_database_inspection(request_id)

    assert result is not None
    assert len(created) == 1
