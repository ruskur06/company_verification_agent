"""Official website human review schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, field_validator


class OfficialWebsiteReviewDecision(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    uncertain = "uncertain"


class OfficialWebsiteReviewSubmitDecision(str, Enum):
    approved = "approved"
    rejected = "rejected"
    uncertain = "uncertain"


class OfficialWebsiteReview(BaseModel):
    decision: OfficialWebsiteReviewDecision = OfficialWebsiteReviewDecision.pending
    note: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class OfficialWebsiteReviewCreate(BaseModel):
    decision: OfficialWebsiteReviewSubmitDecision
    note: str | None = None
    reviewed_by: str | None = None

    @field_validator("reviewed_by")
    @classmethod
    def strip_reviewed_by(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class OfficialWebsiteReviewResponse(BaseModel):
    check_id: int
    official_website_review: OfficialWebsiteReview
    website_candidate_verified: bool
