"""Company check service.

Thin facade over the agent layer. Handles persistence helpers used by CLI/API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from app.agents.company_check_agent import CompanyCheckAgent
from app.agents.report_agent import ReportAgent, json_path_for_check
from app.agents.risk_agent import RiskAgent
from app.db.repositories import (
    CompanyCheckLockedError,
    CompanyCheckNotFoundError,
    add_source_to_company_check,
    create_human_review_record,
    get_company_check_by_id as get_saved_company_check,
    get_sources_for_company_check,
    list_company_checks as list_saved_company_checks,
    save_company_check,
    update_company_check_after_refresh,
)
from app.schemas.company_check import (
    CheckStatus,
    CompanyCheckResponse,
    CompanyCheckResult,
    RefreshReportResponse,
    RiskInfo,
    SummaryInfo,
)
from app.schemas.human_review import HumanReviewCreate, HumanReviewRecordResponse
from app.schemas.risk import HumanReviewStatus, RiskLevel, RiskScoreInput
from app.schemas.source import ConfidenceLevel, ManualSourceCreate, SavedSourceResponse, SourceResult, SourceType
from app.tools.web_search import count_negative_snippets, extract_suspicious_keywords

_report_agent = ReportAgent()
_check_agent = CompanyCheckAgent(report_agent=_report_agent)
_risk_agent = RiskAgent()


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


def add_manual_source_to_company_check(
    company_check_id: int | str,
    source: ManualSourceCreate,
) -> SavedSourceResponse:
    """Attach a human-verified source to an existing saved company check."""
    try:
        saved = add_source_to_company_check(
            str(company_check_id),
            source.model_dump(mode="json"),
        )
    except CompanyCheckNotFoundError as exc:
        raise ValueError(str(exc)) from exc
    except CompanyCheckLockedError:
        raise

    return SavedSourceResponse.model_validate(saved)


def submit_human_review(
    company_check_id: int | str,
    review: HumanReviewCreate,
) -> HumanReviewRecordResponse:
    """Submit a DB-backed human review and lock the company check."""
    try:
        saved = create_human_review_record(
            str(company_check_id),
            review.model_dump(mode="json"),
        )
    except CompanyCheckNotFoundError as exc:
        raise ValueError(str(exc)) from exc
    except CompanyCheckLockedError:
        raise

    return HumanReviewRecordResponse.model_validate(saved)


def _db_source_to_source_result(source_data: dict) -> SourceResult:
    return SourceResult(
        title=source_data["title"],
        url=source_data["url"],
        snippet=source_data.get("snippet") or "",
        source_type=SourceType(source_data.get("source_type") or "other"),
        retrieved_at=source_data["retrieved_at"],
        confidence=ConfidenceLevel(source_data.get("confidence") or "low"),
        is_mock=bool(source_data.get("is_mock", False)),
    )


def _verification_confidence_to_summary_level(level: RiskLevel) -> ConfidenceLevel:
    return {
        RiskLevel.low: ConfidenceLevel.low,
        RiskLevel.medium: ConfidenceLevel.medium,
        RiskLevel.high: ConfidenceLevel.high,
    }[level]


def _build_refreshed_summary(
    result: CompanyCheckResult,
    *,
    has_verified_sources: bool,
    verification_confidence: RiskLevel,
) -> SummaryInfo:
    if has_verified_sources:
        overall_assessment = (
            "This report was refreshed using stored company check data and linked sources from the database. "
            "Manually verified non-mock sources improve verification confidence but do not prove business safety. "
            "Business risk remains unknown unless verified negative business indicators exist. "
            "Final assessment still requires human review."
        )
    else:
        overall_assessment = result.summary.overall_assessment

    return SummaryInfo(
        short_description=result.summary.short_description,
        overall_assessment=overall_assessment,
        confidence=_verification_confidence_to_summary_level(verification_confidence),
    )


def _build_refreshed_unknowns(result: CompanyCheckResult, has_verified_sources: bool) -> list[str]:
    unknowns = list(result.unknowns)
    if has_verified_sources:
        unknowns.append(
            "Manually verified sources were included in this refresh, but sanctions and legal checks are still incomplete."
        )
    return unknowns


def refresh_company_check_report(company_check_id: int | str) -> RefreshReportResponse:
    """Reload sources from DB, recalculate risk, and regenerate JSON/Markdown output."""
    check_id_str = str(company_check_id).strip()
    if not check_id_str:
        raise ValueError("company_check_id must not be empty")

    db_record = get_saved_company_check(check_id_str)
    if db_record is None:
        raise ValueError(f"Company check {check_id_str} was not found.")
    if db_record.get("is_locked"):
        raise CompanyCheckLockedError(f"Company check {check_id_str} is already finalized.")

    try:
        check_id = int(check_id_str)
    except ValueError as exc:
        raise ValueError(f"Invalid company check id: {check_id_str}") from exc

    result = load_company_check(check_id)
    if result is None:
        raise ValueError(
            f"Company check JSON for {check_id_str} was not found. "
            "Run the initial company check before refreshing the report."
        )

    sources = [_db_source_to_source_result(source) for source in get_sources_for_company_check(check_id_str)]
    verified_sources = [source for source in sources if not source.is_mock]
    all_sources_mock = len(sources) == 0 or len(verified_sources) == 0
    has_high_confidence_verified_source = any(
        source.confidence == ConfidenceLevel.high for source in verified_sources
    )
    verified_strong_source_count = sum(
        1
        for source in verified_sources
        if source.confidence in {ConfidenceLevel.medium, ConfidenceLevel.high}
    )

    verified_sources_for_risk = verified_sources if verified_sources else []
    negative_snippets_count = count_negative_snippets(verified_sources_for_risk)
    suspicious_keywords = extract_suspicious_keywords(verified_sources_for_risk)

    domain_dns = result.domain_dns
    registry_check = result.registry_check

    risk_input = RiskScoreInput(
        has_website=bool(result.company.domain) and domain_dns.https_available,
        domain_resolves=domain_dns.has_a_record,
        has_mx_record=domain_dns.has_mx_record,
        https_available=domain_dns.https_available,
        negative_snippets_count=negative_snippets_count,
        registry_found=registry_check.registry_found,
        registry_is_mock=registry_check.is_mock,
        multiple_sources_confirm=len(verified_sources) >= 2,
        suspicious_keywords_found=suspicious_keywords,
        source_count=len(sources),
        all_sources_mock=all_sources_mock,
        verified_non_mock_source_count=len(verified_sources),
        verified_strong_source_count=verified_strong_source_count,
        has_high_confidence_verified_source=has_high_confidence_verified_source,
    )
    risk_result = _risk_agent.run(risk_input)

    result.sources = sources
    result.summary = _build_refreshed_summary(
        result,
        has_verified_sources=bool(verified_sources),
        verification_confidence=risk_result.verification_confidence,
    )
    result.unknowns = _build_refreshed_unknowns(result, bool(verified_sources))
    result.risk = RiskInfo(
        preliminary_score=risk_result.score,
        preliminary_level=risk_result.level,
        verification_confidence=risk_result.verification_confidence,
        verification_risk=risk_result.verification_risk,
        business_risk=risk_result.business_risk,
        factors=risk_result.factors,
        requires_human_review=risk_result.requires_human_review,
        final_score=result.risk.final_score,
        final_level=result.risk.final_level,
        human_review_status=result.risk.human_review_status,
    )

    _, markdown_report_path = _report_agent.save(result)
    json_report_path = str(json_path_for_check(check_id))

    payload = result.model_dump(mode="json")
    payload["check_id"] = check_id_str
    payload["json_report_path"] = json_report_path
    payload["markdown_report_path"] = str(markdown_report_path)

    try:
        update_company_check_after_refresh(payload)
    except CompanyCheckNotFoundError as exc:
        raise ValueError(str(exc)) from exc
    except CompanyCheckLockedError:
        raise

    return RefreshReportResponse(
        check_id=check_id,
        status=CheckStatus.completed,
        json_result=result,
        json_report_path=json_report_path,
        markdown_report_path=str(markdown_report_path),
    )


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
