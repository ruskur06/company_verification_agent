"""Agent that builds and saves JSON/Markdown reports."""

from __future__ import annotations

from pathlib import Path

from app.schemas.company_check import CompanyCheckResult
from app.schemas.source import SourceResult

JSON_DIR = Path("outputs/json")
REPORTS_DIR = Path("outputs/reports")


def json_path_for_check(check_id: int) -> Path:
    """Return JSON output path for a check."""
    return JSON_DIR / f"company_check_{check_id}.json"


def _json_path(check_id: int) -> Path:
    return json_path_for_check(check_id)


def _report_path(check_id: int) -> Path:
    return REPORTS_DIR / f"company_check_{check_id}.md"


def _format_sources_markdown(sources: list[SourceResult]) -> str:
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
    if not warnings:
        return "- No warnings."

    return "\n".join(f"- {warning}" for warning in warnings)


def _format_risk_factors_markdown(result: CompanyCheckResult) -> str:
    if not result.risk.factors:
        return "- No risk factors."

    return "\n".join(
        f"- **{factor.name}**: {factor.impact} — {factor.explanation}"
        for factor in result.risk.factors
    )


def _format_list_markdown(items: list[str]) -> str:
    if not items:
        return "- None."

    return "\n".join(f"- {item}" for item in items)


class ReportAgent:
    """Builds Markdown reports and persists strict JSON output."""

    def build_markdown(self, result: CompanyCheckResult) -> str:
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

    def save(self, result: CompanyCheckResult) -> tuple[Path, Path]:
        """Save strict JSON and Markdown report to disk."""
        JSON_DIR.mkdir(parents=True, exist_ok=True)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        json_path = _json_path(result.check_id)
        report_path = _report_path(result.check_id)

        json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        report_path.write_text(self.build_markdown(result), encoding="utf-8")

        return json_path, report_path
