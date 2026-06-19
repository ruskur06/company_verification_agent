"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router
from app.db.database import init_db
from app.services.company_check_service import load_company_check, run_company_check

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


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


@app.get("/", response_class=HTMLResponse)
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
        return RedirectResponse(url="/?error=missing_input", status_code=303)

    response = run_company_check(
        company_name=company_name,
        country=country,
        domain=domain,
    )

    return RedirectResponse(
        url=f"/result/{response.check_id}",
        status_code=303,
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