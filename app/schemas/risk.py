"""Risk score schemas."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class HumanReviewStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    edited = "edited"


class RiskFactor(BaseModel):
    name: str
    impact: int
    explanation: str


class RiskScoreInput(BaseModel):
    has_website: bool = False
    domain_resolves: bool = False
    has_mx_record: bool = False
    https_available: bool = False
    negative_snippets_count: int = 0
    registry_found: bool = False
    multiple_sources_confirm: bool = False
    suspicious_keywords_found: list[str] = []
    source_count: int = 0


class RiskScoreResult(BaseModel):
    score: int
    level: RiskLevel
    factors: list[RiskFactor]
    requires_human_review: bool


class HumanReviewInput(BaseModel):
    decision: HumanReviewStatus
    final_score: Optional[int] = None
    final_level: Optional[RiskLevel] = None
    notes: str = ""