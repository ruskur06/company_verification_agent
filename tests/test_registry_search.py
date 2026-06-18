from app.schemas.registry import RegistryCheckStatus
from app.tools.registry_search import search_company_registry


def test_registry_search_known_usa_company():
    result = search_company_registry("Servochron", "USA")

    assert result.status == RegistryCheckStatus.found
    assert result.registry_found is True
    assert result.registry_name == "US public business registry search"
    assert result.confidence.value == "medium"
    assert result.is_mock is True


def test_registry_search_empty_company_name():
    result = search_company_registry("", "USA")

    assert result.status == RegistryCheckStatus.error
    assert result.registry_found is False


def test_registry_search_unsupported_country():
    result = search_company_registry("Servochron", "Germany")

    assert result.status == RegistryCheckStatus.not_supported
    assert result.registry_found is False
    assert any("not supported" in note.lower() for note in result.notes)
