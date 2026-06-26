from datetime import datetime, timezone

from app.schemas.source import ConfidenceLevel, RelevanceLevel, SourceResult, SourceType
from app.tools.risk_score import calculate_risk_score
from app.tools.website_candidate_matcher import (
    extract_domain,
    find_website_candidate,
    is_excluded_platform_domain,
    score_website_candidate,
)
from app.schemas.risk import RiskScoreInput


def _relevant_source(
    *,
    title: str,
    url: str,
    snippet: str = "",
    is_mock: bool = False,
    relevance: RelevanceLevel = RelevanceLevel.relevant,
) -> SourceResult:
    now = datetime.now(timezone.utc)
    return SourceResult(
        title=title,
        url=url,
        snippet=snippet,
        source_type=SourceType.search_result,
        retrieved_at=now,
        confidence=ConfidenceLevel.medium,
        is_mock=is_mock,
        relevance=relevance,
        relevance_score=1.0 if relevance == RelevanceLevel.relevant else 0.1,
    )


def test_servochron_relevant_source_becomes_website_candidate():
    source = _relevant_source(
        title="SERVOCHRON GmbH official website",
        url="https://servochron.com",
        snippet="Official company homepage for Servochron.",
    )

    candidate = find_website_candidate("Servochron", [source])

    assert candidate is not None
    assert candidate.candidate_domain == "servochron.com"
    assert candidate.candidate_url == "https://servochron.com"
    assert candidate.is_verified is False
    assert "domain_contains_company_name" in candidate.reasons


def test_irrelevant_sources_do_not_become_website_candidate():
    sources = [
        _relevant_source(
            title="Avron GmbH profile",
            url="https://avron.example/profile",
            relevance=RelevanceLevel.irrelevant,
        ),
        _relevant_source(
            title="DEWETRON company page",
            url="https://dewetron.example",
            relevance=RelevanceLevel.irrelevant,
        ),
    ]

    assert find_website_candidate("Servochron", sources) is None


def test_mock_source_never_becomes_website_candidate():
    source = _relevant_source(
        title="SERVOCHRON GmbH official website",
        url="https://servochron.com",
        is_mock=True,
    )

    assert find_website_candidate("Servochron", [source]) is None
    assert score_website_candidate("Servochron", source) is None


def test_linkedin_and_generic_platform_domains_are_excluded():
    source = _relevant_source(
        title="Servochron company page",
        url="https://www.linkedin.com/company/servochron",
        snippet="Servochron on LinkedIn.",
    )

    assert is_excluded_platform_domain("linkedin.com")
    assert find_website_candidate("Servochron", [source]) is None


def test_extract_domain_normalizes_host():
    assert extract_domain("https://www.servochron.com/about") == "servochron.com"
    assert extract_domain("mock://search/servochron/profile") is None


def test_website_candidate_factor_appears_when_candidate_exists():
    result = calculate_risk_score(
        RiskScoreInput(
            has_website=False,
            has_website_candidate=True,
            domain_resolves=False,
            registry_found=False,
            registry_is_mock=True,
            source_count=1,
            all_sources_mock=False,
            verified_non_mock_source_count=1,
            verified_strong_source_count=1,
        )
    )

    factor_names = [factor.name for factor in result.factors]
    assert "website_candidate_found_pending_verification" in factor_names
    assert "official_website_not_found" not in factor_names


def test_official_website_not_found_when_no_candidate_exists():
    result = calculate_risk_score(
        RiskScoreInput(
            has_website=False,
            has_website_candidate=False,
            domain_resolves=False,
            registry_found=False,
            registry_is_mock=True,
            source_count=1,
            all_sources_mock=True,
        )
    )

    factor_names = [factor.name for factor in result.factors]
    assert "official_website_not_found" in factor_names
    assert "website_candidate_found_pending_verification" not in factor_names


def test_confirmed_website_takes_precedence_over_candidate_flag():
    result = calculate_risk_score(
        RiskScoreInput(
            has_website=True,
            has_website_candidate=True,
            domain_resolves=True,
            https_available=True,
            registry_found=False,
            registry_is_mock=True,
            source_count=1,
            all_sources_mock=False,
            verified_non_mock_source_count=1,
            verified_strong_source_count=1,
        )
    )

    factor_names = [factor.name for factor in result.factors]
    assert "official_website_found" in factor_names
    assert "website_candidate_found_pending_verification" not in factor_names
