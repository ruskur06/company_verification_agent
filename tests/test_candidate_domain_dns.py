from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from app.agents.company_check_agent import CompanyCheckAgent
from app.agents.name_normalizer_agent import NameNormalizer
from app.agents.risk_agent import RiskAgent
from app.schemas.company_check import DomainDnsInfo, DomainDnsStatus
from app.schemas.name_normalizer import NameNormalizerInput
from app.schemas.registry import RegistryCheckResult, RegistryCheckStatus
from app.schemas.risk import HumanReviewStatus, RiskScoreInput
from app.schemas.source import ConfidenceLevel, RelevanceLevel, SourceResult, SourceType
from app.tools.risk_input_helpers import (
    build_domain_risk_fields,
    candidate_domain_dns_succeeds,
)
from app.tools.risk_score import calculate_risk_score
from app.tools.website_candidate_matcher import find_website_candidate


def _checked_dns(
    domain: str,
    *,
    https: bool = True,
    has_a: bool = True,
    has_mx: bool = False,
) -> DomainDnsInfo:
    return DomainDnsInfo(
        status=DomainDnsStatus.checked,
        domain=domain,
        has_a_record=has_a,
        has_mx_record=has_mx,
        https_available=https,
    )


def _relevant_website_source() -> SourceResult:
    now = datetime.now(timezone.utc)
    return SourceResult(
        title="SERVOCHRON GmbH official website",
        url="https://servochron.com",
        snippet="Official company homepage for Servochron.",
        source_type=SourceType.search_result,
        retrieved_at=now,
        confidence=ConfidenceLevel.medium,
        is_mock=False,
        relevance=RelevanceLevel.relevant,
        relevance_score=0.8,
    )


def test_no_website_candidate_means_candidate_domain_dns_is_none():
    domain_agent = MagicMock()
    domain_agent.run.return_value = DomainDnsInfo(status=DomainDnsStatus.not_provided)

    agent = _build_test_agent(domain_agent=domain_agent, sources=[_relevant_website_source()])

    response = agent.run(company_name="Example Corp", country="Austria", domain=None)

    assert response.json_result is not None
    assert response.json_result.website_candidate is None
    assert response.json_result.candidate_domain_dns is None
    domain_agent.run.assert_called_once_with(None)


def test_website_candidate_triggers_domain_agent_for_candidate_domain():
    domain_agent = MagicMock()
    domain_agent.run.side_effect = [
        DomainDnsInfo(status=DomainDnsStatus.not_provided),
        _checked_dns("servochron.com"),
    ]

    agent = _build_test_agent(domain_agent=domain_agent, sources=[_relevant_website_source()])

    response = agent.run(company_name="Servochron", country="Austria", domain=None)

    assert response.json_result is not None
    assert domain_agent.run.call_args_list[0].args == (None,)
    assert domain_agent.run.call_args_list[1].args == ("servochron.com",)
    assert response.json_result.candidate_domain_dns is not None
    assert response.json_result.candidate_domain_dns.domain == "servochron.com"
    assert response.json_result.candidate_domain_dns.has_a_record is True
    assert response.json_result.candidate_domain_dns.https_available is True


def test_user_provided_domain_dns_is_not_overwritten_by_candidate_domain_dns():
    domain_agent = MagicMock()
    domain_agent.run.side_effect = [
        _checked_dns("bad-user-domain.com", https=False, has_a=False),
        _checked_dns("servochron.com"),
    ]

    agent = _build_test_agent(domain_agent=domain_agent, sources=[_relevant_website_source()])

    response = agent.run(
        company_name="Servochron",
        country="Austria",
        domain="bad-user-domain.com",
    )

    assert response.json_result is not None
    assert response.json_result.company.domain == "bad-user-domain.com"
    assert response.json_result.domain_dns.domain == "bad-user-domain.com"
    assert response.json_result.domain_dns.has_a_record is False
    assert response.json_result.candidate_domain_dns is not None
    assert response.json_result.candidate_domain_dns.domain == "servochron.com"


