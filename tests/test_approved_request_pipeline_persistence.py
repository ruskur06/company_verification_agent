"""Focused tests for strict insert-only approved-request persistence."""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, sessionmaker

from app.db import database
from app.db.models import (
    CheckRequestRecord,
    CompanyCheckRecord,
    ReportRecord,
    SourceRecord,
    ToolCallRecord,
)
from app.db.repositories import ApprovedRequestPersistenceFenceError
from app.schemas.approved_request_pipeline import PreparedApprovedRequestCheck
from app.schemas.check_request import CheckRequestStatus
from app.services import approved_request_pipeline_service
from app.services.approved_request_pipeline_service import (
    persist_prepared_approved_request_check,
)


FIXED_STARTED_AT = datetime(2026, 7, 17, 11, 0, 0)
PROCESSING_CHECK_ID = "1782245999201"
JSON_PATH = f"outputs/json/company_check_{PROCESSING_CHECK_ID}.json"
MARKDOWN_PATH = f"outputs/reports/company_check_{PROCESSING_CHECK_ID}.md"
JSON_CONTENT = '{"check_id": "1782245999201", "ok": true}'
MARKDOWN_CONTENT = "# Company Verification Report\nPrepared content.\n"
DEFAULT_TOOL_NAMES = {
    "web_search",
    "domain_dns_check",
    "registry_search",
    "risk_score",
}


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    """Use an isolated SQLite database for persistence tests."""
    database_url = f"sqlite:///{tmp_path / 'approved_request_persistence.db'}"
    database.configure_engine(database_url)
    database.init_db()

    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=database.engine,
    )
    monkeypatch.setattr("app.db.repositories.SessionLocal", session_factory)

    yield session_factory

    database.engine.dispose()


def _result_payload(
    *,
    check_id: str = PROCESSING_CHECK_ID,
    include_tool_calls: bool | None = True,
    tool_calls: list[dict] | None = None,
) -> dict:
    payload: dict = {
        "check_id": check_id,
        "company": {
            "name": "Persist GmbH",
            "country": "Austria",
            "domain": "persist.example.com",
        },
        "risk": {
            "preliminary_score": 42,
            "preliminary_level": SimpleNamespace(value="medium"),
            "human_review_status": SimpleNamespace(value="pending"),
        },
        "registry_check": {
            "status": "found",
            "registry_found": True,
            "notes": ["Mapped registry"],
        },
        "domain_check": {
            "status": "checked",
            "domain": "persist.example.com",
            "has_a_record": True,
        },
        "sources": [
            {
                "title": "Source A",
                "url": "https://example.com/a",
                "snippet": "Snippet A",
                "source_type": "search_result",
                "confidence": "medium",
                "is_mock": False,
                "relevance": "relevant",
                "relevance_score": 0.8,
            },
            {
                "title": "Source B",
                "url": "https://example.com/b",
                "snippet": "Snippet B",
                "source_type": "registry",
                "confidence": "high",
                "is_mock": True,
            },
        ],
        "json_report_path": JSON_PATH,
        "markdown_report_path": MARKDOWN_PATH,
        "created_at": "2026-07-17T11:00:00",
    }
    if include_tool_calls is True and tool_calls is None:
        payload["tool_calls"] = [
            {
                "tool_name": "custom_tool",
                "status": "completed",
                "input": {"q": "x"},
                "output": {"ok": True},
            }
        ]
    elif include_tool_calls is True and tool_calls is not None:
        payload["tool_calls"] = tool_calls
    elif include_tool_calls is False:
        pass
    return payload


def _prepared(
    *,
    request_id: int,
    processing_check_id: str = PROCESSING_CHECK_ID,
    result_payload: dict | None = None,
    json_report_path: str = JSON_PATH,
    markdown_report_path: str = MARKDOWN_PATH,
    json_content: str = JSON_CONTENT,
    markdown_content: str = MARKDOWN_CONTENT,
) -> PreparedApprovedRequestCheck:
    payload = result_payload
    if payload is None:
        payload = _result_payload(check_id=processing_check_id)
    return PreparedApprovedRequestCheck(
        source_check_request_id=request_id,
        processing_check_id=processing_check_id,
        processing_started_at=FIXED_STARTED_AT,
        result_payload=payload,
        json_report_path=json_report_path,
        markdown_report_path=markdown_report_path,
        json_content=json_content,
        markdown_content=markdown_content,
    )


