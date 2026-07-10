import pytest

from app.schemas.registry import RegistryCheckStatus
from app.tools.registry_search import search_company_registry

AUSTRIA_ALIASES = ("Austria", "AT", "AUT", "Österreich", "Oesterreich")


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


@pytest.mark.parametrize("country", ["", "   ", "\t"])
def test_registry_search_empty_country_preserves_note(country: str):
    result = search_company_registry("Servochron", country)

    assert result.status == RegistryCheckStatus.not_supported
    assert result.registry_found is False
    assert result.is_mock is True
    assert result.notes == ["Country was not provided."]


def test_registry_search_unsupported_country():
    result = search_company_registry("Servochron", "Germany")

    assert result.status == RegistryCheckStatus.not_supported
    assert result.registry_found is False
    assert any("not supported" in note.lower() for note in result.notes)


def test_registry_search_israel_remains_not_supported():
    result = search_company_registry("Example Ltd", "Israel")

    assert result.status == RegistryCheckStatus.not_supported
    assert result.registry_found is False
    assert result.is_mock is True
    assert result.notes == ["Israeli registry integration will be added later."]


@pytest.mark.parametrize("country", AUSTRIA_ALIASES)
def test_registry_search_austria_aliases_are_recognized_but_not_supported(
    country: str,
):
    result = search_company_registry("Munchy Gastro GmbH", country)

    assert result.status == RegistryCheckStatus.not_supported
    assert result.registry_found is False
    assert result.is_mock is True
    assert result.status != RegistryCheckStatus.found
    assert any("source profile is configured" in note.lower() for note in result.notes)
    assert any("live registry integration is not implemented" in note.lower() for note in result.notes)


def test_registry_search_austria_note_is_honest_about_missing_integration():
    result = search_company_registry("Example GmbH", "Austria")

    assert result.notes == [
        "Austrian source profile is configured, but live registry integration is not implemented yet."
    ]


def test_registry_search_no_austrian_result_is_marked_found():
    for country in AUSTRIA_ALIASES:
        result = search_company_registry("Example GmbH", country)

        assert result.status != RegistryCheckStatus.found
        assert result.registry_found is False
