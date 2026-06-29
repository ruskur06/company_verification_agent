"""Final human risk review schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator, model_validator

from app.schemas.human_review import ReviewDecision
from app.schemas.risk import HumanReviewStatus, RiskLevel


class FinalRiskReviewCreate(BaseModel):
    decision: ReviewDecision
    final_score: int | None = None
    final_level: RiskLevel | None = None
    notes: str | None = None
    reviewed_by: str | None = None

    @field_validator("reviewed_by")
    @classmethod
    def strip_reviewed_by(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def validate_decision_fields(self) -> FinalRiskReviewCreate:
        if self.decision == ReviewDecision.edited:
            if self.final_score is None or self.final_level is None:
                raise ValueError("final_score and final_level are required for edited decision")

        if self.final_score is not None and not 0 <= self.final_score <= 100:
            raise ValueError("final_score must be between 0 and 100")

        return self


class FinalRiskReviewResponse(BaseModel):
    check_id: int
    human_review_status: HumanReviewStatus
    final_score: int | None = None
    final_level: RiskLevel | None = None
    notes: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
