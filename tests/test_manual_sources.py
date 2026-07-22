import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.models import SourceRecord
from app.db.repositories import CompanyCheckNotFoundError, add_source_to_company_check, save_company_check
from app.main import app
from tests.test_database import sample_check_result


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    """Use an isolated SQLite database for manual source tests."""
    database_url = f"sqlite:///{tmp_path / 'manual_sources.db'}"
    database.configure_engine(database_url)
    database.init_db()

    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=database.engine,
    )
    monkeypatch.setattr("app.db.repositories.SessionLocal", session_factory)

    yield session_factory

    database.engine.dispose()


def _manual_source_payload() -> dict:
    return {
        "title": "SERVOCHRON GmbH - FirmenABC",
        "url": "https://www.firmenabc.at/servochron-gmbh",
        "snippet": "SERVOCHRON GmbH is listed with company register number FN 548828a.",
        "source_type": "registry",
        "confidence": "high",
    }


def test_add_manual_source_success(sqlite_db):
    save_company_check(sample_check_result("1782245998764"))

    saved = add_source_to_company_check("1782245998764", _manual_source_payload())

    assert saved["title"] == "SERVOCHRON GmbH - FirmenABC"
    assert saved["url"].startswith("https://www.firmenabc.at/")
    assert saved["source_type"] == "registry"
    assert saved["confidence"] == "high"
    assert saved["is_mock"] is False
    assert saved["relevance"] == "relevant"
    assert saved["relevance_score"] == 1.0


def test_add_manual_source_is_linked_to_company_check(sqlite_db):
    save_company_check(sample_check_result("1782245998764"))

    saved = add_source_to_company_check("1782245998764", _manual_source_payload())

    session = sqlite_db()
    try:
        records = (
            session.query(SourceRecord)
            .filter(SourceRecord.check_id == "1782245998764")
            .order_by(SourceRecord.id.asc())
            .all()
        )
    finally:
        session.close()

    assert len(records) == 2
    assert saved["company_check_id"] == "1782245998764"
    assert records[-1].id == saved["id"]
    assert records[-1].check_id == "1782245998764"
    assert records[-1].is_mock is False


def test_add_manual_source_raises_when_company_check_missing(sqlite_db):
    with pytest.raises(CompanyCheckNotFoundError):
        add_source_to_company_check("missing-check", _manual_source_payload())


def test_add_manual_source_api_returns_created_source(sqlite_db):
    save_company_check(sample_check_result("1782245998764"))
    client = TestClient(app)

    response = client.post(
        "/company-checks/1782245998764/sources",
        json=_manual_source_payload(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["company_check_id"] == "1782245998764"
    assert body["is_mock"] is False
    assert body["title"] == "SERVOCHRON GmbH - FirmenABC"
    assert body["source_type"] == "registry"
    assert body["confidence"] == "high"
    assert body["relevance"] == "relevant"
    assert body["relevance_score"] == 1.0


def test_add_manual_source_api_returns_404_for_missing_company_check(sqlite_db):
    client = TestClient(app)

    response = client.post(
        "/company-checks/9999999999999/sources",
        json=_manual_source_payload(),
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()
