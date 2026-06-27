from datetime import datetime, timezone

from app.schemas.company_check import DomainDnsInfo, DomainDnsStatus
from app.schemas.risk import BusinessRiskLevel, RiskScoreInput
from app.schemas.source import ConfidenceLevel, RelevanceLevel, SourceResult, SourceType
from app.schemas.website_candidate import WebsiteCandidate
from app.schemas.website_ownership_signals import OwnershipSignalsStatus
from app.tools.risk_score import calculate_risk_score
from app.tools.website_ownership_signals import collect_ownership_signals


def _candidate_source(
    *,
    url: str = "https://servochron.com",
    snippet: str = "Official homepage for Servochron in Austria.",
) -> SourceResult:
    now = datetime.now(timezone.utc)
    return SourceResult(
        title="SERVOCHRON GmbH official website",
        url=url,
        snippet=snippet,
        source_type=SourceType.search_result,
        retrieved_at=now,
        confidence=ConfidenceLevel.medium,
        is_mock=False,
        relevance=RelevanceLevel.relevant,
        relevance_score=0.8,
    )


def _website_candidate() -> WebsiteCandidate:
    return WebsiteCandidate(
        candidate_url="https://servochron.com",
        candidate_domain="servochron.com",
        score=0.8,
        confidence=ConfidenceLevel.medium,
        reasons=["domain_contains_company_name"],
        source_title="SERVOCHRON GmbH official website",
        is_verified=False,
    )


def _candidate_dns(*, has_mx: bool = True) -> DomainDnsInfo:
    return DomainDnsInfo(
        status=DomainDnsStatus.checked,
        domain="servochron.com",
        has_a_record=True,
        has_mx_record=has_mx,
        https_available=True,
    )


def test_no_website_candidate_returns_not_checked_status():
    result = collect_ownership_signals(
        company_name="Servochron",
        country="Austria",
        website_candidate=None,
        candidate_domain_dns=None,
        relevant_sources=[_candidate_source()],
    )

    assert result.status == OwnershipSignalsStatus.not_checked
    assert result.score == 0.0
    assert result.is_officially_confirmed is False
    assert result.signals == []


def test_strong_candidate_dns_and_company_name_returns_signals_found():
    result = collect_ownership_signals(
        company_name="Servochron",
        country="Austria",
        website_candidate=_website_candidate(),
        candidate_domain_dns=_candidate_dns(),
        relevant_sources=[
            _candidate_source(
                snippet=(
                    "Official homepage for Servochron in Austria. "
                    "Contact info@servochron.com for support. Privacy policy and imprint."
                )
            )
        ],
    )

    assert result.status == OwnershipSignalsStatus.signals_found
    assert result.score >= 0.5
    assert result.confidence in {ConfidenceLevel.medium, ConfidenceLevel.high}
    assert result.is_officially_confirmed is False
    assert any(signal.name == "same_domain_email_in_snippet" and signal.found for signal in result.signals)
    assert any(
        signal.name == "contact_or_legal_terms_in_snippet" and signal.found
        for signal in result.signals
    )


def test_same_domain_email_in_snippet_is_detected():
    result = collect_ownership_signals(
        company_name="Servochron",
        country="Austria",
        website_candidate=_website_candidate(),
        candidate_domain_dns=None,
        relevant_sources=[_candidate_source(snippet="Reach us at sales@servochron.com")],
    )

    email_signal = next(
        signal for signal in result.signals if signal.name == "same_domain_email_in_snippet"
    )
    assert email_signal.found is True


def test_contact_and_legal_terms_are_detected():
    result = collect_ownership_signals(
        company_name="Servochron",
        country="Austria",
        website_candidate=_website_candidate(),
        candidate_domain_dns=None,
        relevant_sources=[_candidate_source(snippet="See our imprint and privacy policy pages.")],
    )

    terms_signal = next(
        signal for signal in result.signals if signal.name == "contact_or_legal_terms_in_snippet"
    )
    assert terms_signal.found is True


def test_weak_candidate_without_dns_success_returns_insufficient_signals():
    result = collect_ownership_signals(
        company_name="Servochron",
        country="Austria",
        website_candidate=_website_candidate(),
        candidate_domain_dns=DomainDnsInfo(status=DomainDnsStatus.checked, domain="servochron.com"),
        relevant_sources=[_candidate_source(snippet="Unrelated business directory page.")],
    )

    assert result.status == OwnershipSignalsStatus.insufficient_signals
    assert 0 < result.score < 0.5
    assert result.is_officially_confirmed is False


def test_is_officially_confirmed_always_false():
    result = collect_ownership_signals(
        company_name="Servochron",
        country="Austria",
        website_candidate=_website_candidate(),
        candidate_domain_dns=_candidate_dns(),
        relevant_sources=[_candidate_source()],
    )

    assert result.is_officially_confirmed is False


def test_risk_factor_appears_when_ownership_signals_found():
    result = calculate_risk_score(
        RiskScoreInput(
            has_ownership_signals=True,
            ownership_signals_score=0.75,
            registry_found=False,
            registry_is_mock=True,
            source_count=1,
            all_sources_mock=False,
            verified_non_mock_source_count=1,
            verified_strong_source_count=1,
        )
    )

    assert "website_ownership_signals_found_pending_verification" in [
        factor.name for factor in result.factors
    ]


def test_ownership_signals_do_not_reduce_business_risk_to_low():
    result = calculate_risk_score(
        RiskScoreInput(
            has_ownership_signals=True,
            ownership_signals_score=0.9,
            registry_found=False,
            registry_is_mock=True,
            source_count=1,
            all_sources_mock=False,
            verified_non_mock_source_count=1,
            verified_strong_source_count=1,
        )
    )

    assert result.business_risk == BusinessRiskLevel.unknown
    assert result.requires_human_review is True
