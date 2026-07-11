import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.agents.report_agent import json_path_for_check
from app.db import database
from app.db.repositories import save_company_check
from app.main import app
from app.schemas.final_risk_review import FinalRiskReviewCreate
from app.schemas.human_review import ReviewDecision
from app.schemas.risk import RiskLevel
from app.services.company_check_service import submit_final_risk_review
from tests.test_database import sample_check_result
from tests.test_json_schema import valid_company_check_data


CHECK_ID = "1782245998769"


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'checks_history_ui.db'}"
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


def _write_check_json() -> None:
    data = valid_company_check_data()
    data["check_id"] = int(CHECK_ID)
    data["company"]["name"] = "Servochron"
    data["company"]["country"] = "USA"

    path = json_path_for_check(int(CHECK_ID))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _persist_check() -> None:
    _write_check_json()
    save_company_check(sample_check_result(CHECK_ID))


def test_checks_page_returns_200(sqlite_db, client):
    response = client.get("/checks")

    assert response.status_code == 200
    assert "Company Checks History" in response.text


def test_checks_page_empty_db_shows_message(sqlite_db, client):
    response = client.get("/checks")

    assert response.status_code == 200
    assert "No company checks found." in response.text


def test_checks_page_lists_saved_check(sqlite_db, client):
    _persist_check()

    response = client.get("/checks")
    text = response.text

    assert response.status_code == 200
    assert CHECK_ID in text
    assert "Servochron" in text
    assert "USA" in text


def test_checks_page_shows_preliminary_risk_fields(sqlite_db, client):
    _persist_check()

    response = client.get("/checks")
    text = response.text

    assert "Preliminary score" in text
    assert "Preliminary level" in text
    assert "55" in text
    assert "medium" in text


def test_checks_page_shows_edited_human_review_status_after_final_risk_review(sqlite_db, client):
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

    response = client.get("/checks")
    text = response.text

    assert response.status_code == 200
    assert "Final score" not in text
    assert "Final level" not in text
    assert "edited" in text


def test_checks_page_shows_human_review_status(sqlite_db, client):
    _persist_check()

    response = client.get("/checks")

    assert response.status_code == 200
    assert "Human review" in response.text
    assert "pending" in response.text


def test_checks_page_includes_result_link(sqlite_db, client):
    _persist_check()

    response = client.get("/checks")

    assert response.status_code == 200
    assert f'href="/result/{CHECK_ID}"' in response.text


def test_home_page_includes_checks_history_link(sqlite_db, client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/checks"' in response.text
    assert "View checks history" in response.text


def test_check_page_includes_checks_history_link(sqlite_db, client):
    response = client.get("/check")

    assert response.status_code == 200
    assert 'href="/checks"' in response.text
    assert "View checks history" in response.text
