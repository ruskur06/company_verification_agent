from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.models import (
    CheckRequestRecord,
    CompanyCheckRecord,
)
from app.schemas.check_request import (
    CheckRequestCreate,
    CheckRequestLanguage,
    CheckRequestStatus,
    CheckRequestTransactionType,
)
from app.services.check_request_service import (
    create_check_request,
    get_check_request,
)


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    """Use an isolated SQLite database for request tests."""
    database_url = (
        f"sqlite:///{tmp_path / 'check_requests.db'}"
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


def test_check_request_table_is_created(sqlite_db):
    table_names = inspect(
        database.engine
    ).get_table_names()

    assert "check_request_records" in table_names


def test_create_check_request_persists_required_fields(
    sqlite_db,
):
    request = CheckRequestCreate(
        company_name="Example GmbH",
        country="Austria",
        email="buyer@example.com",
        preferred_language=CheckRequestLanguage.de,
    )

    saved = create_check_request(request)

    assert saved.id > 0
    assert saved.company_name == "Example GmbH"
    assert saved.country == "Austria"
    assert saved.email == "buyer@example.com"
    assert (
        saved.preferred_language
        == CheckRequestLanguage.de
    )


def test_new_check_request_has_pending_status_and_no_check(
    sqlite_db,
):
    request = CheckRequestCreate(
        company_name="Example S.L.",
        country="Spain",
        email="legal@example.com",
        preferred_language=CheckRequestLanguage.es,
    )

    saved = create_check_request(request)

    assert saved.status == CheckRequestStatus.pending
    assert saved.company_check_id is None


def test_optional_fields_can_be_empty(
    sqlite_db,
):
    request = CheckRequestCreate(
        company_name="Example Ltd",
        country="United Kingdom",
        email="procurement@example.com",
        website="   ",
        additional_context="   ",
        preferred_language=CheckRequestLanguage.en,
    )

    saved = create_check_request(request)

    assert saved.website is None
    assert saved.transaction_type is None
    assert saved.additional_context is None


def test_transaction_context_is_persisted(
    sqlite_db,
):
    request = CheckRequestCreate(
        company_name="Developer GmbH",
        country="Germany",
        email="investor@example.com",
        website="https://developer.example",
        transaction_type=(
            CheckRequestTransactionType.real_estate
        ),
        additional_context=(
            "Preliminary review before a property purchase."
        ),
        preferred_language=CheckRequestLanguage.en,
    )

    saved = create_check_request(request)
    loaded = get_check_request(saved.id)

    assert loaded is not None
    assert (
        loaded.transaction_type
        == CheckRequestTransactionType.real_estate
    )
    assert (
        loaded.website
        == "https://developer.example"
    )
    assert (
        loaded.additional_context
        == (
            "Preliminary review before "
            "a property purchase."
        )
    )


def test_public_request_does_not_create_company_check(
    sqlite_db,
):
    request = CheckRequestCreate(
        company_name="Supplier SpA",
        country="Italy",
        email="importer@example.com",
        transaction_type=(
            CheckRequestTransactionType
            .supplier_verification
        ),
        preferred_language=CheckRequestLanguage.en,
    )

    create_check_request(request)

    session = sqlite_db()
    try:
        request_count = (
            session.query(CheckRequestRecord)
            .count()
        )
        company_check_count = (
            session.query(CompanyCheckRecord)
            .count()
        )
    finally:
        session.close()

    assert request_count == 1
    assert company_check_count == 0


@pytest.mark.parametrize(
    "field_name",
    [
        "company_name",
        "country",
        "email",
    ],
)
def test_required_text_fields_reject_blank_values(
    field_name,
):
    values = {
        "company_name": "Example Inc.",
        "country": "USA",
        "email": "buyer@example.com",
        "preferred_language": (
            CheckRequestLanguage.en
        ),
    }
    values[field_name] = "   "

    with pytest.raises(ValidationError):
        CheckRequestCreate(**values)
