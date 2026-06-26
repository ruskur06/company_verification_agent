"""Deterministic risk score calculator."""

from __future__ import annotations

from app.schemas.risk import (
    BusinessRiskLevel,
    RiskFactor,
    RiskLevel,
    RiskScoreInput,
    RiskScoreResult,
)


def _clamp_score(score: int) -> int:
    return max(0, min(100, score))


def _level_from_score(score: int) -> RiskLevel:
    if score <= 30:
        return RiskLevel.low
    if score <= 60:
        return RiskLevel.medium
    return RiskLevel.high


def _registry_confirmed(input_data: RiskScoreInput) -> bool:
    return input_data.registry_found and not input_data.registry_is_mock


def _has_verified_sources(input_data: RiskScoreInput) -> bool:
    return _registry_confirmed(input_data) or input_data.verified_non_mock_source_count > 0


def _verification_confidence(input_data: RiskScoreInput) -> RiskLevel:
    verified = input_data.verified_non_mock_source_count
    strong = input_data.verified_strong_source_count

    if verified == 0 and not _registry_confirmed(input_data):
        return RiskLevel.low

    if strong >= 2:
        return RiskLevel.high

    if strong >= 1 or _registry_confirmed(input_data):
        return RiskLevel.medium

    return RiskLevel.low


def _business_risk(input_data: RiskScoreInput) -> BusinessRiskLevel:
    if not _has_verified_sources(input_data):
        return BusinessRiskLevel.unknown

    if input_data.verified_non_mock_source_count == 0 and not _registry_confirmed(input_data):
        return BusinessRiskLevel.unknown

    verified_negative_count = (
        0 if input_data.verified_non_mock_source_count == 0 else input_data.negative_snippets_count
    )
    verified_keywords = (
        [] if input_data.verified_non_mock_source_count == 0 else input_data.suspicious_keywords_found
    )

    if verified_negative_count == 0 and not verified_keywords:
        return BusinessRiskLevel.unknown

    risk_points = verified_negative_count * 10 + len(verified_keywords) * 10

    if risk_points >= 30:
        return BusinessRiskLevel.high
    if risk_points >= 10:
        return BusinessRiskLevel.medium
    return BusinessRiskLevel.low


def calculate_risk_score(input_data: RiskScoreInput) -> RiskScoreResult:
    """Calculate preliminary verification scoring and separated risk dimensions."""
    score = 50
    factors: list[RiskFactor] = []

    def add_factor(name: str, impact: int, explanation: str) -> None:
        nonlocal score
        score += impact
        factors.append(RiskFactor(name=name, impact=impact, explanation=explanation))

    if input_data.has_website:
        add_factor(
            "official_website_found",
            -15,
            "An official or likely official website was found.",
        )
    elif input_data.has_website_candidate:
        add_factor(
            "website_candidate_found_pending_verification",
            -8,
            (
                "A candidate official website was found in relevant web search results, "
                "but ownership/official status still requires human verification."
            ),
        )
    else:
        add_factor(
            "official_website_not_found",
            15,
            "No official website was confirmed; this increases verification risk, not business risk.",
        )

    if input_data.domain_resolves:
        add_factor("domain_resolves", -10, "The provided domain resolves successfully.")
    else:
        add_factor(
            "domain_does_not_resolve",
            20,
            "The provided domain does not resolve or was not confirmed; this increases verification risk.",
        )

    if input_data.has_mx_record:
        add_factor("mx_record_found", -5, "MX records were found for the domain.")
    else:
        add_factor(
            "mx_record_missing",
            5,
            "No MX records were confirmed for the domain.",
        )

    if input_data.https_available:
        add_factor("https_available", -5, "HTTPS appears to be available.")
    else:
        add_factor("https_not_confirmed", 5, "HTTPS availability was not confirmed.")

    if _registry_confirmed(input_data):
        add_factor(
            "registry_confirmed",
            -20,
            "A verified company registry or equivalent official source was found.",
        )
    elif input_data.registry_found:
        add_factor(
            "registry_found_but_mock",
            10,
            "A registry match was found but is based on mock data and is not verified.",
        )
    else:
        add_factor(
            "registry_not_found",
            20,
            "No official company registry source was confirmed; this increases verification risk.",
        )

    if input_data.multiple_sources_confirm:
        add_factor(
            "multiple_sources_confirm",
            -15,
            "Multiple independent verified sources confirm the company identity.",
        )
    else:
        add_factor(
            "weak_source_confirmation",
            10,
            "Source coverage is weak or not independently confirmed.",
        )

    verified_count = input_data.verified_non_mock_source_count

    if verified_count <= 0:
        if input_data.source_count <= 0:
            add_factor(
                "no_source_coverage",
                15,
                "No source coverage was found; verification risk is elevated.",
            )
        else:
            add_factor(
                "mock_source_coverage_only",
                10,
                "Sources exist but are mock test data and do not provide verified evidence.",
            )
    elif verified_count == 1:
        if input_data.has_high_confidence_verified_source:
            add_factor(
                "verified_relevant_source_found",
                -40,
                "A high-confidence relevant non-mock source is available and improves verification confidence.",
            )
        else:
            add_factor(
                "verified_relevant_source_found",
                -15,
                "A relevant non-mock source is available and improves verification confidence.",
            )
    else:
        add_factor(
            "reasonable_source_coverage",
            -10,
            "Several verified non-mock sources were found.",
        )

    if input_data.verified_non_mock_source_count > 0:
        if input_data.negative_snippets_count > 0:
            add_factor(
                "verified_negative_search_signals",
                0,
                (
                    f"{input_data.negative_snippets_count} negative signal(s) were found in "
                    "verified sources and are reflected in business risk, not verification score."
                ),
            )

        if input_data.suspicious_keywords_found:
            add_factor(
                "verified_suspicious_keywords_found",
                0,
                "Suspicious keywords were found in verified sources: "
                + ", ".join(input_data.suspicious_keywords_found),
            )

    verification_score = _clamp_score(score)
    verification_risk = _level_from_score(verification_score)
    verification_confidence = _verification_confidence(input_data)
    business_risk = _business_risk(input_data)

    if (
        input_data.has_high_confidence_verified_source
        and input_data.verified_non_mock_source_count == 1
        and verification_risk == RiskLevel.high
    ):
        verification_score = min(verification_score, 60)
        verification_risk = RiskLevel.medium

    return RiskScoreResult(
        score=verification_score,
        level=verification_risk,
        verification_confidence=verification_confidence,
        verification_risk=verification_risk,
        business_risk=business_risk,
        factors=factors,
        requires_human_review=True,
    )
