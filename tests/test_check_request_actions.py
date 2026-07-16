from __future__ import annotations

from unittest.mock import Mock

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.models import CheckRequestRecord, CompanyCheckRecord
from app.db.repositories import update_check_request_status
from app.schemas.check_request import (
    CheckRequestCreate,
    CheckRequestLanguage,
    CheckRequestStatus,
    CheckRequestTransactionType,
)
from app.services.check_request_service import (
    CheckRequestNotFoundError,
    InvalidCheckRequestTransitionError,
    approve_check_request,
    create_check_request,
    reject_check_request,
)


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    """Use an isolated SQLite database for action tests."""
    database_url = (
        f"sqlite:///{tmp_path / 'check_request_actions.db'}"
    )

    database.configure_engine(database_url)
    database.init_db()

    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=database.engine,
    )
    monkeypatch.setattr(
        "app.db.repositories.SessionLocal",
        session_factory,
    )

    yield session_factory

    database.engine.dispose()


def _create_pending_request(**overrides):
    payload = {
        "company_name": "Action GmbH",
        "country": "Austria",
        "email": "action@example.com",
        "website": "https://example.com",
        "transaction_type": CheckRequestTransactionType.procurement,
        "additional_context": "Needs manual review.",
        "preferred_language": CheckRequestLanguage.de,
    }
    payload.update(overrides)
    return create_check_request(CheckRequestCreate(**payload))


def _get_record_fields(
    session_factory,
    request_id: int,
) -> dict:
    session = session_factory()
    try:
        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one()
        )
        return {
            "status": record.status,
            "company_name": record.company_name,
            "country": record.country,
            "email": record.email,
            "website": record.website,
            "transaction_type": record.transaction_type,
            "additional_context": record.additional_context,
            "preferred_language": record.preferred_language,
            "company_check_id": record.company_check_id,
        }
    finally:
        session.close()


def _set_status(session_factory, request_id: int, status: str) -> None:
    session = session_factory()
    try:
        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one()
        )
        record.status = status
        session.commit()
    finally:
        session.close()


def test_approve_pending_request(sqlite_db):
    saved = _create_pending_request()

    result = approve_check_request(saved.id)

    assert result.status is CheckRequestStatus.approved
    assert _get_record_fields(sqlite_db, saved.id)["status"] == "approved"


def test_reject_pending_request(sqlite_db):
    saved = _create_pending_request()

    result = reject_check_request(saved.id)

    assert result.status is CheckRequestStatus.rejected
    assert _get_record_fields(sqlite_db, saved.id)["status"] == "rejected"


def test_approving_already_approved_request_fails(sqlite_db):
    saved = _create_pending_request()
    approve_check_request(saved.id)

    with pytest.raises(InvalidCheckRequestTransitionError):
        approve_check_request(saved.id)

    assert _get_record_fields(sqlite_db, saved.id)["status"] == "approved"


def test_rejecting_approved_request_fails(sqlite_db):
    saved = _create_pending_request()
    approve_check_request(saved.id)

    with pytest.raises(InvalidCheckRequestTransitionError):
        reject_check_request(saved.id)

    assert _get_record_fields(sqlite_db, saved.id)["status"] == "approved"


def test_approving_rejected_request_fails(sqlite_db):
    saved = _create_pending_request()
    reject_check_request(saved.id)

    with pytest.raises(InvalidCheckRequestTransitionError):
        approve_check_request(saved.id)

    assert _get_record_fields(sqlite_db, saved.id)["status"] == "rejected"


def test_rejecting_rejected_request_fails(sqlite_db):
    saved = _create_pending_request()
    reject_check_request(saved.id)

    with pytest.raises(InvalidCheckRequestTransitionError):
        reject_check_request(saved.id)

    assert _get_record_fields(sqlite_db, saved.id)["status"] == "rejected"


@pytest.mark.parametrize(
    "action",
    [approve_check_request, reject_check_request],
)
def test_processed_request_rejects_pending_transitions(sqlite_db, action):
    saved = _create_pending_request()
    _set_status(sqlite_db, saved.id, "processed")

    with pytest.raises(InvalidCheckRequestTransitionError):
        action(saved.id)

    assert _get_record_fields(sqlite_db, saved.id)["status"] == "processed"


