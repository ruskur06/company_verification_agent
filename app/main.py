"""FastAPI application entrypoint."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router
from app.db.database import init_db
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


@app.get("/")
def landing_root() -> RedirectResponse:
    """Redirect the root URL to the default English landing page."""
    return RedirectResponse(url="/en", status_code=307)


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


@app.get("/check", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Show the web form."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.post("/run-check")
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
        return RedirectResponse(url="/check?error=missing_input", status_code=303)

    response = run_company_check(
        company_name=company_name,
        country=country,
        domain=domain,
    )

    return RedirectResponse(
        url=f"/result/{response.check_id}",
        status_code=303,
    )


@app.get("/checks", response_class=HTMLResponse)
def checks_history(request: Request) -> HTMLResponse:
    """Show read-only history of saved company checks from the database."""
    checks = list_checks_from_db(limit=50)

    return templates.TemplateResponse(
        request=request,
        name="checks.html",
        context={"checks": checks},
    )


@app.get("/result/{check_id}", response_class=HTMLResponse)
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