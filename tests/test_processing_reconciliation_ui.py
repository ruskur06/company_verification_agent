"""UI tests for read-only processing reconciliation pages."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.models import CheckRequestRecord
from app.schemas.check_request import CheckRequestCreate, CheckRequestLanguage
from app.schemas.processing_reconciliation import (
    ProcessingReconciliationDiagnosis,
    ProcessingReconciliationDiagnosisError,
    ReconciliationClassification,
    ReconciliationDiagnosisErrorReason,
)
from app.services import processing_reconciliation_service as reconciliation_service
from app.services.check_request_service import create_check_request
from app.services.processing_reconciliation_service import (
    PROCESSING_RECONCILIATION_STALE_AFTER,
    ProcessingReconciliationRequestNotFoundError,
)


TOKEN = "1782245999001"
STARTED_AT = datetime(2026, 7, 21, 12, 0, 0)


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    database_url = (
        f"sqlite:///{tmp_path / 'processing_reconciliation_ui.db'}"
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


def _set_request_fields(session_factory, request_id: int, **fields) -> None:
    session = session_factory()
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


def _create_processing_request(
    session_factory,
    *,
    company_name: str = "Processing Co",
    country: str = "Austria",
    processing_check_id: str | None = TOKEN,
    processing_started_at: datetime | None = STARTED_AT,
    company_check_id: str | None = None,
) -> int:
    saved = create_check_request(
        CheckRequestCreate(
            company_name=company_name,
            country=country,
            email="ops@example.com",
            preferred_language=CheckRequestLanguage.en,
        )
    )
    _set_request_fields(
        session_factory,
        saved.id,
        status="processing",
        processing_check_id=processing_check_id,
        processing_started_at=processing_started_at,
        company_check_id=company_check_id,
    )
    return saved.id


def test_reconciliation_list_returns_200(sqlite_db, client):
    response = client.get("/internal/reconciliation")
    assert response.status_code == 200
    assert "Processing Reconciliation" in response.text


def test_reconciliation_list_empty_state(sqlite_db, client):
    response = client.get("/internal/reconciliation")
    assert response.status_code == 200
    assert "No processing requests found." in response.text


def test_reconciliation_list_shows_only_processing(sqlite_db, client):
    processing_id = _create_processing_request(sqlite_db)
    create_check_request(
        CheckRequestCreate(
            company_name="Pending Co",
            country="Austria",
            email="pending@example.com",
            preferred_language=CheckRequestLanguage.en,
        )
    )
    approved = create_check_request(
        CheckRequestCreate(
            company_name="Approved Co",
            country="Austria",
            email="approved@example.com",
            preferred_language=CheckRequestLanguage.en,
        )
    )
    _set_request_fields(sqlite_db, approved.id, status="approved")
    rejected = create_check_request(
        CheckRequestCreate(
            company_name="Rejected Co",
            country="Austria",
            email="rejected@example.com",
            preferred_language=CheckRequestLanguage.en,
        )
    )
    _set_request_fields(sqlite_db, rejected.id, status="rejected")
    processed = create_check_request(
        CheckRequestCreate(
            company_name="Processed Co",
            country="Austria",
            email="processed@example.com",
            preferred_language=CheckRequestLanguage.en,
        )
    )
    _set_request_fields(
        sqlite_db,
        processed.id,
        status="processed",
        processing_check_id="9999999999",
        processing_started_at=STARTED_AT,
    )

    response = client.get("/internal/reconciliation")
    text = response.text
    assert response.status_code == 200
    assert "Processing Co" in text
    assert f'href="/internal/reconciliation/{processing_id}"' in text
    assert "Pending Co" not in text
    assert "Approved Co" not in text
    assert "Rejected Co" not in text
    assert "Processed Co" not in text


def test_reconciliation_list_renders_processing_fields_and_dashes(
    sqlite_db,
    client,
):
    with_token = _create_processing_request(
        sqlite_db,
        company_name="Token Co",
        processing_check_id=TOKEN,
        processing_started_at=STARTED_AT,
    )
    without = _create_processing_request(
        sqlite_db,
        company_name="Sparse Co",
        processing_check_id=None,
        processing_started_at=None,
    )

    response = client.get("/internal/reconciliation")
    text = response.text
    assert response.status_code == 200
    assert TOKEN in text
    assert str(STARTED_AT.replace(tzinfo=timezone.utc)) in text or str(
        STARTED_AT
    ) in text
    assert f'href="/internal/reconciliation/{with_token}"' in text
    assert f'href="/internal/reconciliation/{without}"' in text
    assert "—" in text
    assert "Sparse Co" in text


def test_reconciliation_list_newest_first_and_limit_50(sqlite_db, client):
    ids: list[int] = []
    base = datetime(2026, 7, 21, 10, 0, 0)
    for index in range(51):
        request_id = _create_processing_request(
            sqlite_db,
            company_name=f"Limited Co {index:02d}",
            processing_check_id=str(1_700_000_000_000 + index),
            processing_started_at=base + timedelta(minutes=index),
        )
        ids.append(request_id)

    response = client.get("/internal/reconciliation")
    text = response.text
    assert response.status_code == 200
    assert text.count('href="/internal/reconciliation/') == 50
    assert f'href="/internal/reconciliation/{ids[-1]}"' in text
    assert f'href="/internal/reconciliation/{ids[0]}"' not in text
    assert "Limited Co 50" in text
    assert "Limited Co 00" not in text


def test_reconciliation_list_does_not_call_diagnose(sqlite_db, client, monkeypatch):
    _create_processing_request(sqlite_db)
    diagnose = MagicMock(side_effect=AssertionError("diagnose forbidden"))
    monkeypatch.setattr(
        "app.main.diagnose_processing_reconciliation",
        diagnose,
    )
    response = client.get("/internal/reconciliation")
    assert response.status_code == 200
    diagnose.assert_not_called()


def test_reconciliation_list_get_does_not_mutate_db(sqlite_db, client):
    request_id = _create_processing_request(sqlite_db)
    session = sqlite_db()
    try:
        before = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one()
        )
        before_status = before.status
        before_token = before.processing_check_id
    finally:
        session.close()

    response = client.get("/internal/reconciliation")
    assert response.status_code == 200

    session = sqlite_db()
    try:
        after = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one()
        )
        assert after.status == before_status
        assert after.processing_check_id == before_token
    finally:
        session.close()


def test_reconciliation_list_has_no_forms_and_noindex(sqlite_db, client):
    response = client.get("/internal/reconciliation")
    text = response.text
    assert response.status_code == 200
    assert text.lower().count('method="post"') == 1
    assert 'action="/internal/logout"' in text
    assert text.lower().count("<form") == 1
    assert 'content="noindex, nofollow"' in text
    assert 'href="/internal/requests"' in text
    assert 'href="/internal/checks"' in text
    assert "access control" in text.lower() or "Authentication" in text


def test_reconciliation_list_escapes_unsafe_company_name(sqlite_db, client):
    unsafe = '<script>alert("x")</script>'
    _create_processing_request(sqlite_db, company_name=unsafe)
    response = client.get("/internal/reconciliation")
    text = response.text
    assert unsafe not in text
    assert "&lt;script&gt;" in text


def test_reconciliation_detail_renders_diagnosis(sqlite_db, client, monkeypatch):
    request_id = _create_processing_request(sqlite_db)
    diagnosed_at = datetime(2026, 7, 21, 15, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.main.diagnose_processing_reconciliation",
        MagicMock(
            return_value=ProcessingReconciliationDiagnosis(
                request_id=request_id,
                processing_check_id=TOKEN,
                classification=(
                    ReconciliationClassification.stale_no_result_evidence
                ),
                diagnosed_at=diagnosed_at,
                age_seconds=10800.0,
                reasons=("missing_artifacts", "another_reason"),
            )
        ),
    )
    response = client.get(f"/internal/reconciliation/{request_id}")
    text = response.text
    assert response.status_code == 200
    assert str(request_id) in text
    assert TOKEN in text
    assert "stale_no_result_evidence" in text
    assert "10800.0" in text or "10800" in text
    assert "missing_artifacts" in text
    assert "another_reason" in text
    assert "30 minutes" in text
    assert "json_path" not in text
    assert "sha256" not in text
    assert "snapshot" not in text.lower()


def test_reconciliation_detail_no_reasons_fallback(sqlite_db, client, monkeypatch):
    request_id = _create_processing_request(sqlite_db)
    monkeypatch.setattr(
        "app.main.diagnose_processing_reconciliation",
        MagicMock(
            return_value=ProcessingReconciliationDiagnosis(
                request_id=request_id,
                processing_check_id=TOKEN,
                classification=(
                    ReconciliationClassification.within_processing_window
                ),
                diagnosed_at=datetime(
                    2026, 7, 21, 12, 10, 0, tzinfo=timezone.utc
                ),
                age_seconds=600.0,
                reasons=(),
            )
        ),
    )
    response = client.get(f"/internal/reconciliation/{request_id}")
    assert response.status_code == 200
    assert "No inconsistency reasons." in response.text


def test_reconciliation_detail_diagnosis_error_is_200(
    sqlite_db,
    client,
    monkeypatch,
):
    request_id = _create_processing_request(sqlite_db)
    monkeypatch.setattr(
        "app.main.diagnose_processing_reconciliation",
        MagicMock(
            return_value=ProcessingReconciliationDiagnosisError(
                request_id=request_id,
                processing_check_id=TOKEN,
                reason=(
                    ReconciliationDiagnosisErrorReason.database_inspection_failed
                ),
                detail='db failed <script>alert("x")</script>',
                diagnosed_at=datetime(
                    2026, 7, 21, 15, 0, 0, tzinfo=timezone.utc
                ),
            )
        ),
    )
    response = client.get(f"/internal/reconciliation/{request_id}")
    text = response.text
    assert response.status_code == 200
    assert "database_inspection_failed" in text
    assert "&lt;script&gt;" in text
    assert '<script>alert("x")</script>' not in text


def test_reconciliation_detail_missing_request_is_404(
    sqlite_db,
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.main.diagnose_processing_reconciliation",
        MagicMock(
            side_effect=ProcessingReconciliationRequestNotFoundError(40404)
        ),
    )
    response = client.get("/internal/reconciliation/40404")
    assert response.status_code == 404
    assert "was not found for reconciliation" not in response.text


def test_reconciliation_detail_non_integer_id_is_422(sqlite_db, client):
    response = client.get("/internal/reconciliation/not-an-id")
    assert response.status_code == 422


def test_reconciliation_detail_calls_diagnose_once_with_stale_after(
    sqlite_db,
    client,
    monkeypatch,
):
    request_id = _create_processing_request(sqlite_db)
    diagnose = MagicMock(
        return_value=ProcessingReconciliationDiagnosis(
            request_id=request_id,
            processing_check_id=TOKEN,
            classification=(
                ReconciliationClassification.within_processing_window
            ),
            diagnosed_at=datetime(
                2026, 7, 21, 12, 10, 0, tzinfo=timezone.utc
            ),
            age_seconds=600.0,
        )
    )
    monkeypatch.setattr(
        "app.main.diagnose_processing_reconciliation",
        diagnose,
    )
    response = client.get(f"/internal/reconciliation/{request_id}")
    assert response.status_code == 200
    diagnose.assert_called_once_with(
        request_id,
        stale_after=PROCESSING_RECONCILIATION_STALE_AFTER,
    )
    assert PROCESSING_RECONCILIATION_STALE_AFTER == timedelta(minutes=30)


def test_reconciliation_detail_unexpected_exception_propagates(
    sqlite_db,
    client,
    monkeypatch,
):
    request_id = _create_processing_request(sqlite_db)
    monkeypatch.setattr(
        "app.main.diagnose_processing_reconciliation",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError, match="boom"):
        client.get(f"/internal/reconciliation/{request_id}")


def test_reconciliation_detail_get_does_not_mutate_and_has_no_forms(
    sqlite_db,
    client,
):
    request_id = _create_processing_request(sqlite_db)
    session = sqlite_db()
    try:
        before = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one()
        )
        before_status = before.status
    finally:
        session.close()

    response = client.get(f"/internal/reconciliation/{request_id}")
    text = response.text
    assert response.status_code == 200
    assert text.lower().count('method="post"') == 1
    assert 'action="/internal/logout"' in text
    assert text.lower().count("<form") == 1
    assert 'content="noindex, nofollow"' in text
    assert 'href="/internal/reconciliation"' in text
    assert 'href="/internal/requests"' in text

    session = sqlite_db()
    try:
        after = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one()
        )
        assert after.status == before_status
    finally:
        session.close()


@pytest.mark.parametrize(
    "method",
    ["post", "put", "patch", "delete"],
)
@pytest.mark.parametrize(
    "path",
    ["/internal/reconciliation", "/internal/reconciliation/1"],
)
def test_reconciliation_paths_reject_non_get_methods(
    sqlite_db,
    client,
    method: str,
    path: str,
):
    response = getattr(client, method)(path)
    assert response.status_code == 405


def test_check_requests_page_links_to_reconciliation(sqlite_db, client):
    response = client.get("/internal/requests")
    assert response.status_code == 200
    assert 'href="/internal/reconciliation"' in response.text


def test_stale_after_constant_is_thirty_minutes():
    assert reconciliation_service.PROCESSING_RECONCILIATION_STALE_AFTER == (
        timedelta(minutes=30)
    )
