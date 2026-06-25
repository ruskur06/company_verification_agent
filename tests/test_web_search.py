import pytest

from app.agents.web_search_agent import WebSearchAgent
from app.core.config import settings
from app.tools.web_search import web_search


@pytest.fixture(autouse=True)
def use_mock_web_search_provider():
    original_provider = settings.web_search_provider
    original_api_key = settings.web_search_api_key

    settings.web_search_provider = "mock"
    settings.web_search_api_key = ""

    yield

    settings.web_search_provider = original_provider
    settings.web_search_api_key = original_api_key


def test_web_search_uses_multiple_normalized_name_variants():
    search_names = [
        "Servochron",
        "Servochron GmbH",
        "Servochron AG",
        "Servochron Ltd",
    ]

    sources = web_search(search_names=search_names, country="Austria", max_results=5)

    assert len(sources) == 5
    assert all(source.is_mock for source in sources)

    titles = [source.title for source in sources]
    assert any("Servochron GmbH" in title for title in titles)
    assert any("Servochron AG" in title for title in titles)
    assert any("Servochron Ltd" in title for title in titles)


def test_web_search_deduplicates_search_names_case_insensitively():
    sources = web_search(
        search_names=["Servochron", "servochron", " SERVOCHRON "],
        country="Austria",
        max_results=3,
    )

    assert len(sources) == 3
    assert all("Servochron" in source.title for source in sources)
    assert all(source.is_mock for source in sources)


def test_web_search_agent_passes_search_names_to_tool():
    agent = WebSearchAgent()
    search_names = ["Servochron", "Servochron GmbH"]

    sources = agent.run(search_names=search_names, country="Austria", max_results=2)

    assert len(sources) == 2
    assert all(source.is_mock for source in sources)
    assert {source.title for source in sources} == {
        "Mock search result: Servochron company profile",
        "Mock search result: Servochron GmbH company profile",
    }
