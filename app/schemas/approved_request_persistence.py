"""Schemas for strict approved-request persistence results."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.schemas.check_request import CheckRequestStatus


class PersistedApprovedRequestCheck(BaseModel):
    """Successful strict persistence result."""

    model_config = ConfigDict(frozen=True)

    source_check_request_id: int
    company_check_id: str
    status: CheckRequestStatus
