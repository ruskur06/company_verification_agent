"""Human review workflow schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, field_validator

from app.schemas.risk import BusinessRiskLevel, RiskLevel


class ReviewDecision(str, Enum):
    approved = "approved"
    edited = "edited"
    rejected = "rejected"


class HumanReviewCreate(BaseModel):
    decision: ReviewDecision
    reviewer_name: str
    reviewer_notes: str | None = None
    final_verification_confidence: RiskLevel
    final_verification_risk: RiskLevel
    final_business_risk: BusinessRiskLevel
    overrides: dict[str, Any] = {}

    @field_validator("reviewer_name")
    @classmethod
    def reviewer_name_not_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reviewer_name must not be empty")
        return value


class HumanReviewRecordResponse(BaseModel):
    id: int
    company_check_id: str
    decision: ReviewDecision
    reviewer_name: str
    reviewer_notes: str | None = None
    final_verification_confidence: RiskLevel
    final_verification_risk: RiskLevel
    final_business_risk: BusinessRiskLevel
    overrides: dict[str, Any]
    is_locked: bool
    created_at: datetime