def _insert_processing_request(
    session_factory,
    *,
    processing_check_id: str = PROCESSING_CHECK_ID,
    company_check_id: str | None = None,
    processing_started_at: datetime | None = FIXED_STARTED_AT,
) -> int:
    session = session_factory()
    try:
        record = CheckRequestRecord(
            company_name="Persist GmbH",
            country="Austria",
            email="persist@example.com",
            website="https://persist.example.com",
            transaction_type="procurement",
            additional_context="Persistence tests.",
            preferred_language="de",
            status="processing",
            company_check_id=company_check_id,
            processing_check_id=processing_check_id,
            processing_started_at=processing_started_at,
        )
        session.add(record)
        session.commit()
        return record.id
    finally:
        session.close()


def _get_request(session_factory, request_id: int) -> CheckRequestRecord:
    session = session_factory()
    try:
        return (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one()
        )
    finally:
        session.close()


def _count(session_factory, model) -> int:
    session = session_factory()
    try:
        return session.query(model).count()
    finally:
        session.close()


def _count_for_check(session_factory, model, check_id: str) -> int:
    session = session_factory()
    try:
        return session.query(model).filter(model.check_id == check_id).count()
    finally:
        session.close()


def _insert_company_check(
    session_factory,
    *,
    check_id: str,
    source_check_request_id: int | None = None,
) -> None:
    session = session_factory()
    try:
        session.add(
            CompanyCheckRecord(
                check_id=check_id,
                source_check_request_id=source_check_request_id,
                company_name="Existing",
                country="Austria",
                human_review_status="pending",
            )
        )
        session.commit()
    finally:
        session.close()


def _tracking_session_factory(
    session_factory,
    *,
    events: list[str],
    fail_commit=None,
    fail_rollback=None,
    fail_flush=None,
):
    """Wrap SessionLocal to observe flush/commit/rollback ordering."""

    def factory():
        session = session_factory()
        original_flush = session.flush
        original_commit = session.commit
        original_rollback = session.rollback

        def flush(*args, **kwargs):
            events.append("flush")
            if fail_flush is not None:
                raise fail_flush
            return original_flush(*args, **kwargs)

        def commit(*args, **kwargs):
            events.append("commit")
            if fail_commit is not None:
                raise fail_commit
            return original_commit(*args, **kwargs)

        def rollback(*args, **kwargs):
            events.append("rollback")
            if fail_rollback is not None:
                raise fail_rollback
            return original_rollback(*args, **kwargs)

        session.flush = flush  # type: ignore[method-assign]
        session.commit = commit  # type: ignore[method-assign]
        session.rollback = rollback  # type: ignore[method-assign]
        return session

    return factory


def test_successful_persistence_returns_result(sqlite_db):
    request_id = _insert_processing_request(sqlite_db)
    prepared = _prepared(request_id=request_id)

    result = persist_prepared_approved_request_check(prepared)

    assert isinstance(result.source_check_request_id, int)
    assert result.source_check_request_id == request_id
    assert result.company_check_id == PROCESSING_CHECK_ID
    assert result.status == CheckRequestStatus.processed


def test_successful_persistence_inserts_company_check_and_mapping(sqlite_db):
    request_id = _insert_processing_request(sqlite_db)
    persist_prepared_approved_request_check(_prepared(request_id=request_id))

    assert _count(sqlite_db, CompanyCheckRecord) == 1
    session = sqlite_db()
    try:
        record = session.query(CompanyCheckRecord).one()
        assert record.check_id == PROCESSING_CHECK_ID
        assert record.source_check_request_id == request_id
        assert record.company_name == "Persist GmbH"
        assert record.country == "Austria"
        assert record.domain == "persist.example.com"
        assert record.risk_score == 42
        assert record.risk_level == "medium"
        assert record.human_review_status == "pending"
        assert json.loads(record.registry_check_json)["registry_found"] is True
        assert json.loads(record.domain_check_json)["domain"] == "persist.example.com"
        assert record.official_website_review_decision is None
        assert record.official_website_review_note is None
    finally:
        session.close()


