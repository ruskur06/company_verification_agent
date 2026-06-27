from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from app.agents.company_check_agent import CompanyCheckAgent
from app.agents.name_normalizer_agent import NameNormalizer
from app.agents.risk_agent import RiskAgent
from app.schemas.company_check import DomainDnsInfo, DomainDnsStatus
from app.schemas.name_normalizer import NameNormalizerInput
from app.schemas.registry import RegistryCheckResult, RegistryCheckStatus
from app.schemas.risk import HumanReviewStatus
from app.schemas.source import ConfidenceLevel, RelevanceLevel, SourceResult, SourceType


def _build_agent(*, web_search_agent: MagicMock) -> CompanyCheckAgent:
    registry_agent = MagicMock()
    registry_agent.run.return_value = RegistryCheckResult(
        company_name="Servochron",
        country="Austria",
        status=RegistryCheckStatus.not_found,
        registry_found=False,
        confidence=ConfidenceLevel.low,
        is_mock=True,
    )

    domain_agent = MagicMock()
    domain_agent.run.return_value = DomainDnsInfo(status=DomainDnsStatus.not_provided)

    report_agent = MagicMock()
    report_agent.save.return_value = (Path("mock.json"), Path("mock.md"))

    human_review_agent = MagicMock()
    human_review_agent.run.return_value = HumanReviewStatus.pending

    name_normalizer = MagicMock(spec=NameNormalizer)
    name_normalizer.run.return_value = NameNormalizer().run(
        NameNormalizerInput(
            company_name="Servochron",
            country="Austria",
            domain=None,
        )
    )

    return CompanyCheckAgent(
        name_normalizer=name_normalizer,
        web_search_agent=web_search_agent,
        domain_agent=domain_agent,
        registry_agent=registry_agent,
        risk_agent=RiskAgent(),
        report_agent=report_agent,
        human_review_agent=human_review_agent,
    )


def _real_source(
    *,
    title: str,
    url: str | None = None,
    snippet: str | None = None,
    confidence: ConfidenceLevel = ConfidenceLevel.medium,
) -> SourceResult:
    now = datetime.now(timezone.utc)
    return SourceResult(
        title=title,
        url=url or f"https://example.com/{title.lower().replace(' ', '-')}",
        snippet=snippet or f"Real web search snippet for {title}.",
        source_type=SourceType.search_result,
        retrieved_at=now,
        confidence=confidence,
        is_mock=False,
    )


def _mock_source(title: str = "Mock search result") -> SourceResult:
    now = datetime.now(timezone.utc)
    return SourceResult(
        title=title,
        url="mock://search/servochron/profile",
        snippet="Mock source for local MVP testing.",
        source_type=SourceType.search_result,
        retrieved_at=now,
        confidence=ConfidenceLevel.low,
        is_mock=True,
    )


def test_company_check_with_real_web_sources_does_not_use_mock_risk_factor():
    web_search_agent = MagicMock()
    web_search_agent.run.return_value = [
        _real_source(title="SERVOCHRON GmbH profile"),
        _real_source(title="SERVOCHRON business listing"),
    ]

    response = _build_agent(web_search_agent=web_search_agent).run(
        company_name="Servochron",
        country="Austria",
    )

    assert response.json_result is not None
    assert (
        response.json_result.summary.confidence.value
        == response.json_result.risk.verification_confidence.value
    )
    factor_names = [factor.name for factor in response.json_result.risk.factors]
    assert "mock_source_coverage_only" not in factor_names
    assert any(name in factor_names for name in {"verified_relevant_source_found", "reasonable_source_coverage"})

    sources = response.json_result.sources
    assert len(sources) == 2
    assert all(source.relevance == RelevanceLevel.relevant for source in sources)
    assert all(not source.is_mock for source in sources)


def test_company_check_with_real_web_sources_uses_non_mock_summary_and_unknowns():
    web_search_agent = MagicMock()
    web_search_agent.run.return_value = [_real_source(title="SERVOCHRON GmbH profile")]

    response = _build_agent(web_search_agent=web_search_agent).run(
        company_name="Servochron",
        country="Austria",
    )

    assert response.json_result is not None
    assessment = response.json_result.summary.overall_assessment.lower()
    assert "real web search sources" in assessment
    assert "mock web search output" not in assessment

    unknowns_text = " ".join(response.json_result.unknowns).lower()
    assert "mock results" not in unknowns_text
    assert "real web search sources were found" in unknowns_text


def test_company_check_with_mock_web_sources_keeps_mock_wording():
    web_search_agent = MagicMock()
    web_search_agent.run.return_value = [_mock_source(), _mock_source(title="Mock search result 2")]

    response = _build_agent(web_search_agent=web_search_agent).run(
        company_name="Servochron",
        country="Austria",
    )

    assert response.json_result is not None
    factor_names = [factor.name for factor in response.json_result.risk.factors]
    assert "mock_source_coverage_only" in factor_names

    assessment = response.json_result.summary.overall_assessment.lower()
    assert "mock web search output" in assessment

    unknowns_text = " ".join(response.json_result.unknowns).lower()
    assert "mock results" in unknowns_text


