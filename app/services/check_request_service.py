"""Public company check request service."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.db.repositories import (
    claim_approved_check_request_record,
    company_check_id_exists,
    create_check_request_record,
    get_check_request_by_id,
    list_check_requests as list_check_request_records,
    processing_check_id_exists,
    update_check_request_status,
)
from app.schemas.check_request import (
    CheckRequestCreate,
    CheckRequestResponse,
    CheckRequestStatus,
    ClaimedCheckRequest,
)


_PROCESSING_CHECK_ID_MAX_ATTEMPTS = 5


class CheckRequestNotFoundError(Exception):
    """Raised when a requested CheckRequest does not exist."""


class InvalidCheckRequestTransitionError(Exception):
    """Raised when a status transition is not allowed."""


class ProcessingCheckIdAllocationError(RuntimeError):
    """Raised when a unique processing check ID cannot be allocated."""


def create_check_request(
    request: CheckRequestCreate,
) -> CheckRequestResponse:
    """Save a public request without running verification."""
    saved = create_check_request_record(
        request.model_dump(mode="json")
    )

    return CheckRequestResponse.model_validate(saved)


def get_check_request(
    request_id: int,
) -> CheckRequestResponse | None:
    """Load one saved public request."""
    saved = get_check_request_by_id(request_id)

    if saved is None:
        return None

    return CheckRequestResponse.model_validate(saved)


def list_check_requests(
    limit: int = 50,
) -> list[CheckRequestResponse]:
    """List recent public check requests for internal review."""
    records = list_check_request_records(limit=limit)
    return [
        CheckRequestResponse.model_validate(record)
        for record in records
    ]


def approve_check_request(
    request_id: int,
) -> CheckRequestResponse:
    """Approve a pending public check request."""
    return _transition_pending_request(
        request_id,
        new_status=CheckRequestStatus.approved,
    )


def reject_check_request(
    request_id: int,
) -> CheckRequestResponse:
    """Reject a pending public check request."""
    return _transition_pending_request(
        request_id,
        new_status=CheckRequestStatus.rejected,
    )


def claim_approved_check_request(
    request_id: int,
) -> ClaimedCheckRequest:
    """Atomically claim an approved request for processing."""
    current = get_check_request_by_id(request_id)
    if current is None:
        raise CheckRequestNotFoundError(
            f"Check request {request_id} was not found"
        )

    if (
        current.get("status") != CheckRequestStatus.approved.value
        or current.get("company_check_id") is not None
        or current.get("processing_check_id") is not None
        or current.get("processing_started_at") is not None
    ):
        raise InvalidCheckRequestTransitionError(
            f"Check request {request_id} cannot be claimed from status "
            f"{current.get('status')}"
        )

    processing_started_at = _utc_now_naive()

    for _attempt in range(_PROCESSING_CHECK_ID_MAX_ATTEMPTS):
        candidate_id = _new_processing_check_id()
        if (
            isinstance(candidate_id, bool)
            or not isinstance(candidate_id, int)
            or candidate_id <= 0
        ):
            raise ProcessingCheckIdAllocationError(
                f"Generated processing check ID for request {request_id} "
                "must be a positive integer."
            )

        candidate_id_str = str(candidate_id)

        # Pre-check CompanyCheckRecord.check_id to reduce cross-table collision
        # risk. This is not an atomic cross-table reservation; strict persistence
        # still relies on database uniqueness and fencing.
        if company_check_id_exists(candidate_id_str):
            continue

        if processing_check_id_exists(candidate_id_str):
            continue

        try:
            claimed = claim_approved_check_request_record(
                request_id,
                processing_check_id=candidate_id_str,
                processing_started_at=processing_started_at,
            )
        except IntegrityError:
            if processing_check_id_exists(candidate_id_str):
                continue
            raise

        if claimed is None:
            refreshed = get_check_request_by_id(request_id)
            if refreshed is None:
                raise CheckRequestNotFoundError(
                    f"Check request {request_id} was not found"
                )
            raise InvalidCheckRequestTransitionError(
                f"Check request {request_id} could not be claimed"
            )

        stored_processing_check_id = claimed.get("processing_check_id")
        stored_processing_started_at = claimed.get("processing_started_at")
        if stored_processing_check_id != candidate_id_str:
            raise ProcessingCheckIdAllocationError(
                f"Claimed processing check ID for request {request_id} "
                "did not match the allocated candidate."
            )
        if stored_processing_started_at is None:
            raise ProcessingCheckIdAllocationError(
                f"Claimed request {request_id} is missing processing_started_at."
            )

        return ClaimedCheckRequest(
            request=CheckRequestResponse.model_validate(claimed),
            processing_check_id=int(stored_processing_check_id),
            processing_started_at=stored_processing_started_at,
        )

    raise ProcessingCheckIdAllocationError(
        f"Could not allocate a unique processing check ID for request "
        f"{request_id} after {_PROCESSING_CHECK_ID_MAX_ATTEMPTS} attempts."
    )


def _new_processing_check_id() -> int:
    """Generate a microsecond-resolution numeric processing check ID."""
    return time.time_ns() // 1_000


def _utc_now_naive() -> datetime:
    """Return the current UTC time as a naive datetime for DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _transition_pending_request(
    request_id: int,
    *,
    new_status: CheckRequestStatus,
) -> CheckRequestResponse:
    current = get_check_request(request_id)
    if current is None:
        raise CheckRequestNotFoundError(
            f"Check request {request_id} was not found"
        )

    if current.status != CheckRequestStatus.pending:
        raise InvalidCheckRequestTransitionError(
            f"Check request {request_id} cannot leave status "
            f"{current.status.value}"
        )

    updated = update_check_request_status(
        request_id,
        expected_status=CheckRequestStatus.pending.value,
        new_status=new_status.value,
    )
    if updated is None:
        raise InvalidCheckRequestTransitionError(
            f"Check request {request_id} is no longer pending"
        )

    return CheckRequestResponse.model_validate(updated)
