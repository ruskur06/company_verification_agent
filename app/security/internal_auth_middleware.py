"""ASGI middleware enforcing the internal auth boundary and CSRF checks."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.security.internal_auth import (
    INTERNAL_SESSION_COOKIE,
    InternalAuthService,
    csrf_tokens_match,
    extract_csrf_candidate,
    is_public_request,
    read_request_body,
    replay_receive,
    request_csrf_headers,
)

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class InternalAuthMiddleware:
    """Default-deny operator auth with CSRF for authenticated unsafe requests."""

    def __init__(self, app: ASGIApp, auth_service: InternalAuthService) -> None:
        self.app = app
        self.auth_service = auth_service

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        method = request.method.upper()
        path = request.url.path

        if is_public_request(method, path):
            await self.app(scope, receive, send)
            return

        session = self.auth_service.load_session(
            request.cookies.get(INTERNAL_SESSION_COOKIE)
        )
        if session is None:
            response = self._unauthenticated_response(method)
            await response(scope, receive, send)
            return

        scope.setdefault("state", {})
        request.state.internal_session = session

        body = b""
        effective_receive = receive
        if method in _UNSAFE_METHODS:
            body = await read_request_body(receive)
            effective_receive = replay_receive(body)
            try:
                provided = extract_csrf_candidate(
                    headers=request_csrf_headers(request),
                    content_type=request.headers.get("content-type"),
                    body=body,
                )
            except ValueError:
                response = self._forbidden_response()
                await response(scope, effective_receive, send)
                return
            if not csrf_tokens_match(session.csrf_token, provided):
                response = self._forbidden_response()
                await response(scope, effective_receive, send)
                return

        await self.app(
            scope,
            effective_receive,
            _no_store_send(send),
        )

    def _unauthenticated_response(self, method: str) -> Response:
        if method in {"GET", "HEAD"}:
            response: Response = RedirectResponse(
                url="/internal/login",
                status_code=303,
            )
        else:
            response = PlainTextResponse("Unauthorized", status_code=401)
        response.headers["Cache-Control"] = "no-store"
        return response

    def _forbidden_response(self) -> Response:
        response = PlainTextResponse("Forbidden", status_code=403)
        response.headers["Cache-Control"] = "no-store"
        return response


def _no_store_send(send: Send) -> Send:
    async def send_wrapper(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = MutableHeaders(scope=message)
            headers["cache-control"] = "no-store"
        await send(message)

    return send_wrapper
