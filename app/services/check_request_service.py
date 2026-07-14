"""Public company check request service."""

from __future__ import annotations

from app.db.repositories import (
    create_check_request_record,
    get_check_request_by_id,
    list_check_requests as list_check_request_records,
)
from app.schemas.check_request import (
    CheckRequestCreate,
    CheckRequestResponse,
)


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
