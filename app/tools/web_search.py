"""Web search tool.

Current MVP behavior:
- returns deterministic mock search results
- does not call real search APIs
- marks all mock results with is_mock=True

Later this file can be connected to Bing, Brave, Tavily, SerpAPI, Google CSE, etc.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.source import ConfidenceLevel, SourceResult, SourceType


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


def web_search(
    company_name: str,
    country: str,
    max_results: int = 5,
) -> list[SourceResult]:
    """Return mock search results for the company.

    This is intentionally deterministic for local development and tests.
    It does not claim that real sources were checked.
    """
    company_name = company_name.strip()
    country = country.strip()

    now = datetime.now(timezone.utc)

    mock_results = [
        SourceResult(
            title=f"Mock search result: {company_name} company profile",
            url=f"mock://search/{company_name.lower().replace(' ', '-')}/profile",
            snippet=(
                f"Mock source for local MVP testing. Mentions {company_name} "
                f"as a company associated with {country}. This is not a verified source."
            ),
            source_type=SourceType.search_result,
            retrieved_at=now,
            confidence=ConfidenceLevel.low,
            is_mock=True,
        ),
        SourceResult(
            title=f"Mock search result: {company_name} possible website mention",
            url=f"mock://search/{company_name.lower().replace(' ', '-')}/website",
            snippet=(
                f"Mock source for local MVP testing. Possible website or business listing "
                f"mention for {company_name}. Manual verification is required."
            ),
            source_type=SourceType.search_result,
            retrieved_at=now,
            confidence=ConfidenceLevel.low,
            is_mock=True,
        ),
        SourceResult(
            title=f"Mock search result: {company_name} reviews and public mentions",
            url=f"mock://search/{company_name.lower().replace(' ', '-')}/reviews",
            snippet=(
                f"Mock source for local MVP testing. Public mentions and reviews should be "
                f"checked manually for {company_name}."
            ),
            source_type=SourceType.search_result,
            retrieved_at=now,
            confidence=ConfidenceLevel.low,
            is_mock=True,
        ),
    ]

    return mock_results[:max_results]


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