def test_successful_candidate_dns_adds_pending_ownership_factor():
    result = calculate_risk_score(
        RiskScoreInput(
            user_domain_provided=False,
            candidate_domain_dns_succeeds=True,
            candidate_has_mx_record=True,
            has_website_candidate=True,
            domain_resolves=False,
            https_available=False,
            registry_found=False,
            registry_is_mock=True,
            source_count=1,
            all_sources_mock=False,
            verified_non_mock_source_count=1,
            verified_strong_source_count=1,
        )
    )

    factor_names = [factor.name for factor in result.factors]
    assert "candidate_domain_resolves_pending_ownership_verification" in factor_names
    assert "domain_does_not_resolve" not in factor_names
    assert "https_not_confirmed" not in factor_names
    assert "mx_record_missing" not in factor_names


def test_no_user_domain_with_candidate_mx_suppresses_mx_record_missing():
    result = calculate_risk_score(
        RiskScoreInput(
            user_domain_provided=False,
            candidate_has_mx_record=True,
            has_mx_record=False,
            registry_found=False,
            registry_is_mock=True,
            source_count=1,
            all_sources_mock=True,
        )
    )

    assert "mx_record_missing" not in [factor.name for factor in result.factors]


def test_user_provided_domain_without_mx_keeps_mx_record_missing():
    result = calculate_risk_score(
        RiskScoreInput(
            user_domain_provided=True,
            has_mx_record=False,
            candidate_has_mx_record=True,
            domain_resolves=True,
            registry_found=False,
            registry_is_mock=True,
            source_count=1,
            all_sources_mock=True,
        )
    )

    assert "mx_record_missing" in [factor.name for factor in result.factors]


def test_failed_user_domain_is_not_hidden_by_candidate_dns_success():
    result = calculate_risk_score(
        RiskScoreInput(
            user_domain_provided=True,
            candidate_domain_dns_succeeds=True,
            has_website=False,
            domain_resolves=False,
            https_available=False,
            registry_found=False,
            registry_is_mock=True,
            source_count=1,
            all_sources_mock=False,
            verified_non_mock_source_count=1,
            verified_strong_source_count=1,
        )
    )

    factor_names = [factor.name for factor in result.factors]
    assert "domain_does_not_resolve" in factor_names
    assert "https_not_confirmed" in factor_names
    assert "mx_record_missing" in factor_names
    assert "candidate_domain_resolves_pending_ownership_verification" not in factor_names


def test_candidate_dns_success_does_not_set_website_candidate_verified():
    domain_agent = MagicMock()
    domain_agent.run.side_effect = [
        DomainDnsInfo(status=DomainDnsStatus.not_provided),
        _checked_dns("servochron.com"),
    ]

    agent = _build_test_agent(domain_agent=domain_agent, sources=[_relevant_website_source()])
    response = agent.run(company_name="Servochron", country="Austria", domain=None)

    assert response.json_result is not None
    assert response.json_result.website_candidate is not None
    assert response.json_result.website_candidate.is_verified is False


def test_candidate_domain_dns_succeeds_helper():
    assert candidate_domain_dns_succeeds(None) is False
    assert candidate_domain_dns_succeeds(_checked_dns("servochron.com")) is True
    assert candidate_domain_dns_succeeds(_checked_dns("servochron.com", https=False)) is False


def test_build_domain_risk_fields_separates_user_and_candidate_domains():
    fields = build_domain_risk_fields(
        user_domain=None,
        domain_dns=DomainDnsInfo(status=DomainDnsStatus.not_provided),
        candidate_domain_dns=_checked_dns("servochron.com"),
        website_candidate=find_website_candidate("Servochron", [_relevant_website_source()]),
    )

    assert fields["user_domain_provided"] is False
    assert fields["candidate_domain_dns_succeeds"] is True
    assert fields["has_website"] is False
    assert fields["has_website_candidate"] is True


def _build_test_agent(*, domain_agent: MagicMock, sources: list[SourceResult]) -> CompanyCheckAgent:
    web_search_agent = MagicMock()
    web_search_agent.run.return_value = sources

    registry_agent = MagicMock()
    registry_agent.run.return_value = RegistryCheckResult(
        company_name="Servochron",
        country="Austria",
        status=RegistryCheckStatus.not_found,
        registry_found=False,
        confidence=ConfidenceLevel.low,
        is_mock=True,
    )

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
