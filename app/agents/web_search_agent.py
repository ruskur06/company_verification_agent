"""Agent that wraps the web search tool."""

from __future__ import annotations

from app.schemas.source import SourceResult
from app.tools.web_search import web_search


class WebSearchAgent:
    """Runs company web search and returns source results."""

    def run(self, company_name: str, country: str, max_results: int = 5) -> list[SourceResult]:
        """Search for company information and return source results."""
        return web_search(
            company_name=company_name,
            country=country,
            max_results=max_results,
        )
