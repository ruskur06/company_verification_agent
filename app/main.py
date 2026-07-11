"""FastAPI application entrypoint."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.api.routes import router
from app.db.database import init_db
from app.schemas.check_request import CheckRequestCreate
from app.services.check_request_service import create_check_request
from app.services.public_request_guard import (
    PUBLIC_REQUEST_MAX_BODY_BYTES,
    PUBLIC_REQUEST_RATE_WINDOW_SECONDS,
    public_request_rate_limiter,
)
from app.services.company_check_service import (
    list_checks_from_db,
    load_company_check,
    run_company_check,
)

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"
TRANSLATIONS_DIR = WEB_DIR / "translations"
SUPPORTED_PUBLIC_LANGUAGES = frozenset({"en", "de", "es"})


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Company Verification Agent",
    description="Simple AI-assisted company verification MVP.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _load_landing_copy(language: str) -> dict[str, str]:
    """Load one landing-page translation file."""
    translation_path = TRANSLATIONS_DIR / f"{language}.json"

    with translation_path.open(encoding="utf-8") as translation_file:
        copy = json.load(translation_file)

    if not isinstance(copy, dict):
        raise RuntimeError(
            f"Landing translation {translation_path} must contain a JSON object."
        )

    return copy


def _render_landing(request: Request, language: str) -> HTMLResponse:
    """Render the shared landing-page template in one supported language."""
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={
            "language": language,
            "copy": _load_landing_copy(language),
        },
    )



def _require_supported_public_language(
    language: str,
) -> str:
    """Return a supported public language or raise 404."""
    if language not in SUPPORTED_PUBLIC_LANGUAGES:
        raise HTTPException(
            status_code=404,
            detail="Language not supported",
        )

    return language


def _form_value(
    form_data: dict[str, list[str]],
    field_name: str,
) -> str:
    """Read one HTML form value safely."""
    return form_data.get(field_name, [""])[0]


async def _read_limited_public_request_body(
    request: Request,
) -> bytes | None:
    """Read a public form body up to the configured limit."""
    content_length = request.headers.get(
        "content-length"
    )

    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = None

        if (
            declared_size is not None
            and declared_size
            > PUBLIC_REQUEST_MAX_BODY_BYTES
        ):
            return None

    body = bytearray()

    async for chunk in request.stream():
        if (
            len(body) + len(chunk)
            > PUBLIC_REQUEST_MAX_BODY_BYTES
        ):
            return None

        body.extend(chunk)

    return bytes(body)


def _public_request_client_key(
    request: Request,
) -> str:
    """Return the direct client address for rate limiting."""
    if request.client is None:
        return "unknown"

    return request.client.host


def _render_check_request(
    request: Request,
    language: str,
    *,
    form_values: dict[str, str] | None = None,
    error: str | None = None,
    submitted: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the localized public request page."""
    language = _require_supported_public_language(
        language
    )

    return templates.TemplateResponse(
        request=request,
        name="request_check.html",
        context={
            "language": language,
            "copy": _load_landing_copy(language),
            "form_values": form_values or {},
            "error": error,
            "submitted": submitted,
        },
        status_code=status_code,
    )


@app.get("/")
def landing_root() -> RedirectResponse:
    """Redirect the root URL to the default English landing page."""
    return RedirectResponse(url="/en", status_code=307)


@app.get(
    "/robots.txt",
    response_class=PlainTextResponse,
    include_in_schema=False,
)
def robots_txt() -> PlainTextResponse:
    """Tell search engines not to index the internal web interface."""
    return PlainTextResponse(
        "User-agent: *\n"
        "Disallow: /internal/\n"
    )


@app.get("/en", response_class=HTMLResponse)
def landing_english(request: Request) -> HTMLResponse:
    """Show the English landing page."""
    return _render_landing(request, "en")


@app.get("/de", response_class=HTMLResponse)
def landing_german(request: Request) -> HTMLResponse:
    """Show the German landing page."""
    return _render_landing(request, "de")


@app.get("/es", response_class=HTMLResponse)
def landing_spanish(request: Request) -> HTMLResponse:
    """Show the Spanish landing page."""
    return _render_landing(request, "es")