@pytest.mark.parametrize(
    "action",
    [approve_check_request, reject_check_request],
)
def test_missing_request_raises_not_found(sqlite_db, action):
    with pytest.raises(CheckRequestNotFoundError):
        action(999999)


def test_repository_stale_expected_status_returns_none(sqlite_db):
    saved = _create_pending_request()
    _set_status(sqlite_db, saved.id, "approved")

    result = update_check_request_status(
        saved.id,
        expected_status="pending",
        new_status="rejected",
    )

    assert result is None
    assert _get_record_fields(sqlite_db, saved.id)["status"] == "approved"


def test_pending_detail_shows_action_forms(sqlite_db, client):
    saved = _create_pending_request()

    response = client.get(f"/internal/requests/{saved.id}")
    text = response.text

    assert response.status_code == 200
    assert (
        f'action="/internal/requests/{saved.id}/approve"'
        in text
    )
    assert (
        f'action="/internal/requests/{saved.id}/reject"'
        in text
    )
    assert 'method="post"' in text
    assert "Approve request" in text
    assert "Reject request" in text


def test_approved_detail_hides_action_forms(sqlite_db, client):
    saved = _create_pending_request()
    approve_check_request(saved.id)

    response = client.get(f"/internal/requests/{saved.id}")
    text = response.text

    assert response.status_code == 200
    assert (
        f'action="/internal/requests/{saved.id}/approve"'
        not in text
    )
    assert (
        f'action="/internal/requests/{saved.id}/reject"'
        not in text
    )
    assert "<strong>approved</strong>" in text


def test_rejected_detail_hides_action_forms(sqlite_db, client):
    saved = _create_pending_request()
    reject_check_request(saved.id)

    response = client.get(f"/internal/requests/{saved.id}")
    text = response.text

    assert response.status_code == 200
    assert (
        f'action="/internal/requests/{saved.id}/approve"'
        not in text
    )
    assert (
        f'action="/internal/requests/{saved.id}/reject"'
        not in text
    )
    assert "<strong>rejected</strong>" in text


def test_processed_detail_hides_action_forms(sqlite_db, client):
    saved = _create_pending_request()
    _set_status(sqlite_db, saved.id, "processed")

    response = client.get(f"/internal/requests/{saved.id}")
    text = response.text

    assert response.status_code == 200
    assert "<strong>processed</strong>" in text
    assert (
        f'action="/internal/requests/{saved.id}/approve"'
        not in text
    )
    assert (
        f'action="/internal/requests/{saved.id}/reject"'
        not in text
    )


def test_post_approve_pending_redirects_and_persists(sqlite_db, client):
    saved = _create_pending_request()

    response = client.post(
        f"/internal/requests/{saved.id}/approve",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == f"/internal/requests/{saved.id}"
    )
    assert _get_record_fields(sqlite_db, saved.id)["status"] == "approved"

    detail = client.get(f"/internal/requests/{saved.id}")
    assert detail.status_code == 200
    assert "<strong>approved</strong>" in detail.text
    assert (
        f'action="/internal/requests/{saved.id}/approve"'
        not in detail.text
    )
    assert (
        f'action="/internal/requests/{saved.id}/reject"'
        not in detail.text
    )


def test_post_reject_pending_redirects_and_persists(sqlite_db, client):
    saved = _create_pending_request()

    response = client.post(
        f"/internal/requests/{saved.id}/reject",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == f"/internal/requests/{saved.id}"
    )
    assert _get_record_fields(sqlite_db, saved.id)["status"] == "rejected"
    detail = client.get(f"/internal/requests/{saved.id}")
    assert detail.status_code == 200
    assert "<strong>rejected</strong>" in detail.text
    assert (
        f'action="/internal/requests/{saved.id}/approve"'
        not in detail.text
    )
    assert (
        f'action="/internal/requests/{saved.id}/reject"'
        not in detail.text
    )


def test_repeated_approve_redirects_with_invalid_transition(
    sqlite_db,
    client,
):
    saved = _create_pending_request()
    first = client.post(
        f"/internal/requests/{saved.id}/approve",
        follow_redirects=False,
    )
    assert first.status_code == 303

    second = client.post(
        f"/internal/requests/{saved.id}/approve",
        follow_redirects=False,
    )

    assert second.status_code == 303
    assert second.headers["location"] == (
        f"/internal/requests/{saved.id}?error=invalid_transition"
    )
    assert _get_record_fields(sqlite_db, saved.id)["status"] == "approved"

    detail = client.get(second.headers["location"])
    assert detail.status_code == 200
    assert (
        "This request is no longer pending and cannot be updated."
        in detail.text
    )


