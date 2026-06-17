"""Deterministic risk score calculator."""

from __future__ import annotations

from app.schemas.risk import RiskFactor, RiskLevel, RiskScoreInput, RiskScoreResult


def _clamp_score(score: int) -> int:
    return max(0, min(100, score))


def _level_from_score(score: int) -> RiskLevel:
    if score <= 30:
        return RiskLevel.low
    if score <= 60:
        return RiskLevel.medium
    return RiskLevel.high


def calculate_risk_score(input_data: RiskScoreInput) -> RiskScoreResult:
    """Calculate preliminary risk score using deterministic rules."""
    score = 50
    factors: list[RiskFactor] = []

    def add_factor(name: str, impact: int, explanation: str) -> None:
        nonlocal score
        score += impact
        factors.append(RiskFactor(name=name, impact=impact, explanation=explanation))

    if input_data.has_website:
        add_factor("official_website_found", -15, "An official or likely official website was found.")
    else:
        add_factor("official_website_not_found", 15, "No official website was confirmed.")

    if input_data.domain_resolves:
        add_factor("domain_resolves", -10, "The provided domain resolves successfully.")
    else:
        add_factor("domain_does_not_resolve", 20, "The provided domain does not resolve or was not confirmed.")

    if input_data.has_mx_record:
        add_factor("mx_record_found", -5, "MX records were found for the domain.")
    else:
        add_factor("mx_record_missing", 5, "No MX records were confirmed for the domain.")

    if input_data.https_available:
        add_factor("https_available", -5, "HTTPS appears to be available.")
    else:
        add_factor("https_not_confirmed", 5, "HTTPS availability was not confirmed.")

    if input_data.registry_found:
        add_factor("registry_found", -20, "A company registry or equivalent official source was found.")
    else:
        add_factor("registry_not_found", 20, "No official company registry source was confirmed.")

    if input_data.multiple_sources_confirm:
        add_factor("multiple_sources_confirm", -15, "Multiple independent sources confirm the company identity.")
    else:
        add_factor("weak_source_confirmation", 10, "Source coverage is weak or not independently confirmed.")

    if input_data.source_count <= 0:
        add_factor("no_source_coverage", 15, "No source coverage was found.")
    elif input_data.source_count < 3:
        add_factor("limited_source_coverage", 10, "Only limited source coverage was found.")
    else:
        add_factor("reasonable_source_coverage", -10, "Several sources were found.")

    if input_data.negative_snippets_count > 0:
        impact = min(30, input_data.negative_snippets_count * 10)
        add_factor("negative_search_signals", impact, f"{input_data.negative_snippets_count} negative signal(s) were found.")

    if input_data.suspicious_keywords_found:
        impact = min(30, len(input_data.suspicious_keywords_found) * 10)
        add_factor(
            "suspicious_keywords_found",
            impact,
            "Suspicious keywords were found: " + ", ".join(input_data.suspicious_keywords_found),
        )

    final_score = _clamp_score(score)

    return RiskScoreResult(
        score=final_score,
        level=_level_from_score(final_score),
        factors=factors,
        requires_human_review=True,
    )