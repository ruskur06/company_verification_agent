import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.agents.report_agent import ReportAgent, json_path_for_check
from app.db import database
from app.db.repositories import get_company_check_by_id, save_company_check
from app.main import app
from app.schemas.company_check import CompanyCheckResult
from app.schemas.official_website_review import (
    OfficialWebsiteReview,
    OfficialWebsiteReviewCreate,
    OfficialWebsiteReviewDecision,
    OfficialWebsiteReviewSubmitDecision,
)
from app.schemas.risk import BusinessRiskLevel, RiskScoreInput
from app.services.company_check_service import submit_official_website_review
from app.tools.official_website_review import official_website_review_status_message
from app.tools.risk_score import calculate_risk_score
from tests.test_database import sample_check_result
from tests.test_json_schema import valid_company_check_data


CHECK_ID = "1782245998765"


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'official_website_review.db'}"
    database.configure_engine(database_url)
    database.init_db()

    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=database.engine,
    )
    monkeypatch.setattr("app.db.repositories.SessionLocal", session_factory)

    outputs_dir = tmp_path / "outputs"
    (outputs_dir / "json").mkdir(parents=True)
    (outputs_dir / "reports").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    yield session_factory

    database.engine.dispose()


def _website_candidate_payload() -> dict:
    return {
        "candidate_url": "https://servochron.com",
        "candidate_domain": "servochron.com",
        "score": 0.8,
        "confidence": "medium",
        "reasons": ["domain_contains_company_name"],
        "source_title": "SERVOCHRON GmbH official website",
        "is_verified": False,
    }


def _write_check_json(*, is_verified: bool = False) -> None:
    data = valid_company_check_data()
    data["check_id"] = int(CHECK_ID)
    data["company"]["name"] = "Servochron"
    data["company"]["country"] = "Austria"
    data["website_candidate"] = _website_candidate_payload()
    data["website_candidate"]["is_verified"] = is_verified
    data["website_ownership_signals"] = {
        "status": "signals_found",
        "score": 0.75,
        "confidence": "high",
        "signals": [],
        "warnings": ["Official website status still requires human verification."],
        "is_officially_confirmed": False,
    }

    path = json_path_for_check(int(CHECK_ID))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _review_payload(decision: str) -> dict:
    return {
        "decision": decision,
        "note": "Confirmed manually.",
        "reviewed_by": "human",
    }


def test_default_official_website_review_state_is_pending():
    result = CompanyCheckResult.model_validate(valid_company_check_data())

    assert result.official_website_review.decision == OfficialWebsiteReviewDecision.pending
    assert result.official_website_review.note is None
    assert result.official_website_review.reviewed_by is None
    assert result.official_website_review.reviewed_at is None


def test_pending_review_wording():
    message = official_website_review_status_message(OfficialWebsiteReview())
    assert message == "Candidate official website pending human verification"


@pytest.mark.parametrize(
    ("decision", "expected_message", "expected_verified"),
    [
        ("approved", "Official website verified by human reviewer", True),
        ("rejected", "Candidate website rejected by human reviewer", False),
        ("uncertain", "Candidate website remains uncertain after human review", False),
    ],
)
def test_submit_official_website_review_updates_candidate_and_db(
    sqlite_db,
    decision,
    expected_message,
    expected_verified,
):
    _write_check_json()
    save_company_check(sample_check_result(CHECK_ID))

    response = submit_official_website_review(
        CHECK_ID,
        OfficialWebsiteReviewCreate(
            decision=OfficialWebsiteReviewSubmitDecision(decision),
            note="Confirmed manually.",
            reviewed_by="human",
        ),
    )

    assert response.website_candidate_verified is expected_verified
    assert response.official_website_review.decision.value == decision
    assert response.official_website_review.reviewed_by == "human"

    saved_json = CompanyCheckResult.model_validate_json(
        json_path_for_check(int(CHECK_ID)).read_text(encoding="utf-8")
    )
    assert saved_json.website_candidate is not None
    assert saved_json.website_candidate.is_verified is expected_verified
    assert saved_json.website_ownership_signals is not None
    assert saved_json.website_ownership_signals.is_officially_confirmed is False
    assert official_website_review_status_message(saved_json.official_website_review) == expected_message

    db_record = get_company_check_by_id(CHECK_ID)
    assert db_record is not None
    assert db_record["official_website_review"]["decision"] == decision
    assert db_record["official_website_review"]["reviewed_by"] == "human"


