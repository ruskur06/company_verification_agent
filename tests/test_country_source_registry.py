import pytest
from pydantic import ValidationError

from app.schemas.country_source import CountrySourceKind
from app.tools.country_source_registry import resolve_country_source_profile

AUSTRIA_ALIASES = ("Austria", "AT", "AUT", "Österreich", "Oesterreich")
DECOMPOSED_OESTERREICH = "O\u0308sterreich"


@pytest.mark.parametrize("alias", AUSTRIA_ALIASES)
def test_austria_aliases_resolve_to_at(alias: str) -> None:
    profile = resolve_country_source_profile(alias)

    assert profile is not None
    assert profile.country_code == "AT"


def test_resolution_trims_whitespace() -> None:
    profile = resolve_country_source_profile("  Austria  ")

    assert profile is not None
    assert profile.country_code == "AT"


def test_resolution_is_case_insensitive() -> None:
    profile = resolve_country_source_profile("austria")

    assert profile is not None
    assert profile.country_code == "AT"


def test_unicode_alias_oesterreich_resolves() -> None:
    profile = resolve_country_source_profile("Österreich")

    assert profile is not None
    assert profile.country_code == "AT"


def test_unicode_normalization_handles_decomposed_oesterreich() -> None:
    profile = resolve_country_source_profile(DECOMPOSED_OESTERREICH)

    assert profile is not None
    assert profile.country_code == "AT"


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_empty_and_whitespace_only_input_return_none(value: str) -> None:
    assert resolve_country_source_profile(value) is None


@pytest.mark.parametrize("value", ["Germany", "France", "Unknown"])
def test_unknown_countries_return_none(value: str) -> None:
    assert resolve_country_source_profile(value) is None


def test_austria_profile_contains_expected_configuration() -> None:
    profile = resolve_country_source_profile("Austria")

    assert profile is not None
    assert profile.registry_adapter_key == "austria_firmenbuch"
    assert profile.source_definitions
    assert profile.legal_page_patterns
    assert profile.expected_identifiers


def test_austria_official_sources_are_marked_official() -> None:
    profile = resolve_country_source_profile("Austria")

    assert profile is not None
    official_sources = {
        source.source_id: source
        for source in profile.source_definitions
        if source.source_id in {"at_firmenbuch", "at_gisa", "eu_vat_vies"}
    }

    assert set(official_sources) == {"at_firmenbuch", "at_gisa", "eu_vat_vies"}
    assert all(source.is_official for source in official_sources.values())


def test_austria_website_impressum_is_not_official() -> None:
    profile = resolve_country_source_profile("Austria")

    assert profile is not None
    impressum = next(
        source
        for source in profile.source_definitions
        if source.source_id == "website_impressum"
    )

    assert impressum.source_kind == CountrySourceKind.website_legal_notice
    assert impressum.is_official is False


def test_every_austria_source_has_live_integration_disabled() -> None:
    profile = resolve_country_source_profile("Austria")

    assert profile is not None
    assert all(
        source.live_integration_available is False
        for source in profile.source_definitions
    )


def test_country_profile_and_nested_definitions_are_immutable() -> None:
    profile = resolve_country_source_profile("Austria")

    assert profile is not None

    with pytest.raises(ValidationError):
        profile.country_code = "DE"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        profile.source_definitions[0].source_id = "changed"  # type: ignore[index]


@pytest.mark.parametrize(
    ("alias", "expected_code"),
    [
        ("USA", "US"),
        ("United States", "US"),
        ("US", "US"),
        ("United States of America", "US"),
        ("Israel", "IL"),
        ("IL", "IL"),
    ],
)
def test_usa_and_israel_aliases_resolve(alias: str, expected_code: str) -> None:
    profile = resolve_country_source_profile(alias)

    assert profile is not None
    assert profile.country_code == expected_code


def test_oesterreich_ascii_alias_resolves() -> None:
    profile = resolve_country_source_profile("Oesterreich")

    assert profile is not None
    assert profile.country_code == "AT"
