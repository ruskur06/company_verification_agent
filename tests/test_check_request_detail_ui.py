from __future__ import annotations

import re
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.models import CheckRequestRecord
from app.schemas.check_request import (
    CheckRequestCreate,
    CheckRequestLanguage,
    CheckRequestTransactionType,
)
from app.services.check_request_service import create_check_request


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    """Use an isolated SQLite database for detail UI tests."""
    database_url = (
        f"sqlite:///{tmp_path / 'check_request_detail_ui.db'}"
    )

    database.configure_engine(database_url)
    database.init_db()

    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=database.engine,
    )
    monkeypatch.setattr(
        "app.db.repositories.SessionLocal",
        session_factory,
    )

    yield session_factory

    database.engine.dispose()


def _create_full_request():
    return create_check_request(
        CheckRequestCreate(
            company_name="Detail GmbH",
            country="Austria",
            email="detail@example.com",
            website="https://example.com",
            transaction_type=CheckRequestTransactionType.procurement,
            additional_context="Buyer asked for a formal check.",
            preferred_language=CheckRequestLanguage.de,
        )
    )


def test_check_request_detail_returns_200(sqlite_db, client):
    saved = _create_full_request()

    response = client.get(f"/internal/requests/{saved.id}")

    assert response.status_code == 200
    assert "Check Request Detail" in response.text


def test_check_request_detail_renders_all_important_fields(
    sqlite_db,
    client,
):
    saved = _create_full_request()

    response = client.get(f"/internal/requests/{saved.id}")
    text = response.text

    assert response.status_code == 200
    assert f"<strong>{saved.id}</strong>" in text
    assert "<strong>Detail GmbH</strong>" in text
    assert "<strong>Austria</strong>" in text
    assert "<strong>detail@example.com</strong>" in text
    assert "<strong>https://example.com</strong>" in text
    assert "<strong>procurement</strong>" in text
    assert "Buyer asked for a formal check." in text
    assert "<strong>de</strong>" in text
    assert "<strong>pending</strong>" in text
    assert f"<strong>{saved.created_at}</strong>" in text
    assert "CheckRequestLanguage." not in text
    assert "CheckRequestTransactionType." not in text
    assert "CheckRequestStatus." not in text


@pytest.mark.parametrize(
    ("field_name", "create_kwargs", "fallback_pattern"),
    [
        (
            "website",
            {
                "company_name": "No Website Co",
                "country": "Austria",
                "email": "noweb@example.com",
                "website": None,
                "transaction_type": CheckRequestTransactionType.procurement,
                "additional_context": "Context present for website case.",
                "preferred_language": CheckRequestLanguage.en,
            },
            re.compile(
                r'<span class="label">Website</span>\s*'
                r"<strong>—</strong>",
                re.MULTILINE,
            ),
        ),
        (
            "transaction_type",
            {
                "company_name": "No Transaction Co",
                "country": "Austria",
                "email": "none@example.com",
                "website": "https://example.com",
                "transaction_type": None,
                "additional_context": "Context present for transaction case.",
                "preferred_language": CheckRequestLanguage.en,
            },
            re.compile(
                r'<span class="label">Transaction type</span>\s*'
                r"<strong>—</strong>",
                re.MULTILINE,
            ),
        ),
        (
            "additional_context",
            {
                "company_name": "No Context Co",
                "country": "Austria",
                "email": "nocontext@example.com",
                "website": "https://example.com",
                "transaction_type": CheckRequestTransactionType.real_estate,
                "additional_context": None,
                "preferred_language": CheckRequestLanguage.en,
            },
            re.compile(
                r"<h2>Additional context</h2>\s*"
                r"<p>\s*—\s*</p>",
                re.MULTILINE,
            ),
        ),
    ],
)
def test_check_request_detail_optional_field_fallbacks(
    sqlite_db,
    client,
    field_name,
    create_kwargs,
    fallback_pattern,
):
    saved = create_check_request(CheckRequestCreate(**create_kwargs))

    response = client.get(f"/internal/requests/{saved.id}")
    text = response.text

    assert response.status_code == 200
    assert fallback_pattern.search(text) is not None

    if field_name == "website":
        assert "<strong>https://example.com</strong>" not in text
        assert "<strong>procurement</strong>" in text
        assert "Context present for website case." in text
    elif field_name == "transaction_type":
        assert "<strong>https://example.com</strong>" in text
        assert "<strong>procurement</strong>" not in text
        assert "<strong>real_estate</strong>" not in text
        assert "Context present for transaction case." in text
    elif field_name == "additional_context":
        assert "<strong>https://example.com</strong>" in text
        assert "<strong>real_estate</strong>" in text
        assert "Context present for" not in text


