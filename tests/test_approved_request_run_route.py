"""Focused tests for POST /internal/requests/{request_id}/run."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.db.repositories import ApprovedRequestPersistenceFenceError
from app.main import app
from app.schemas.approved_request_persistence import PersistedApprovedRequestCheck
from app.schemas.check_request import CheckRequestStatus
from app.services.approved_request_pipeline_service import (
    PreparedCheckValidationError,
)
from app.services.check_request_service import (
    CheckRequestNotFoundError,
    InvalidCheckRequestTransitionError,
)
from tests.conftest import CsrfAuthenticatedClient


REQUEST_ID = 42
COMPANY_CHECK_ID = "1782245999999"
RUN_URL = f"/internal/requests/{REQUEST_ID}/run"


def _persisted_result() -> PersistedApprovedRequestCheck:
    return PersistedApprovedRequestCheck(
        source_check_request_id=REQUEST_ID,
        company_check_id=COMPANY_CHECK_ID,
        status=CheckRequestStatus.processed,
    )


def _authenticated_client(**kwargs) -> CsrfAuthenticatedClient:
    return CsrfAuthenticatedClient(
        TestClient(app, base_url="https://testserver", **kwargs)
    )


def test_successful_run_redirects_to_result(client, monkeypatch):
    orchestration = MagicMock(return_value=_persisted_result())
    monkeypatch.setattr("app.main.run_approved_request_check", orchestration)

    response = client.post(RUN_URL, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/internal/result/{COMPANY_CHECK_ID}"
    orchestration.assert_called_once_with(REQUEST_ID)


def test_missing_request_returns_404(client, monkeypatch):
    internal_message = "secret missing request details"
    orchestration = MagicMock(
        side_effect=CheckRequestNotFoundError(internal_message)
    )
    monkeypatch.setattr("app.main.run_approved_request_check", orchestration)

    response = client.post(RUN_URL, follow_redirects=False)

    assert response.status_code == 404
    assert internal_message not in response.text
    orchestration.assert_called_once_with(REQUEST_ID)


def test_invalid_transition_returns_409(client, monkeypatch):
    internal_message = "secret invalid transition details"
    orchestration = MagicMock(
        side_effect=InvalidCheckRequestTransitionError(internal_message)
    )
    monkeypatch.setattr("app.main.run_approved_request_check", orchestration)

    response = client.post(RUN_URL, follow_redirects=False)

    assert response.status_code == 409
    assert response.headers.get("location") is None
    assert internal_message not in response.text
    assert "Check request cannot be run from its current state." in response.text
    assert "invalid_transition" not in response.text
    orchestration.assert_called_once_with(REQUEST_ID)


@pytest.mark.parametrize(
    "server_error",
    [
        RuntimeError("pipeline internal details"),
        PreparedCheckValidationError("validation internal details"),
    ],
)
def test_pipeline_or_validation_exception_is_server_error(
    monkeypatch,
    server_error: Exception,
):
    orchestration = MagicMock(side_effect=server_error)
    monkeypatch.setattr("app.main.run_approved_request_check", orchestration)

    with pytest.raises(type(server_error)) as exc_info:
        client = _authenticated_client()
        client.post(RUN_URL, follow_redirects=False)

    assert exc_info.value is server_error

    no_raise_client = _authenticated_client(raise_server_exceptions=False)
    response = no_raise_client.post(RUN_URL, follow_redirects=False)

    assert response.status_code == 500
    assert str(server_error) not in response.text
    assert response.status_code not in {303, 404, 409}


def test_fencing_exception_is_server_error(monkeypatch):
    fence_error = ApprovedRequestPersistenceFenceError(
        "fencing internal details",
        source_check_request_id=REQUEST_ID,
        processing_check_id=COMPANY_CHECK_ID,
    )
    orchestration = MagicMock(side_effect=fence_error)
    monkeypatch.setattr("app.main.run_approved_request_check", orchestration)

    with pytest.raises(ApprovedRequestPersistenceFenceError) as exc_info:
        client = _authenticated_client()
        client.post(RUN_URL, follow_redirects=False)

    assert exc_info.value is fence_error

    no_raise_client = _authenticated_client(raise_server_exceptions=False)
    response = no_raise_client.post(RUN_URL, follow_redirects=False)

    assert response.status_code == 500
    assert "fencing internal details" not in response.text
    assert response.status_code not in {303, 404, 409}


def test_run_route_does_not_call_legacy_pipeline(client, monkeypatch):
    orchestration = MagicMock(return_value=_persisted_result())
    legacy_pipeline = MagicMock(
        side_effect=AssertionError("legacy pipeline must not run")
    )
    monkeypatch.setattr("app.main.run_approved_request_check", orchestration)
    monkeypatch.setattr("app.main.run_company_check", legacy_pipeline)

    response = client.post(RUN_URL, follow_redirects=False)

    assert response.status_code == 303
    orchestration.assert_called_once_with(REQUEST_ID)
    legacy_pipeline.assert_not_called()
