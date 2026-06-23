"""Agent that wraps the web search tool."""

from __future__ import annotations

from app.schemas.source import SourceResult
from app.tools.web_search import web_search


class WebSearchAgent:
    """Runs company web search and returns source results."""

    def run(
        self,
        search_names: list[str],
        country: str,
        max_results: int = 5,
    ) -> list[SourceResult]:
        """Search for company information across normalized name variants."""
        return web_search(
            search_names=search_names,
            country=country,
            max_results=max_results,
        )
