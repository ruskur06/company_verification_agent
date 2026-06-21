from pathlib import Path
from unittest.mock import MagicMock

from app.agents.company_check_agent import CompanyCheckAgent
from app.agents.name_normalizer_agent import NameNormalizer
from app.schemas.company_check import CheckStatus, DomainDnsInfo, DomainDnsStatus
from app.schemas.name_normalizer import NameNormalizerInput
from app.schemas.registry import RegistryCheckResult, RegistryCheckStatus
from app.schemas.risk import HumanReviewStatus, RiskLevel, RiskScoreResult
from app.schemas.source import ConfidenceLevel


def _build_agent(
    *,
    registry_agent: MagicMock,
    web_search_agent: MagicMock,
    name_normalizer: NameNormalizer | MagicMock | None = None,
) -> CompanyCheckAgent:
    domain_agent = MagicMock()
    domain_agent.run.return_value = DomainDnsInfo(status=DomainDnsStatus.not_provided)

    risk_agent = MagicMock()
    risk_agent.run.return_value = RiskScoreResult(
        score=45,
        level=RiskLevel.medium,
        factors=[],
        requires_human_review=True,
    )

    report_agent = MagicMock()
    report_agent.save.return_value = (Path("mock.json"), Path("mock.md"))

    human_review_agent = MagicMock()
    human_review_agent.run.return_value = HumanReviewStatus.pending

    return CompanyCheckAgent(
        name_normalizer=name_normalizer or NameNormalizer(),
        web_search_agent=web_search_agent,
        domain_agent=domain_agent,
        registry_agent=registry_agent,
        risk_agent=risk_agent,
        report_agent=report_agent,
        human_review_agent=human_review_agent,
    )


def test_company_check_calls_name_normalizer_and_uses_normalized_names():
    registry_agent = MagicMock()
    registry_agent.run.return_value = RegistryCheckResult(
        company_name="Servochron",
        country="USA",
        status=RegistryCheckStatus.found,
        registry_found=True,
        confidence=ConfidenceLevel.medium,
        is_mock=True,
    )

    web_search_agent = MagicMock()
    web_search_agent.run.return_value = []

    name_normalizer = MagicMock(spec=NameNormalizer)
    name_normalizer.run.return_value = NameNormalizer().run(
        NameNormalizerInput(
            company_name="Servochron LLC",
            country="USA",
            domain="https://www.servochron.com",
        )
    )

    agent = _build_agent(
        registry_agent=registry_agent,
        web_search_agent=web_search_agent,
        name_normalizer=name_normalizer,
    )

    response = agent.run(
        company_name="Servochron LLC",
        country="USA",
        domain="https://www.servochron.com",
    )

    name_normalizer.run.assert_called_once()
    expected_search_names = name_normalizer.run.return_value.search_names
    registry_agent.run.assert_called_once_with(
        search_names=expected_search_names,
        country="USA",
    )
    web_search_agent.run.assert_called_once_with(
        company_name="Servochron",
        country="USA",
    )

    assert response.status == CheckStatus.completed
    assert response.json_result is not None
    assert response.json_result.company.name == "Servochron LLC"
    assert response.json_result.name_normalization is not None
    assert response.json_result.name_normalization.normalized_name == "Servochron"
    assert "Servochron Inc" in response.json_result.name_normalization.search_names
    assert response.json_result.registry_check.registry_found is True
