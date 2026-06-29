import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.agents.report_agent import json_path_for_check
from app.db import database
from app.db.repositories import save_company_check
from app.main import app
from app.schemas.company_check import CompanyCheckResult
from app.schemas.risk import HumanReviewStatus, RiskLevel
from tests.test_database import sample_check_result
from tests.test_json_schema import valid_company_check_data


CHECK_ID = "1782245998768"
RESULT_URL = f"/result/{CHECK_ID}"
FORM_URL = f"/company-check/{CHECK_ID}/final-risk-review/form"


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'final_risk_review_ui.db'}"
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


@pytest.fixture()
def client():
    return TestClient(app)


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
    data["company"]["name"] = "Servochron"
    data["company"]["country"] = "Austria"
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


def _form_data(decision: str, **overrides) -> dict[str, str]:
    payload = {
        "final_risk_decision": decision,
        "notes": "UI final risk note",
        "reviewed_by": "human",
    }
    payload.update(overrides)
    return payload


def test_result_page_renders_final_risk_review_section(sqlite_db, client):
    _persist_check()

    response = client.get(RESULT_URL)

    assert response.status_code == 200
    assert "Final Risk Human Review" in response.text


def test_result_page_shows_preliminary_risk_and_form_options(sqlite_db, client):
    _persist_check()

    response = client.get(RESULT_URL)
    text = response.text

    assert response.status_code == 200
    assert "Preliminary score" in text
    assert "Preliminary level" in text
    assert "final-risk-review/form" in text
    assert 'id="final_risk_decision"' in text
    assert 'value="approved"' in text
    assert 'value="edited"' in text
    assert 'value="rejected"' in text


def test_approved_form_submit_redirects_and_copies_preliminary(sqlite_db, client):
    _persist_check()

    response = client.post(
        FORM_URL,
        data=_form_data("approved"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == RESULT_URL

    saved = _load_saved_result()
    assert saved.risk.final_score == saved.risk.preliminary_score == 45
    assert saved.risk.final_level == saved.risk.preliminary_level == RiskLevel.medium
    assert saved.risk.human_review_status == HumanReviewStatus.approved
    assert saved.risk.notes == "UI final risk note"
    assert saved.risk.reviewed_by == "human"


def test_approved_form_submit_ignores_provided_final_values(sqlite_db, client):
    _persist_check()

    client.post(
        FORM_URL,
        data=_form_data(
            "approved",
            final_score="10",
            final_level="low",
        ),
        follow_redirects=False,
    )

    saved = _load_saved_result()
    assert saved.risk.final_score == saved.risk.preliminary_score == 45
    assert saved.risk.final_level == saved.risk.preliminary_level == RiskLevel.medium
    assert saved.risk.human_review_status == HumanReviewStatus.approved


def test_edited_form_submit_saves_provided_final_values(sqlite_db, client):
    _persist_check()

    response = client.post(
        FORM_URL,
        data=_form_data(
            "edited",
            final_score="35",
            final_level="medium",
            notes="Adjusted after human review.",
        ),
        follow_redirects=False,
    )

    assert response.status_code == 303

    saved = _load_saved_result()
    assert saved.risk.final_score == 35
    assert saved.risk.final_level == RiskLevel.medium
    assert saved.risk.preliminary_score == 45
    assert saved.risk.human_review_status == HumanReviewStatus.edited
    assert saved.risk.notes == "Adjusted after human review."


def test_rejected_form_submit_clears_final_values(sqlite_db, client):
    _persist_check()
    client.post(
        FORM_URL,
        data=_form_data("approved"),
        follow_redirects=False,
    )

    response = client.post(
        FORM_URL,
        data=_form_data(
            "rejected",
            final_score="10",
            final_level="low",
            notes="Rejected after review.",
        ),
        follow_redirects=False,
    )

    assert response.status_code == 303

    saved = _load_saved_result()
    assert saved.risk.final_score is None
    assert saved.risk.final_level is None
    assert saved.risk.preliminary_score == 45
    assert saved.risk.preliminary_level == RiskLevel.medium
    assert saved.risk.human_review_status == HumanReviewStatus.rejected


@pytest.mark.parametrize(
    ("decision", "expected_message"),
    [
        ("approved", "Final risk approved by human reviewer."),
        ("edited", "Final risk edited by human reviewer."),
        ("rejected", "Risk assessment rejected by human reviewer."),
    ],
)
def test_result_page_shows_status_wording_after_review(
    sqlite_db,
    client,
    decision,
    expected_message,
):
    _persist_check()
    payload = _form_data(decision)
    if decision == "edited":
        payload["final_score"] = "35"
        payload["final_level"] = "medium"

    client.post(FORM_URL, data=payload, follow_redirects=False)

    response = client.get(RESULT_URL)
    assert response.status_code == 200
    assert expected_message in response.text


def test_edited_form_submit_without_final_values_returns_422(sqlite_db, client):
    _persist_check()

    response = client.post(
        FORM_URL,
        data=_form_data("edited"),
        follow_redirects=False,
    )

    assert response.status_code == 422

    saved = _load_saved_result()
    assert saved.risk.human_review_status == HumanReviewStatus.pending
    assert saved.risk.final_score is None


def test_empty_final_score_and_level_strings_are_accepted_for_approved(sqlite_db, client):
    _persist_check()

    response = client.post(
        FORM_URL,
        data=_form_data("approved", final_score="", final_level=""),
        follow_redirects=False,
    )

    assert response.status_code == 303

    saved = _load_saved_result()
    assert saved.risk.human_review_status == HumanReviewStatus.approved
    assert saved.risk.final_score == 45


def test_official_website_review_ui_still_renders(sqlite_db, client):
    _persist_check()

    response = client.get(RESULT_URL)
    text = response.text

    assert response.status_code == 200
    assert "Official Website Human Review" in text
    assert "official-website-review/form" in text
