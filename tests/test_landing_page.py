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
    assert (
        f'href="/{language}/request-check"'
        in response.text
    )


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


@pytest.mark.parametrize(
    "old_path",
    [
        "/check",
        "/checks",
        "/result/1782245998769",
    ],
)
def test_old_internal_ui_routes_are_not_available(
    client,
    old_path,
):
    response = client.get(
        old_path,
        follow_redirects=False,
    )

    assert response.status_code == 404


def test_robots_disallows_internal_routes(client):
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert "User-agent: *" in response.text
    assert "Disallow: /internal/" in response.text


@pytest.mark.parametrize(
    "template_name",
    [
        "index.html",
        "checks.html",
        "result.html",
    ],
)
def test_internal_templates_are_not_indexable(
    template_name,
):
    templates_dir = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "web"
        / "templates"
    )

    html = (
        templates_dir
        / template_name
    ).read_text(encoding="utf-8")

    assert (
        '<meta name="robots" '
        'content="noindex, nofollow">'
    ) in html


@pytest.mark.parametrize(
    "language",
    [
        "en",
        "de",
        "es",
    ],
)
def test_public_landing_does_not_link_to_internal_ui(
    client,
    language,
):
    response = client.get(f"/{language}")

    assert response.status_code == 200
    assert 'href="/internal/' not in response.text
    assert 'href="/check"' not in response.text
