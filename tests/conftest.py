"""Shared pytest fixtures.

Internal auth environment must be set before importing app.main so the web
application fails closed with validated operator credentials in tests.
"""

from __future__ import annotations

import os
import re
from typing import Any

# Must run before importing app.main / InternalAuthSettings.
os.environ.setdefault("INTERNAL_AUTH_USERNAME", "test-operator")
os.environ.setdefault("INTERNAL_AUTH_PASSWORD", "test-password-12")
os.environ.setdefault(
    "INTERNAL_SESSION_SECRET_KEY",
    "test-session-secret-key-32chars!",
)
os.environ.setdefault("INTERNAL_SESSION_MAX_AGE_SECONDS", "28800")
os.environ.setdefault("INTERNAL_AUTH_COOKIE_SECURE", "true")
os.environ.setdefault("APP_ENV", "test")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security.internal_auth import INTERNAL_SESSION_COOKIE


_CSRF_INPUT_RE = re.compile(
    r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']'
    r'|value=["\']([^"\']+)["\'][^>]*name=["\']csrf_token["\']',
    re.IGNORECASE,
)
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _extract_csrf_token(html: str) -> str:
    match = _CSRF_INPUT_RE.search(html)
    if match is None:
        raise AssertionError("CSRF token not found in HTML response")
    return match.group(1) or match.group(2)


def login_operator(client: TestClient) -> str:
    """POST /internal/login with env credentials; return CSRF from an internal page."""
    login = client.post(
        "/internal/login",
        data={
            "username": os.environ["INTERNAL_AUTH_USERNAME"],
            "password": os.environ["INTERNAL_AUTH_PASSWORD"],
        },
        follow_redirects=False,
    )
    assert login.status_code == 303, login.text
    assert INTERNAL_SESSION_COOKIE in login.cookies

    page = client.get("/internal/check")
    assert page.status_code == 200, page.text
    return _extract_csrf_token(page.text)


class CsrfAuthenticatedClient:
    """TestClient wrapper that logs in and auto-sends X-CSRF-Token."""

    def __init__(self, client: TestClient) -> None:
        self._client = client
        self.csrf_token = login_operator(client)

    def request(self, method: str, url: str, **kwargs: Any):
        method_upper = method.upper()
        if method_upper in _UNSAFE_METHODS:
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("X-CSRF-Token", self.csrf_token)
            kwargs["headers"] = headers
        return self._client.request(method, url, **kwargs)

    def get(self, url: str, **kwargs: Any):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any):
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any):
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any):
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs: Any):
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs: Any):
        return self.request("OPTIONS", url, **kwargs)

    @property
    def cookies(self):
        return self._client.cookies

    def __getattr__(self, name: str):
        return getattr(self._client, name)


def _make_test_client() -> TestClient:
    return TestClient(app, base_url="https://testserver")


@pytest.fixture()
def unauthenticated_client():
    """Provide a TestClient without an operator session."""
    return _make_test_client()


@pytest.fixture()
def authenticated_client_no_csrf():
    """Authenticated client that does not auto-attach CSRF headers."""
    client = _make_test_client()
    login_operator(client)
    return client


@pytest.fixture()
def client():
    """Provide an authenticated TestClient with automatic CSRF headers."""
    return CsrfAuthenticatedClient(_make_test_client())
