"""Focused tests for approved → processing claim and ID allocation."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.models import CheckRequestRecord, CompanyCheckRecord
from app.db.repositories import (
    claim_approved_check_request_record,
    company_check_id_exists,
    processing_check_id_exists,
)
from app.schemas.check_request import (
    CheckRequestCreate,
    CheckRequestLanguage,
    CheckRequestResponse,
    CheckRequestStatus,
    CheckRequestTransactionType,
    ClaimedCheckRequest,
)
from app.services import check_request_service
from app.services.check_request_service import (
    CheckRequestNotFoundError,
    InvalidCheckRequestTransitionError,
    ProcessingCheckIdAllocationError,
    approve_check_request,
    claim_approved_check_request,
    create_check_request,
)


FIXED_STARTED_AT = datetime(2026, 7, 17, 10, 0, 0)
CANDIDATE_A = 1782245999001
CANDIDATE_B = 1782245999002


def _approved_request_dict(
    *,
    status: str = "approved",
    processing_check_id: str | None = None,
    processing_started_at: datetime | None = None,
) -> dict:
    return {
        "id": 1,
        "company_name": "Claim GmbH",
        "country": "Austria",
        "email": "claim@example.com",
        "website": "https://claim.example.com",
        "transaction_type": "procurement",
        "additional_context": "Needs claim testing.",
        "preferred_language": "de",
        "status": status,
        "company_check_id": None,
        "processing_check_id": processing_check_id,
        "processing_started_at": processing_started_at,
        "created_at": FIXED_STARTED_AT,
    }


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    """Use an isolated SQLite database for claim tests."""
    database_url = f"sqlite:///{tmp_path / 'approved_request_claim.db'}"
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


def _create_pending_request(**overrides):
    payload = {
        "company_name": "Claim GmbH",
        "country": "Austria",
        "email": "claim@example.com",
        "website": "https://claim.example.com",
        "transaction_type": CheckRequestTransactionType.procurement,
        "additional_context": "Needs claim testing.",
        "preferred_language": CheckRequestLanguage.de,
    }
    payload.update(overrides)
    return create_check_request(CheckRequestCreate(**payload))


def _approve(request_id: int):
    return approve_check_request(request_id)


def _get_request_row(session_factory, request_id: int) -> CheckRequestRecord:
    session = session_factory()
    try:
        return (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one()
        )
    finally:
        session.close()


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


def _insert_company_check(session_factory, check_id: str) -> None:
    session = session_factory()
    try:
        session.add(
            CompanyCheckRecord(
                check_id=check_id,
                company_name="Existing Check",
                country="Austria",
                human_review_status="pending",
                is_locked=False,
            )
        )
        session.commit()
    finally:
        session.close()


def _company_check_count(session_factory) -> int:
    session = session_factory()
    try:
        return session.query(CompanyCheckRecord).count()
    finally:
        session.close()


def test_claim_approved_request_atomically_sets_processing_fields(sqlite_db):
    saved = _approve(_create_pending_request().id)
    started_at = FIXED_STARTED_AT

    claimed = claim_approved_check_request_record(
        saved.id,
        processing_check_id=str(CANDIDATE_A),
        processing_started_at=started_at,
    )

    assert claimed is not None
    assert claimed["status"] == "processing"
    assert claimed["processing_check_id"] == str(CANDIDATE_A)
    assert claimed["processing_started_at"] == started_at
    assert claimed["company_check_id"] is None

    row = _get_request_row(sqlite_db, saved.id)
    assert row.status == "processing"
    assert row.processing_check_id == str(CANDIDATE_A)
    assert isinstance(row.processing_check_id, str)
    assert row.processing_started_at == started_at
    assert row.company_check_id is None
    assert row.company_name == "Claim GmbH"
    assert row.country == "Austria"
    assert row.email == "claim@example.com"
    assert row.website == "https://claim.example.com"
    assert row.transaction_type == "procurement"
    assert row.additional_context == "Needs claim testing."
    assert row.preferred_language == "de"


@pytest.mark.parametrize(
    "status",
    ["pending", "rejected", "processing", "processed"],
)
def test_claim_rejects_non_approved_status(sqlite_db, status):
    saved = _create_pending_request()
    _set_request_fields(sqlite_db, saved.id, status=status)

    claimed = claim_approved_check_request_record(
        saved.id,
        processing_check_id=str(CANDIDATE_A),
        processing_started_at=FIXED_STARTED_AT,
    )

    assert claimed is None
    row = _get_request_row(sqlite_db, saved.id)
    assert row.status == status
    assert row.processing_check_id is None
    assert row.processing_started_at is None


def test_claim_rejects_approved_with_company_check_id(sqlite_db):
    saved = _approve(_create_pending_request().id)
    _set_request_fields(sqlite_db, saved.id, company_check_id="already-linked")

    claimed = claim_approved_check_request_record(
        saved.id,
        processing_check_id=str(CANDIDATE_A),
        processing_started_at=FIXED_STARTED_AT,
    )

    assert claimed is None
    row = _get_request_row(sqlite_db, saved.id)
    assert row.status == "approved"
    assert row.processing_check_id is None
    assert row.company_check_id == "already-linked"


def test_claim_rejects_approved_with_processing_check_id(sqlite_db):
    saved = _approve(_create_pending_request().id)
    _set_request_fields(sqlite_db, saved.id, processing_check_id=str(CANDIDATE_B))

    claimed = claim_approved_check_request_record(
        saved.id,
        processing_check_id=str(CANDIDATE_A),
        processing_started_at=FIXED_STARTED_AT,
    )

    assert claimed is None
    row = _get_request_row(sqlite_db, saved.id)
    assert row.processing_check_id == str(CANDIDATE_B)
    assert row.processing_started_at is None
    assert row.status == "approved"


def test_claim_rejects_approved_with_processing_started_at(sqlite_db):
    saved = _approve(_create_pending_request().id)
    _set_request_fields(sqlite_db, saved.id, processing_started_at=FIXED_STARTED_AT)

    claimed = claim_approved_check_request_record(
        saved.id,
        processing_check_id=str(CANDIDATE_A),
        processing_started_at=datetime(2026, 7, 17, 11, 0, 0),
    )

    assert claimed is None
    row = _get_request_row(sqlite_db, saved.id)
    assert row.processing_check_id is None
    assert row.processing_started_at == FIXED_STARTED_AT
    assert row.status == "approved"


def test_claim_missing_request_returns_none(sqlite_db):
    claimed = claim_approved_check_request_record(
        999999,
        processing_check_id=str(CANDIDATE_A),
        processing_started_at=FIXED_STARTED_AT,
    )
    assert claimed is None


def test_stale_second_claim_does_not_overwrite_first(sqlite_db):
    saved = _approve(_create_pending_request().id)
    first_started = FIXED_STARTED_AT
    second_started = datetime(2026, 7, 17, 12, 0, 0)

    first = claim_approved_check_request_record(
        saved.id,
        processing_check_id=str(CANDIDATE_A),
        processing_started_at=first_started,
    )
    second = claim_approved_check_request_record(
        saved.id,
        processing_check_id=str(CANDIDATE_B),
        processing_started_at=second_started,
    )

    assert first is not None
    assert second is None
    row = _get_request_row(sqlite_db, saved.id)
    assert row.processing_check_id == str(CANDIDATE_A)
    assert row.processing_started_at == first_started
    assert row.status == "processing"


def test_service_claim_stores_string_id_and_returns_int(sqlite_db, monkeypatch):
    saved = _approve(_create_pending_request().id)
    monkeypatch.setattr(
        check_request_service,
        "_new_processing_check_id",
        MagicMock(return_value=CANDIDATE_A),
    )
    monkeypatch.setattr(
        check_request_service,
        "_utc_now_naive",
        MagicMock(return_value=FIXED_STARTED_AT),
    )
    pipeline = MagicMock(side_effect=AssertionError("pipeline must not run"))
    monkeypatch.setattr(
        "app.services.company_check_service.execute_company_check_pipeline",
        pipeline,
    )

    claimed = claim_approved_check_request(saved.id)

    assert isinstance(claimed, ClaimedCheckRequest)
    assert claimed.processing_check_id == CANDIDATE_A
    assert isinstance(claimed.processing_check_id, int)
    assert claimed.processing_started_at == FIXED_STARTED_AT
    assert claimed.request.status is CheckRequestStatus.processing
    assert claimed.request.company_name == "Claim GmbH"
    assert claimed.request.country == "Austria"
    assert claimed.request.email == "claim@example.com"
    assert claimed.request.company_check_id is None

    row = _get_request_row(sqlite_db, saved.id)
    assert row.processing_check_id == str(CANDIDATE_A)
    assert row.status == "processing"
    assert _company_check_count(sqlite_db) == 0
    pipeline.assert_not_called()


def test_service_retries_on_company_check_id_collision(sqlite_db, monkeypatch):
    saved = _approve(_create_pending_request().id)
    _insert_company_check(sqlite_db, str(CANDIDATE_A))
    monkeypatch.setattr(
        check_request_service,
        "_new_processing_check_id",
        MagicMock(side_effect=[CANDIDATE_A, CANDIDATE_B]),
    )
    monkeypatch.setattr(
        check_request_service,
        "_utc_now_naive",
        MagicMock(return_value=FIXED_STARTED_AT),
    )

    claimed = claim_approved_check_request(saved.id)

    assert claimed.processing_check_id == CANDIDATE_B
    assert _get_request_row(sqlite_db, saved.id).processing_check_id == str(CANDIDATE_B)
    assert company_check_id_exists(str(CANDIDATE_A)) is True


def test_service_retries_on_processing_check_id_collision(sqlite_db, monkeypatch):
    first = _approve(_create_pending_request(company_name="First GmbH").id)
    second = _approve(_create_pending_request(company_name="Second GmbH").id)
    _set_request_fields(
        sqlite_db,
        first.id,
        status="processing",
        processing_check_id=str(CANDIDATE_A),
        processing_started_at=FIXED_STARTED_AT,
    )
    monkeypatch.setattr(
        check_request_service,
        "_new_processing_check_id",
        MagicMock(side_effect=[CANDIDATE_A, CANDIDATE_B]),
    )
    monkeypatch.setattr(
        check_request_service,
        "_utc_now_naive",
        MagicMock(return_value=FIXED_STARTED_AT),
    )

    claimed = claim_approved_check_request(second.id)

    assert claimed.processing_check_id == CANDIDATE_B
    assert processing_check_id_exists(str(CANDIDATE_A)) is True


def test_service_retries_integrity_error_when_candidate_exists(sqlite_db, monkeypatch):
    saved = _approve(_create_pending_request().id)
    monkeypatch.setattr(
        check_request_service,
        "_new_processing_check_id",
        MagicMock(side_effect=[CANDIDATE_A, CANDIDATE_B]),
    )
    monkeypatch.setattr(
        check_request_service,
        "_utc_now_naive",
        MagicMock(return_value=FIXED_STARTED_AT),
    )

    claim_mock = MagicMock(
        side_effect=[
            IntegrityError("stmt", {}, Exception("unique")),
            {
                "id": saved.id,
                "company_name": "Claim GmbH",
                "country": "Austria",
                "email": "claim@example.com",
                "website": "https://claim.example.com",
                "transaction_type": "procurement",
                "additional_context": "Needs claim testing.",
                "preferred_language": "de",
                "status": "processing",
                "company_check_id": None,
                "processing_check_id": str(CANDIDATE_B),
                "processing_started_at": FIXED_STARTED_AT,
                "created_at": FIXED_STARTED_AT,
            },
        ]
    )
    monkeypatch.setattr(
        check_request_service,
        "claim_approved_check_request_record",
        claim_mock,
    )
    monkeypatch.setattr(
        check_request_service,
        "processing_check_id_exists",
        MagicMock(side_effect=[False, True, False]),
    )
    monkeypatch.setattr(
        check_request_service,
        "company_check_id_exists",
        MagicMock(return_value=False),
    )

    claimed = claim_approved_check_request(saved.id)

    assert claimed.processing_check_id == CANDIDATE_B
    assert claim_mock.call_count == 2


def test_service_propagates_integrity_error_without_proven_collision(
    sqlite_db, monkeypatch
):
    saved = _approve(_create_pending_request().id)
    monkeypatch.setattr(
        check_request_service,
        "_new_processing_check_id",
        MagicMock(return_value=CANDIDATE_A),
    )
    monkeypatch.setattr(
        check_request_service,
        "_utc_now_naive",
        MagicMock(return_value=FIXED_STARTED_AT),
    )
    monkeypatch.setattr(
        check_request_service,
        "company_check_id_exists",
        MagicMock(return_value=False),
    )
    monkeypatch.setattr(
        check_request_service,
        "processing_check_id_exists",
        MagicMock(return_value=False),
    )
    monkeypatch.setattr(
        check_request_service,
        "claim_approved_check_request_record",
        MagicMock(side_effect=IntegrityError("stmt", {}, Exception("other"))),
    )

    with pytest.raises(IntegrityError):
        claim_approved_check_request(saved.id)


def test_service_raises_after_five_collisions(sqlite_db, monkeypatch):
    saved = _approve(_create_pending_request().id)
    monkeypatch.setattr(
        check_request_service,
        "_new_processing_check_id",
        MagicMock(side_effect=[CANDIDATE_A] * 5),
    )
    monkeypatch.setattr(
        check_request_service,
        "_utc_now_naive",
        MagicMock(return_value=FIXED_STARTED_AT),
    )
    monkeypatch.setattr(
        check_request_service,
        "company_check_id_exists",
        MagicMock(return_value=True),
    )

    with pytest.raises(
        ProcessingCheckIdAllocationError,
        match=f"request {saved.id} after 5 attempts",
    ):
        claim_approved_check_request(saved.id)


@pytest.mark.parametrize("invalid_id", [True, 0, -1, "123"])
def test_service_rejects_invalid_generated_ids(sqlite_db, monkeypatch, invalid_id):
    saved = _approve(_create_pending_request().id)
    monkeypatch.setattr(
        check_request_service,
        "_new_processing_check_id",
        MagicMock(return_value=invalid_id),
    )
    monkeypatch.setattr(
        check_request_service,
        "_utc_now_naive",
        MagicMock(return_value=FIXED_STARTED_AT),
    )

    with pytest.raises(
        ProcessingCheckIdAllocationError,
        match="must be a positive integer",
    ):
        claim_approved_check_request(saved.id)


def test_service_missing_request_raises(sqlite_db):
    with pytest.raises(CheckRequestNotFoundError, match="999999"):
        claim_approved_check_request(999999)


def test_service_non_approved_raises(sqlite_db):
    saved = _create_pending_request()

    with pytest.raises(InvalidCheckRequestTransitionError):
        claim_approved_check_request(saved.id)


def test_service_stale_processing_fields_raise(sqlite_db):
    saved = _approve(_create_pending_request().id)
    _set_request_fields(
        sqlite_db,
        saved.id,
        processing_check_id=str(CANDIDATE_A),
        processing_started_at=FIXED_STARTED_AT,
    )

    with pytest.raises(InvalidCheckRequestTransitionError):
        claim_approved_check_request(saved.id)


def test_service_does_not_retry_when_atomic_claim_loses_race(monkeypatch):
    approved = _approved_request_dict()
    processing = _approved_request_dict(
        status="processing",
        processing_check_id=str(CANDIDATE_A),
        processing_started_at=FIXED_STARTED_AT,
    )
    get_request = MagicMock(side_effect=[approved, processing])
    new_id = MagicMock(return_value=CANDIDATE_A)
    claim_record = MagicMock(return_value=None)
    monkeypatch.setattr(
        check_request_service,
        "get_check_request_by_id",
        get_request,
    )
    monkeypatch.setattr(
        check_request_service,
        "_new_processing_check_id",
        new_id,
    )
    monkeypatch.setattr(
        check_request_service,
        "_utc_now_naive",
        MagicMock(return_value=FIXED_STARTED_AT),
    )
    monkeypatch.setattr(
        check_request_service,
        "company_check_id_exists",
        MagicMock(return_value=False),
    )
    monkeypatch.setattr(
        check_request_service,
        "processing_check_id_exists",
        MagicMock(return_value=False),
    )
    monkeypatch.setattr(
        check_request_service,
        "claim_approved_check_request_record",
        claim_record,
    )

    with pytest.raises(InvalidCheckRequestTransitionError):
        claim_approved_check_request(approved["id"])

    assert get_request.call_count == 2
    new_id.assert_called_once_with()
    claim_record.assert_called_once_with(
        approved["id"],
        processing_check_id=str(CANDIDATE_A),
        processing_started_at=FIXED_STARTED_AT,
    )


def test_service_does_not_retry_when_request_disappears_after_claim(monkeypatch):
    approved = _approved_request_dict()
    get_request = MagicMock(side_effect=[approved, None])
    new_id = MagicMock(return_value=CANDIDATE_A)
    claim_record = MagicMock(return_value=None)
    monkeypatch.setattr(
        check_request_service,
        "get_check_request_by_id",
        get_request,
    )
    monkeypatch.setattr(
        check_request_service,
        "_new_processing_check_id",
        new_id,
    )
    monkeypatch.setattr(
        check_request_service,
        "_utc_now_naive",
        MagicMock(return_value=FIXED_STARTED_AT),
    )
    monkeypatch.setattr(
        check_request_service,
        "company_check_id_exists",
        MagicMock(return_value=False),
    )
    monkeypatch.setattr(
        check_request_service,
        "processing_check_id_exists",
        MagicMock(return_value=False),
    )
    monkeypatch.setattr(
        check_request_service,
        "claim_approved_check_request_record",
        claim_record,
    )

    with pytest.raises(CheckRequestNotFoundError):
        claim_approved_check_request(approved["id"])

    assert get_request.call_count == 2
    new_id.assert_called_once_with()
    claim_record.assert_called_once_with(
        approved["id"],
        processing_check_id=str(CANDIDATE_A),
        processing_started_at=FIXED_STARTED_AT,
    )


def test_processing_started_at_generated_once_across_retries(sqlite_db, monkeypatch):
    saved = _approve(_create_pending_request().id)
    utc_now = MagicMock(return_value=FIXED_STARTED_AT)
    monkeypatch.setattr(check_request_service, "_utc_now_naive", utc_now)
    monkeypatch.setattr(
        check_request_service,
        "_new_processing_check_id",
        MagicMock(side_effect=[CANDIDATE_A, CANDIDATE_B]),
    )
    monkeypatch.setattr(
        check_request_service,
        "company_check_id_exists",
        MagicMock(side_effect=[True, False]),
    )
    monkeypatch.setattr(
        check_request_service,
        "processing_check_id_exists",
        MagicMock(return_value=False),
    )

    claim_mock = MagicMock(
        return_value={
            "id": saved.id,
            "company_name": "Claim GmbH",
            "country": "Austria",
            "email": "claim@example.com",
            "website": "https://claim.example.com",
            "transaction_type": "procurement",
            "additional_context": "Needs claim testing.",
            "preferred_language": "de",
            "status": "processing",
            "company_check_id": None,
            "processing_check_id": str(CANDIDATE_B),
            "processing_started_at": FIXED_STARTED_AT,
            "created_at": FIXED_STARTED_AT,
        }
    )
    monkeypatch.setattr(
        check_request_service,
        "claim_approved_check_request_record",
        claim_mock,
    )

    claim_approved_check_request(saved.id)

    utc_now.assert_called_once_with()
    assert claim_mock.call_args.kwargs["processing_started_at"] == FIXED_STARTED_AT


def test_claimed_check_request_schema_validates():
    request = CheckRequestResponse(
        id=1,
        company_name="Schema GmbH",
        country="Austria",
        email="schema@example.com",
        preferred_language=CheckRequestLanguage.en,
        status=CheckRequestStatus.processing,
        created_at=FIXED_STARTED_AT,
    )
    claimed = ClaimedCheckRequest(
        request=request,
        processing_check_id=CANDIDATE_A,
        processing_started_at=FIXED_STARTED_AT,
    )

    assert claimed.processing_check_id == CANDIDATE_A
    assert claimed.request.status is CheckRequestStatus.processing


def test_ordinary_check_request_response_does_not_require_processing_fields():
    response = CheckRequestResponse(
        id=1,
        company_name="Pending GmbH",
        country="Austria",
        email="pending@example.com",
        preferred_language=CheckRequestLanguage.en,
        status=CheckRequestStatus.pending,
        created_at=FIXED_STARTED_AT,
    )

    assert response.status is CheckRequestStatus.pending
    assert response.company_check_id is None
    assert "processing_check_id" not in CheckRequestResponse.model_fields
    assert "processing_started_at" not in CheckRequestResponse.model_fields
