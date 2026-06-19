"""FastAPI routes for Company Verification Agent."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.company_check import CompanyCheckRequest, CompanyCheckResponse, CompanyCheckResult
from app.schemas.risk import HumanReviewInput
from app.services.company_check_service import (
    apply_human_review,
    list_checks_from_db,
    list_company_checks,
    load_company_check,
    run_company_check,
)

router = APIRouter()


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


@router.get("/checks")
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


@router.post("/company-check/{check_id}/human-review", response_model=CompanyCheckResult)
def review_company_check(check_id: int, review: HumanReviewInput) -> CompanyCheckResult:
    """Apply human review to a company check."""
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