def test_successful_persistence_inserts_sources_tools_and_report(sqlite_db, monkeypatch):
    request_id = _insert_processing_request(sqlite_db)
    read_mock = MagicMock(side_effect=AssertionError("_read_text_file must not be called"))
    monkeypatch.setattr(
        "app.db.repositories._read_text_file",
        read_mock,
    )

    persist_prepared_approved_request_check(_prepared(request_id=request_id))

    assert _count_for_check(sqlite_db, SourceRecord, PROCESSING_CHECK_ID) == 2
    assert _count_for_check(sqlite_db, ToolCallRecord, PROCESSING_CHECK_ID) == 1
    assert _count_for_check(sqlite_db, ReportRecord, PROCESSING_CHECK_ID) == 1

    session = sqlite_db()
    try:
        tool = session.query(ToolCallRecord).one()
        assert tool.tool_name == "custom_tool"
        report = session.query(ReportRecord).one()
        assert report.json_path == JSON_PATH
        assert report.markdown_path == MARKDOWN_PATH
        assert report.json_content == JSON_CONTENT
        assert report.markdown_content == MARKDOWN_CONTENT
    finally:
        session.close()
    read_mock.assert_not_called()


def test_missing_tool_calls_inserts_four_defaults(sqlite_db):
    request_id = _insert_processing_request(sqlite_db)
    payload = _result_payload(include_tool_calls=False)
    persist_prepared_approved_request_check(
        _prepared(request_id=request_id, result_payload=payload)
    )

    session = sqlite_db()
    try:
        names = {row.tool_name for row in session.query(ToolCallRecord).all()}
        assert names == DEFAULT_TOOL_NAMES
        assert len(names) == 4
    finally:
        session.close()


def test_empty_tool_calls_list_inserts_four_defaults(sqlite_db):
    request_id = _insert_processing_request(sqlite_db)
    payload = _result_payload(tool_calls=[])
    persist_prepared_approved_request_check(
        _prepared(request_id=request_id, result_payload=payload)
    )

    session = sqlite_db()
    try:
        names = {row.tool_name for row in session.query(ToolCallRecord).all()}
        assert names == DEFAULT_TOOL_NAMES
    finally:
        session.close()


def test_successful_persistence_finalizes_request(sqlite_db):
    request_id = _insert_processing_request(sqlite_db)
    persist_prepared_approved_request_check(_prepared(request_id=request_id))

    request = _get_request(sqlite_db, request_id)
    assert request.status == "processed"
    assert request.company_check_id == PROCESSING_CHECK_ID
    assert request.processing_check_id is None
    assert request.processing_started_at is None


def test_commit_once_and_flush_before_update(sqlite_db, monkeypatch):
    request_id = _insert_processing_request(sqlite_db)
    events: list[str] = []

    original_update = Query.update

    def tracked_update(self, values, synchronize_session="evaluate", update_args=None):
        events.append("update")
        return original_update(
            self,
            values,
            synchronize_session=synchronize_session,
            update_args=update_args,
        )

    monkeypatch.setattr(Query, "update", tracked_update)
    monkeypatch.setattr(
        "app.db.repositories.SessionLocal",
        _tracking_session_factory(sqlite_db, events=events),
    )

    persist_prepared_approved_request_check(_prepared(request_id=request_id))

    assert events.count("commit") == 1
    assert events.index("flush") < events.index("update")
    assert events.index("update") < events.index("commit")


def test_duplicate_check_id_raises_integrity_error_and_rolls_back(sqlite_db):
    request_id = _insert_processing_request(sqlite_db)
    _insert_company_check(sqlite_db, check_id=PROCESSING_CHECK_ID)

    with pytest.raises(IntegrityError):
        persist_prepared_approved_request_check(_prepared(request_id=request_id))

    assert _count(sqlite_db, CompanyCheckRecord) == 1
    assert _count_for_check(sqlite_db, SourceRecord, PROCESSING_CHECK_ID) == 0
    assert _count_for_check(sqlite_db, ToolCallRecord, PROCESSING_CHECK_ID) == 0
    assert _count_for_check(sqlite_db, ReportRecord, PROCESSING_CHECK_ID) == 0
    request = _get_request(sqlite_db, request_id)
    assert request.status == "processing"
    assert request.company_check_id is None
    assert request.processing_check_id == PROCESSING_CHECK_ID
    assert request.processing_started_at == FIXED_STARTED_AT