def test_company_check_irrelevant_real_source_stays_in_output_but_not_coverage():
    web_search_agent = MagicMock()
    web_search_agent.run.return_value = [
        _real_source(title="SERVOCHRON GmbH profile"),
        _real_source(title="Avron GmbH unrelated listing"),
    ]

    response = _build_agent(web_search_agent=web_search_agent).run(
        company_name="Servochron",
        country="Austria",
    )

    assert response.json_result is not None
    assert len(response.json_result.sources) == 2

    irrelevant = next(
        source for source in response.json_result.sources if "Avron" in source.title
    )
    assert irrelevant.is_mock is False
    assert irrelevant.relevance == RelevanceLevel.irrelevant

    factor_names = [factor.name for factor in response.json_result.risk.factors]
    assert "reasonable_source_coverage" not in factor_names


def test_company_check_relevant_servochron_url_detects_website_candidate():
    web_search_agent = MagicMock()
    web_search_agent.run.return_value = [
        _real_source(
            title="SERVOCHRON GmbH official website",
            url="https://servochron.com",
            snippet=(
                "Official homepage for Servochron in Austria. "
                "Contact info@servochron.com. Privacy policy and imprint."
            ),
        ),
    ]

    domain_agent = MagicMock()
    domain_agent.run.side_effect = [
        DomainDnsInfo(status=DomainDnsStatus.not_provided),
        DomainDnsInfo(
            status=DomainDnsStatus.checked,
            domain="servochron.com",
            has_a_record=True,
            has_mx_record=True,
            https_available=True,
        ),
    ]

    agent = _build_agent(web_search_agent=web_search_agent)
    agent.domain_agent = domain_agent

    response = agent.run(
        company_name="Servochron",
        country="Austria",
        domain=None,
    )

    assert response.json_result is not None
    candidate = response.json_result.website_candidate
    assert candidate is not None
    assert candidate.candidate_domain == "servochron.com"
    assert candidate.is_verified is False
    assert response.json_result.candidate_domain_dns is not None
    assert response.json_result.candidate_domain_dns.domain == "servochron.com"

    ownership = response.json_result.website_ownership_signals
    assert ownership is not None
    assert ownership.status.value == "signals_found"
    assert ownership.is_officially_confirmed is False

    factor_names = [factor.name for factor in response.json_result.risk.factors]
    assert "website_candidate_found_pending_verification" in factor_names
    assert "website_ownership_signals_found_pending_verification" in factor_names
    assert "candidate_domain_resolves_pending_ownership_verification" in factor_names
    assert "official_website_not_found" not in factor_names
    assert "domain_does_not_resolve" not in factor_names
    assert "https_not_confirmed" not in factor_names
    assert "mx_record_missing" not in factor_names

    unknowns_text = " ".join(response.json_result.unknowns).lower()
    assert "registry" in unknowns_text
    assert response.json_result.risk.business_risk.value == "unknown"


def test_water_rockets_manual_does_not_trigger_multiple_source_coverage():
    web_search_agent = MagicMock()
    web_search_agent.run.return_value = [
        _real_source(
            title="SERVOCHRON GmbH official website",
            url="https://servochron.com",
            snippet=(
                "Official homepage for Servochron in Austria. "
                "Contact info@servochron.com. Privacy policy and imprint."
            ),
        ),
        _real_source(
            title="ServoChron Parachute deployment system - Construction and Programming User Manual",
            url="http://www.uswaterrockets.com/documents/ServoChron/manual.htm",
            snippet="Tutorial documentation for the ServoChron deployment system.",
        ),
    ]

    domain_agent = MagicMock()
    domain_agent.run.side_effect = [
        DomainDnsInfo(status=DomainDnsStatus.not_provided),
        DomainDnsInfo(
            status=DomainDnsStatus.checked,
            domain="servochron.com",
            has_a_record=True,
            has_mx_record=True,
            https_available=True,
        ),
    ]

    agent = _build_agent(web_search_agent=web_search_agent)
    agent.domain_agent = domain_agent

    response = agent.run(company_name="Servochron", country="Austria", domain=None)

    assert response.json_result is not None
    manual_source = next(
        source
        for source in response.json_result.sources
        if "uswaterrockets.com" in source.url
    )
    assert manual_source.relevance != RelevanceLevel.relevant

    servochron_source = next(
        source for source in response.json_result.sources if "servochron.com" in source.url
    )
    assert servochron_source.relevance == RelevanceLevel.relevant

    factor_names = [factor.name for factor in response.json_result.risk.factors]
    assert "multiple_sources_confirm" not in factor_names
    assert "reasonable_source_coverage" not in factor_names
    assert "verified_relevant_source_found" in factor_names

    ownership = response.json_result.website_ownership_signals
    assert ownership is not None
    assert ownership.status.value == "signals_found"

    checklist = response.json_result.manual_verification_checklist
    assert "Review real web sources and confirm relevance/ownership manually." in checklist
    assert "Replace mock web search with real source verification." not in checklist
