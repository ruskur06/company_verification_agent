"""Deterministic offline entity relevance matching for sources."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.source import ConfidenceLevel, RelevanceLevel, SourceResult

RELEVANT_THRESHOLD = 0.5
UNCERTAIN_THRESHOLD = 0.15

LEGAL_SUFFIXES = (
    "gmbh",
    "ag",
    "llc",
    "ltd",
    "limited",
    "inc",
    "corp",
    "corporation",
    "plc",
    "sa",
    "bv",
    "kg",
    "co",
    "company",
)


@dataclass(frozen=True)
class RelevanceResult:
    level: RelevanceLevel
    score: float
    reasons: list[str]


def normalize_text(value: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", value.casefold())
    return re.sub(r"\s+", " ", cleaned).strip()


def strip_legal_suffixes(company_name: str) -> str:
    tokens = normalize_text(company_name).split()
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def company_tokens(company_name: str) -> list[str]:
    core_name = strip_legal_suffixes(company_name)
    return [token for token in core_name.split() if len(token) >= 2]


def _level_from_score(score: float) -> RelevanceLevel:
    if score >= RELEVANT_THRESHOLD:
        return RelevanceLevel.relevant
    if score >= UNCERTAIN_THRESHOLD:
        return RelevanceLevel.uncertain
    return RelevanceLevel.irrelevant


def score_relevance(company_name: str, country: str, source: SourceResult) -> RelevanceResult:
    """Score whether a source likely refers to the requested company."""
    if source.is_mock:
        return RelevanceResult(
            level=RelevanceLevel.relevant,
            score=1.0,
            reasons=["mock_source"],
        )

    tokens = company_tokens(company_name)
    if not tokens:
        return RelevanceResult(
            level=RelevanceLevel.uncertain,
            score=0.0,
            reasons=["empty_company_name"],
        )

    title = normalize_text(source.title)
    url = normalize_text(source.url)
    snippet = normalize_text(source.snippet)
    country_norm = normalize_text(country)
    core_name = " ".join(tokens)

    score = 0.0
    reasons: list[str] = []
    combined = " ".join(part for part in (title, url, snippet) if part)

    if core_name and core_name in title:
        score += 0.45
        reasons.append("company_name_in_title")
    elif all(token in title for token in tokens):
        score += 0.4
        reasons.append("company_tokens_in_title")

    if core_name and core_name.replace(" ", "") in url.replace(" ", ""):
        score += 0.35
        reasons.append("company_name_in_url")
    elif any(token in url for token in tokens):
        score += 0.3
        reasons.append("company_tokens_in_url")

    if core_name and core_name in snippet:
        score += 0.25
        reasons.append("company_name_in_snippet")
    elif any(token in snippet for token in tokens):
        score += 0.2
        reasons.append("company_tokens_in_snippet")

    if country_norm and country_norm in combined:
        score += 0.1
        reasons.append("country_mentioned")

    if not any(token in combined for token in tokens):
        score = min(score, 0.1)
        reasons.append("no_company_name_overlap")

    score = max(0.0, min(score, 1.0))
    return RelevanceResult(level=_level_from_score(score), score=score, reasons=reasons)


def annotate_relevance(
    company_name: str,
    country: str,
    sources: list[SourceResult],
) -> list[SourceResult]:
    """Return new SourceResult objects annotated with relevance metadata."""
    annotated: list[SourceResult] = []

    for source in sources:
        result = score_relevance(company_name, country, source)
        annotated.append(
            source.model_copy(
                update={
                    "relevance": result.level,
                    "relevance_score": result.score,
                    "relevance_reasons": result.reasons,
                }
            )
        )

    return annotated


def is_verified_coverage_source(source: SourceResult) -> bool:
    """Return whether a source counts toward verified coverage."""
    return not source.is_mock and source.relevance == RelevanceLevel.relevant


def verified_coverage_sources(sources: list[SourceResult]) -> list[SourceResult]:
    return [source for source in sources if is_verified_coverage_source(source)]


def source_coverage_flags(sources: list[SourceResult]) -> dict[str, int | bool]:
    verified_sources = verified_coverage_sources(sources)
    verified_strong_sources = [
        source
        for source in verified_sources
        if source.confidence in {ConfidenceLevel.medium, ConfidenceLevel.high}
    ]

    return {
        "all_sources_mock": len(sources) == 0 or len(verified_sources) == 0,
        "verified_non_mock_source_count": len(verified_sources),
        "verified_strong_source_count": len(verified_strong_sources),
        "has_high_confidence_verified_source": any(
            source.confidence == ConfidenceLevel.high for source in verified_sources
        ),
        "multiple_sources_confirm": len(verified_sources) >= 2,
    }
