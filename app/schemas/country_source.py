"""Country source registry schemas."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class CountrySourceKind(str, Enum):
    company_register = "company_register"
    business_license_registry = "business_license_registry"
    vat_validation = "vat_validation"
    website_legal_notice = "website_legal_notice"


class CountrySourceDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    display_name: str
    source_kind: CountrySourceKind
    is_official: bool
    adapter_key: str | None
    live_integration_available: bool = False
    base_url: str | None = None
    notes: str | None = None


class CountrySourceProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    country_code: str
    canonical_name: str
    aliases: tuple[str, ...]
    registry_adapter_key: str | None
    source_definitions: tuple[CountrySourceDefinition, ...]
    legal_page_patterns: tuple[str, ...]
    expected_identifiers: tuple[str, ...]
