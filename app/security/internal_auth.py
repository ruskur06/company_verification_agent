"""Signed cookie session and CSRF helpers for internal operator auth."""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qs

from itsdangerous import (
    BadPayload,
    BadSignature,
    BadTimeSignature,
    SignatureExpired,
)
from itsdangerous.url_safe import URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import InternalAuthSettings

INTERNAL_SESSION_COOKIE = "cva_operator_session"
INTERNAL_SESSION_SUBJECT = "operator"
_CSRF_HEADER = "x-csrf-token"
_CSRF_FORM_FIELD = "csrf_token"
_SERIALIZER_SALT = "cva-internal-operator-session"
_EXPECTED_TOKEN_ERRORS = (
    BadSignature,
    SignatureExpired,
    BadTimeSignature,
    BadPayload,
)


@dataclass(frozen=True)
class InternalSession:
    """Validated operator session payload carried in the signed cookie."""

    subject: str
    issued_at: int
    csrf_token: str


def _constant_time_str_eq(left: str, right: str) -> bool:
    """Compare Unicode strings as UTF-8 bytes without raising TypeError."""
    return secrets.compare_digest(
        left.encode("utf-8"),
        right.encode("utf-8"),
    )


class InternalAuthService:
    """Create, verify, and clear signed operator sessions."""

    def __init__(self, settings: InternalAuthSettings) -> None:
        self._settings = settings
        self._serializer = URLSafeTimedSerializer(
            secret_key=settings.internal_session_secret_key.get_secret_value(),
            salt=_SERIALIZER_SALT,
            signer_kwargs={"digest_method": hashlib.sha256},
        )

    @property
    def settings(self) -> InternalAuthSettings:
        return self._settings

    def create_session(self) -> tuple[str, InternalSession]:
        """Return a signed cookie value and the matching session payload."""
        session = InternalSession(
            subject=INTERNAL_SESSION_SUBJECT,
            issued_at=int(time.time()),
            csrf_token=secrets.token_urlsafe(32),
        )
        token = self._serializer.dumps(
            {
                "sub": session.subject,
                "iat": session.issued_at,
                "csrf": session.csrf_token,
            }
        )
        return token, session

    def load_session(self, cookie_value: str | None) -> InternalSession | None:
        """Return a valid session or None for missing/invalid/expired cookies."""
        if cookie_value is None or cookie_value == "":
            return None
        try:
            payload = self._serializer.loads(
                cookie_value,
                max_age=self._settings.internal_session_max_age_seconds,
            )
        except _EXPECTED_TOKEN_ERRORS:
            return None

        try:
            session = _session_from_payload(payload)
        except ValueError:
            return None

        age = int(time.time()) - session.issued_at
        if age < 0 or age > self._settings.internal_session_max_age_seconds:
            return None
        return session

    def verify_credentials(self, username: str, password: str) -> bool:
        """Constant-time compare against configured operator credentials."""
        expected_username = self._settings.internal_auth_username
        expected_password = (
            self._settings.internal_auth_password.get_secret_value()
        )
        username_ok = _constant_time_str_eq(username, expected_username)
        password_ok = _constant_time_str_eq(password, expected_password)
        return username_ok and password_ok

    def apply_session_cookie(self, response: Response, token: str) -> None:
        response.set_cookie(
            key=INTERNAL_SESSION_COOKIE,
            value=token,
            max_age=self._settings.internal_session_max_age_seconds,
            httponly=True,
            secure=self._settings.internal_auth_cookie_secure,
            samesite="strict",
            path="/",
        )

    def clear_session_cookie(self, response: Response) -> None:
        response.delete_cookie(
            key=INTERNAL_SESSION_COOKIE,
            path="/",
            httponly=True,
            secure=self._settings.internal_auth_cookie_secure,
            samesite="strict",
        )


def _session_from_payload(payload: Any) -> InternalSession:
    if not isinstance(payload, Mapping):
        raise ValueError("session payload must be a mapping")
    subject = payload.get("sub")
    issued_at = payload.get("iat")
    csrf_token = payload.get("csrf")
    if subject != INTERNAL_SESSION_SUBJECT:
        raise ValueError("invalid session subject")
    if not isinstance(issued_at, int) or isinstance(issued_at, bool):
        raise ValueError("invalid session issued_at")
    if not isinstance(csrf_token, str) or csrf_token.strip() == "":
        raise ValueError("invalid session csrf token")
    if len(payload) != 3:
        raise ValueError("session payload contains unexpected fields")
    return InternalSession(
        subject=subject,
        issued_at=issued_at,
        csrf_token=csrf_token,
    )


def extract_csrf_candidate(
    *,
    headers: Mapping[str, str],
    content_type: str | None,
    body: bytes,
) -> str | None:
    """Return a CSRF token from header or urlencoded form body."""
    header_token = headers.get(_CSRF_HEADER)
    if header_token is not None and header_token != "":
        return header_token

    if content_type is None:
        return None
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/x-www-form-urlencoded":
        return None
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError as exc:
        raise ValueError("malformed form body") from exc
    values = parsed.get(_CSRF_FORM_FIELD)
    if not values:
        return None
    return values[0]


def csrf_tokens_match(expected: str, provided: str | None) -> bool:
    if provided is None or provided == "":
        return False
    return _constant_time_str_eq(expected, provided)


def is_public_request(method: str, path: str) -> bool:
    """Return True only for the explicit public allowlist."""
    method = method.upper()
    if path == "/static" or path.startswith("/static/"):
        return method in {"GET", "HEAD"}
    if method == "GET" and path in {"/", "/health", "/en", "/de", "/es"}:
        return True
    if method in {"GET", "POST"} and path in {
        "/en/request-check",
        "/de/request-check",
        "/es/request-check",
        "/internal/login",
    }:
        return True
    return False


async def read_request_body(receive) -> bytes:
    """Read the full HTTP request body from an ASGI receive callable."""
    body = bytearray()
    while True:
        message = await receive()
        message_type = message["type"]
        if message_type == "http.disconnect":
            return bytes(body)
        if message_type != "http.request":
            raise RuntimeError(
                f"unexpected ASGI message type while reading body: "
                f"{message_type}"
            )
        body.extend(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return bytes(body)


def replay_receive(body: bytes):
    """Return an ASGI receive callable that replays a buffered body once."""
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }
        return {"type": "http.disconnect"}

    return receive


def request_csrf_headers(request: Request) -> dict[str, str]:
    return {key.lower(): value for key, value in request.headers.items()}