def test_check_request_detail_company_check_id_link(
    sqlite_db,
    client,
):
    saved = _create_full_request()
    linked_check_id = "1782245998769"

    session = sqlite_db()
    try:
        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == saved.id)
            .one()
        )
        record.company_check_id = linked_check_id
        session.commit()
    finally:
        session.close()

    response = client.get(f"/internal/requests/{saved.id}")
    text = response.text

    assert response.status_code == 200
    assert (
        f'href="/internal/result/{linked_check_id}"'
        in text
    )
    assert linked_check_id in text
    assert "No verification run yet" not in text


def test_check_request_detail_company_check_id_absent(
    sqlite_db,
    client,
):
    saved = _create_full_request()

    response = client.get(f"/internal/requests/{saved.id}")
    text = response.text

    assert response.status_code == 200
    assert 'href="/internal/result/' not in text
    assert "No verification run yet" in text


def test_check_request_detail_unknown_id_returns_404(
    sqlite_db,
    client,
):
    response = client.get("/internal/requests/999999")

    assert response.status_code == 404


def test_check_request_detail_non_integer_id_returns_422(
    sqlite_db,
    client,
):
    response = client.get("/internal/requests/abc")

    assert response.status_code == 422


def test_check_request_detail_has_noindex_nofollow(
    sqlite_db,
    client,
):
    saved = _create_full_request()

    response = client.get(f"/internal/requests/{saved.id}")

    assert response.status_code == 200
    assert 'content="noindex, nofollow"' in response.text


def test_check_request_detail_has_internal_navigation(
    sqlite_db,
    client,
):
    saved = _create_full_request()

    response = client.get(f"/internal/requests/{saved.id}")
    text = response.text

    assert response.status_code == 200
    assert 'href="/internal/requests"' in text
    assert 'href="/internal/checks"' in text


def test_check_request_detail_escapes_unsafe_html(
    sqlite_db,
    client,
):
    unsafe_company = '<script>alert("company")</script>'
    unsafe_context = '<img src=x onerror=alert("context")>'
    saved = create_check_request(
        CheckRequestCreate(
            company_name=unsafe_company,
            country="Austria",
            email="safe@example.com",
            additional_context=unsafe_context,
            preferred_language=CheckRequestLanguage.en,
        )
    )

    response = client.get(f"/internal/requests/{saved.id}")
    text = response.text

    assert response.status_code == 200
    assert unsafe_company not in text
    assert unsafe_context not in text
    assert "<script>" not in text
    assert "<img" not in text
    assert "&lt;script&gt;" in text
    assert "&lt;/script&gt;" in text
    assert "&lt;img" in text
    assert "onerror" in text


def test_check_request_detail_is_read_only(sqlite_db, client):
    saved = _create_full_request()

    response = client.get(f"/internal/requests/{saved.id}")
    text = response.text.lower()

    assert response.status_code == 200
    assert 'method="post"' not in text
    assert ">approve<" not in text
    assert ">reject<" not in text
    assert "run check" not in text
    assert "approve request" not in text
    assert "reject request" not in text


def test_check_request_detail_does_not_call_pipeline(
    sqlite_db,
    client,
    monkeypatch,
):
    pipeline = Mock()
    monkeypatch.setattr("app.main.run_company_check", pipeline)
    saved = _create_full_request()

    response = client.get(f"/internal/requests/{saved.id}")

    assert response.status_code == 200
    pipeline.assert_not_called()


def test_check_request_detail_does_not_mutate_record(
    sqlite_db,
    client,
):
    saved = _create_full_request()

    response = client.get(f"/internal/requests/{saved.id}")
    assert response.status_code == 200

    session = sqlite_db()
    try:
        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == saved.id)
            .one()
        )
        assert record.status == "pending"
        assert record.company_check_id is None
        assert record.company_name == "Detail GmbH"
        assert record.country == "Austria"
        assert record.email == "detail@example.com"
        assert record.website == "https://example.com"
        assert record.additional_context == (
            "Buyer asked for a formal check."
        )
        assert record.preferred_language == "de"
        assert record.transaction_type == "procurement"
    finally:
        session.close()
