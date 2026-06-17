"""Company check service.

MVP version:
- runs mock web search
- runs domain/DNS check
- calculates preliminary risk score
- saves strict JSON result
- saves Markdown report

Later this service can be connected to:
- real search APIs
- official company registries
- SQLite/PostgreSQL repositories
- background jobs
- real human review storage
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.schemas.company_check import (
    CheckStatus,
    CompanyCheckRequest,
    CompanyCheckResponse,
    CompanyCheckResult,
    CompanyInfo,
    RiskInfo,
    SummaryInfo,
)
from app.schemas.risk import HumanReviewStatus, RiskLevel, RiskScoreInput
from app.schemas.source import ConfidenceLevel, SourceResult
from app.tools.domain_dns_check import domain_dns_check
from app.tools.risk_score import calculate_risk_score
from app.tools.web_search import (
    count_negative_snippets,
    extract_suspicious_keywords,
    web_search,
)

JSON_DIR = Path("outputs/json")
REPORTS_DIR = Path("outputs/reports")


def _new_check_id() -> int:
    """Create a simple numeric check id."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _json_path(check_id: int) -> Path:
    """Return JSON output path for a check."""
    return JSON_DIR / f"company_check_{check_id}.json"


def _report_path(check_id: int) -> Path:
    """Return Markdown report output path for a check."""
    return REPORTS_DIR / f"company_check_{check_id}.md"


def _format_sources_markdown(sources: list[SourceResult]) -> str:
    """Format source results for the Markdown report."""
    if not sources:
        return "- No sources found."

    lines: list[str] = []

    for source in sources:
        mock_label = "yes" if source.is_mock else "no"

        lines.append(
            "\n".join(
                [
                    f"- **{source.title}**",
                    f"  - URL: `{source.url}`",
                    f"  - Type: `{source.source_type.value}`",
                    f"  - Confidence: `{source.confidence.value}`",
                    f"  - Mock: `{mock_label}`",
                    f"  - Snippet: {source.snippet}",
                ]
            )
        )

    return "\n".join(lines)


def _format_warnings_markdown(warnings: list[str]) -> str:
    """Format warning messages for the Markdown report."""
    if not warnings:
        return "- No warnings."

    return "\n".join(f"- {warning}" for warning in warnings)


def _format_risk_factors_markdown(result: CompanyCheckResult) -> str:
    """Format risk factors for the Markdown report."""
    if not result.risk.factors:
        return "- No risk factors."

    return "\n".join(
        f"- **{factor.name}**: {factor.impact} — {factor.explanation}"
        for factor in result.risk.factors
    )


def _format_list_markdown(items: list[str]) -> str:
    """Format a list of strings for the Markdown report."""
    if not items:
        return "- None."

    return "\n".join(f"- {item}" for item in items)


def _build_markdown_report(result: CompanyCheckResult) -> str:
    """Build Markdown report from strict JSON result."""
    return f"""# Company Verification Report

## 1. Input

- Company name: {result.company.name}
- Country: {result.company.country}
- Domain: {result.company.domain or "Not provided"}

## 2. Executive Summary

{result.summary.overall_assessment}

Confidence: **{result.summary.confidence.value}**

## 3. Source Coverage

Sources found: {len(result.sources)}

{_format_sources_markdown(result.sources)}

## 4. Domain and DNS Findings

- Status: {result.domain_dns.status.value}
- Domain: {result.domain_dns.domain or "Not provided"}
- Has A record: {result.domain_dns.has_a_record}
- Has MX record: {result.domain_dns.has_mx_record}
- Has TXT record: {result.domain_dns.has_txt_record}
- HTTPS available: {result.domain_dns.https_available}

Warnings:

{_format_warnings_markdown(result.domain_dns.warnings)}

## 5. Preliminary Risk Score

- Score: {result.risk.preliminary_score}
- Level: {result.risk.preliminary_level.value}
- Requires human review: {result.risk.requires_human_review}

## 6. Risk Factors

{_format_risk_factors_markdown(result)}

## 7. Unknowns and Data Gaps

{_format_list_markdown(result.unknowns)}

## 8. Manual Verification Checklist

{_format_list_markdown(result.manual_verification_checklist)}

## 9. Human Review

Status: {result.risk.human_review_status.value}

Final score: {result.risk.final_score if result.risk.final_score is not None else "Pending"}

Final level: {result.risk.final_level.value if result.risk.final_level else "Pending"}

## 10. Disclaimer

This report is based on limited open-source information and should not be treated as a final legal, financial, or compliance decision.
"""


