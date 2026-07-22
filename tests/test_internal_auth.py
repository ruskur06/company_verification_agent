"""Focused tests for internal operator authentication and CSRF."""

from __future__ import annotations

import os
import re
import time
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from app.core.config import InternalAuthSettings, Settings
from app.db import database
from app.main import app, internal_auth_service
from app.schemas.check_request import CheckRequestCreate, CheckRequestLanguage
from app.security.internal_auth import (
    INTERNAL_SESSION_COOKIE,
    InternalAuthService,
)
from app.services.check_request_service import create_check_request


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'internal_auth.db'}"
    database.configure_engine(database_url)
    database.init_db()
    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=database.engine,
    )
    monkeypatch.setattr("app.db.repositories.SessionLocal", session_factory)
    yield session_factory
    database.engine.dispose()


def _middleware_auth_service():
    middleware_app = app.middleware_stack
    while middleware_app is not None and not hasattr(
        middleware_app,
        "auth_service",
    ):
        middleware_app = getattr(middleware_app, "app", None)
    assert middleware_app is not None
    return middleware_app


def test_internal_auth_settings_missing_required(monkeypatch):
    monkeypatch.delenv("INTERNAL_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("INTERNAL_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("INTERNAL_SESSION_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        InternalAuthSettings(_env_file=None)


def test_internal_auth_settings_blank_and_short_values():
    with pytest.raises(ValidationError):
        InternalAuthSettings(
            internal_auth_username="   ",
            internal_auth_password="long-enough-pass",
            internal_session_secret_key="x" * 32,
            app_env="test",
        )
    with pytest.raises(ValidationError):
        InternalAuthSettings(
            internal_auth_username="operator",
            internal_auth_password="short",
            internal_session_secret_key="x" * 32,
            app_env="test",
        )
    with pytest.raises(ValidationError):
        InternalAuthSettings(
            internal_auth_username="operator",
            internal_auth_password="long-enough-pass",
            internal_session_secret_key="too-short",
            app_env="test",
        )


def test_cookie_secure_false_rejected_outside_dev_test():
    with pytest.raises(ValidationError):
        InternalAuthSettings(
            internal_auth_username="operator",
            internal_auth_password="long-enough-pass",
            internal_session_secret_key="x" * 32,
            internal_auth_cookie_secure=False,
            app_env="production",
        )


def test_cookie_secure_false_allowed_in_test():
    settings = InternalAuthSettings(
        internal_auth_username="operator",
        internal_auth_password="long-enough-pass",
        internal_session_secret_key="x" * 32,
        internal_auth_cookie_secure=False,
        app_env="test",
    )
    assert settings.internal_auth_cookie_secure is False


def test_general_settings_importable_without_auth_env(monkeypatch):
    monkeypatch.delenv("INTERNAL_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("INTERNAL_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("INTERNAL_SESSION_SECRET_KEY", raising=False)
    loaded = Settings()
    assert isinstance(loaded.database_url, str)


def test_public_health_and_landing_remain_public(unauthenticated_client):
    assert unauthenticated_client.get("/health").status_code == 200
    assert unauthenticated_client.get("/en").status_code == 200
    assert unauthenticated_client.get("/en/request-check").status_code == 200
    assert unauthenticated_client.get("/static/style.css").status_code == 200


def test_public_request_creation_without_session(sqlite_db, unauthenticated_client):
    response = unauthenticated_client.post(
        "/en/request-check",
        data={
            "company_name": "Public Auth Co",
            "country": "Austria",
            "email": "public@example.com",
            "preferred_language": "en",
        },
    )
    assert response.status_code == 200
    assert 'class="request-success"' in response.text

    session = sqlite_db()
    try:
        from app.db.models import CheckRequestRecord

        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.company_name == "Public Auth Co")
            .one()
        )
        assert record.email == "public@example.com"
    finally:
        session.close()


def test_protected_get_redirects_to_login(unauthenticated_client):
    response = unauthenticated_client.get(
        "/internal/requests",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/internal/login"
    assert response.headers.get("cache-control") == "no-store"


def test_protected_unsafe_without_session_is_401(unauthenticated_client):
    response = unauthenticated_client.post("/internal/run-check", data={})
    assert response.status_code == 401
    assert response.headers.get("cache-control") == "no-store"


def test_company_check_api_and_docs_are_protected(unauthenticated_client):
    assert (
        unauthenticated_client.post(
            "/company-check",
            json={"company_name": "X", "country": "Austria"},
        ).status_code
        == 401
    )
    assert unauthenticated_client.get(
        "/api/checks",
        follow_redirects=False,
    ).status_code == 303
    assert unauthenticated_client.get(
        "/docs",
        follow_redirects=False,
    ).status_code == 303
    assert unauthenticated_client.get(
        "/openapi.json",
        follow_redirects=False,
    ).status_code == 303
    assert unauthenticated_client.get(
        "/redoc",
        follow_redirects=False,
    ).status_code == 303


def test_valid_session_allows_internal_page(client):
    response = client.get("/internal/check")
    assert response.status_code == 200
    assert "Company Verification Agent" in response.text
    assert "csrf_token" in response.text


def test_valid_login_sets_cookie_flags(unauthenticated_client):
    response = unauthenticated_client.post(
        "/internal/login",
        data={
            "username": os.environ["INTERNAL_AUTH_USERNAME"],
            "password": os.environ["INTERNAL_AUTH_PASSWORD"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/internal/check"
    set_cookie = response.headers.get("set-cookie", "")
    assert INTERNAL_SESSION_COOKIE in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "samesite=strict" in set_cookie.lower()
    assert "Path=/" in set_cookie
    assert (
        f"Max-Age={internal_auth_service.settings.internal_session_max_age_seconds}"
        in set_cookie
        or f"max-age={internal_auth_service.settings.internal_session_max_age_seconds}"
        in set_cookie.lower()
    )


def test_invalid_login_is_401_without_cookie(unauthenticated_client):
    response = unauthenticated_client.post(
        "/internal/login",
        data={"username": "wrong", "password": "wrong-password"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert INTERNAL_SESSION_COOKIE not in response.cookies
    assert "Invalid username or password." in response.text
    assert os.environ["INTERNAL_AUTH_PASSWORD"] not in response.text


def test_tampered_cookie_rejected(unauthenticated_client):
    unauthenticated_client.cookies.set(
        INTERNAL_SESSION_COOKIE,
        "not-a-valid-signed-session",
        domain="testserver",
        path="/",
    )
    response = unauthenticated_client.get(
        "/internal/check",
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_expired_cookie_rejected(unauthenticated_client):
    middleware = _middleware_auth_service()
    original = middleware.auth_service
    short = InternalAuthService(
        InternalAuthSettings(
            internal_auth_username=os.environ["INTERNAL_AUTH_USERNAME"],
            internal_auth_password=os.environ["INTERNAL_AUTH_PASSWORD"],
            internal_session_secret_key=os.environ[
                "INTERNAL_SESSION_SECRET_KEY"
            ],
            internal_session_max_age_seconds=1,
            internal_auth_cookie_secure=True,
            app_env="test",
        )
    )
    middleware.auth_service = short
    try:
        token, _ = short.create_session()
        unauthenticated_client.cookies.set(
            INTERNAL_SESSION_COOKIE,
            token,
            domain="testserver",
            path="/",
        )
        time.sleep(1.1)
        response = unauthenticated_client.get(
            "/internal/check",
            follow_redirects=False,
        )
        assert response.status_code == 303
    finally:
        middleware.auth_service = original


def test_malformed_payload_rejected(unauthenticated_client):
    bad = internal_auth_service._serializer.dumps({"sub": "operator"})
    unauthenticated_client.cookies.set(
        INTERNAL_SESSION_COOKIE,
        bad,
        domain="testserver",
        path="/",
    )
    response = unauthenticated_client.get(
        "/internal/check",
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_fixed_expiry_does_not_slide(client, sqlite_db):
    first = client.get("/internal/check")
    assert first.status_code == 200
    assert "set-cookie" not in {k.lower() for k in first.headers.keys()}
    second = client.get("/internal/requests")
    assert second.status_code == 200
    assert "set-cookie" not in {k.lower() for k in second.headers.keys()}


def test_logout_deletes_cookie_and_denies_access(client):
    assert client.get("/internal/check").status_code == 200
    logout = client.post("/internal/logout", follow_redirects=False)
    assert logout.status_code == 303
    assert logout.headers["location"] == "/internal/login"
    denied = client.get("/internal/check", follow_redirects=False)
    assert denied.status_code == 303


def test_csrf_missing_token_forbidden(authenticated_client_no_csrf):
    response = authenticated_client_no_csrf.post(
        "/internal/run-check",
        data={"company_name": "A", "country": "Austria"},
    )
    assert response.status_code == 403


def test_csrf_wrong_token_forbidden(authenticated_client_no_csrf):
    response = authenticated_client_no_csrf.post(
        "/internal/run-check",
        data={
            "company_name": "A",
            "country": "Austria",
            "csrf_token": "not-the-session-token",
        },
    )
    assert response.status_code == 403


def test_csrf_token_from_other_session_forbidden(
    authenticated_client_no_csrf,
    unauthenticated_client,
):
    other_login = unauthenticated_client.post(
        "/internal/login",
        data={
            "username": os.environ["INTERNAL_AUTH_USERNAME"],
            "password": os.environ["INTERNAL_AUTH_PASSWORD"],
        },
        follow_redirects=False,
    )
    assert other_login.status_code == 303
    other_page = unauthenticated_client.get("/internal/check")
    match = re.search(
        r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']',
        other_page.text,
    )
    assert match is not None
    response = authenticated_client_no_csrf.post(
        "/internal/run-check",
        data={
            "company_name": "A",
            "country": "Austria",
            "csrf_token": match.group(1),
        },
    )
    assert response.status_code == 403


def test_csrf_valid_header_token_works(client, sqlite_db):
    response = client.post(
        "/internal/run-check",
        data={"company_name": "Header Co", "country": "Austria"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_csrf_valid_form_token_works(authenticated_client_no_csrf, sqlite_db):
    page = authenticated_client_no_csrf.get("/internal/check")
    match = re.search(
        r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']',
        page.text,
    )
    assert match is not None
    response = authenticated_client_no_csrf.post(
        "/internal/run-check",
        data={
            "company_name": "Form Co",
            "country": "Austria",
            "csrf_token": match.group(1),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_auth_failure_happens_before_service(unauthenticated_client, monkeypatch):
    mocked = MagicMock(side_effect=AssertionError("service must not run"))
    monkeypatch.setattr("app.main.run_company_check", mocked)
    response = unauthenticated_client.post(
        "/internal/run-check",
        data={"company_name": "A", "country": "Austria"},
    )
    assert response.status_code == 401
    mocked.assert_not_called()


def test_csrf_failure_happens_before_service(
    authenticated_client_no_csrf,
    monkeypatch,
):
    mocked = MagicMock(side_effect=AssertionError("service must not run"))
    monkeypatch.setattr("app.main.run_company_check", mocked)
    response = authenticated_client_no_csrf.post(
        "/internal/run-check",
        data={"company_name": "A", "country": "Austria"},
    )
    assert response.status_code == 403
    mocked.assert_not_called()


def test_public_posts_do_not_require_csrf(sqlite_db, unauthenticated_client):
    response = unauthenticated_client.post(
        "/en/request-check",
        data={
            "company_name": "No CSRF Public",
            "country": "Austria",
            "email": "nocsrf@example.com",
            "preferred_language": "en",
        },
    )
    assert response.status_code == 200
    login = unauthenticated_client.post(
        "/internal/login",
        data={
            "username": os.environ["INTERNAL_AUTH_USERNAME"],
            "password": os.environ["INTERNAL_AUTH_PASSWORD"],
        },
        follow_redirects=False,
    )
    assert login.status_code == 303


def test_unexpected_auth_exception_propagates(
    unauthenticated_client,
    monkeypatch,
):
    middleware = _middleware_auth_service()
    monkeypatch.setattr(
        middleware.auth_service,
        "load_session",
        MagicMock(side_effect=RuntimeError("serializer boom")),
    )
    with pytest.raises(RuntimeError, match="serializer boom"):
        unauthenticated_client.get("/internal/check")


def test_login_page_noindex_and_templates_have_csrf(
    client,
    unauthenticated_client,
    sqlite_db,
):
    login_page = unauthenticated_client.get("/internal/login")
    assert login_page.status_code == 200
    assert 'content="noindex, nofollow"' in login_page.text
    assert os.environ["INTERNAL_AUTH_PASSWORD"] not in login_page.text

    internal = client.get("/internal/requests")
    assert 'name="csrf_token"' in internal.text
    assert 'action="/internal/logout"' in internal.text
    assert (
        'method="post"' in internal.text.lower()
        or "method='post'" in internal.text.lower()
    )


def test_operator_actions_still_work_with_csrf(sqlite_db, client):
    saved = create_check_request(
        CheckRequestCreate(
            company_name="Auth Flow Co",
            country="Austria",
            email="authflow@example.com",
            preferred_language=CheckRequestLanguage.en,
        )
    )
    response = client.post(
        f"/internal/requests/{saved.id}/approve",
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_non_ascii_wrong_credentials_return_401(unauthenticated_client):
    response = unauthenticated_client.post(
        "/internal/login",
        data={
            "username": "оператор",
            "password": "пароль-неверный",
        },
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert INTERNAL_SESSION_COOKIE not in response.cookies
    assert "оператор" not in response.text
    assert "пароль-неверный" not in response.text


def test_non_ascii_wrong_csrf_returns_403(authenticated_client_no_csrf):
    response = authenticated_client_no_csrf.post(
        "/internal/run-check",
        data={
            "company_name": "A",
            "country": "Austria",
            "csrf_token": "токен-неверный",
        },
    )
    assert response.status_code == 403


def test_bad_payload_treated_as_unauthenticated(unauthenticated_client, monkeypatch):
    from itsdangerous import BadPayload

    middleware = _middleware_auth_service()
    monkeypatch.setattr(
        middleware.auth_service._serializer,
        "loads",
        MagicMock(side_effect=BadPayload("bad payload")),
    )
    unauthenticated_client.cookies.set(
        INTERNAL_SESSION_COOKIE,
        "signed-but-unloadable",
        domain="testserver",
        path="/",
    )
    response = unauthenticated_client.get(
        "/internal/check",
        follow_redirects=False,
    )
    assert response.status_code == 303


@pytest.mark.asyncio
async def test_read_request_body_returns_on_disconnect():
    from app.security.internal_auth import read_request_body

    calls = {"n": 0}

    async def receive():
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "type": "http.request",
                "body": b"partial",
                "more_body": True,
            }
        if calls["n"] == 2:
            return {"type": "http.disconnect"}
        raise AssertionError("read_request_body looped after disconnect")

    body = await read_request_body(receive)
    assert body == b"partial"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_read_request_body_rejects_unexpected_message_type():
    from app.security.internal_auth import read_request_body

    async def receive():
        return {"type": "http.response.start"}

    with pytest.raises(RuntimeError, match="unexpected ASGI message type"):
        await read_request_body(receive)


def test_static_get_head_public_post_denied(unauthenticated_client):
    assert unauthenticated_client.get("/static/style.css").status_code == 200
    assert unauthenticated_client.head("/static/style.css").status_code == 200
    assert (
        unauthenticated_client.post("/static/style.css").status_code == 401
    )
    assert unauthenticated_client.put("/static/style.css").status_code == 401
    assert (
        unauthenticated_client.patch("/static/style.css").status_code == 401
    )
    assert (
        unauthenticated_client.delete("/static/style.css").status_code == 401
    )


def test_omitted_app_env_rejects_insecure_cookies(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(ValidationError):
        InternalAuthSettings(
            internal_auth_username="operator",
            internal_auth_password="long-enough-pass",
            internal_session_secret_key="x" * 32,
            internal_auth_cookie_secure=False,
            _env_file=None,
        )


def test_serializer_uses_sha256():
    import hashlib

    signer = internal_auth_service._serializer.make_signer()
    assert signer.digest_method is hashlib.sha256


def test_malformed_utf8_login_body_does_not_return_500(unauthenticated_client):
    response = unauthenticated_client.post(
        "/internal/login",
        content=b"username=\xff\xfe&password=test",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert INTERNAL_SESSION_COOKIE not in response.cookies
    assert response.headers.get("cache-control") == "no-store"
    assert "UnicodeDecodeError" not in response.text
    assert "codec can't decode" not in response.text.lower()
    assert "\xff" not in response.text
