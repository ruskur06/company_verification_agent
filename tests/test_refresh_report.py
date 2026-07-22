import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import sessionmaker

from app.agents.report_agent import json_path_for_check
from app.db import database
from app.db.models import ReportRecord, SourceRecord
from app.schemas.company_check import DomainDnsInfo, DomainDnsStatus
from app.schemas.official_website_review import OfficialWebsiteReviewDecision
from app.db.repositories import (
    add_source_to_company_check,
    get_sources_for_company_check,
    save_company_check,
)
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

    mock_domain_agent = MagicMock()
    mock_domain_agent.run.return_value = DomainDnsInfo(status=DomainDnsStatus.not_provided)
    monkeypatch.setattr("app.services.company_check_service._domain_agent", mock_domain_agent)

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


def test_refresh_report_endpoint_returns_404_for_missing_company_check(sqlite_db, client):
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


def test_refresh_report_api_endpoint_exists(sqlite_db, client):
    _write_initial_json()
    save_company_check(sample_check_result(CHECK_ID))
    add_source_to_company_check(CHECK_ID, _manual_source_payload())

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


def test_refresh_report_recomputes_website_candidate_from_saved_sources(sqlite_db, monkeypatch):
    mock_domain_agent = MagicMock()
    mock_domain_agent.run.return_value = DomainDnsInfo(
        status=DomainDnsStatus.checked,
        domain="servochron.com",
        has_a_record=True,
        https_available=True,
    )
    monkeypatch.setattr("app.services.company_check_service._domain_agent", mock_domain_agent)

    _write_initial_json()
    save_company_check(sample_check_result(CHECK_ID))

    session = sqlite_db()
    try:
        session.add(
            SourceRecord(
                check_id=CHECK_ID,
                title="SERVOCHRON GmbH official website",
                url="https://servochron.com",
                snippet="Official company homepage for Servochron.",
                source_type="search_result",
                confidence="medium",
                is_mock=False,
                relevance="relevant",
                relevance_score=0.8,
            )
        )
        session.commit()
    finally:
        session.close()

    response = refresh_company_check_report(CHECK_ID)

    assert response.json_result.website_candidate is not None
    assert response.json_result.website_candidate.candidate_domain == "servochron.com"
    assert response.json_result.candidate_domain_dns is not None
    assert response.json_result.candidate_domain_dns.domain == "servochron.com"
    assert response.json_result.website_ownership_signals is not None
    assert response.json_result.website_ownership_signals.is_officially_confirmed is False
    mock_domain_agent.run.assert_called_once_with("servochron.com")

    factor_names = [factor.name for factor in response.json_result.risk.factors]
    assert "website_candidate_found_pending_verification" in factor_names
    assert "candidate_domain_resolves_pending_ownership_verification" in factor_names
    assert "official_website_not_found" not in factor_names

    markdown = Path(response.markdown_report_path).read_text(encoding="utf-8")
    assert "Website Candidate (pending verification)" in markdown
    assert "Candidate Domain DNS/HTTPS (pending ownership verification)" in markdown
    assert "Website Ownership Signals (pending verification)" in markdown
    assert "servochron.com" in markdown


def _provided_domain_candidate_payload() -> dict:
    return {
        "candidate_url": "https://munchy.at",
        "candidate_domain": "munchy.at",
        "score": 0.5,
        "confidence": "medium",
        "reasons": ["provided_domain"],
        "source_title": "User-provided domain",
        "is_verified": False,
    }


