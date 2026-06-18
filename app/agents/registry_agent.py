"""Agent that wraps the company registry search tool."""

from __future__ import annotations

from app.schemas.registry import RegistryCheckResult
from app.tools.registry_search import search_company_registry


class RegistryAgent:
    """Searches official company registries for registration evidence."""

    def run(self, company_name: str, country: str) -> RegistryCheckResult:
        """Run registry search for the given company and country."""
        return search_company_registry(company_name=company_name, country=country)