def test_conflicting_reject_after_approve_keeps_approved(
    sqlite_db,
    client,
):
    saved = _create_pending_request()
    client.post(
        f"/internal/requests/{saved.id}/approve",
        follow_redirects=False,
    )

    response = client.post(
        f"/internal/requests/{saved.id}/reject",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=invalid_transition" in response.headers["location"]
    assert _get_record_fields(sqlite_db, saved.id)["status"] == "approved"


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_unknown_request_post_returns_404(sqlite_db, client, action):
    response = client.post(
        f"/internal/requests/999999/{action}",
        follow_redirects=False,
    )

    assert response.status_code == 404


def test_actions_do_not_call_pipeline(sqlite_db, client, monkeypatch):
    pipeline = Mock()
    monkeypatch.setattr("app.main.run_company_check", pipeline)

    approve_id = _create_pending_request(company_name="Approve Pipeline Co").id
    reject_id = _create_pending_request(company_name="Reject Pipeline Co").id

    client.post(
        f"/internal/requests/{approve_id}/approve",
        follow_redirects=False,
    )
    client.post(
        f"/internal/requests/{reject_id}/reject",
        follow_redirects=False,
    )

    pipeline.assert_not_called()


def test_actions_do_not_create_company_check(sqlite_db, client):
    approve_id = _create_pending_request(company_name="Approve No Check").id
    reject_id = _create_pending_request(company_name="Reject No Check").id

    client.post(
        f"/internal/requests/{approve_id}/approve",
        follow_redirects=False,
    )
    client.post(
        f"/internal/requests/{reject_id}/reject",
        follow_redirects=False,
    )

    session = sqlite_db()
    try:
        assert session.query(CompanyCheckRecord).count() == 0
    finally:
        session.close()


def test_actions_do_not_mutate_unrelated_fields(sqlite_db):
    saved = _create_pending_request()
    original = {
        "company_name": saved.company_name,
        "country": saved.country,
        "email": saved.email,
        "website": saved.website,
        "transaction_type": saved.transaction_type,
        "additional_context": saved.additional_context,
        "preferred_language": saved.preferred_language,
        "company_check_id": saved.company_check_id,
    }

    approve_check_request(saved.id)
    approved = _get_record_fields(sqlite_db, saved.id)

    assert approved["company_name"] == original["company_name"]
    assert approved["country"] == original["country"]
    assert approved["email"] == original["email"]
    assert approved["website"] == original["website"]
    assert (
        approved["transaction_type"]
        == original["transaction_type"].value
    )
    assert (
        approved["additional_context"]
        == original["additional_context"]
    )
    assert (
        approved["preferred_language"]
        == original["preferred_language"].value
    )
    assert approved["company_check_id"] == original["company_check_id"]

    _set_status(sqlite_db, saved.id, "pending")
    reject_check_request(saved.id)
    rejected = _get_record_fields(sqlite_db, saved.id)

    assert rejected["company_name"] == original["company_name"]
    assert rejected["country"] == original["country"]
    assert rejected["email"] == original["email"]
    assert rejected["website"] == original["website"]
    assert (
        rejected["transaction_type"]
        == original["transaction_type"].value
    )
    assert (
        rejected["additional_context"]
        == original["additional_context"]
    )
    assert (
        rejected["preferred_language"]
        == original["preferred_language"].value
    )
    assert rejected["company_check_id"] == original["company_check_id"]


def test_invalid_transition_message_only_for_known_query(
    sqlite_db,
    client,
):
    saved = _create_pending_request()
    approve_check_request(saved.id)
    message = (
        "This request is no longer pending and cannot be updated."
    )

    with_error = client.get(
        f"/internal/requests/{saved.id}?error=invalid_transition"
    )
    without_error = client.get(f"/internal/requests/{saved.id}")
    unknown_error = client.get(
        f"/internal/requests/{saved.id}?error=something_else"
    )

    assert with_error.status_code == 200
    assert message in with_error.text
    assert message not in without_error.text
    assert message not in unknown_error.text
    assert "<script>" not in with_error.text