def _save_result(result: CompanyCheckResult) -> tuple[Path, Path]:
    """Save strict JSON and Markdown report to disk."""
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    json_path = _json_path(result.check_id)
    report_path = _report_path(result.check_id)

    json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    report_path.write_text(_build_markdown_report(result), encoding="utf-8")

    return json_path, report_path


def run_company_check(
    company_name: str,
    country: str,
    domain: Optional[str] = None,
) -> CompanyCheckResponse:
    """Run a preliminary company check."""
    request = CompanyCheckRequest(
        company_name=company_name,
        country=country,
        domain=domain,
    )

    check_id = _new_check_id()

    dns_info = domain_dns_check(request.domain)

    sources = web_search(
        company_name=request.company_name,
        country=request.country,
        max_results=5,
    )

    negative_snippets_count = count_negative_snippets(sources)
    suspicious_keywords = extract_suspicious_keywords(sources)

    risk_input = RiskScoreInput(
        has_website=bool(request.domain) and dns_info.https_available,
        domain_resolves=dns_info.has_a_record,
        has_mx_record=dns_info.has_mx_record,
        https_available=dns_info.https_available,
        negative_snippets_count=negative_snippets_count,
        registry_found=False,
        multiple_sources_confirm=False,
        suspicious_keywords_found=suspicious_keywords,
        source_count=len(sources),
    )

    risk_result = calculate_risk_score(risk_input)

    result = CompanyCheckResult(
        check_id=check_id,
        company=CompanyInfo(
            name=request.company_name,
            country=request.country,
            domain=request.domain,
        ),
        summary=SummaryInfo(
            short_description=f"Preliminary local check for {request.company_name}.",
            overall_assessment=(
                "This is a preliminary local check based on DNS data and mock web search output. "
                "Mock sources are useful for testing the pipeline but must not be treated as verified evidence. "
                "Final risk assessment requires human review."
            ),
            confidence=ConfidenceLevel.low,
        ),
        sources=sources,
        domain_dns=dns_info,
        risk=RiskInfo(
            preliminary_score=risk_result.score,
            preliminary_level=risk_result.level,
            factors=risk_result.factors,
            requires_human_review=risk_result.requires_human_review,
            final_score=None,
            final_level=None,
            human_review_status=HumanReviewStatus.pending,
        ),
        manual_verification_checklist=[
            "Check official company registry.",
            "Confirm legal company name.",
            "Verify company address.",
            "Verify website ownership.",
            "Check sanctions lists.",
            "Check legal disputes and complaints.",
            "Replace mock web search with real source verification.",
        ],
        unknowns=[
            "No official company registry result was confirmed.",
            "Current web search results are mock results and not verified evidence.",
            "Final risk score requires human review.",
        ],
        created_at=datetime.now(timezone.utc),
    )

    _, report_path = _save_result(result)

    return CompanyCheckResponse(
        check_id=check_id,
        status=CheckStatus.completed,
        json_result=result,
        markdown_report_path=str(report_path),
    )


def load_company_check(check_id: int) -> CompanyCheckResult | None:
    """Load one company check from JSON storage."""
    path = _json_path(check_id)

    if not path.exists():
        return None

    return CompanyCheckResult.model_validate_json(path.read_text(encoding="utf-8"))


def list_company_checks() -> list[CompanyCheckResult]:
    """List saved company checks from JSON storage."""
    JSON_DIR.mkdir(parents=True, exist_ok=True)

    results: list[CompanyCheckResult] = []

    for path in sorted(JSON_DIR.glob("company_check_*.json")):
        try:
            content = path.read_text(encoding="utf-8")
            results.append(CompanyCheckResult.model_validate_json(content))
        except Exception:
            continue

    return results


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

    _save_result(result)

    return result