"""Web search tool.

Supports mock results for local development and optional Tavily provider.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import settings
from app.schemas.source import ConfidenceLevel, SourceResult, SourceType
from app.tools.tavily_search import TavilySearchError, tavily_search


def _dedupe_case_insensitive(names: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for name in names:
        cleaned = name.strip()
        if not cleaned:
            continue

        key = cleaned.casefold()
        if key not in seen:
            seen.add(key)
            result.append(cleaned)

    return result


NEGATIVE_KEYWORDS = [
    "scam",
    "fraud",
    "lawsuit",
    "sanction",
    "bankruptcy",
    "complaint",
    "warning",
    "criminal",
    "debt",
    "fake",
]


def build_company_search_queries(company_name: str, country: str) -> list[str]:
    """Build standard OSINT-style company search queries."""
    company_name = company_name.strip()
    country = country.strip()

    return [
        f'"{company_name}" "{country}" company',
        f'"{company_name}" official website',
        f'"{company_name}" registration',
        f'"{company_name}" reviews',
        f'"{company_name}" lawsuit',
        f'"{company_name}" sanctions',
        f'"{company_name}" scam',
    ]


def _mock_results_for_name(
    company_name: str,
    country: str,
    retrieved_at: datetime,
) -> list[SourceResult]:
    slug = company_name.lower().replace(" ", "-")

    return [
        SourceResult(
            title=f"Mock search result: {company_name} company profile",
            url=f"mock://search/{slug}/profile",
            snippet=(
                f"Mock source for local MVP testing. Mentions {company_name} "
                f"as a company associated with {country}. This is not a verified source."
            ),
            source_type=SourceType.search_result,
            retrieved_at=retrieved_at,
            confidence=ConfidenceLevel.low,
            is_mock=True,
        ),
        SourceResult(
            title=f"Mock search result: {company_name} possible website mention",
            url=f"mock://search/{slug}/website",
            snippet=(
                f"Mock source for local MVP testing. Possible website or business listing "
                f"mention for {company_name}. Manual verification is required."
            ),
            source_type=SourceType.search_result,
            retrieved_at=retrieved_at,
            confidence=ConfidenceLevel.low,
            is_mock=True,
        ),
        SourceResult(
            title=f"Mock search result: {company_name} reviews and public mentions",
            url=f"mock://search/{slug}/reviews",
            snippet=(
                f"Mock source for local MVP testing. Public mentions and reviews should be "
                f"checked manually for {company_name}."
            ),
            source_type=SourceType.search_result,
            retrieved_at=retrieved_at,
            confidence=ConfidenceLevel.low,
            is_mock=True,
        ),
    ]


def _mock_web_search(
    candidates: list[str],
    country: str,
    max_results: int,
) -> list[SourceResult]:
    now = datetime.now(timezone.utc)
    per_name = [_mock_results_for_name(name, country, now) for name in candidates]

    results: list[SourceResult] = []
    template_index = 0

    while len(results) < max_results:
        added = False
        for name_results in per_name:
            if template_index < len(name_results):
                results.append(name_results[template_index])
                added = True
                if len(results) >= max_results:
                    break
        if not added:
            break
        template_index += 1

    return results


def _tavily_web_search(
    primary_name: str,
    country: str,
    max_results: int,
) -> list[SourceResult]:
    return tavily_search(
        company_name=primary_name,
        country=country,
        max_results=max_results,
        api_key=settings.web_search_api_key,
        timeout_seconds=settings.web_search_timeout_seconds,
    )


def web_search(
    search_names: list[str],
    country: str,
    max_results: int = 5,
) -> list[SourceResult]:
    """Return web search results using the configured provider with mock fallback."""
    country = country.strip()
    candidates = _dedupe_case_insensitive(search_names)

    if not candidates:
        return []

    effective_max_results = max_results or settings.web_search_max_results

    if settings.web_search_provider == "tavily" and settings.web_search_api_key:
        try:
            tavily_results = _tavily_web_search(
                candidates[0],
                country,
                effective_max_results,
            )
            if tavily_results:
                return tavily_results[:effective_max_results]
        except TavilySearchError:
            pass

    return _mock_web_search(candidates, country, effective_max_results)


def count_negative_snippets(sources: list[SourceResult]) -> int:
    """Count source snippets containing suspicious or negative keywords."""
    count = 0

    for source in sources:
        text = f"{source.title} {source.snippet}".lower()
        if any(keyword in text for keyword in NEGATIVE_KEYWORDS):
            count += 1

    return count


def extract_suspicious_keywords(sources: list[SourceResult]) -> list[str]:
    """Extract suspicious keywords found in source titles/snippets."""
    found: set[str] = set()

    for source in sources:
        text = f"{source.title} {source.snippet}".lower()
        for keyword in NEGATIVE_KEYWORDS:
            if keyword in text:
                found.add(keyword)

    return sorted(found)
