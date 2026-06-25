"""Tavily web search adapter."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.schemas.source import ConfidenceLevel, SourceResult, SourceType

TAVILY_API_URL = "https://api.tavily.com/search"


class TavilySearchError(Exception):
    """Raised when Tavily search cannot be completed."""


def build_tavily_query(company_name: str, country: str) -> str:
    """Build a Tavily query for company verification research."""
    company_name = company_name.strip()
    country = country.strip()
    return (
        f'"{company_name}" "{country}" company official website registry business profile'
    )


def _normalize_tavily_results(payload: dict, *, max_results: int) -> list[SourceResult]:
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        raise TavilySearchError("Tavily response is missing a valid results list.")

    retrieved_at = datetime.now(timezone.utc)
    normalized: list[SourceResult] = []

    for item in raw_results:
        if not isinstance(item, dict):
            continue

        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("content") or item.get("snippet") or "").strip()

        if not title or not url:
            continue

        normalized.append(
            SourceResult(
                title=title,
                url=url,
                snippet=snippet,
                source_type=SourceType.search_result,
                retrieved_at=retrieved_at,
                confidence=ConfidenceLevel.medium,
                is_mock=False,
            )
        )

        if len(normalized) >= max_results:
            break

    return normalized


def tavily_search(
    company_name: str,
    country: str,
    max_results: int = 5,
    api_key: str | None = None,
    timeout_seconds: float | None = None,
) -> list[SourceResult]:
    """Search Tavily and return normalized source results."""
    if not api_key:
        raise TavilySearchError("Tavily API key is not configured.")

    query = build_tavily_query(company_name, country)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
    }

    try:
        response = httpx.post(
            TAVILY_API_URL,
            headers=headers,
            json=body,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise TavilySearchError(f"Tavily request failed: {exc}") from exc
    except ValueError as exc:
        raise TavilySearchError("Tavily response was not valid JSON.") from exc

    if not isinstance(payload, dict):
        raise TavilySearchError("Tavily response must be a JSON object.")

    return _normalize_tavily_results(payload, max_results=max_results)