@app.get(
    "/{language}/request-check",
    response_class=HTMLResponse,
)
def request_check_form(
    request: Request,
    language: str,
) -> HTMLResponse:
    """Show the localized public request form."""
    return _render_check_request(
        request,
        language,
    )


@app.post(
    "/{language}/request-check",
    response_class=HTMLResponse,
)
async def submit_check_request_form(
    request: Request,
    language: str,
) -> HTMLResponse:
    """Save a request without running verification."""
    language = _require_supported_public_language(
        language
    )

    copy = _load_landing_copy(language)

    body = await _read_limited_public_request_body(
        request
    )

    if body is None:
        return _render_check_request(
            request,
            language,
            error=copy["request_too_large"],
            status_code=413,
        )

    try:
        decoded_body = body.decode("utf-8")
    except UnicodeDecodeError:
        return _render_check_request(
            request,
            language,
            error=copy["request_error"],
            status_code=422,
        )

    form_data = parse_qs(
        decoded_body,
        keep_blank_values=True,
    )

    form_values = {
        "company_name": _form_value(
            form_data,
            "company_name",
        ),
        "country": _form_value(
            form_data,
            "country",
        ),
        "email": _form_value(
            form_data,
            "email",
        ),
        "website": _form_value(
            form_data,
            "website",
        ),
        "transaction_type": _form_value(
            form_data,
            "transaction_type",
        ),
        "additional_context": _form_value(
            form_data,
            "additional_context",
        ),
    }

    honeypot_value = _form_value(
        form_data,
        "company_website",
    ).strip()

    if honeypot_value:
        return _render_check_request(
            request,
            language,
            submitted=True,
        )

    try:
        request_data = CheckRequestCreate(
            company_name=(
                form_values["company_name"]
            ),
            country=form_values["country"],
            email=form_values["email"],
            website=(
                form_values["website"]
                or None
            ),
            transaction_type=(
                form_values["transaction_type"]
                or None
            ),
            additional_context=(
                form_values["additional_context"]
                or None
            ),
            preferred_language=language,
        )
    except ValidationError:
        return _render_check_request(
            request,
            language,
            form_values=form_values,
            error=copy["request_error"],
            status_code=422,
        )

    client_key = _public_request_client_key(
        request
    )

    if not public_request_rate_limiter.allow(
        client_key
    ):
        response = _render_check_request(
            request,
            language,
            form_values=form_values,
            error=copy["request_rate_limited"],
            status_code=429,
        )

        response.headers["Retry-After"] = str(
            PUBLIC_REQUEST_RATE_WINDOW_SECONDS
        )

        return response

    create_check_request(request_data)

    return _render_check_request(
        request,
        language,
        submitted=True,
    )


@app.get("/internal/check", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Show the web form."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.post("/internal/run-check")
async def run_check_from_form(request: Request) -> RedirectResponse:
    """Run company check from HTML form.

    Form body is parsed manually to avoid adding python-multipart dependency.
    """
    body = await request.body()
    form_data = parse_qs(body.decode("utf-8"))

    company_name = form_data.get("company_name", [""])[0].strip()
    country = form_data.get("country", [""])[0].strip()
    domain = form_data.get("domain", [""])[0].strip() or None

    if not company_name or not country:
        return RedirectResponse(url="/internal/check?error=missing_input", status_code=303)

    response = run_company_check(
        company_name=company_name,
        country=country,
        domain=domain,
    )

    return RedirectResponse(
        url=f"/internal/result/{response.check_id}",
        status_code=303,
    )


@app.get("/internal/checks", response_class=HTMLResponse)
def checks_history(request: Request) -> HTMLResponse:
    """Show read-only history of saved company checks from the database."""
    checks = list_checks_from_db(limit=50)

    return templates.TemplateResponse(
        request=request,
        name="checks.html",
        context={"checks": checks},
    )


@app.get("/internal/result/{check_id}", response_class=HTMLResponse)
def show_result(request: Request, check_id: int) -> HTMLResponse:
    """Show company check result page."""
    result = load_company_check(check_id)

    return templates.TemplateResponse(
        request=request,
        name="result.html",
        context={
            "check_id": check_id,
            "result": result,
        },
    )