import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from app.agents.report_agent import ReportAgent, json_path_for_check
from app.db import database
from app.db.repositories import get_company_check_by_id, save_company_check
from app.main import app
from app.schemas.company_check import CompanyCheckResult
from app.schemas.final_risk_review import FinalRiskReviewCreate
from app.schemas.human_review import ReviewDecision
from app.schemas.official_website_review import (
    OfficialWebsiteReviewCreate,
    OfficialWebsiteReviewSubmitDecision,
)
from app.schemas.risk import HumanReviewStatus, RiskLevel
from app.services.company_check_service import submit_final_risk_review, submit_official_website_review
from app.tools.final_risk_review import final_risk_review_status_message
from tests.test_database import sample_check_result
from tests.test_json_schema import valid_company_check_data


CHECK_ID = "1782245998767"


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'final_risk_review.db'}"
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


def _write_check_json() -> None:
    data = valid_company_check_data()
    data["check_id"] = int(CHECK_ID)
    data["website_candidate"] = _website_candidate_payload()

    path = json_path_for_check(int(CHECK_ID))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _persist_check() -> None:
    _write_check_json()
    save_company_check(sample_check_result(CHECK_ID))


def _load_saved_result() -> CompanyCheckResult:
    return CompanyCheckResult.model_validate_json(
        json_path_for_check(int(CHECK_ID)).read_text(encoding="utf-8")
    )


def _review_payload(**overrides) -> dict:
    payload = {
        "decision": "approved",
        "notes": "Looks consistent with preliminary assessment.",
        "reviewed_by": "human",
    }
    payload.update(overrides)
    return payload


def test_default_final_risk_state_is_pending():
    result = CompanyCheckResult.model_validate(valid_company_check_data())

    assert result.risk.human_review_status == HumanReviewStatus.pending
    assert result.risk.final_score is None
    assert result.risk.final_level is None
    assert result.risk.notes is None
    assert result.risk.reviewed_by is None
    assert result.risk.reviewed_at is None
    assert final_risk_review_status_message(result.risk.human_review_status) == (
        "Final risk assessment requires human review."
    )


def test_pending_report_wording(sqlite_db):
    _persist_check()
    result = _load_saved_result()
    markdown = ReportAgent().build_markdown(result)

    assert "Final risk assessment requires human review." in markdown
    assert "Preliminary verification score (legacy): 45" in markdown
    assert "Final score:" not in markdown


def test_approved_copies_preliminary_to_final(sqlite_db):
    _persist_check()

    response = submit_final_risk_review(
        CHECK_ID,
        FinalRiskReviewCreate(
            decision=ReviewDecision.approved,
            notes="Approved as-is.",
            reviewed_by="human",
        ),
    )

    saved = _load_saved_result()
    assert response.human_review_status == HumanReviewStatus.approved
    assert saved.risk.final_score == saved.risk.preliminary_score == 45
    assert saved.risk.final_level == saved.risk.preliminary_level == RiskLevel.medium
    assert saved.risk.human_review_status == HumanReviewStatus.approved
    assert saved.risk.notes == "Approved as-is."
    assert saved.risk.reviewed_by == "human"
    assert saved.risk.reviewed_at is not None


def test_approved_ignores_reviewer_provided_final_values(sqlite_db):
    _persist_check()

    submit_final_risk_review(
        CHECK_ID,
        FinalRiskReviewCreate(
            decision=ReviewDecision.approved,
            final_score=10,
            final_level=RiskLevel.low,
            reviewed_by="human",
        ),
    )

    saved = _load_saved_result()
    assert saved.risk.final_score == saved.risk.preliminary_score == 45
    assert saved.risk.final_level == saved.risk.preliminary_level == RiskLevel.medium
    assert saved.risk.human_review_status == HumanReviewStatus.approved


def test_edited_requires_final_score_and_level():
    with pytest.raises(ValidationError):
        FinalRiskReviewCreate.model_validate({"decision": "edited"})

    with pytest.raises(ValidationError):
        FinalRiskReviewCreate.model_validate(
            {"decision": "edited", "final_score": 35}
        )


def test_edited_saves_provided_final_values(sqlite_db):
    _persist_check()

    response = submit_final_risk_review(
        CHECK_ID,
        FinalRiskReviewCreate(
            decision=ReviewDecision.edited,
            final_score=35,
            final_level=RiskLevel.medium,
            notes="Adjusted after human review.",
            reviewed_by="analyst",
        ),
    )

    saved = _load_saved_result()
    assert response.human_review_status == HumanReviewStatus.edited
    assert saved.risk.final_score == 35
    assert saved.risk.final_level == RiskLevel.medium
    assert saved.risk.preliminary_score == 45
    assert saved.risk.preliminary_level == RiskLevel.medium
    assert saved.risk.human_review_status == HumanReviewStatus.edited
    assert saved.risk.notes == "Adjusted after human review."
    assert saved.risk.reviewed_by == "analyst"


