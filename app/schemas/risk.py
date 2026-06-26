"""Risk score schemas."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class BusinessRiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    unknown = "unknown"


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
    has_website_candidate: bool = False
    user_domain_provided: bool = False
    candidate_domain_dns_succeeds: bool = False
    candidate_has_mx_record: bool = False
    domain_resolves: bool = False
    has_mx_record: bool = False
    https_available: bool = False
    negative_snippets_count: int = 0
    registry_found: bool = False
    registry_is_mock: bool = True
    multiple_sources_confirm: bool = False
    suspicious_keywords_found: list[str] = []
    source_count: int = 0
    all_sources_mock: bool = True
    verified_non_mock_source_count: int = 0
    verified_strong_source_count: int = 0
    has_high_confidence_verified_source: bool = False


class RiskScoreResult(BaseModel):
    score: int
    level: RiskLevel
    verification_confidence: RiskLevel
    verification_risk: RiskLevel
    business_risk: BusinessRiskLevel
    factors: list[RiskFactor]
    requires_human_review: bool


class HumanReviewInput(BaseModel):
    decision: HumanReviewStatus
    final_score: Optional[int] = None
    final_level: Optional[RiskLevel] = None
    notes: str = ""