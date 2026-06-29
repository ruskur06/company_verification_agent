import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.agents.report_agent import json_path_for_check
from app.db import database
from app.db.repositories import save_company_check
from app.main import app
from app.schemas.company_check import CompanyCheckResult
from tests.test_database import sample_check_result
from tests.test_json_schema import valid_company_check_data


CHECK_ID = "1782245998766"
RESULT_URL = f"/result/{CHECK_ID}"
FORM_URL = f"/company-check/{CHECK_ID}/official-website-review/form"


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'official_website_review_ui.db'}"
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


def _candidate_domain_dns_payload() -> dict:
    return {
        "status": "checked",
        "domain": "servochron.com",
        "has_a_record": True,
        "has_mx_record": False,
        "has_txt_record": False,
        "https_available": True,
        "warnings": [],
    }


def _ownership_signals_payload() -> dict:
    return {
        "status": "signals_found",
        "score": 0.75,
        "confidence": "high",
        "signals": [],
        "warnings": ["Official website status still requires human verification."],
        "is_officially_confirmed": False,
    }


def _write_check_json(*, with_candidate: bool = True) -> None:
    data = valid_company_check_data()
    data["check_id"] = int(CHECK_ID)
    data["company"]["name"] = "Servochron"
    data["company"]["country"] = "Austria"

    if with_candidate:
        data["website_candidate"] = _website_candidate_payload()
        data["candidate_domain_dns"] = _candidate_domain_dns_payload()
        data["website_ownership_signals"] = _ownership_signals_payload()
    else:
        data.pop("website_candidate", None)
        data.pop("candidate_domain_dns", None)
        data.pop("website_ownership_signals", None)

    path = json_path_for_check(int(CHECK_ID))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _persist_check(*, with_candidate: bool = True) -> None:
    _write_check_json(with_candidate=with_candidate)
    save_company_check(sample_check_result(CHECK_ID))


def _load_saved_result() -> CompanyCheckResult:
    return CompanyCheckResult.model_validate_json(
        json_path_for_check(int(CHECK_ID)).read_text(encoding="utf-8")
    )


def _form_data(decision: str) -> dict[str, str]:
    return {
        "decision": decision,
        "note": "UI review note",
        "reviewed_by": "human",
    }


def test_result_page_renders_review_section(sqlite_db, client):
    _persist_check()

    response = client.get(RESULT_URL)

    assert response.status_code == 200
    assert "Official Website Human Review" in response.text


def test_result_page_shows_review_form_when_candidate_exists(sqlite_db, client):
    _persist_check()

    response = client.get(RESULT_URL)
    text = response.text

    assert response.status_code == 200
    assert 'action="/company-check/' in text
    assert "official-website-review/form" in text
    assert 'name="decision"' in text
    assert 'value="approved"' in text
    assert 'value="rejected"' in text
    assert 'value="uncertain"' in text
    assert "servochron.com" in text
    assert "https://servochron.com" in text


def test_approved_form_submit_persists_decision(sqlite_db, client):
    _persist_check()

    response = client.post(FORM_URL, data=_form_data("approved"), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == RESULT_URL

    saved = _load_saved_result()
    assert saved.official_website_review.decision.value == "approved"
    assert saved.website_candidate is not None
    assert saved.website_candidate.is_verified is True
    assert saved.website_ownership_signals is not None
    assert saved.website_ownership_signals.is_officially_confirmed is False


def test_rejected_form_submit_persists_decision(sqlite_db, client):
    _persist_check()

    response = client.post(FORM_URL, data=_form_data("rejected"), follow_redirects=False)

    assert response.status_code == 303

    saved = _load_saved_result()
    assert saved.official_website_review.decision.value == "rejected"
    assert saved.website_candidate is not None
    assert saved.website_candidate.is_verified is False
    assert saved.website_ownership_signals is not None
    assert saved.website_ownership_signals.is_officially_confirmed is False


def test_uncertain_form_submit_persists_decision(sqlite_db, client):
    _persist_check()

    response = client.post(FORM_URL, data=_form_data("uncertain"), follow_redirects=False)

    assert response.status_code == 303

    saved = _load_saved_result()
    assert saved.official_website_review.decision.value == "uncertain"
    assert saved.website_candidate is not None
    assert saved.website_candidate.is_verified is False
    assert saved.website_ownership_signals is not None
    assert saved.website_ownership_signals.is_officially_confirmed is False


def test_result_page_without_candidate_shows_message_and_no_form(sqlite_db, client):
    _persist_check(with_candidate=False)

    response = client.get(RESULT_URL)
    text = response.text

    assert response.status_code == 200
    assert "Official Website Human Review" in text
    assert "No candidate website available for human review." in text
    assert "official-website-review/form" not in text
    assert 'name="decision"' not in text


def test_approved_review_wording_on_result_page(sqlite_db, client):
    _persist_check()
    client.post(FORM_URL, data=_form_data("approved"), follow_redirects=False)

    response = client.get(RESULT_URL)
    text = response.text

    assert response.status_code == 200
    assert "Official website verified by human reviewer" in text
    assert "Official website status still requires human verification." not in text


def test_invalid_decision_form_submit_returns_validation_error(sqlite_db, client):
    _persist_check()

    response = client.post(
        FORM_URL,
        data={
            "decision": "definitely-not-a-real-decision",
            "reviewed_by": "human",
        },
        follow_redirects=False,
    )

    assert response.status_code == 422

    saved = _load_saved_result()
    assert saved.official_website_review.decision.value == "pending"
    assert saved.website_candidate is not None
    assert saved.website_candidate.is_verified is False
