import json
from pathlib import Path

import pytest


TRANSLATIONS_DIR = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "web"
    / "translations"
)


def test_root_redirects_to_english_landing(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/en"


@pytest.mark.parametrize(
    ("language", "expected_text"),
    [
        ("en", "Verify a foreign company"),
        ("de", "Prüfen Sie ein ausländisches Unternehmen"),
        ("es", "Verifique una empresa extranjera"),
    ],
)
def test_supported_landing_pages_render(
    client,
    language,
    expected_text,
):
    response = client.get(f"/{language}")

    assert response.status_code == 200
    assert f'<html lang="{language}">' in response.text
    assert expected_text in response.text
    assert 'href="/check"' in response.text


@pytest.mark.parametrize("language", ["en", "de", "es"])
def test_landing_pages_include_all_language_links(client, language):
    response = client.get(f"/{language}")

    assert response.status_code == 200
    assert 'hreflang="en"' in response.text
    assert 'hreflang="de"' in response.text
    assert 'hreflang="es"' in response.text
    assert 'hreflang="x-default"' in response.text


def test_unknown_language_path_returns_not_found(client):
    response = client.get("/fr")

    assert response.status_code == 404


def test_translation_files_have_the_same_keys():
    translations = {}

    for language in ("en", "de", "es"):
        path = TRANSLATIONS_DIR / f"{language}.json"
        translations[language] = json.loads(
            path.read_text(encoding="utf-8")
        )

    expected_keys = set(translations["en"])

    assert set(translations["de"]) == expected_keys
    assert set(translations["es"]) == expected_keys


@pytest.mark.parametrize("language", ["en", "de", "es"])
def test_translation_values_are_non_empty_strings(language):
    path = TRANSLATIONS_DIR / f"{language}.json"
    translation = json.loads(path.read_text(encoding="utf-8"))

    assert translation

    for key, value in translation.items():
        assert isinstance(value, str), key
        assert value.strip(), key


@pytest.mark.parametrize("language", ["en", "de", "es"])
def test_internal_registry_statuses_are_not_exposed(client, language):
    response = client.get(f"/{language}")

    assert response.status_code == 200
    assert "candidates_found" not in response.text
    assert "no_candidates" not in response.text
    assert "configuration_error" not in response.text
