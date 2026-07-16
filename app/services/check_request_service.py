"""Public company check request service."""

from __future__ import annotations

from app.db.repositories import (
    create_check_request_record,
    get_check_request_by_id,
    list_check_requests as list_check_request_records,
    update_check_request_status,
)
from app.schemas.check_request import (
    CheckRequestCreate,
    CheckRequestResponse,
    CheckRequestStatus,
)


class CheckRequestNotFoundError(Exception):
    """Raised when a requested CheckRequest does not exist."""


class InvalidCheckRequestTransitionError(Exception):
    """Raised when a status transition is not allowed."""


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
