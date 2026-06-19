"""Company check service.

Thin facade over the agent layer. Handles persistence helpers used by CLI/API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from app.agents.company_check_agent import CompanyCheckAgent
from app.agents.report_agent import ReportAgent, json_path_for_check
from app.db.repositories import (
    get_company_check_by_id as get_saved_company_check,
    list_company_checks as list_saved_company_checks,
    save_company_check,
)
from app.schemas.company_check import CompanyCheckResponse, CompanyCheckResult
from app.schemas.risk import HumanReviewStatus, RiskLevel

_report_agent = ReportAgent()
_check_agent = CompanyCheckAgent(report_agent=_report_agent)


def _persist_company_check(response: CompanyCheckResponse) -> None:
    """Save company check result to PostgreSQL without breaking file-based flow."""
    if response.json_result is None:
        return

    payload = response.json_result.model_dump(mode="json")
    payload["check_id"] = str(response.check_id)
    payload["json_report_path"] = str(json_path_for_check(response.check_id))
    payload["markdown_report_path"] = response.markdown_report_path

    try:
        save_company_check(payload)
    except Exception:
        # File-based MVP flow must keep working even if DB is unavailable.
        pass


def run_company_check(
    company_name: str,
    country: str,
    domain: Optional[str] = None,
) -> CompanyCheckResponse:
    """Run a preliminary company check."""
    response = _check_agent.run(
        company_name=company_name,
        country=country,
        domain=domain,
    )
    _persist_company_check(response)
    return response


def load_company_check(check_id: int) -> CompanyCheckResult | None:
    """Load one company check from JSON storage."""
    path = json_path_for_check(check_id)

    if not path.exists():
        return None

    return CompanyCheckResult.model_validate_json(path.read_text(encoding="utf-8"))


def list_company_checks() -> list[CompanyCheckResult]:
    """List saved company checks from JSON storage."""
    json_dir = Path("outputs/json")
    json_dir.mkdir(parents=True, exist_ok=True)

    results: list[CompanyCheckResult] = []

    for path in sorted(json_dir.glob("company_check_*.json")):
        try:
            content = path.read_text(encoding="utf-8")
            results.append(CompanyCheckResult.model_validate_json(content))
        except Exception:
            continue

    return results


def list_checks_from_db(limit: int = 20) -> list[dict[str, Any]]:
    """List recent company checks stored in PostgreSQL."""
    return list_saved_company_checks(limit=limit)


def get_check_from_db(check_id: str) -> dict[str, Any] | None:
    """Load one company check record from PostgreSQL."""
    return get_saved_company_check(check_id)


def apply_human_review(
    check_id: int,
    decision: str,
    final_score: Optional[int] = None,
    final_level: Optional[str] = None,
    notes: str = "",
) -> CompanyCheckResult:
    """Apply human review to an existing company check.

    The `notes` argument is accepted for CLI/API compatibility.
    It is not stored in the strict JSON schema yet.
    """
    result = load_company_check(check_id)

    if result is None:
        raise ValueError(f"Check with id {check_id} was not found.")

    review_status = HumanReviewStatus(decision)

    if final_score is not None and not 0 <= final_score <= 100:
        raise ValueError("final_score must be between 0 and 100.")

    parsed_final_level = RiskLevel(final_level) if final_level else None

    if review_status in {HumanReviewStatus.approved, HumanReviewStatus.edited}:
        if final_score is None:
            final_score = result.risk.preliminary_score

        if parsed_final_level is None:
            parsed_final_level = result.risk.preliminary_level

    result.risk.human_review_status = review_status
    result.risk.final_score = final_score
    result.risk.final_level = parsed_final_level

    _report_agent.save(result)

    return result