def test_duplicate_source_check_request_id_raises_and_rolls_back(sqlite_db):
    request_id = _insert_processing_request(sqlite_db)
    _insert_company_check(
        sqlite_db,
        check_id="other-check-id",
        source_check_request_id=request_id,
    )

    with pytest.raises(IntegrityError):
        persist_prepared_approved_request_check(_prepared(request_id=request_id))

    assert _count(sqlite_db, CompanyCheckRecord) == 1
    assert _count_for_check(sqlite_db, SourceRecord, PROCESSING_CHECK_ID) == 0
    assert (
        _count_for_check(sqlite_db, ToolCallRecord, PROCESSING_CHECK_ID) == 0
    )
    assert _count_for_check(sqlite_db, ReportRecord, PROCESSING_CHECK_ID) == 0
    request = _get_request(sqlite_db, request_id)
    assert request.status == "processing"
    assert request.processing_check_id == PROCESSING_CHECK_ID


def test_flush_failure_prevents_update_and_commit(sqlite_db, monkeypatch):
    request_id = _insert_processing_request(sqlite_db)
    events: list[str] = []
    original_update = Query.update

    def tracked_update(self, values, synchronize_session="evaluate", update_args=None):
        events.append("update")
        return original_update(
            self,
            values,
            synchronize_session=synchronize_session,
            update_args=update_args,
        )

    monkeypatch.setattr(Query, "update", tracked_update)
    monkeypatch.setattr(
        "app.db.repositories.SessionLocal",
        _tracking_session_factory(
            sqlite_db,
            events=events,
            fail_flush=RuntimeError("flush failed"),
        ),
    )

    with pytest.raises(RuntimeError, match="flush failed"):
        persist_prepared_approved_request_check(_prepared(request_id=request_id))

    assert "flush" in events
    assert "update" not in events
    assert "commit" not in events
    assert _count(sqlite_db, CompanyCheckRecord) == 0
    request = _get_request(sqlite_db, request_id)
    assert request.status == "processing"


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "approved"},
        {"processing_check_id": "mismatched-id"},
        {"company_check_id": "already-set"},
        {"processing_started_at": None},
    ],
)
def test_fence_failures_raise(sqlite_db, fields):
    request_id = _insert_processing_request(sqlite_db)
    session = sqlite_db()
    try:
        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one()
        )
        for key, value in fields.items():
            setattr(record, key, value)
        session.commit()
    finally:
        session.close()

    with pytest.raises(ApprovedRequestPersistenceFenceError) as exc_info:
        persist_prepared_approved_request_check(_prepared(request_id=request_id))

    assert exc_info.value.source_check_request_id == request_id
    assert exc_info.value.processing_check_id == PROCESSING_CHECK_ID
    assert str(request_id) in str(exc_info.value)
    assert PROCESSING_CHECK_ID in str(exc_info.value)


def test_fence_failure_for_missing_request(sqlite_db):
    with pytest.raises(ApprovedRequestPersistenceFenceError):
        persist_prepared_approved_request_check(_prepared(request_id=999999))


def test_fence_failure_rolls_back_related_rows_and_skips_commit(sqlite_db, monkeypatch):
    request_id = _insert_processing_request(sqlite_db)
    session = sqlite_db()
    try:
        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one()
        )
        record.status = "approved"
        session.commit()
    finally:
        session.close()

    events: list[str] = []
    monkeypatch.setattr(
        "app.db.repositories.SessionLocal",
        _tracking_session_factory(sqlite_db, events=events),
    )

    with pytest.raises(ApprovedRequestPersistenceFenceError):
        persist_prepared_approved_request_check(_prepared(request_id=request_id))

    assert "commit" not in events
    assert "rollback" in events
    assert _count(sqlite_db, CompanyCheckRecord) == 0
    assert _count(sqlite_db, SourceRecord) == 0
    assert _count(sqlite_db, ToolCallRecord) == 0
    assert _count(sqlite_db, ReportRecord) == 0


def test_commit_exception_propagates_without_retry(sqlite_db, monkeypatch):
    request_id = _insert_processing_request(sqlite_db)
    events: list[str] = []
    commit_error = RuntimeError("commit failed")
    monkeypatch.setattr(
        "app.db.repositories.SessionLocal",
        _tracking_session_factory(
            sqlite_db,
            events=events,
            fail_commit=commit_error,
        ),
    )

    with pytest.raises(RuntimeError, match="commit failed") as exc_info:
        persist_prepared_approved_request_check(_prepared(request_id=request_id))

    assert exc_info.value is commit_error
    assert events.count("commit") == 1
    assert "rollback" in events


