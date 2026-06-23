"""Main company check request and result schemas."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, field_validator

from app.schemas.name_normalizer import NameNormalizerResult
from app.schemas.registry import RegistryCheckResult
from app.schemas.source import SourceResult, ConfidenceLevel
from app.schemas.risk import BusinessRiskLevel, RiskFactor, RiskLevel, HumanReviewStatus


class CheckStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class CompanyCheckRequest(BaseModel):
    company_name: str
    country: str
    domain: Optional[str] = None

    @field_validator("company_name", "country")
    @classmethod
    def not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Field must not be empty")
        return v


class CompanyInfo(BaseModel):
    name: str
    country: str
    domain: Optional[str] = None


class SummaryInfo(BaseModel):
    short_description: str
    overall_assessment: str
    confidence: ConfidenceLevel


class DomainDnsStatus(str, Enum):
    not_provided = "not_provided"
    checked = "checked"
    failed = "failed"


class DomainDnsInfo(BaseModel):
    status: DomainDnsStatus
    domain: Optional[str] = None
    has_a_record: bool = False
    has_mx_record: bool = False
    has_txt_record: bool = False
    https_available: bool = False
    warnings: list[str] = []


class RiskInfo(BaseModel):
    preliminary_score: int
    preliminary_level: RiskLevel
    verification_confidence: RiskLevel
    verification_risk: RiskLevel
    business_risk: BusinessRiskLevel
    factors: list[RiskFactor]
    requires_human_review: bool
    final_score: Optional[int] = None
    final_level: Optional[RiskLevel] = None
    human_review_status: HumanReviewStatus = HumanReviewStatus.pending


class CompanyCheckResult(BaseModel):
    """The full, strict JSON output for a company check."""

    check_id: int
    company: CompanyInfo
    name_normalization: Optional[NameNormalizerResult] = None
    summary: SummaryInfo
    sources: list[SourceResult]
    domain_dns: DomainDnsInfo
    registry_check: RegistryCheckResult
    risk: RiskInfo
    manual_verification_checklist: list[str]
    unknowns: list[str]
    created_at: datetime


class CompanyCheckResponse(BaseModel):
    """API response wrapper."""

    check_id: int
    status: CheckStatus
    json_result: Optional[CompanyCheckResult] = None
    markdown_report_path: Optional[str] = None
    error: Optional[str] = None