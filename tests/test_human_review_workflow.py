import json
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.agents.report_agent import json_path_for_check
from app.db import database
from app.db.models import HumanReviewRecord
from app.db.repositories import CompanyCheckLockedError, create_human_review_record, get_human_reviews_for_company_check, save_company_check
from tests.test_database import sample_check_result
from tests.test_json_schema import valid_company_check_data
from tests.test_manual_sources import _manual_source_payload


CHECK_ID = "1782245998764"


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    """Use an isolated SQLite database for human review workflow tests."""
    database_url = f"sqlite:///{tmp_path / 'human_review.db'}"
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


def _write_initial_json(check_id: str = CHECK_ID) -> None:
    data = valid_company_check_data()
    data["check_id"] = int(check_id)
    path = json_path_for_check(int(check_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _review_payload(**overrides) -> dict:
    payload = {
        "decision": "approved",
        "reviewer_name": "analyst_1",
        "reviewer_notes": "Manual registry source checked.",
        "final_verification_confidence": "medium",
        "final_verification_risk": "medium",
        "final_business_risk": "unknown",
        "overrides": {},
    }
    payload.update(overrides)
    return payload


def test_submit_human_review_creates_record_and_returns_201(sqlite_db, client):
    save_company_check(sample_check_result(CHECK_ID))

    response = client.post(
        f"/company-checks/{CHECK_ID}/human-review",
        json=_review_payload(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["company_check_id"] == CHECK_ID
    assert body["decision"] == "approved"
    assert body["reviewer_name"] == "analyst_1"
    assert body["final_verification_confidence"] == "medium"
    assert body["final_verification_risk"] == "medium"
    assert body["final_business_risk"] == "unknown"
    assert body["is_locked"] is True


def test_second_human_review_on_same_check_returns_409(sqlite_db, client):
    save_company_check(sample_check_result(CHECK_ID))

    first = client.post(f"/company-checks/{CHECK_ID}/human-review", json=_review_payload())
    assert first.status_code == 201

    second = client.post(
        f"/company-checks/{CHECK_ID}/human-review",
        json=_review_payload(decision="edited", reviewer_name="analyst_2"),
    )

    assert second.status_code == 409


def test_refresh_report_after_human_review_lock_returns_409(sqlite_db, client):
    _write_initial_json()
    save_company_check(sample_check_result(CHECK_ID))

    review = client.post(f"/company-checks/{CHECK_ID}/human-review", json=_review_payload())
    assert review.status_code == 201

    refresh = client.post(f"/company-checks/{CHECK_ID}/refresh-report")
    assert refresh.status_code == 409


def test_add_manual_source_after_human_review_lock_returns_409(sqlite_db, client):
    save_company_check(sample_check_result(CHECK_ID))

    review = client.post(f"/company-checks/{CHECK_ID}/human-review", json=_review_payload())
    assert review.status_code == 201

    source = client.post(
        f"/company-checks/{CHECK_ID}/sources",
        json=_manual_source_payload(),
    )
    assert source.status_code == 409


def test_human_review_missing_check_returns_404(sqlite_db, client):
    response = client.post(
        "/company-checks/9999999999999/human-review",
        json=_review_payload(),
    )

    assert response.status_code == 404


def test_human_review_pending_decision_returns_422(sqlite_db, client):
    save_company_check(sample_check_result(CHECK_ID))

    response = client.post(
        f"/company-checks/{CHECK_ID}/human-review",
        json=_review_payload(decision="pending"),
    )

    assert response.status_code == 422


def test_human_review_overrides_roundtrip(sqlite_db, client):
    save_company_check(sample_check_result(CHECK_ID))
    overrides = {"registry_note": "FN 548828a confirmed manually"}

    response = client.post(
        f"/company-checks/{CHECK_ID}/human-review",
        json=_review_payload(overrides=overrides),
    )

    assert response.status_code == 201
    assert response.json()["overrides"] == overrides


def test_human_review_records_are_append_only(sqlite_db, client):
    save_company_check(sample_check_result(CHECK_ID))

    first = client.post(f"/company-checks/{CHECK_ID}/human-review", json=_review_payload())
    assert first.status_code == 201
    first_id = first.json()["id"]

    second = client.post(
        f"/company-checks/{CHECK_ID}/human-review",
        json=_review_payload(decision="rejected", reviewer_name="analyst_2"),
    )
    assert second.status_code == 409

    reviews = get_human_reviews_for_company_check(CHECK_ID)
    assert len(reviews) == 1
    assert reviews[0]["id"] == first_id
    assert reviews[0]["decision"] == "approved"

    session = sqlite_db()
    try:
        db_reviews = session.query(HumanReviewRecord).filter(
            HumanReviewRecord.check_id == CHECK_ID
        ).all()
    finally:
        session.close()

    assert len(db_reviews) == 1
    assert db_reviews[0].decision == "approved"


def test_create_human_review_record_via_repository(sqlite_db):
    save_company_check(sample_check_result(CHECK_ID))

    saved = create_human_review_record(CHECK_ID, _review_payload())

    assert saved["is_locked"] is True
    assert saved["overrides"] == {}

    with pytest.raises(CompanyCheckLockedError):
        create_human_review_record(CHECK_ID, _review_payload(decision="edited"))
