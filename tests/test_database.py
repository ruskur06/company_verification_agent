import pytest
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.models import CompanyCheckRecord, ReportRecord
from app.db.repositories import (
    CompanyCheckLockedError,
    get_company_check_by_id,
    list_company_checks,
    save_company_check,
    update_company_check_after_refresh,
)


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    """Use an isolated SQLite database for repository tests."""
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    database.configure_engine(database_url)
    database.init_db()

    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=database.engine,
    )
    monkeypatch.setattr("app.db.repositories.SessionLocal", session_factory)

    yield database.engine

    database.engine.dispose()


def sample_check_result(check_id: str = "1234567890") -> dict:
    return {
        "check_id": check_id,
        "company_name": "Servochron",
        "country": "USA",
        "domain": "servochron.com",
        "risk_score": 55,
        "risk_level": "medium",
        "human_review_status": "pending",
        "json_report_path": "outputs/json/company_check_1234567890.json",
        "markdown_report_path": "outputs/reports/company_check_1234567890.md",
        "registry_check": {
            "company_name": "Servochron",
            "country": "USA",
            "status": "found",
            "registry_found": True,
            "registry_name": "US public business registry search",
            "source_url": None,
            "confidence": "medium",
            "notes": ["Mock registry match for local MVP testing."],
            "is_mock": True,
        },
        "domain_check": {
            "status": "checked",
            "domain": "servochron.com",
            "has_a_record": True,
            "has_mx_record": False,
            "has_txt_record": False,
            "https_available": True,
            "warnings": [],
        },
        "sources": [
            {
                "title": "Mock search result",
                "url": "mock://search/servochron/profile",
                "snippet": "Mock source for local MVP testing.",
                "source_type": "search_result",
                "confidence": "low",
                "is_mock": True,
            }
        ],
        "created_at": "2026-01-01T12:00:00+00:00",
    }


def test_database_models_initialize(sqlite_db):
    assert sqlite_db is not None
    assert database.engine.url.database is not None


def test_save_company_check_saves_sample_result(sqlite_db):
    payload = sample_check_result()

    save_company_check(payload)

    saved = get_company_check_by_id("1234567890")
    assert saved is not None
    assert saved["company_name"] == "Servochron"
    assert saved["country"] == "USA"
    assert saved["risk_score"] == 55
    assert saved["risk_level"] == "medium"
    assert saved["registry_check"]["registry_found"] is True
    assert saved["domain_check"]["domain"] == "servochron.com"


def test_list_company_checks_returns_saved_records(sqlite_db):
    save_company_check(sample_check_result("111"))
    save_company_check(sample_check_result("222"))

    records = list_company_checks(limit=20)

    assert len(records) == 2
    check_ids = {record["check_id"] for record in records}
    assert check_ids == {"111", "222"}


def test_get_company_check_by_id_returns_expected_record(sqlite_db):
    save_company_check(sample_check_result("999"))

    record = get_company_check_by_id("999")

    assert record is not None
    assert record["check_id"] == "999"
    assert record["company_name"] == "Servochron"
    assert record["human_review_status"] == "pending"


def test_update_company_check_after_refresh_persists_official_review_with_risk(
    tmp_path,
    sqlite_db,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    check_id = "1234567890"
    save_company_check(sample_check_result(check_id))

    json_path = tmp_path / "outputs/json/company_check_1234567890.json"
    markdown_path = tmp_path / "outputs/reports/company_check_1234567890.md"
    json_path.parent.mkdir(parents=True)
    markdown_path.parent.mkdir(parents=True)
    json_path.write_text('{"risk": {"preliminary_score": 40}}', encoding="utf-8")
    markdown_path.write_text("# report", encoding="utf-8")

    payload = {
        "check_id": check_id,
        "risk": {
            "preliminary_score": 35,
            "preliminary_level": "low",
            "human_review_status": "pending",
        },
        "json_report_path": str(json_path),
        "markdown_report_path": str(markdown_path),
    }
    review_data = {
        "decision": "approved",
        "note": "Confirmed manually.",
        "reviewed_by": "human",
        "reviewed_at": "2026-01-02T00:00:00+00:00",
    }

    update_company_check_after_refresh(payload, official_website_review_data=review_data)

    saved = get_company_check_by_id(check_id)
    assert saved is not None
    assert saved["risk_score"] == 35
    assert saved["risk_level"] == "low"
    assert saved["official_website_review"]["decision"] == "approved"
    assert saved["official_website_review"]["note"] == "Confirmed manually."
    assert saved["official_website_review"]["reviewed_by"] == "human"
    assert saved["official_website_review"]["reviewed_at"] is not None

    session_factory = sessionmaker(bind=sqlite_db)
    session = session_factory()
    try:
        report_count = (
            session.query(ReportRecord)
            .filter(ReportRecord.check_id == check_id)
            .count()
        )
    finally:
        session.close()
    assert report_count == 2


def test_update_company_check_after_refresh_rejects_locked_check_without_mutation(
    tmp_path,
    sqlite_db,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    check_id = "1234567890"
    save_company_check(sample_check_result(check_id))

    json_path = tmp_path / "outputs/json/company_check_1234567890.json"
    markdown_path = tmp_path / "outputs/reports/company_check_1234567890.md"
    json_path.parent.mkdir(parents=True)
    markdown_path.parent.mkdir(parents=True)
    json_path.write_text('{"risk": {"preliminary_score": 40}}', encoding="utf-8")
    markdown_path.write_text("# report", encoding="utf-8")

    session_factory = sessionmaker(bind=sqlite_db)
    session = session_factory()
    try:
        record = (
            session.query(CompanyCheckRecord)
            .filter(CompanyCheckRecord.check_id == check_id)
            .one()
        )
        record.is_locked = True
        session.commit()
    finally:
        session.close()

    before = get_company_check_by_id(check_id)
    session = session_factory()
    try:
        report_count_before = (
            session.query(ReportRecord)
            .filter(ReportRecord.check_id == check_id)
            .count()
        )
    finally:
        session.close()

    payload = {
        "check_id": check_id,
        "risk": {
            "preliminary_score": 10,
            "preliminary_level": "low",
            "human_review_status": "pending",
        },
        "json_report_path": str(json_path),
        "markdown_report_path": str(markdown_path),
    }
    review_data = {
        "decision": "approved",
        "note": "Should not persist.",
        "reviewed_by": "human",
        "reviewed_at": "2026-01-02T00:00:00+00:00",
    }

    with pytest.raises(CompanyCheckLockedError):
        update_company_check_after_refresh(payload, official_website_review_data=review_data)

    after = get_company_check_by_id(check_id)
    assert after is not None
    assert before is not None
    assert after["risk_score"] == before["risk_score"]
    assert after["risk_level"] == before["risk_level"]
    assert (
        after["official_website_review"]["decision"]
        == before["official_website_review"]["decision"]
    )
    assert (
        after["official_website_review"]["note"]
        == before["official_website_review"]["note"]
    )

    session = session_factory()
    try:
        report_count_after = (
            session.query(ReportRecord)
            .filter(ReportRecord.check_id == check_id)
            .count()
        )
    finally:
        session.close()
    assert report_count_after == report_count_before
