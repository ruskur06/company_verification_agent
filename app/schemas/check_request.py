"""Public company check request schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, field_validator


class CheckRequestStatus(str, Enum):
    """Lifecycle status of a public company check request."""

    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    processed = "processed"


class CheckRequestLanguage(str, Enum):
    """Supported language selected through the public route."""

    en = "en"
    de = "de"
    es = "es"


class CheckRequestTransactionType(str, Enum):
    """Supported transaction contexts for a public request."""

    real_estate = "real_estate"
    supplier_verification = "supplier_verification"
    procurement = "procurement"
    legal_advisory = "legal_advisory"
    other = "other"


class CheckRequestCreate(BaseModel):
    """Validated data accepted when creating a public check request."""

    company_name: str
    country: str
    email: str
    website: str | None = None
    transaction_type: CheckRequestTransactionType | None = None
    additional_context: str | None = None
    preferred_language: CheckRequestLanguage

    @field_validator(
        "company_name",
        "country",
        "email",
    )
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        """Strip required text and reject empty values."""
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value

    @field_validator(
        "website",
        "additional_context",
    )
    @classmethod
    def strip_optional_text(
        cls,
        value: str | None,
    ) -> str | None:
        """Strip optional text and convert blanks to None."""
        if value is None:
            return None

        value = value.strip()
        return value or None


class CheckRequestResponse(BaseModel):
    """Saved public company check request."""

    id: int
    company_name: str
    country: str
    email: str
    website: str | None = None
    transaction_type: CheckRequestTransactionType | None = None
    additional_context: str | None = None
    preferred_language: CheckRequestLanguage
    status: CheckRequestStatus
    company_check_id: str | None = None
    created_at: datetime
