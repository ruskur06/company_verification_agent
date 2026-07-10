"""Company registry search tool.

Current MVP behavior:
- returns deterministic mock registry results
- does not scrape real registry websites
- marks all mock results with is_mock=True

Later this file can be connected to official registry APIs and providers.
"""

from __future__ import annotations

from app.schemas.registry import RegistryCheckResult, RegistryCheckStatus
from app.schemas.source import ConfidenceLevel
from app.tools.country_source_registry import resolve_country_source_profile

KNOWN_USA_DEMO_COMPANIES = {
    "servochron",
}


def _normalize_name(value: str) -> str:
    return value.strip()


def search_company_registry(company_name: str, country: str) -> RegistryCheckResult:
    """Search for a company in an official registry (mock implementation)."""
    name = _normalize_name(company_name)

    if not name:
        return RegistryCheckResult(
            company_name=company_name,
            country=country,
            status=RegistryCheckStatus.error,
            registry_found=False,
            notes=["Company name must not be empty."],
            is_mock=True,
        )

    if not country.strip():
        return RegistryCheckResult(
            company_name=name,
            country=country,
            status=RegistryCheckStatus.not_supported,
            registry_found=False,
            notes=["Country was not provided."],
            is_mock=True,
        )

    profile = resolve_country_source_profile(country)
    if profile is None:
        return RegistryCheckResult(
            company_name=name,
            country=country,
            status=RegistryCheckStatus.not_supported,
            registry_found=False,
            notes=[f"Registry search is not supported for country: {country}."],
            is_mock=True,
        )

    if profile.country_code == "IL":
        return RegistryCheckResult(
            company_name=name,
            country=country,
            status=RegistryCheckStatus.not_supported,
            registry_found=False,
            notes=["Israeli registry integration will be added later."],
            is_mock=True,
        )

    if profile.country_code == "AT":
        return RegistryCheckResult(
            company_name=name,
            country=country,
            status=RegistryCheckStatus.not_supported,
            registry_found=False,
            notes=[
                "Austrian source profile is configured, but live registry integration is not implemented yet."
            ],
            is_mock=True,
        )

    if profile.country_code == "US":
        if name.lower() in KNOWN_USA_DEMO_COMPANIES:
            return RegistryCheckResult(
                company_name=name,
                country=country,
                status=RegistryCheckStatus.found,
                registry_found=True,
                registry_name="US public business registry search",
                source_url=None,
                confidence=ConfidenceLevel.medium,
                notes=["Mock registry match for local MVP testing."],
                is_mock=True,
            )

        return RegistryCheckResult(
            company_name=name,
            country=country,
            status=RegistryCheckStatus.not_found,
            registry_found=False,
            notes=["No mock registry match was found for this company name."],
            is_mock=True,
        )

    return RegistryCheckResult(
        company_name=name,
        country=country,
        status=RegistryCheckStatus.not_supported,
        registry_found=False,
        notes=[f"Registry search is not supported for country: {country}."],
        is_mock=True,
    )
