"""Unit tests for approved-request orchestration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.db.repositories import ApprovedRequestPersistenceFenceError
from app.schemas.approved_request_persistence import PersistedApprovedRequestCheck
from app.schemas.approved_request_pipeline import PreparedApprovedRequestCheck
from app.schemas.check_request import (
    CheckRequestLanguage,
    CheckRequestResponse,
    CheckRequestStatus,
    ClaimedCheckRequest,
)
from app.services import approved_request_pipeline_service
from app.services.approved_request_pipeline_service import (
    PreparedCheckValidationError,
    ReportFileCollisionError,
    run_approved_request_check,
)
from app.services.check_request_service import (
    CheckRequestNotFoundError,
    InvalidCheckRequestTransitionError,
    ProcessingCheckIdAllocationError,
)


REQUEST_ID = 17
PROCESSING_CHECK_ID = 1782245999876
FIXED_STARTED_AT = datetime(2026, 7, 17, 12, 0, 0)


def _claimed(
    *,
    processing_check_id: int = PROCESSING_CHECK_ID,
) -> ClaimedCheckRequest:
    return ClaimedCheckRequest(
        request=CheckRequestResponse(
            id=REQUEST_ID,
            company_name="Orchestration GmbH",
            country="Austria",
            email="orchestration@example.com",
            website="https://orchestration.example.com",
            preferred_language=CheckRequestLanguage.en,
            status=CheckRequestStatus.processing,
            created_at=FIXED_STARTED_AT,
        ),
        processing_check_id=processing_check_id,
        processing_started_at=FIXED_STARTED_AT,
    )


def _prepared(
    claimed: ClaimedCheckRequest | None = None,
) -> PreparedApprovedRequestCheck:
    claimed = claimed or _claimed()
    processing_check_id = str(claimed.processing_check_id)
    return PreparedApprovedRequestCheck(
        source_check_request_id=claimed.request.id,
        processing_check_id=processing_check_id,
        processing_started_at=claimed.processing_started_at,
        result_payload={
            "check_id": processing_check_id,
            "company_name": claimed.request.company_name,
            "country": claimed.request.country,
        },
        json_report_path=f"outputs/json/company_check_{processing_check_id}.json",
        markdown_report_path=(
            f"outputs/reports/company_check_{processing_check_id}.md"
        ),
        json_content='{"check_id": "' + processing_check_id + '"}',
        markdown_content="# report\n",
    )


def _persisted(
    prepared: PreparedApprovedRequestCheck | None = None,
) -> PersistedApprovedRequestCheck:
    prepared = prepared or _prepared()
    return PersistedApprovedRequestCheck(
        source_check_request_id=prepared.source_check_request_id,
        company_check_id=prepared.processing_check_id,
        status=CheckRequestStatus.processed,
    )


def test_run_approved_request_check_success_order_and_passthrough(monkeypatch):
    claimed = _claimed(processing_check_id=1782245999999)
    prepared = _prepared(claimed)
    persisted = _persisted(prepared)
    call_order: list[str] = []

    claim = MagicMock(side_effect=lambda request_id: (
        call_order.append("claim"),
        claimed,
    )[1])
    execute = MagicMock(side_effect=lambda received: (
        call_order.append("execute"),
        prepared,
    )[1])
    persist = MagicMock(side_effect=lambda received: (
        call_order.append("persist"),
        persisted,
    )[1])

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "claim_approved_check_request",
        claim,
    )
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_claimed_check_request",
        execute,
    )
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "persist_prepared_approved_request_check",
        persist,
    )

    result = run_approved_request_check(REQUEST_ID)

    assert call_order == ["claim", "execute", "persist"]
    claim.assert_called_once_with(REQUEST_ID)
    execute.assert_called_once_with(claimed)
    assert execute.call_args.args[0] is claimed
    assert execute.call_args.args[0].processing_check_id == 1782245999999
    persist.assert_called_once_with(prepared)
    assert persist.call_args.args[0] is prepared
    assert result is persisted


@pytest.mark.parametrize(
    "claim_error",
    [
        CheckRequestNotFoundError("missing request"),
        InvalidCheckRequestTransitionError("not eligible"),
        ProcessingCheckIdAllocationError("id allocation failed"),
    ],
)
def test_claim_failure_propagates_and_skips_later_steps(
    monkeypatch,
    claim_error: Exception,
):
    claim = MagicMock(side_effect=claim_error)
    execute = MagicMock(side_effect=AssertionError("execute must not run"))
    persist = MagicMock(side_effect=AssertionError("persist must not run"))

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "claim_approved_check_request",
        claim,
    )
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_claimed_check_request",
        execute,
    )
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "persist_prepared_approved_request_check",
        persist,
    )

    with pytest.raises(type(claim_error)) as exc_info:
        run_approved_request_check(REQUEST_ID)

    assert exc_info.value is claim_error
    claim.assert_called_once_with(REQUEST_ID)
    execute.assert_not_called()
    persist.assert_not_called()


@pytest.mark.parametrize(
    "execution_error",
    [
        RuntimeError("pipeline failed"),
        PreparedCheckValidationError("artifact validation failed"),
        ReportFileCollisionError(
            "report collision",
            source_check_request_id=REQUEST_ID,
            processing_check_id=PROCESSING_CHECK_ID,
            colliding_paths=(Path("outputs/json/collision.json"),),
        ),
    ],
)
def test_execution_failure_propagates_and_skips_persistence(
    monkeypatch,
    execution_error: Exception,
):
    claimed = _claimed()
    claim = MagicMock(return_value=claimed)
    execute = MagicMock(side_effect=execution_error)
    persist = MagicMock(side_effect=AssertionError("persist must not run"))

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "claim_approved_check_request",
        claim,
    )
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_claimed_check_request",
        execute,
    )
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "persist_prepared_approved_request_check",
        persist,
    )

    with pytest.raises(type(execution_error)) as exc_info:
        run_approved_request_check(REQUEST_ID)

    assert exc_info.value is execution_error
    claim.assert_called_once_with(REQUEST_ID)
    execute.assert_called_once_with(claimed)
    persist.assert_not_called()


def test_persistence_fence_error_propagates_unchanged(monkeypatch):
    claimed = _claimed()
    prepared = _prepared(claimed)
    fence_error = ApprovedRequestPersistenceFenceError(
        "stale attempt cannot finalize",
        source_check_request_id=REQUEST_ID,
        processing_check_id=str(PROCESSING_CHECK_ID),
    )

    claim = MagicMock(return_value=claimed)
    execute = MagicMock(return_value=prepared)
    persist = MagicMock(side_effect=fence_error)

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "claim_approved_check_request",
        claim,
    )
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_claimed_check_request",
        execute,
    )
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "persist_prepared_approved_request_check",
        persist,
    )

    with pytest.raises(ApprovedRequestPersistenceFenceError) as exc_info:
        run_approved_request_check(REQUEST_ID)

    assert exc_info.value is fence_error
    claim.assert_called_once_with(REQUEST_ID)
    execute.assert_called_once_with(claimed)
    persist.assert_called_once_with(prepared)
