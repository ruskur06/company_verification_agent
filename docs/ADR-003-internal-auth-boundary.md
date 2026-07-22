# ADR-003: Internal Operator Authentication Boundary

- Status: Accepted for implementation
- Date: 2026-07-22
- Scope: Protect the complete operator/API surface with a default-deny auth boundary
- Depends on: existing FastAPI `app.main` route surface (no router refactor in this change)
- Review status: Security milestone before further internal functionality

## 1. Context

Internal operator pages, company-check APIs, and OpenAPI docs were reachable without authentication. Commit 6 reconciliation UI explicitly deferred access control. Before adding more internal capability, the operator surface must fail closed.

Public traffic that must remain open:

- health and marketing/landing pages;
- localized public request-check forms;
- static assets;
- operator login endpoints only.

Everything else requires a valid operator session.

## 2. Decision

### Public allowlist / default deny

Enforce an exact public allowlist in central ASGI middleware. Any path or method not listed is protected, including future routes unless explicitly allowlisted.

### Signed session

Use a signed, time-limited cookie session via `itsdangerous.URLSafeTimedSerializer`.

Payload contains only:

- fixed subject `operator`;
- `issued_at` unix timestamp;
- random CSRF token.

Username/password never enter the cookie. One operator identity is loaded from environment variables (`InternalAuthSettings`), not from a database.

### Middleware as load-bearing enforcement

Authentication and CSRF checks run in middleware before route/business logic. Auth failure returns 303 (safe methods) or 401 (unsafe methods) without invoking handlers. CSRF failure returns 403.

### CSRF

Authenticated `POST`/`PUT`/`PATCH`/`DELETE` must present the session CSRF token through:

1. `X-CSRF-Token` header; or
2. hidden `csrf_token` form field for `application/x-www-form-urlencoded` bodies.

Public `POST /{language}/request-check` and `POST /internal/login` are exempt. SameSite alone is not treated as sufficient CSRF defense.

### Fixed expiry

Sessions use a fixed max age with no sliding refresh. Each authenticated response does not re-issue a renewed cookie.

### Stateless logout limitation

`POST /internal/logout` deletes the browser cookie. A copied cookie value remains cryptographically valid until expiry. Global invalidation requires rotating `INTERNAL_SESSION_SECRET_KEY`.

### Deployment requirement

`INTERNAL_AUTH_COOKIE_SECURE` defaults to true and may be false only when `APP_ENV` is `development` or `test`. Production deployments must serve operator traffic over HTTPS so Secure cookies are sent.

## 3. Deferred

- Moving routes onto separate APIRouters;
- database-backed multi-user accounts;
- JWT/OAuth providers;
- server-side session revocation lists.

## 4. Consequences

- Unrelated CLI/config imports can continue using global `Settings` without auth env vars.
- The web app instantiates `InternalAuthSettings` at import/startup and fails closed when required auth config is missing or invalid.
- Operator HTML forms must include CSRF tokens; JSON/API clients must send `X-CSRF-Token`.
