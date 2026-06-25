import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.agents.report_agent import json_path_for_check
from app.db import database
from app.db.models import ReportRecord, SourceRecord
from app.db.repositories import (
    add_source_to_company_check,
    get_sources_for_company_check,
    save_company_check,
)
from app.main import app
from app.schemas.risk import BusinessRiskLevel, RiskLevel
from app.schemas.source import RelevanceLevel
from app.services.company_check_service import refresh_company_check_report
from tests.test_database import sample_check_result
from tests.test_json_schema import valid_company_check_data
from tests.test_manual_sources import _manual_source_payload


CHECK_ID = "1782245998764"


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    """Use an isolated SQLite database for refresh report tests."""
    database_url = f"sqlite:///{tmp_path / 'refresh_report.db'}"
    database.configure_engine(database_url)
    database.init_db()

    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=database.engine,
    )
    monkeypatch.setattr("app.db.repositories.SessionLocal", session_factory)

    outputs_dir = tmp_path / "outputs"
    json_dir = outputs_dir / "json"
    reports_dir = outputs_dir / "reports"
    json_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    yield session_factory

    database.engine.dispose()


def _write_initial_json(check_id: str = CHECK_ID) -> None:
    data = valid_company_check_data()
    data["check_id"] = int(check_id)
    data["company"]["name"] = "Servochron"
    data["risk"]["verification_confidence"] = "low"
    data["risk"]["verification_risk"] = "high"
    data["risk"]["business_risk"] = "unknown"
    data["risk"]["preliminary_score"] = 100
    data["risk"]["preliminary_level"] = "high"
    data["sources"] = [
        {
            "title": "Mock search result",
            "url": "mock://search/servochron/profile",
            "snippet": "Mock source for local MVP testing.",
            "source_type": "search_result",
            "confidence": "low",
            "is_mock": True,
            "retrieved_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    path = json_path_for_check(int(check_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_refresh_report_endpoint_returns_404_for_missing_company_check(sqlite_db):
    client = TestClient(app)

    response = client.post("/company-checks/9999999999999/refresh-report")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_refresh_report_with_manual_source_updates_output(sqlite_db):
    _write_initial_json()
    save_company_check(sample_check_result(CHECK_ID))
    add_source_to_company_check(CHECK_ID, _manual_source_payload())

    response = refresh_company_check_report(CHECK_ID)

    assert response.check_id == int(CHECK_ID)
    assert response.json_report_path.endswith(f"company_check_{CHECK_ID}.json")
    assert Path(response.markdown_report_path).exists()

    titles = [source.title for source in response.json_result.sources]
    assert "SERVOCHRON GmbH - FirmenABC" in titles
    manual_sources = [source for source in response.json_result.sources if not source.is_mock]
    assert len(manual_sources) == 1
    assert manual_sources[0].is_mock is False

    assert response.json_result.risk.verification_confidence == RiskLevel.medium
    assert response.json_result.risk.verification_risk == RiskLevel.medium
    assert response.json_result.risk.business_risk == BusinessRiskLevel.unknown
    assert response.json_result.summary.confidence.value == "medium"

    saved_json = json.loads(json_path_for_check(int(CHECK_ID)).read_text(encoding="utf-8"))
    assert saved_json["risk"]["verification_confidence"] == "medium"
    assert saved_json["risk"]["business_risk"] == "unknown"
    assert saved_json["summary"]["confidence"] == "medium"
    assert not any(
        factor["name"] == "reasonable_source_coverage"
        for factor in saved_json["risk"]["factors"]
    )
    assert any(
        factor["name"] == "verified_relevant_source_found"
        for factor in saved_json["risk"]["factors"]
    )
    assert any(source["title"] == "SERVOCHRON GmbH - FirmenABC" for source in saved_json["sources"])

    markdown = Path(response.markdown_report_path).read_text(encoding="utf-8")
    assert "SERVOCHRON GmbH - FirmenABC" in markdown
    assert "Evidence type: `verified/manual`" in markdown
    assert "Preliminary Risk Score" not in markdown


def test_refresh_report_mock_only_keeps_low_confidence_high_verification_risk(sqlite_db):
    _write_initial_json()
    save_company_check(sample_check_result(CHECK_ID))

    response = refresh_company_check_report(CHECK_ID)

    assert response.json_result.risk.verification_confidence == RiskLevel.low
    assert response.json_result.risk.verification_risk == RiskLevel.high
    assert response.json_result.risk.business_risk == BusinessRiskLevel.unknown


def test_refresh_report_api_endpoint_exists(sqlite_db):
    _write_initial_json()
    save_company_check(sample_check_result(CHECK_ID))
    add_source_to_company_check(CHECK_ID, _manual_source_payload())
    client = TestClient(app)

    response = client.post(f"/company-checks/{CHECK_ID}/refresh-report")

    assert response.status_code == 200
    body = response.json()
    assert body["check_id"] == int(CHECK_ID)
    assert body["json_result"]["risk"]["verification_confidence"] == "medium"
    assert body["json_result"]["risk"]["business_risk"] == "unknown"


def test_refresh_report_appends_report_record(sqlite_db):
    _write_initial_json()
    save_company_check(sample_check_result(CHECK_ID))

    refresh_company_check_report(CHECK_ID)

    session = sqlite_db()
    try:
        report_records = (
            session.query(ReportRecord)
            .filter(ReportRecord.check_id == CHECK_ID)
            .all()
        )
        db_sources = get_sources_for_company_check(CHECK_ID)
    finally:
        session.close()

    assert len(report_records) == 2
    assert report_records[-1].json_content is not None
    assert report_records[-1].markdown_content is not None
    assert len(db_sources) == 1


def test_refresh_report_irrelevant_real_source_does_not_strengthen_coverage(sqlite_db):
    _write_initial_json()
    save_company_check(sample_check_result(CHECK_ID))

    session = sqlite_db()
    try:
        session.add(
            SourceRecord(
                check_id=CHECK_ID,
                title="Avron GmbH unrelated listing",
                url="https://example.com/avron-gmbh",
                snippet="Avron GmbH company information.",
                source_type="search_result",
                confidence="high",
                is_mock=False,
                relevance="irrelevant",
                relevance_score=0.1,
            )
        )
        session.commit()
    finally:
        session.close()

    response = refresh_company_check_report(CHECK_ID)

    titles = [source.title for source in response.json_result.sources]
    assert "Avron GmbH unrelated listing" in titles

    irrelevant = next(
        source for source in response.json_result.sources if "Avron" in source.title
    )
    assert irrelevant.is_mock is False
    assert irrelevant.relevance == RelevanceLevel.irrelevant

    assert response.json_result.risk.verification_confidence == RiskLevel.low
    assert response.json_result.risk.verification_risk == RiskLevel.high
    assert not any(
        factor.name == "verified_relevant_source_found"
        for factor in response.json_result.risk.factors
    )
