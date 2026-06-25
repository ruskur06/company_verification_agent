import httpx
import pytest

from app.core.config import settings
from app.tools.tavily_search import (
    TAVILY_API_URL,
    TavilySearchError,
    build_tavily_query,
    tavily_search,
)
from app.tools.web_search import web_search


@pytest.fixture(autouse=True)
def reset_web_search_settings():
    original_provider = settings.web_search_provider
    original_api_key = settings.web_search_api_key
    original_max_results = settings.web_search_max_results
    original_timeout = settings.web_search_timeout_seconds

    yield

    settings.web_search_provider = original_provider
    settings.web_search_api_key = original_api_key
    settings.web_search_max_results = original_max_results
    settings.web_search_timeout_seconds = original_timeout


def test_default_provider_returns_mock_results():
    settings.web_search_provider = "mock"
    settings.web_search_api_key = ""

    results = web_search(search_names=["Servochron"], country="Austria", max_results=3)

    assert len(results) == 3
    assert all(result.is_mock for result in results)


def test_tavily_provider_without_api_key_returns_mock_results():
    settings.web_search_provider = "tavily"
    settings.web_search_api_key = ""

    results = web_search(search_names=["Servochron"], country="Austria", max_results=2)

    assert len(results) == 2
    assert all(result.is_mock for result in results)


def test_tavily_provider_with_mocked_httpx_returns_non_mock_results(monkeypatch):
    settings.web_search_provider = "tavily"
    settings.web_search_api_key = "test-tavily-key"

    def fake_post(url, headers=None, json=None, timeout=None):
        assert url == TAVILY_API_URL
        assert headers["Authorization"] == "Bearer test-tavily-key"
        assert "api_key" not in json
        assert json["query"] == build_tavily_query("Servochron", "Austria")
        assert json["max_results"] == 2
        assert json["search_depth"] == "basic"

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "results": [
                        {
                            "title": "SERVOCHRON GmbH profile",
                            "url": "https://example.com/servochron",
                            "content": "Official business profile for SERVOCHRON GmbH.",
                        }
                    ]
                }

        return FakeResponse()

    monkeypatch.setattr("app.tools.tavily_search.httpx.post", fake_post)

    results = web_search(search_names=["Servochron", "Servochron GmbH"], country="Austria", max_results=2)

    assert len(results) == 1
    assert results[0].is_mock is False
    assert results[0].title == "SERVOCHRON GmbH profile"
    assert results[0].url == "https://example.com/servochron"
    assert results[0].snippet == "Official business profile for SERVOCHRON GmbH."


def test_tavily_request_uses_authorization_bearer_header(monkeypatch):
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["headers"] = headers
        captured["json"] = json

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"results": []}

        return FakeResponse()

    monkeypatch.setattr("app.tools.tavily_search.httpx.post", fake_post)

    results = tavily_search("Servochron", "Austria", api_key="secret-key")

    assert captured["headers"]["Authorization"] == "Bearer secret-key"
    assert "api_key" not in captured["json"]
    assert results == []


def test_tavily_maps_title_url_and_content(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "results": [
                        {
                            "title": "Registry listing",
                            "url": "https://registry.example/servochron",
                            "content": "Company register number FN 548828a.",
                        }
                    ]
                }

        return FakeResponse()

    monkeypatch.setattr("app.tools.tavily_search.httpx.post", fake_post)

    results = tavily_search("Servochron", "Austria", api_key="secret-key", max_results=1)

    assert len(results) == 1
    assert results[0].title == "Registry listing"
    assert results[0].url == "https://registry.example/servochron"
    assert results[0].snippet == "Company register number FN 548828a."
    assert results[0].is_mock is False


def test_httpx_error_falls_back_to_mock(monkeypatch):
    settings.web_search_provider = "tavily"
    settings.web_search_api_key = "secret-key"

    def fake_post(url, headers=None, json=None, timeout=None):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr("app.tools.tavily_search.httpx.post", fake_post)

    results = web_search(search_names=["Servochron"], country="Austria", max_results=2)

    assert len(results) == 2
    assert all(result.is_mock for result in results)


def test_malformed_tavily_response_falls_back_to_mock(monkeypatch):
    settings.web_search_provider = "tavily"
    settings.web_search_api_key = "secret-key"

    def fake_post(url, headers=None, json=None, timeout=None):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"unexpected": "payload"}

        return FakeResponse()

    monkeypatch.setattr("app.tools.tavily_search.httpx.post", fake_post)

    results = web_search(search_names=["Servochron"], country="Austria", max_results=2)

    assert len(results) == 2
    assert all(result.is_mock for result in results)


def test_build_tavily_query_includes_company_and_country():
    query = build_tavily_query("Servochron", "Austria")

    assert "Servochron" in query
    assert "Austria" in query
    assert "company" in query


def test_tavily_search_raises_when_no_api_key():
    with pytest.raises(TavilySearchError, match="API key"):
        tavily_search("Servochron", "Austria", api_key=None)