def test_rejected_clears_final_values_but_keeps_preliminary(sqlite_db):
    _persist_check()
    submit_final_risk_review(
        CHECK_ID,
        FinalRiskReviewCreate(
            decision=ReviewDecision.approved,
            reviewed_by="human",
        ),
    )

    response = submit_final_risk_review(
        CHECK_ID,
        FinalRiskReviewCreate(
            decision=ReviewDecision.rejected,
            final_score=10,
            final_level=RiskLevel.low,
            notes="Assessment rejected.",
            reviewed_by="human",
        ),
    )

    saved = _load_saved_result()
    assert response.human_review_status == HumanReviewStatus.rejected
    assert saved.risk.final_score is None
    assert saved.risk.final_level is None
    assert saved.risk.preliminary_score == 45
    assert saved.risk.preliminary_level == RiskLevel.medium
    assert saved.risk.human_review_status == HumanReviewStatus.rejected


def test_json_and_markdown_reports_are_updated(sqlite_db):
    _persist_check()
    submit_final_risk_review(
        CHECK_ID,
        FinalRiskReviewCreate(
            decision=ReviewDecision.edited,
            final_score=35,
            final_level=RiskLevel.medium,
            notes="Adjusted after human review.",
            reviewed_by="human",
        ),
    )

    saved = _load_saved_result()
    markdown = ReportAgent().build_markdown(saved)
    report_path = Path("outputs/reports") / f"company_check_{CHECK_ID}.md"
    assert report_path.exists()
    saved_markdown = report_path.read_text(encoding="utf-8")

    assert "Final risk edited by human reviewer." in markdown
    assert "Final risk edited by human reviewer." in saved_markdown
    assert "Final score: `35`" in markdown
    assert "Preliminary verification score (legacy): 45" in markdown


@pytest.mark.parametrize(
    ("decision", "expected_message"),
    [
        ("approved", "Final risk approved by human reviewer."),
        ("edited", "Final risk edited by human reviewer."),
        ("rejected", "Risk assessment rejected by human reviewer."),
    ],
)
def test_markdown_wording_for_final_review_decisions(
    sqlite_db,
    decision,
    expected_message,
):
    _persist_check()
    payload = _review_payload(decision=decision)
    if decision == "edited":
        payload["final_score"] = 35
        payload["final_level"] = "medium"

    submit_final_risk_review(CHECK_ID, FinalRiskReviewCreate.model_validate(payload))

    markdown = ReportAgent().build_markdown(_load_saved_result())
    assert expected_message in markdown


def test_website_approval_does_not_finalize_risk(sqlite_db):
    _persist_check()
    submit_official_website_review(
        CHECK_ID,
        OfficialWebsiteReviewCreate(
            decision=OfficialWebsiteReviewSubmitDecision.approved,
            note="Confirmed manually.",
            reviewed_by="human",
        ),
    )

    saved = _load_saved_result()
    assert saved.website_candidate is not None
    assert saved.website_candidate.is_verified is True
    assert saved.risk.human_review_status == HumanReviewStatus.pending
    assert saved.risk.final_score is None
    assert saved.risk.final_level is None


def test_final_risk_review_api_endpoint(sqlite_db):
    _persist_check()
    client = TestClient(app)

    response = client.post(
        f"/company-check/{CHECK_ID}/final-risk-review",
        json=_review_payload(
            decision="edited",
            final_score=35,
            final_level="medium",
            notes="Adjusted after human review.",
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["check_id"] == int(CHECK_ID)
    assert body["human_review_status"] == "edited"
    assert body["final_score"] == 35
    assert body["final_level"] == "medium"
    assert body["notes"] == "Adjusted after human review."
    assert body["reviewed_by"] == "human"
    assert body["reviewed_at"] is not None

    db_record = get_company_check_by_id(CHECK_ID)
    assert db_record is not None
    assert db_record["human_review_status"] == "edited"


def test_final_risk_review_missing_check_returns_404(sqlite_db):
    client = TestClient(app)

    response = client.post(
        "/company-check/9999999999999/final-risk-review",
        json=_review_payload(),
    )

    assert response.status_code == 404


def test_invalid_decision_returns_validation_error(sqlite_db):
    _persist_check()
    client = TestClient(app)

    response = client.post(
        f"/company-check/{CHECK_ID}/final-risk-review",
        json={
            "decision": "definitely-not-a-real-decision",
            "reviewed_by": "human",
        },
    )

    assert response.status_code == 422

    saved = _load_saved_result()
    assert saved.risk.human_review_status == HumanReviewStatus.pending
    assert saved.risk.final_score is None
