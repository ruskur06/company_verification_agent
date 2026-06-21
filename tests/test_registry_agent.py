from unittest.mock import patch

from app.agents.registry_agent import RegistryAgent
from app.schemas.registry import RegistryCheckResult, RegistryCheckStatus
from app.schemas.source import ConfidenceLevel


def test_registry_agent_returns_registry_result():
    agent = RegistryAgent()
    result = agent.run(country="USA", search_names=["Servochron"])

    assert result.company_name == "Servochron"
    assert result.country == "USA"
    assert result.status == RegistryCheckStatus.found
    assert result.registry_found is True
    assert result.matched_name == "Servochron"
    assert result.searched_names == ["Servochron"]


def test_registry_agent_finds_match_using_later_search_name():
    agent = RegistryAgent()
    result = agent.run(
        country="USA",
        search_names=["Nonexistent Demo Corp", "Servochron"],
    )

    assert result.registry_found is True
    assert result.matched_name == "Servochron"
    assert result.searched_names == ["Nonexistent Demo Corp", "Servochron"]


def test_registry_agent_deduplicates_search_names():
    agent = RegistryAgent()
    result = agent.run(
        country="USA",
        search_names=["Servochron", "servochron", " SERVOCHRON "],
    )

    assert result.searched_names == ["Servochron"]
    assert result.registry_found is True


def test_registry_agent_tries_multiple_candidates_until_match():
    agent = RegistryAgent()
    not_found = RegistryCheckResult(
        company_name="Unknown Corp",
        country="USA",
        status=RegistryCheckStatus.not_found,
        registry_found=False,
        is_mock=True,
    )
    found = RegistryCheckResult(
        company_name="Servochron",
        country="USA",
        status=RegistryCheckStatus.found,
        registry_found=True,
        registry_name="US public business registry search",
        confidence=ConfidenceLevel.medium,
        is_mock=True,
    )

    with patch(
        "app.agents.registry_agent.search_company_registry",
        side_effect=[not_found, found],
    ) as mock_search:
        result = agent.run(
            country="USA",
            search_names=["Unknown Corp", "Servochron"],
        )

    assert mock_search.call_count == 2
    assert mock_search.call_args_list[0].kwargs == {
        "company_name": "Unknown Corp",
        "country": "USA",
    }
    assert mock_search.call_args_list[1].kwargs == {
        "company_name": "Servochron",
        "country": "USA",
    }
    assert result.registry_found is True
    assert result.matched_name == "Servochron"
