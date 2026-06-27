"""Deterministic website ownership signal collection from existing check data."""

from __future__ import annotations

import re

from app.schemas.company_check import DomainDnsInfo
from app.schemas.source import ConfidenceLevel, RelevanceLevel, SourceResult
from app.schemas.website_candidate import WebsiteCandidate
from app.schemas.website_ownership_signals import (
    OwnershipSignal,
    OwnershipSignalsStatus,
    WebsiteOwnershipSignals,
)
from app.tools.entity_matcher import company_tokens, normalize_text
from app.tools.website_candidate_matcher import extract_domain

SIGNALS_FOUND_THRESHOLD = 0.5
HIGH_CONFIDENCE_THRESHOLD = 0.7

CONTACT_LEGAL_TERMS = (
    "contact",
    "about us",
    "about",
    "legal",
    "imprint",
    "impressum",
    "privacy",
    "terms",
)


def _confidence_from_score(score: float) -> ConfidenceLevel:
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        return ConfidenceLevel.high
    if score >= SIGNALS_FOUND_THRESHOLD:
        return ConfidenceLevel.medium
    return ConfidenceLevel.low


def _status_from_score(score: float) -> OwnershipSignalsStatus:
    if score >= SIGNALS_FOUND_THRESHOLD:
        return OwnershipSignalsStatus.signals_found
    return OwnershipSignalsStatus.insufficient_signals


def _candidate_sources(
    website_candidate: WebsiteCandidate,
    relevant_sources: list[SourceResult],
) -> list[SourceResult]:
    matched: list[SourceResult] = []
    candidate_domain = website_candidate.candidate_domain.casefold()

    for source in relevant_sources:
        source_domain = extract_domain(source.url)
        if source_domain == candidate_domain:
            matched.append(source)
            continue
        if source.title == website_candidate.source_title:
            matched.append(source)

    return matched


def _company_name_in_text(company_name: str, text: str) -> bool:
    tokens = company_tokens(company_name)
    if not tokens:
        return False

    normalized = normalize_text(text)
    core_name = " ".join(tokens)
    return core_name in normalized or all(token in normalized for token in tokens)


def _same_domain_email_in_text(domain: str, text: str) -> bool:
    pattern = rf"[\w.+-]+@{re.escape(domain.casefold())}"
    return re.search(pattern, text.casefold()) is not None


def _contact_or_legal_terms_in_text(text: str) -> bool:
    normalized = normalize_text(text)
    return any(term in normalized for term in CONTACT_LEGAL_TERMS)


def collect_ownership_signals(
    company_name: str,
    country: str,
    website_candidate: WebsiteCandidate | None,
    candidate_domain_dns: DomainDnsInfo | None,
    relevant_sources: list[SourceResult],
) -> WebsiteOwnershipSignals:
    """Collect ownership signals from existing website candidate and source data."""
    if website_candidate is None:
        return WebsiteOwnershipSignals(
            status=OwnershipSignalsStatus.not_checked,
            score=0.0,
            confidence=ConfidenceLevel.low,
            signals=[],
            warnings=[],
            is_officially_confirmed=False,
        )

    candidate_sources = _candidate_sources(website_candidate, relevant_sources)
    combined_text = " ".join(
        f"{source.title} {source.snippet}" for source in candidate_sources
    )
    if not combined_text.strip():
        combined_text = website_candidate.source_title

    tokens = company_tokens(company_name)
    domain_compact = website_candidate.candidate_domain.replace("-", "").replace(".", "")
    country_norm = normalize_text(country)

    signal_defs: list[tuple[str, bool, float, str]] = [
        (
            "company_name_in_candidate_source_title_or_snippet",
            _company_name_in_text(company_name, combined_text),
            0.15,
            "Company name appears in the candidate source title or snippet.",
        ),
        (
            "candidate_domain_contains_company_name_token",
            bool(tokens) and any(token in domain_compact for token in tokens),
            0.15,
            "Candidate domain contains a company name token.",
        ),
        (
            "candidate_domain_resolves",
            bool(candidate_domain_dns and candidate_domain_dns.has_a_record),
            0.15,
            "Candidate domain resolves to an A record.",
        ),
        (
            "candidate_https_available",
            bool(candidate_domain_dns and candidate_domain_dns.https_available),
            0.10,
            "HTTPS is available for the candidate domain.",
        ),
        (
            "candidate_mx_record_exists",
            bool(candidate_domain_dns and candidate_domain_dns.has_mx_record),
            0.10,
            "MX records exist for the candidate domain.",
        ),
        (
            "same_domain_email_in_snippet",
            _same_domain_email_in_text(website_candidate.candidate_domain, combined_text),
            0.15,
            "A same-domain email address appears in candidate source text.",
        ),
        (
            "contact_or_legal_terms_in_snippet",
            _contact_or_legal_terms_in_text(combined_text),
            0.10,
            "Contact, about, legal, imprint, or privacy terms appear in candidate source text.",
        ),
        (
            "country_mentioned_in_candidate_source",
            bool(country_norm) and country_norm in normalize_text(combined_text),
            0.10,
            "Requested country is mentioned in candidate source text.",
        ),
    ]

    signals = [
        OwnershipSignal(name=name, found=found, weight=weight, detail=detail)
        for name, found, weight, detail in signal_defs
    ]
    score = round(sum(signal.weight for signal in signals if signal.found), 3)

    warnings = [
        "Ownership signals do not confirm legal website ownership.",
        "Official website status still requires human verification.",
    ]

    return WebsiteOwnershipSignals(
        status=_status_from_score(score),
        score=score,
        confidence=_confidence_from_score(score),
        signals=signals,
        warnings=warnings,
        is_officially_confirmed=False,
    )


def relevant_sources_for_ownership(sources: list[SourceResult]) -> list[SourceResult]:
    return [
        source
        for source in sources
        if not source.is_mock and source.relevance == RelevanceLevel.relevant
    ]
