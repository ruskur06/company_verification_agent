from __future__ import annotations

from datetime import datetime, timedelta
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
    """Use an isolated SQLite database for internal request UI tests."""
    database_url = (
        f"sqlite:///{tmp_path / 'check_requests_ui.db'}"
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


def _create_request(
    *,
    company_name: str,
    country: str = "Austria",
    email: str = "buyer@example.com",
    preferred_language: CheckRequestLanguage = CheckRequestLanguage.en,
    transaction_type: CheckRequestTransactionType | None = (
        CheckRequestTransactionType.supplier_verification
    ),
) -> int:
    saved = create_check_request(
        CheckRequestCreate(
            company_name=company_name,
            country=country,
            email=email,
            preferred_language=preferred_language,
            transaction_type=transaction_type,
        )
    )
    return saved.id


def test_check_requests_page_returns_200(sqlite_db, client):
    response = client.get("/internal/requests")

    assert response.status_code == 200
    assert "Check Requests" in response.text


def test_check_requests_page_empty_state(sqlite_db, client):
    response = client.get("/internal/requests")

    assert response.status_code == 200
    assert "No check requests found." in response.text


def test_check_requests_page_renders_records(sqlite_db, client):
    saved = create_check_request(
        CheckRequestCreate(
            company_name="Example GmbH",
            country="Austria",
            email="ops@example.com",
            preferred_language=CheckRequestLanguage.de,
            transaction_type=CheckRequestTransactionType.real_estate,
        )
    )

    response = client.get("/internal/requests")
    text = response.text

    assert response.status_code == 200
    assert f"<td>{saved.id}</td>" in text
    assert "<td>Example GmbH</td>" in text
    assert "<td>Austria</td>" in text
    assert "<td>ops@example.com</td>" in text
    assert "<td>de</td>" in text
    assert "<td>real_estate</td>" in text
    assert "<td>pending</td>" in text
    assert f"<td>{saved.created_at}</td>" in text
    assert "CheckRequestLanguage." not in text
    assert "CheckRequestTransactionType." not in text
    assert "CheckRequestStatus." not in text


def test_check_requests_page_renders_missing_transaction_type_as_em_dash(
    sqlite_db,
    client,
):
    saved = create_check_request(
        CheckRequestCreate(
            company_name="No Transaction Co",
            country="Austria",
            email="none@example.com",
            preferred_language=CheckRequestLanguage.en,
            transaction_type=None,
        )
    )

    response = client.get("/internal/requests")
    text = response.text

    assert response.status_code == 200
    assert f"<td>{saved.id}</td>" in text
    assert "<td>—</td>" in text
    assert "CheckRequestTransactionType." not in text


def test_check_requests_page_orders_newest_first(sqlite_db, client):
    older_id = _create_request(company_name="Older Co")
    newer_id = _create_request(company_name="Newer Co")

    session = sqlite_db()
    try:
        older = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == older_id)
            .one()
        )
        newer = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == newer_id)
            .one()
        )
        older.created_at = datetime.utcnow() - timedelta(hours=2)
        newer.created_at = datetime.utcnow()
        session.commit()
    finally:
        session.close()

    response = client.get("/internal/requests")
    text = response.text

    assert response.status_code == 200
    assert text.index("Newer Co") < text.index("Older Co")


def test_check_requests_page_includes_detail_links(sqlite_db, client):
    request_id = _create_request(company_name="Linked Co")

    response = client.get("/internal/requests")

    assert response.status_code == 200
    assert (
        f'href="/internal/requests/{request_id}"'
        in response.text
    )


def test_check_requests_page_has_noindex_nofollow(sqlite_db, client):
    response = client.get("/internal/requests")

    assert response.status_code == 200
    assert (
        'content="noindex, nofollow"'
        in response.text
    )


def test_check_requests_page_is_read_only_without_actions(
    sqlite_db,
    client,
):
    _create_request(company_name="Read Only Co")

    response = client.get("/internal/requests")
    text = response.text.lower()

    assert response.status_code == 200
    assert "approve" not in text
    assert "reject" not in text
    assert "run check" not in text
    assert 'method="post"' not in text


def test_check_requests_page_does_not_call_pipeline(
    sqlite_db,
    client,
    monkeypatch,
):
    pipeline = Mock()
    monkeypatch.setattr(
        "app.main.run_company_check",
        pipeline,
    )
    _create_request(company_name="Pipeline Co")

    response = client.get("/internal/requests")

    assert response.status_code == 200
    pipeline.assert_not_called()


def test_check_requests_page_does_not_mutate_records(
    sqlite_db,
    client,
):
    request_id = _create_request(company_name="Stable Co")

    response = client.get("/internal/requests")
    assert response.status_code == 200

    session = sqlite_db()
    try:
        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one()
        )
        assert record.status == "pending"
        assert record.company_check_id is None
        assert record.company_name == "Stable Co"
    finally:
        session.close()


def test_check_requests_page_limits_to_fifty_records(sqlite_db, client):
    request_ids: list[int] = []
    for index in range(51):
        request_ids.append(
            _create_request(company_name=f"Limited Co {index:02d}")
        )

    session = sqlite_db()
    try:
        shared_created_at = datetime.utcnow()
        records = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id.in_(request_ids))
            .all()
        )
        for record in records:
            record.created_at = shared_created_at
        session.commit()
    finally:
        session.close()

    oldest_id = min(request_ids)
    newest_id = max(request_ids)

    response = client.get("/internal/requests")
    text = response.text

    assert response.status_code == 200
    assert text.count('href="/internal/requests/') == 50
    assert f'href="/internal/requests/{newest_id}"' in text
    assert f'href="/internal/requests/{oldest_id}"' not in text
    assert "Limited Co 00" not in text
    assert "Limited Co 50" in text


def test_check_requests_page_escapes_unsafe_company_name(sqlite_db, client):
    unsafe_name = '<script>alert("x")</script>'
    _create_request(company_name=unsafe_name)

    response = client.get("/internal/requests")
    text = response.text

    assert response.status_code == 200
    assert unsafe_name not in text
    assert "&lt;script&gt;" in text
    assert "&lt;/script&gt;" in text


def test_internal_check_page_links_to_check_requests(
    sqlite_db,
    client,
):
    response = client.get("/internal/check")

    assert response.status_code == 200
    assert 'href="/internal/requests"' in response.text
    assert "View check requests" in response.text
