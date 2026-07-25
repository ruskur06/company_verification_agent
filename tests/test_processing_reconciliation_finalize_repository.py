"""Repository tests for atomic processing reconciliation finalization.

SQLite sequential tests do not prove overlapping PostgreSQL transactions,
MVCC visibility, or real contention. Those remain for Commit 10.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.models import (
    CheckRequestRecord,
    CompanyCheckRecord,
    ReconciliationActionRecord,
    ReportRecord,
    ToolCallRecord,
)
from app.db.repositories import (
    REQUIRED_RECONCILIATION_TOOL_CALLS,
    ProcessingReconciliationFinalizeRequestNotFoundError,
    finalize_processing_reconciliation_request,
)


TOKEN = "1782245999001"
OTHER_TOKEN = "1782245999002"
STARTED_AT = datetime(2026, 7, 20, 12, 0, 0)
ACTOR = "internal-unauthenticated"
SNAPSHOT = '{"classification":"stale_persisted_complete"}'
JSON_HASH = "a" * 64
MD_HASH = "b" * 64


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    database_url = (
        f"sqlite:///{tmp_path / 'processing_reconciliation_finalize.db'}"
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
) -> int:
    session = session_factory()
    try:
        record = CheckRequestRecord(
            company_name="Finalize GmbH",
            country="Austria",
            email="finalize@example.com",
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
) -> int:
    session = session_factory()
    try:
        record = CompanyCheckRecord(
            check_id=check_id,
            source_check_request_id=source_check_request_id,
            company_name="Finalize GmbH",
            country="Austria",
            json_report_path=f"outputs/json/company_check_{check_id}.json",
            markdown_report_path=(
                f"outputs/reports/company_check_{check_id}.md"
            ),
        )
        session.add(record)
        session.commit()
        return record.id
    finally:
        session.close()


def _insert_report(session_factory, *, check_id: str = TOKEN) -> int:
    session = session_factory()
    try:
        record = ReportRecord(
            check_id=check_id,
            json_path=f"outputs/json/company_check_{check_id}.json",
            markdown_path=f"outputs/reports/company_check_{check_id}.md",
            json_content='{"check_id":"%s"}' % check_id,
            markdown_content="# Report\n",
        )
        session.add(record)
        session.commit()
        return record.id
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


def _insert_required_tools(
    session_factory,
    *,
    check_id: str = TOKEN,
    tools: tuple[str, ...] = REQUIRED_RECONCILIATION_TOOL_CALLS,
) -> None:
    for tool_name in tools:
        _insert_tool_call(
            session_factory,
            tool_name=tool_name,
            check_id=check_id,
        )


def _seed_ready_request(session_factory) -> int:
    request_id = _insert_request(session_factory)
    _insert_company_check(
        session_factory,
        source_check_request_id=request_id,
    )
    _insert_report(session_factory)
    _insert_required_tools(session_factory)
    return request_id


def _finalize(
    request_id: int,
    *,
    expected_processing_check_id: str = TOKEN,
    operator_note: str | None = "operator note",
):
    return finalize_processing_reconciliation_request(
        request_id,
        expected_processing_check_id=expected_processing_check_id,
        actor_label=ACTOR,
        diagnosis_snapshot_json=SNAPSHOT,
        operator_note=operator_note,
        artifact_json_sha256=JSON_HASH,
        artifact_markdown_sha256=MD_HASH,
    )


def _load_request(session_factory, request_id: int) -> dict:
    session = session_factory()
    try:
        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one()
        )
        return {
            "status": record.status,
            "company_check_id": record.company_check_id,
            "processing_check_id": record.processing_check_id,
            "processing_started_at": record.processing_started_at,
        }
    finally:
        session.close()


def _load_audits(session_factory, request_id: int) -> list[dict]:
    session = session_factory()
    try:
        rows = (
            session.query(ReconciliationActionRecord)
            .filter(ReconciliationActionRecord.check_request_id == request_id)
            .order_by(ReconciliationActionRecord.id.asc())
            .all()
        )
        return [
            {
                "id": row.id,
                "action": row.action,
                "outcome": row.outcome,
                "processing_check_id": row.processing_check_id,
                "actor_label": row.actor_label,
                "operator_note": row.operator_note,
                "diagnosis_snapshot_json": row.diagnosis_snapshot_json,
                "artifact_json_sha256": row.artifact_json_sha256,
                "artifact_markdown_sha256": row.artifact_markdown_sha256,
            }
            for row in rows
        ]
    finally:
        session.close()


def test_successful_finalization(sqlite_db):
    request_id = _seed_ready_request(sqlite_db)
    result = _finalize(request_id)
    assert result.outcome == "finalized"
    assert result.request_id == request_id
    assert result.expected_processing_check_id == TOKEN

    record = _load_request(sqlite_db, request_id)
    assert record["status"] == "processed"
    assert record["company_check_id"] == TOKEN
    assert record["processing_check_id"] is None
    assert record["processing_started_at"] is None


def test_finalized_audit_row_has_snapshot_and_hashes(sqlite_db):
    request_id = _seed_ready_request(sqlite_db)
    result = _finalize(request_id)
    audits = _load_audits(sqlite_db, request_id)
    assert len(audits) == 1
    audit = audits[0]
    assert audit["id"] == result.audit_id
    assert audit["action"] == "finalize"
    assert audit["outcome"] == "finalized"
    assert audit["processing_check_id"] == TOKEN
    assert audit["actor_label"] == ACTOR
    assert audit["operator_note"] == "operator note"
    assert audit["diagnosis_snapshot_json"] == SNAPSHOT
    assert audit["artifact_json_sha256"] == JSON_HASH
    assert audit["artifact_markdown_sha256"] == MD_HASH


def test_wrong_processing_token_is_conflict(sqlite_db):
    request_id = _seed_ready_request(sqlite_db)
    result = _finalize(request_id, expected_processing_check_id=OTHER_TOKEN)
    assert result.outcome == "conflict"
    record = _load_request(sqlite_db, request_id)
    assert record["status"] == "processing"
    assert record["processing_check_id"] == TOKEN
    assert record["company_check_id"] is None


def test_missing_processing_started_at_is_precondition_failed(sqlite_db):
    request_id = _insert_request(
        sqlite_db,
        processing_started_at=None,
    )
    _insert_company_check(sqlite_db, source_check_request_id=request_id)
    _insert_report(sqlite_db)
    _insert_required_tools(sqlite_db)
    result = _finalize(request_id)
    assert result.outcome == "precondition_failed"
    record = _load_request(sqlite_db, request_id)
    assert record["status"] == "processing"
    assert record["processing_check_id"] == TOKEN


def test_already_processed_same_check_id(sqlite_db):
    request_id = _seed_ready_request(sqlite_db)
    first = _finalize(request_id)
    assert first.outcome == "finalized"
    second = _finalize(request_id)
    assert second.outcome == "already_processed"
    record = _load_request(sqlite_db, request_id)
    assert record["status"] == "processed"
    assert record["company_check_id"] == TOKEN
    audits = _load_audits(sqlite_db, request_id)
    assert [row["outcome"] for row in audits] == [
        "finalized",
        "already_processed",
    ]


def test_processed_with_different_check_id_is_conflict(sqlite_db):
    request_id = _insert_request(
        sqlite_db,
        status="processed",
        company_check_id=OTHER_TOKEN,
        processing_check_id=None,
        processing_started_at=None,
    )
    result = _finalize(request_id)
    assert result.outcome == "conflict"


def test_existing_company_check_id_invalid_state_is_precondition_failed(
    sqlite_db,
):
    request_id = _insert_request(
        sqlite_db,
        company_check_id=TOKEN,
    )
    _insert_company_check(sqlite_db, source_check_request_id=request_id)
    _insert_report(sqlite_db)
    _insert_required_tools(sqlite_db)
    result = _finalize(request_id)
    assert result.outcome == "precondition_failed"
    record = _load_request(sqlite_db, request_id)
    assert record["status"] == "processing"
    assert record["company_check_id"] == TOKEN


def test_missing_company_check_record(sqlite_db):
    request_id = _insert_request(sqlite_db)
    _insert_report(sqlite_db)
    _insert_required_tools(sqlite_db)
    result = _finalize(request_id)
    assert result.outcome == "precondition_failed"
    assert _load_request(sqlite_db, request_id)["status"] == "processing"


def test_company_check_linked_to_different_source_request(sqlite_db):
    foreign = _insert_request(
        sqlite_db,
        status="approved",
        processing_check_id=None,
        processing_started_at=None,
    )
    request_id = _insert_request(sqlite_db)
    _insert_company_check(sqlite_db, source_check_request_id=foreign)
    _insert_report(sqlite_db)
    _insert_required_tools(sqlite_db)
    result = _finalize(request_id)
    assert result.outcome == "precondition_failed"


def test_duplicate_matching_company_check_blocked_by_schema(sqlite_db):
    request_id = _seed_ready_request(sqlite_db)
    with pytest.raises(IntegrityError):
        _insert_company_check(
            sqlite_db,
            check_id=TOKEN,
            source_check_request_id=request_id,
        )


def test_missing_report_record(sqlite_db):
    request_id = _insert_request(sqlite_db)
    _insert_company_check(sqlite_db, source_check_request_id=request_id)
    _insert_required_tools(sqlite_db)
    result = _finalize(request_id)
    assert result.outcome == "precondition_failed"


def test_duplicate_report_record(sqlite_db):
    request_id = _seed_ready_request(sqlite_db)
    _insert_report(sqlite_db)
    result = _finalize(request_id)
    assert result.outcome == "precondition_failed"
    assert _load_request(sqlite_db, request_id)["status"] == "processing"


@pytest.mark.parametrize("missing_tool", REQUIRED_RECONCILIATION_TOOL_CALLS)
def test_each_required_tool_missing(sqlite_db, missing_tool: str):
    request_id = _insert_request(sqlite_db)
    _insert_company_check(sqlite_db, source_check_request_id=request_id)
    _insert_report(sqlite_db)
    for tool_name in REQUIRED_RECONCILIATION_TOOL_CALLS:
        if tool_name != missing_tool:
            _insert_tool_call(sqlite_db, tool_name=tool_name)
    result = _finalize(request_id)
    assert result.outcome == "precondition_failed"


@pytest.mark.parametrize("duplicated_tool", REQUIRED_RECONCILIATION_TOOL_CALLS)
def test_each_required_tool_duplicated(sqlite_db, duplicated_tool: str):
    request_id = _seed_ready_request(sqlite_db)
    _insert_tool_call(sqlite_db, tool_name=duplicated_tool)
    result = _finalize(request_id)
    assert result.outcome == "precondition_failed"


def test_additional_known_tool_call(sqlite_db):
    request_id = _seed_ready_request(sqlite_db)
    _insert_tool_call(
    sqlite_db,
    tool_name="official_website_review",
    )
    result = _finalize(request_id)
    assert result.outcome == "precondition_failed"


def test_unknown_tool_call(sqlite_db):
    request_id = _seed_ready_request(sqlite_db)
    _insert_tool_call(sqlite_db, tool_name="mystery_tool")
    result = _finalize(request_id)
    assert result.outcome == "precondition_failed"


def test_duplicate_plus_missing_still_four_tools(sqlite_db):
    request_id = _insert_request(sqlite_db)
    _insert_company_check(sqlite_db, source_check_request_id=request_id)
    _insert_report(sqlite_db)
    _insert_tool_call(sqlite_db, tool_name="web_search")
    _insert_tool_call(sqlite_db, tool_name="web_search")
    _insert_tool_call(sqlite_db, tool_name="domain_dns_check")
    _insert_tool_call(sqlite_db, tool_name="registry_search")
    # risk_score missing; total count still 4
    result = _finalize(request_id)
    assert result.outcome == "precondition_failed"
    assert _load_request(sqlite_db, request_id)["status"] == "processing"


def test_stale_attempt_cannot_finalize_newer_processing_attempt(sqlite_db):
    request_id = _seed_ready_request(sqlite_db)
    session = sqlite_db()
    try:
        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one()
        )
        record.processing_check_id = OTHER_TOKEN
        session.commit()
    finally:
        session.close()

    result = _finalize(request_id, expected_processing_check_id=TOKEN)
    assert result.outcome == "conflict"
    record = _load_request(sqlite_db, request_id)
    assert record["status"] == "processing"
    assert record["processing_check_id"] == OTHER_TOKEN
    assert record["company_check_id"] is None


def test_rowcount_zero_causes_no_mutation(sqlite_db):
    request_id = _insert_request(sqlite_db)
    before = _load_request(sqlite_db, request_id)
    result = _finalize(request_id)
    assert result.outcome == "precondition_failed"
    after = _load_request(sqlite_db, request_id)
    assert after["status"] == before["status"]
    assert after["processing_check_id"] == before["processing_check_id"]
    assert after["company_check_id"] == before["company_check_id"]


def test_repeated_known_failure_creates_multiple_audit_rows(sqlite_db):
    request_id = _insert_request(sqlite_db)
    first = _finalize(request_id)
    second = _finalize(request_id)
    assert first.outcome == "precondition_failed"
    assert second.outcome == "precondition_failed"
    audits = _load_audits(sqlite_db, request_id)
    assert len(audits) == 2
    assert {row["outcome"] for row in audits} == {"precondition_failed"}


def test_missing_request_raises_without_audit(sqlite_db):
    with pytest.raises(ProcessingReconciliationFinalizeRequestNotFoundError):
        _finalize(40404)
    session = sqlite_db()
    try:
        assert session.query(ReconciliationActionRecord).count() == 0
    finally:
        session.close()


def test_failure_after_update_before_audit_rolls_back_mutation(
    sqlite_db,
    monkeypatch,
):
    request_id = _seed_ready_request(sqlite_db)
    from app.db import repositories as repo_mod

    original_session_local = repo_mod.SessionLocal

    def failing_session_local():
        session = original_session_local()
        real_add = session.add

        def add(obj):
            if (
                isinstance(obj, ReconciliationActionRecord)
                and obj.outcome == "finalized"
            ):
                raise RuntimeError("audit persistence failed")
            return real_add(obj)

        session.add = add  # type: ignore[method-assign]
        return session

    monkeypatch.setattr(repo_mod, "SessionLocal", failing_session_local)
    with pytest.raises(RuntimeError, match="audit persistence failed"):
        _finalize(request_id)

    record = _load_request(sqlite_db, request_id)
    assert record["status"] == "processing"
    assert record["processing_check_id"] == TOKEN
    assert record["company_check_id"] is None
    assert _load_audits(sqlite_db, request_id) == []


def test_commit_failure_rolls_back_mutation_and_audit(
    sqlite_db,
    monkeypatch,
):
    request_id = _seed_ready_request(sqlite_db)
    from app.db import repositories as repo_mod

    original_session_local = repo_mod.SessionLocal

    def failing_session_local():
        session = original_session_local()

        def commit():
            raise RuntimeError("commit failed")

        session.commit = commit  # type: ignore[method-assign]
        return session

    monkeypatch.setattr(repo_mod, "SessionLocal", failing_session_local)
    with pytest.raises(RuntimeError, match="commit failed"):
        _finalize(request_id)

    record = _load_request(sqlite_db, request_id)
    assert record["status"] == "processing"
    assert record["processing_check_id"] == TOKEN
    assert record["company_check_id"] is None
    assert _load_audits(sqlite_db, request_id) == []


def test_commit_failure_preserves_original_when_rollback_also_fails(
    sqlite_db,
    monkeypatch,
):
    request_id = _seed_ready_request(sqlite_db)
    from app.db import repositories as repo_mod

    original_session_local = repo_mod.SessionLocal

    def failing_session_local():
        session = original_session_local()

        def commit():
            raise RuntimeError("original commit failed")

        def rollback():
            raise RuntimeError("rollback also failed")

        session.commit = commit  # type: ignore[method-assign]
        session.rollback = rollback  # type: ignore[method-assign]
        return session

    monkeypatch.setattr(repo_mod, "SessionLocal", failing_session_local)
    with pytest.raises(RuntimeError, match="original commit failed") as exc:
        _finalize(request_id)

    assert "rollback also failed" not in str(exc.value)

    record = _load_request(sqlite_db, request_id)
    assert record["status"] == "processing"
    assert record["processing_check_id"] == TOKEN
    assert record["company_check_id"] is None
    audits = _load_audits(sqlite_db, request_id)
    assert audits == []
    assert all(row.get("outcome") != "finalized" for row in audits)


def test_no_audit_says_finalized_unless_mutation_committed(sqlite_db):
    request_id = _insert_request(sqlite_db)
    result = _finalize(request_id)
    assert result.outcome == "precondition_failed"
    audits = _load_audits(sqlite_db, request_id)
    assert all(row["outcome"] != "finalized" for row in audits)
    assert _load_request(sqlite_db, request_id)["status"] == "processing"


def test_successful_mutation_always_has_matching_audit(sqlite_db):
    request_id = _seed_ready_request(sqlite_db)
    result = _finalize(request_id)
    assert result.outcome == "finalized"
    audits = _load_audits(sqlite_db, request_id)
    assert len(audits) == 1
    assert audits[0]["outcome"] == "finalized"
    assert audits[0]["id"] == result.audit_id
    assert _load_request(sqlite_db, request_id)["status"] == "processed"
