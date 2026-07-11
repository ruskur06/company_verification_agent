from __future__ import annotations

from unittest.mock import Mock

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.models import (
    CheckRequestRecord,
    CompanyCheckRecord,
)


@pytest.fixture()
def sqlite_request_db(
    tmp_path,
    monkeypatch,
):
    """Use isolated persistence for public form tests."""
    database_url = (
        f"sqlite:///{tmp_path / 'public_requests.db'}"
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


@pytest.mark.parametrize(
    ("language", "expected_title"),
    [
        (
            "en",
            "Tell us which company you want to verify",
        ),
        (
            "de",
            "Welches Unternehmen möchten Sie prüfen?",
        ),
        (
            "es",
            "Indíquenos qué empresa desea verificar",
        ),
    ],
)
def test_localized_request_form_renders(
    client,
    language,
    expected_title,
):
    response = client.get(
        f"/{language}/request-check"
    )

    assert response.status_code == 200
    assert (
        f'<html lang="{language}">'
        in response.text
    )
    assert expected_title in response.text
    assert (
        f'action="/{language}/request-check"'
        in response.text
    )
    assert 'name="company_website"' in response.text


def test_unknown_request_language_returns_404(
    client,
):
    response = client.get(
        "/fr/request-check"
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "transaction_type",
    [
        "real_estate",
        "supplier_verification",
        "procurement",
        "legal_advisory",
        "other",
    ],
)
def test_request_form_contains_transaction_types(
    client,
    transaction_type,
):
    response = client.get(
        "/en/request-check"
    )

    assert response.status_code == 200
    assert (
        f'value="{transaction_type}"'
        in response.text
    )


def test_valid_public_request_is_persisted_without_pipeline(
    client,
    sqlite_request_db,
    monkeypatch,
):
    pipeline_mock = Mock()

    monkeypatch.setattr(
        "app.main.run_company_check",
        pipeline_mock,
    )

    response = client.post(
        "/de/request-check",
        data={
            "company_name": "Example GmbH",
            "country": "Austria",
            "email": "buyer@example.com",
            "website": "https://example.com",
            "transaction_type": "real_estate",
            "additional_context": (
                "Review before property purchase."
            ),
            "company_website": "",
        },
    )

    assert response.status_code == 200
    assert (
        "Ihre Anfrage wurde empfangen"
        in response.text
    )

    session = sqlite_request_db()

    try:
        request_record = (
            session.query(CheckRequestRecord)
            .one()
        )

        company_check_count = (
            session.query(CompanyCheckRecord)
            .count()
        )
    finally:
        session.close()

    assert request_record.company_name == "Example GmbH"
    assert request_record.country == "Austria"
    assert request_record.email == "buyer@example.com"
    assert request_record.preferred_language == "de"
    assert request_record.status == "pending"
    assert request_record.company_check_id is None
    assert company_check_count == 0

    pipeline_mock.assert_not_called()


@pytest.mark.parametrize(
    ("language", "expected_error"),
    [
        (
            "en",
            "Please check the required fields",
        ),
        (
            "de",
            "Bitte prüfen Sie die Pflichtfelder",
        ),
        (
            "es",
            "Revise los campos obligatorios",
        ),
    ],
)
def test_invalid_request_shows_localized_error(
    client,
    sqlite_request_db,
    language,
    expected_error,
):
    response = client.post(
        f"/{language}/request-check",
        data={
            "company_name": "",
            "country": "",
            "email": "not-an-email",
        },
    )

    assert response.status_code == 422
    assert expected_error in response.text

    session = sqlite_request_db()

    try:
        request_count = (
            session.query(CheckRequestRecord)
            .count()
        )
    finally:
        session.close()

    assert request_count == 0


def test_honeypot_returns_success_without_persisting(
    client,
    sqlite_request_db,
):
    response = client.post(
        "/en/request-check",
        data={
            "company_name": "",
            "country": "",
            "email": "",
            "company_website": (
                "https://spam.example"
            ),
        },
    )

    assert response.status_code == 200
    assert (
        "Your request has been received"
        in response.text
    )

    session = sqlite_request_db()

    try:
        request_count = (
            session.query(CheckRequestRecord)
            .count()
        )
    finally:
        session.close()

    assert request_count == 0


def test_oversized_request_is_rejected(
    client,
    sqlite_request_db,
):
    response = client.post(
        "/en/request-check",
        data={
            "company_name": "X" * 256,
            "country": "USA",
            "email": "buyer@example.com",
        },
    )

    assert response.status_code == 422

    session = sqlite_request_db()

    try:
        request_count = (
            session.query(CheckRequestRecord)
            .count()
        )
    finally:
        session.close()

    assert request_count == 0


def test_public_form_includes_field_length_limits(
    client,
):
    response = client.get(
        "/en/request-check"
    )

    assert response.status_code == 200
    assert 'maxlength="255"' in response.text
    assert 'maxlength="100"' in response.text
    assert 'maxlength="320"' in response.text
    assert 'maxlength="500"' in response.text
    assert 'maxlength="3000"' in response.text


def test_public_form_contains_privacy_notice(
    client,
):
    response = client.get(
        "/en/request-check"
    )

    assert response.status_code == 200
    assert (
        "We will use your email only to contact "
        "you about this verification request."
        in response.text
    )
