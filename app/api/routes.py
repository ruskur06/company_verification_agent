"""FastAPI routes for Company Verification Agent."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from app.db.repositories import CompanyCheckLockedError
from app.schemas.company_check import (
    CompanyCheckRequest,
    CompanyCheckResponse,
    CompanyCheckResult,
    RefreshReportResponse,
)
from app.schemas.final_risk_review import FinalRiskReviewCreate, FinalRiskReviewResponse
from app.schemas.human_review import HumanReviewCreate, HumanReviewRecordResponse
from app.schemas.official_website_review import (
    OfficialWebsiteReviewCreate,
    OfficialWebsiteReviewResponse,
    OfficialWebsiteReviewSubmitDecision,
)
from app.schemas.risk import HumanReviewInput
from app.schemas.source import ManualSourceCreate, SavedSourceResponse
from app.services.company_check_service import (
    add_manual_source_to_company_check,
    apply_human_review,
    list_checks_from_db,
    list_company_checks,
    load_company_check,
    refresh_company_check_report,
    run_company_check,
    submit_final_risk_review,
    submit_human_review,
    submit_official_website_review,
)

router = APIRouter()


def _http_error_from_service(exc: Exception) -> HTTPException:
    if isinstance(exc, CompanyCheckLockedError):
        return HTTPException(status_code=409, detail=str(exc))

    message = str(exc)
    if "not found" in message.lower():
        return HTTPException(status_code=404, detail=message)
    return HTTPException(status_code=400, detail=message)


@router.get("/health")
def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@router.post("/company-check", response_model=CompanyCheckResponse)
def create_company_check(request: CompanyCheckRequest) -> CompanyCheckResponse:
    """Run a preliminary company check."""
    try:
        return run_company_check(
            company_name=request.company_name,
            country=request.country,
            domain=request.domain,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/checks")
def get_saved_checks() -> list[dict]:
    """List recent company checks stored in PostgreSQL."""
    return list_checks_from_db(limit=20)


@router.get("/company-check", response_model=list[CompanyCheckResult])
def get_company_checks() -> list[CompanyCheckResult]:
    """List saved company checks."""
    return list_company_checks()


@router.get("/company-check/{check_id}", response_model=CompanyCheckResult)
def get_company_check(check_id: int) -> CompanyCheckResult:
    """Get one saved company check."""
    result = load_company_check(check_id)

    if result is None:
        raise HTTPException(status_code=404, detail=f"Check {check_id} was not found.")

    return result


@router.post(
    "/company-checks/{company_check_id}/sources",
    response_model=SavedSourceResponse,
    status_code=201,
)
def add_company_check_source(
    company_check_id: int,
    source: ManualSourceCreate,
) -> SavedSourceResponse:
    """Attach a human-verified source to an existing company check."""
    try:
        return add_manual_source_to_company_check(company_check_id, source)
    except CompanyCheckLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise _http_error_from_service(exc) from exc


@router.post(
    "/company-checks/{company_check_id}/refresh-report",
    response_model=RefreshReportResponse,
)
def refresh_company_check_report_endpoint(company_check_id: int) -> RefreshReportResponse:
    """Refresh JSON/Markdown output using linked database sources and updated risk."""
    try:
        return refresh_company_check_report(company_check_id)
    except CompanyCheckLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise _http_error_from_service(exc) from exc


@router.post(
    "/company-checks/{company_check_id}/human-review",
    response_model=HumanReviewRecordResponse,
    status_code=201,
)
def submit_company_check_human_review(
    company_check_id: int,
    review: HumanReviewCreate,
) -> HumanReviewRecordResponse:
    """Submit official DB-backed human review and lock the company check."""
    try:
        return submit_human_review(company_check_id, review)
    except CompanyCheckLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise _http_error_from_service(exc) from exc


@router.post(
    "/company-check/{check_id}/official-website-review",
    response_model=OfficialWebsiteReviewResponse,
)
def submit_official_website_review_endpoint(
    check_id: int,
    review: OfficialWebsiteReviewCreate,
) -> OfficialWebsiteReviewResponse:
    """Submit human review for the website candidate."""
    try:
        return submit_official_website_review(check_id, review)
    except ValueError as exc:
        raise _http_error_from_service(exc) from exc


@router.post("/company-check/{check_id}/official-website-review/form")
def submit_official_website_review_form(
    check_id: int,
    decision: str = Form(...),
    note: str | None = Form(None),
    reviewed_by: str | None = Form("human"),
) -> RedirectResponse:
    """Submit human website review from the result page HTML form."""
    try:
        review = OfficialWebsiteReviewCreate.model_validate(
            {
                "decision": decision,
                "note": note,
                "reviewed_by": reviewed_by,
            }
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    try:
        submit_official_website_review(check_id, review)
    except ValueError as exc:
        raise _http_error_from_service(exc) from exc

    return RedirectResponse(url=f"/result/{check_id}", status_code=303)


@router.post(
    "/company-check/{check_id}/final-risk-review",
    response_model=FinalRiskReviewResponse,
)
def submit_final_risk_review_endpoint(
    check_id: int,
    review: FinalRiskReviewCreate,
) -> FinalRiskReviewResponse:
    """Submit final human review for overall company risk."""
    try:
        return submit_final_risk_review(check_id, review)
    except ValueError as exc:
        raise _http_error_from_service(exc) from exc


@router.post("/company-check/{check_id}/final-risk-review/form")
def submit_final_risk_review_form(
    check_id: int,
    final_risk_decision: str = Form(...),
    final_score: str | None = Form(None),
    final_level: str | None = Form(None),
    notes: str | None = Form(None),
    reviewed_by: str | None = Form("human"),
) -> RedirectResponse:
    """Submit final risk human review from the result page HTML form."""
    score_value = None if final_score in (None, "") else final_score
    level_value = None if final_level in (None, "") else final_level

    try:
        review = FinalRiskReviewCreate.model_validate(
            {
                "decision": final_risk_decision,
                "final_score": score_value,
                "final_level": level_value,
                "notes": notes,
                "reviewed_by": reviewed_by,
            }
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=[
                {"type": error["type"], "loc": error["loc"], "msg": error["msg"]}
                for error in exc.errors()
            ],
        ) from exc

    try:
        submit_final_risk_review(check_id, review)
    except ValueError as exc:
        raise _http_error_from_service(exc) from exc

    return RedirectResponse(url=f"/result/{check_id}", status_code=303)


@router.post("/company-check/{check_id}/human-review", response_model=CompanyCheckResult)
def review_company_check(check_id: int, review: HumanReviewInput) -> CompanyCheckResult:
    """Legacy file-based human review endpoint.

    Prefer POST /company-checks/{company_check_id}/human-review for the official
    DB-backed workflow.
    """
    try:
        return apply_human_review(
            check_id=check_id,
            decision=review.decision.value,
            final_score=review.final_score,
            final_level=review.final_level.value if review.final_level else None,
            notes=review.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