def test_commit_exception_preserves_original_when_rollback_fails(sqlite_db, monkeypatch):
    request_id = _insert_processing_request(sqlite_db)
    commit_error = RuntimeError("commit failed")
    monkeypatch.setattr(
        "app.db.repositories.SessionLocal",
        _tracking_session_factory(
            sqlite_db,
            events=[],
            fail_commit=commit_error,
            fail_rollback=RuntimeError("rollback failed"),
        ),
    )

    with pytest.raises(RuntimeError, match="commit failed") as exc_info:
        persist_prepared_approved_request_check(_prepared(request_id=request_id))

    assert exc_info.value is commit_error


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"source_check_request_id": 0}, "source_check_request_id"),
        ({"processing_check_id": "   "}, "processing_check_id"),
        ({"processing_check_id": "x" * 65}, "64"),
        ({"json_report_path": "  "}, "json_report_path"),
        ({"markdown_report_path": ""}, "markdown_report_path"),
        ({"json_content": "\n"}, "json_content"),
        ({"markdown_content": "   "}, "markdown_content"),
    ],
)
def test_service_validation_failures(sqlite_db, monkeypatch, overrides, match):
    request_id = _insert_processing_request(sqlite_db)
    prepared = _prepared(request_id=request_id)
    data = prepared.model_dump()
    data.update(overrides)
    if "processing_check_id" in overrides and overrides["processing_check_id"].strip():
        data["result_payload"] = {
            **data["result_payload"],
            "check_id": overrides["processing_check_id"],
        }
    invalid = PreparedApprovedRequestCheck(**data)

    repo_mock = MagicMock(side_effect=AssertionError("repository must not be called"))
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "persist_prepared_approved_request_check_record",
        repo_mock,
    )

    with pytest.raises(ValueError, match=match):
        persist_prepared_approved_request_check(invalid)

    repo_mock.assert_not_called()


def test_service_validation_rejects_missing_payload_check_id(sqlite_db, monkeypatch):
    request_id = _insert_processing_request(sqlite_db)
    payload = _result_payload()
    del payload["check_id"]
    prepared = _prepared(request_id=request_id, result_payload=payload)
    repo_mock = MagicMock(side_effect=AssertionError("repository must not be called"))
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "persist_prepared_approved_request_check_record",
        repo_mock,
    )

    with pytest.raises(ValueError, match="check_id"):
        persist_prepared_approved_request_check(prepared)
    repo_mock.assert_not_called()


def test_service_validation_rejects_integer_payload_check_id(sqlite_db, monkeypatch):
    request_id = _insert_processing_request(sqlite_db)
    payload = _result_payload()
    payload["check_id"] = int(PROCESSING_CHECK_ID)
    prepared = _prepared(request_id=request_id, result_payload=payload)
    repo_mock = MagicMock(side_effect=AssertionError("repository must not be called"))
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "persist_prepared_approved_request_check_record",
        repo_mock,
    )

    with pytest.raises(ValueError, match="string"):
        persist_prepared_approved_request_check(prepared)
    repo_mock.assert_not_called()


def test_service_validation_rejects_mismatched_payload_check_id(sqlite_db, monkeypatch):
    request_id = _insert_processing_request(sqlite_db)
    payload = _result_payload()
    payload["check_id"] = "different-id"
    prepared = _prepared(request_id=request_id, result_payload=payload)
    repo_mock = MagicMock(side_effect=AssertionError("repository must not be called"))
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "persist_prepared_approved_request_check_record",
        repo_mock,
    )

    with pytest.raises(ValueError, match="equal"):
        persist_prepared_approved_request_check(prepared)
    repo_mock.assert_not_called()


def test_strict_persistence_never_calls_legacy_helpers(sqlite_db, monkeypatch):
    request_id = _insert_processing_request(sqlite_db)

    def fail_legacy(*_args, **_kwargs):
        raise AssertionError("legacy helper must not be called")

    monkeypatch.setattr("app.db.repositories.save_company_check", fail_legacy)
    monkeypatch.setattr("app.db.repositories._read_text_file", fail_legacy)
    monkeypatch.setattr("app.db.repositories._delete_related_records", fail_legacy)
    monkeypatch.setattr("app.db.repositories.update_check_request_status", fail_legacy)
    monkeypatch.setattr(
        "app.db.repositories.claim_approved_check_request_record",
        fail_legacy,
    )

    persist_prepared_approved_request_check(_prepared(request_id=request_id))

    request = _get_request(sqlite_db, request_id)
    assert request.status == "processed"