def _write_initial_json_with_provided_domain_candidate(
    *,
    check_id: str = CHECK_ID,
    is_verified: bool = False,
    official_review_decision: str | None = None,
) -> None:
    data = valid_company_check_data()
    data["check_id"] = int(check_id)
    data["company"]["name"] = "Munchy Gastro GmbH"
    data["company"]["domain"] = "munchy.at"
    data["domain_dns"] = {
        "status": "checked",
        "domain": "munchy.at",
        "has_a_record": True,
        "has_mx_record": True,
        "has_txt_record": False,
        "https_available": True,
        "warnings": [],
    }
    data["website_candidate"] = _provided_domain_candidate_payload()
    data["website_candidate"]["is_verified"] = is_verified
    if official_review_decision is not None:
        data["official_website_review"] = {
            "decision": official_review_decision,
            "note": "Confirmed manually.",
            "reviewed_by": "human",
            "reviewed_at": "2026-01-02T00:00:00+00:00",
        }

    path = json_path_for_check(int(check_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_refresh_report_preserves_provided_domain_candidate_when_sources_have_no_match(
    sqlite_db,
    monkeypatch,
):
    mock_domain_agent = MagicMock()
    mock_domain_agent.run.return_value = DomainDnsInfo(
        status=DomainDnsStatus.checked,
        domain="munchy.at",
        has_a_record=True,
        https_available=True,
    )
    monkeypatch.setattr("app.services.company_check_service._domain_agent", mock_domain_agent)

    _write_initial_json_with_provided_domain_candidate()
    save_company_check(sample_check_result(CHECK_ID))

    response = refresh_company_check_report(CHECK_ID)

    candidate = response.json_result.website_candidate
    assert candidate is not None
    assert candidate.candidate_domain == "munchy.at"
    assert candidate.candidate_url == "https://munchy.at"
    assert candidate.source_title == "User-provided domain"
    assert candidate.reasons == ["provided_domain"]
    assert candidate.score == 0.5
    assert candidate.confidence.value == "medium"
    assert candidate.is_verified is False
    assert response.json_result.candidate_domain_dns is not None
    assert response.json_result.candidate_domain_dns.domain == "munchy.at"
    mock_domain_agent.run.assert_called_once_with("munchy.at")


def test_refresh_report_applies_approved_review_to_preserved_provided_domain_candidate(
    sqlite_db,
    monkeypatch,
):
    mock_domain_agent = MagicMock()
    mock_domain_agent.run.return_value = DomainDnsInfo(
        status=DomainDnsStatus.checked,
        domain="munchy.at",
        has_a_record=True,
        https_available=True,
    )
    monkeypatch.setattr("app.services.company_check_service._domain_agent", mock_domain_agent)

    _write_initial_json_with_provided_domain_candidate(
        official_review_decision=OfficialWebsiteReviewDecision.approved.value,
    )
    save_company_check(sample_check_result(CHECK_ID))

    response = refresh_company_check_report(CHECK_ID)

    candidate = response.json_result.website_candidate
    assert candidate is not None
    assert candidate.candidate_domain == "munchy.at"
    assert candidate.candidate_url == "https://munchy.at"
    assert candidate.is_verified is True

    factor_names = [factor.name for factor in response.json_result.risk.factors]
    assert "official_website_found" in factor_names
    assert "website_candidate_found_pending_verification" not in factor_names


def test_refresh_report_without_candidate_stays_none(sqlite_db):
    _write_initial_json()
    save_company_check(sample_check_result(CHECK_ID))

    response = refresh_company_check_report(CHECK_ID)

    assert response.json_result.website_candidate is None
    assert response.json_result.candidate_domain_dns is None


def test_refresh_report_preserves_final_risk_review_state(sqlite_db):
    data = valid_company_check_data()
    data["check_id"] = int(CHECK_ID)
    data["risk"]["human_review_status"] = "approved"
    data["risk"]["final_score"] = 42
    data["risk"]["final_level"] = "medium"
    data["risk"]["notes"] = "Approved after manual review."
    data["risk"]["reviewed_by"] = "human"
    data["risk"]["reviewed_at"] = "2026-01-02T00:00:00+00:00"

    path = json_path_for_check(int(CHECK_ID))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    save_company_check(sample_check_result(CHECK_ID))

    response = refresh_company_check_report(CHECK_ID)

    assert response.json_result.risk.human_review_status.value == "approved"
    assert response.json_result.risk.final_score == 42
    assert response.json_result.risk.final_level.value == "medium"
    assert response.json_result.risk.notes == "Approved after manual review."
    assert response.json_result.risk.reviewed_by == "human"
    assert response.json_result.risk.reviewed_at is not None


def test_refresh_report_preserves_approved_final_risk_summary_wording(sqlite_db):
    data = valid_company_check_data()
    data["check_id"] = int(CHECK_ID)
    data["company"]["name"] = "Servochron"
    data["risk"]["human_review_status"] = "approved"
    data["risk"]["final_score"] = 42
    data["risk"]["final_level"] = "medium"
    data["risk"]["notes"] = "Approved after manual review."
    data["risk"]["reviewed_by"] = "human"
    data["risk"]["reviewed_at"] = "2026-01-02T00:00:00+00:00"
    data["summary"]["overall_assessment"] = (
        "This report was refreshed using stored company check data and linked sources from the database. "
        "Manually verified non-mock sources improve verification confidence but do not prove business safety. "
        "Business risk remains unknown unless verified negative business indicators exist. "
        "Final risk approved by human reviewer."
    )
    data["unknowns"] = ["No official registry result was confirmed."]
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

    path = json_path_for_check(int(CHECK_ID))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    save_company_check(sample_check_result(CHECK_ID))
    add_source_to_company_check(CHECK_ID, _manual_source_payload())

    response = refresh_company_check_report(CHECK_ID)

    assessment = response.json_result.summary.overall_assessment
    assert "Final risk approved by human reviewer." in assessment
    assert "Final assessment still requires human review." not in assessment
    assert "Final risk assessment requires human review." not in assessment
