"""Agent that builds and saves JSON/Markdown reports."""

from __future__ import annotations

from pathlib import Path

from app.schemas.company_check import CompanyCheckResult, DomainDnsInfo
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


def _format_source_relevance_markdown(source: SourceResult) -> list[str]:
    lines = [
        f"  - Relevance: `{source.relevance.value}`",
        f"  - Relevance score: `{source.relevance_score}`",
    ]
    if source.relevance_reasons:
        lines.append(
            f"  - Relevance reasons: {', '.join(source.relevance_reasons)}"
        )
    return lines


def _format_sources_markdown(sources: list[SourceResult]) -> str:
    if not sources:
        return "- No sources found."

    lines: list[str] = []

    for source in sources:
        if source.is_mock:
            evidence_label = "mock"
        else:
            evidence_label = "verified/manual"

        lines.append(
            "\n".join(
                [
                    f"- **{source.title}**",
                    f"  - URL: `{source.url}`",
                    f"  - Type: `{source.source_type.value}`",
                    f"  - Confidence: `{source.confidence.value}`",
                    *_format_source_relevance_markdown(source),
                    f"  - Evidence type: `{evidence_label}`",
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


def _format_registry_markdown(result: CompanyCheckResult) -> str:
    registry = result.registry_check
    source_url = registry.source_url or "Not available"
    mock_label = "yes" if registry.is_mock else "no"

    return "\n".join(
        [
            f"- Status: {registry.status.value}",
            f"- Registry found: {registry.registry_found}",
            f"- Registry name: {registry.registry_name or 'Not available'}",
            f"- Source URL: {source_url}",
            f"- Confidence: {registry.confidence.value}",
            f"- Mock: {mock_label}",
            "",
            "Notes:",
            "",
            _format_list_markdown(registry.notes),
        ]
    )


def _format_domain_dns_markdown(domain_dns: DomainDnsInfo, *, heading: str) -> str:
    return "\n".join(
        [
            f"- Status: {domain_dns.status.value}",
            f"- Domain: {domain_dns.domain or 'Not provided'}",
            f"- Has A record: {domain_dns.has_a_record}",
            f"- Has MX record: {domain_dns.has_mx_record}",
            f"- Has TXT record: {domain_dns.has_txt_record}",
            f"- HTTPS available: {domain_dns.https_available}",
            "",
            f"{heading}:",
            "",
            _format_warnings_markdown(domain_dns.warnings),
        ]
    )


def _format_candidate_domain_dns_markdown(result: CompanyCheckResult) -> str:
    if result.candidate_domain_dns is None:
        return "- No candidate domain DNS check was performed."

    return "\n".join(
        [
            _format_domain_dns_markdown(
                result.candidate_domain_dns,
                heading="Warnings",
            ),
            "",
            (
                "Candidate domain technically resolves and HTTPS may be available, "
                "but official ownership is not confirmed."
            ),
        ]
    )


def _format_website_candidate_markdown(result: CompanyCheckResult) -> str:
    candidate = result.website_candidate
    if candidate is None:
        return "- No website candidate detected from relevant sources."

    return "\n".join(
        [
            "- Status: **candidate pending human verification**",
            f"- Candidate URL: `{candidate.candidate_url}`",
            f"- Candidate domain: `{candidate.candidate_domain}`",
            f"- Score: `{candidate.score}`",
            f"- Confidence: `{candidate.confidence.value}`",
            f"- Source title: {candidate.source_title}",
            f"- Verified official website: `{candidate.is_verified}`",
            f"- Reasons: {', '.join(candidate.reasons)}",
            "",
            "This is a candidate website from relevant web search results, not a confirmed official website.",
        ]
    )


def _format_verification_risk_overview_markdown(result: CompanyCheckResult) -> str:
    return "\n".join(
        [
            f"- Verification confidence: **{result.risk.verification_confidence.value.upper()}**",
            f"- Verification risk: **{result.risk.verification_risk.value.upper()}**",
            f"- Business risk: **{result.risk.business_risk.value.upper()}**",
            "",
            "High verification risk means the check lacks enough verified evidence. "
            "It is not proof of misconduct or high business risk. "
            "Mock sources are not verified evidence.",
            "",
            f"- Preliminary verification score (legacy): {result.risk.preliminary_score}",
            f"- Preliminary verification level (legacy): {result.risk.preliminary_level.value}",
        ]
    )


class ReportAgent:
    """Builds Markdown reports and persists strict JSON output."""

    def build_markdown(self, result: CompanyCheckResult) -> str:
        """Build Markdown report from strict JSON result."""
        return f"""# Company Verification Report

## 1. Input

- Company name: {result.company.name}
- Country: {result.company.country}
- Domain: {result.company.domain or "Not provided"}

## 2. Verification and Risk Overview

{_format_verification_risk_overview_markdown(result)}

## 3. Executive Summary

{result.summary.overall_assessment}

Confidence: **{result.summary.confidence.value}**

## 4. Source Coverage

Sources found: {len(result.sources)}

{_format_sources_markdown(result.sources)}

## 5. Domain and DNS Findings (user-provided domain)

{_format_domain_dns_markdown(result.domain_dns, heading="Warnings")}

## 5a. Website Candidate (pending verification)

{_format_website_candidate_markdown(result)}

## 5b. Candidate Domain DNS/HTTPS (pending ownership verification)

{_format_candidate_domain_dns_markdown(result)}

## 6. Registry Check

{_format_registry_markdown(result)}

## 7. Verification Risk Factors

{_format_risk_factors_markdown(result)}

## 8. Unknowns and Data Gaps

{_format_list_markdown(result.unknowns)}

## 9. Manual Verification Checklist

{_format_list_markdown(result.manual_verification_checklist)}

## 10. Human Review

Status: {result.risk.human_review_status.value}

Requires human review: {result.risk.requires_human_review}

Final score: {result.risk.final_score if result.risk.final_score is not None else "Pending"}

Final level: {result.risk.final_level.value if result.risk.final_level else "Pending"}

## 11. Disclaimer

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
