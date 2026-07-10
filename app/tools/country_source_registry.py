"""Deterministic country source profile registry."""

from __future__ import annotations

import unicodedata

from app.schemas.country_source import (
    CountrySourceDefinition,
    CountrySourceKind,
    CountrySourceProfile,
)

_AUSTRIA_SOURCE_DEFINITIONS: tuple[CountrySourceDefinition, ...] = (
    CountrySourceDefinition(
        source_id="at_firmenbuch",
        display_name="Austrian Company Register (Firmenbuch)",
        source_kind=CountrySourceKind.company_register,
        is_official=True,
        adapter_key="austria_firmenbuch",
        live_integration_available=False,
    ),
    CountrySourceDefinition(
        source_id="at_gisa",
        display_name="Austrian Business Licence Information System (GISA)",
        source_kind=CountrySourceKind.business_license_registry,
        is_official=True,
        adapter_key="austria_gisa",
        live_integration_available=False,
    ),
    CountrySourceDefinition(
        source_id="eu_vat_vies",
        display_name="EU VAT Validation (VIES)",
        source_kind=CountrySourceKind.vat_validation,
        is_official=True,
        adapter_key="eu_vies",
        live_integration_available=False,
    ),
    CountrySourceDefinition(
        source_id="website_impressum",
        display_name="Company Website Legal Notice (Impressum)",
        source_kind=CountrySourceKind.website_legal_notice,
        is_official=False,
        adapter_key="website_legal_page_scan",
        live_integration_available=False,
    ),
)

_USA_PROFILE = CountrySourceProfile(
    country_code="US",
    canonical_name="United States",
    aliases=("USA", "United States", "US", "United States of America"),
    registry_adapter_key=None,
    source_definitions=(),
    legal_page_patterns=(),
    expected_identifiers=(),
)

_ISRAEL_PROFILE = CountrySourceProfile(
    country_code="IL",
    canonical_name="Israel",
    aliases=("Israel", "IL"),
    registry_adapter_key=None,
    source_definitions=(),
    legal_page_patterns=(),
    expected_identifiers=(),
)

_AUSTRIA_PROFILE = CountrySourceProfile(
    country_code="AT",
    canonical_name="Austria",
    aliases=("Austria", "AT", "AUT", "Österreich", "Oesterreich"),
    registry_adapter_key="austria_firmenbuch",
    source_definitions=_AUSTRIA_SOURCE_DEFINITIONS,
    legal_page_patterns=(
        "impressum",
        "legal",
        "legal-notice",
        "kontakt",
        "contact",
        "datenschutz",
        "privacy",
    ),
    expected_identifiers=(
        "legal_name",
        "registration_number",
        "registered_address",
        "vat_number",
        "legal_representative",
    ),
)

_PROFILES: tuple[CountrySourceProfile, ...] = (
    _USA_PROFILE,
    _ISRAEL_PROFILE,
    _AUSTRIA_PROFILE,
)


def _normalize_country_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _build_alias_lookup() -> dict[str, CountrySourceProfile]:
    lookup: dict[str, CountrySourceProfile] = {}
    for profile in _PROFILES:
        for alias in profile.aliases:
            lookup[_normalize_country_key(alias)] = profile
    return lookup


_ALIAS_LOOKUP = _build_alias_lookup()


def resolve_country_source_profile(country: str) -> CountrySourceProfile | None:
    """Resolve a country string to a configured source profile."""
    normalized = _normalize_country_key(country)
    if not normalized:
        return None
    return _ALIAS_LOOKUP.get(normalized)
