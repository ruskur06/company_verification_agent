"""Source / search result schemas."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, HttpUrl, field_validator


class SourceType(str, Enum):
    search_result = "search_result"
    registry = "registry"
    website = "website"
    dns = "dns"
    other = "other"


class ConfidenceLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class SourceResult(BaseModel):
    title: str
    url: str
    snippet: str
    source_type: SourceType = SourceType.search_result
    retrieved_at: datetime
    confidence: ConfidenceLevel = ConfidenceLevel.low
    is_mock: bool = False


class ManualSourceCreate(BaseModel):
    """Request body for attaching a human-verified source to a company check."""

    title: str
    url: str
    snippet: str | None = None
    source_type: SourceType = SourceType.search_result
    confidence: ConfidenceLevel = ConfidenceLevel.medium

    @field_validator("title", "url")
    @classmethod
    def not_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Field must not be empty")
        return value


class SavedSourceResponse(BaseModel):
    """Persisted source linked to a company check."""

    id: int
    company_check_id: str
    title: str
    url: str
    snippet: str | None = None
    source_type: SourceType
    confidence: ConfidenceLevel
    is_mock: bool
    retrieved_at: datetime
    created_at: datetime