"""Company registry check schemas."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel

from app.schemas.source import ConfidenceLevel


class RegistryCheckStatus(str, Enum):
    found = "found"
    not_found = "not_found"
    not_supported = "not_supported"
    error = "error"


class RegistryCheckResult(BaseModel):
    company_name: str
    country: str
    status: RegistryCheckStatus
    registry_found: bool
    registry_name: Optional[str] = None
    source_url: Optional[str] = None
    confidence: ConfidenceLevel = ConfidenceLevel.low
    notes: list[str] = []
    is_mock: bool = True
    matched_name: Optional[str] = None
    searched_names: list[str] = []
