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