def test_report_wording_for_approved_review(sqlite_db):
    _write_check_json()
    save_company_check(sample_check_result(CHECK_ID))
    submit_official_website_review(
        CHECK_ID,
        OfficialWebsiteReviewCreate(
            decision=OfficialWebsiteReviewSubmitDecision.approved,
            note="Confirmed manually.",
            reviewed_by="human",
        ),
    )

    result = CompanyCheckResult.model_validate_json(
        json_path_for_check(int(CHECK_ID)).read_text(encoding="utf-8")
    )
    markdown = ReportAgent().build_markdown(result)

    assert "Official website verified by human reviewer" in markdown
    assert "Official Website Human Review" in markdown
    assert "Ownership signals are supporting signals only; human review decision is recorded separately." in markdown
    assert "Official website status still requires human verification." not in markdown
    assert result.website_candidate is not None
    assert result.website_candidate.is_verified is True
    assert result.website_ownership_signals is not None
    assert result.website_ownership_signals.is_officially_confirmed is False


def test_report_wording_for_rejected_review(sqlite_db):
    _write_check_json()
    save_company_check(sample_check_result(CHECK_ID))
    submit_official_website_review(
        CHECK_ID,
        OfficialWebsiteReviewCreate(
            decision=OfficialWebsiteReviewSubmitDecision.rejected,
            note="Not the official company website.",
            reviewed_by="human",
        ),
    )

    result = CompanyCheckResult.model_validate_json(
        json_path_for_check(int(CHECK_ID)).read_text(encoding="utf-8")
    )
    markdown = ReportAgent().build_markdown(result)

    assert "Candidate website rejected by human reviewer" in markdown
    assert "Official Website Human Review" in markdown
    assert result.website_candidate is not None
    assert result.website_candidate.is_verified is False
    assert result.website_ownership_signals is not None
    assert result.website_ownership_signals.is_officially_confirmed is False


def test_report_wording_for_uncertain_review(sqlite_db):
    _write_check_json()
    save_company_check(sample_check_result(CHECK_ID))
    submit_official_website_review(
        CHECK_ID,
        OfficialWebsiteReviewCreate(
            decision=OfficialWebsiteReviewSubmitDecision.uncertain,
            note="Needs more evidence.",
            reviewed_by="human",
        ),
    )

    result = CompanyCheckResult.model_validate_json(
        json_path_for_check(int(CHECK_ID)).read_text(encoding="utf-8")
    )
    markdown = ReportAgent().build_markdown(result)

    assert "Candidate website remains uncertain after human review" in markdown
    assert "Official Website Human Review" in markdown
    assert result.website_candidate is not None
    assert result.website_candidate.is_verified is False
    assert result.website_ownership_signals is not None
    assert result.website_ownership_signals.is_officially_confirmed is False


def test_no_automatic_official_confirmation_before_review():
    data = valid_company_check_data()
    data["website_candidate"] = _website_candidate_payload()

    result = CompanyCheckResult.model_validate(data)

    assert result.website_candidate is not None
    assert result.website_candidate.is_verified is False
    assert result.official_website_review.decision == OfficialWebsiteReviewDecision.pending


def test_ownership_signals_do_not_auto_confirm_official_website():
    result = calculate_risk_score(
        RiskScoreInput(
            has_ownership_signals=True,
            ownership_signals_score=0.9,
            registry_found=False,
            registry_is_mock=True,
            source_count=1,
            all_sources_mock=False,
            verified_non_mock_source_count=1,
            verified_strong_source_count=1,
        )
    )

    assert result.business_risk == BusinessRiskLevel.unknown
    assert result.requires_human_review is True


def test_official_website_review_api_endpoint(sqlite_db):
    _write_check_json()
    save_company_check(sample_check_result(CHECK_ID))
    client = TestClient(app)

    response = client.post(
        f"/company-check/{CHECK_ID}/official-website-review",
        json=_review_payload("approved"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["website_candidate_verified"] is True
    assert body["official_website_review"]["decision"] == "approved"


def test_official_website_review_missing_check_returns_404(sqlite_db):
    client = TestClient(app)

    response = client.post(
        "/company-check/9999999999999/official-website-review",
        json=_review_payload("approved"),
    )

    assert response.status_code == 404


def test_official_website_review_without_candidate_returns_400(sqlite_db):
    data = valid_company_check_data()
    data["check_id"] = int(CHECK_ID)
    path = json_path_for_check(int(CHECK_ID))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    save_company_check(sample_check_result(CHECK_ID))

    client = TestClient(app)
    response = client.post(
        f"/company-check/{CHECK_ID}/official-website-review",
        json=_review_payload("approved"),
    )

    assert response.status_code == 400
    assert "no website candidate" in response.json()["detail"].lower()
