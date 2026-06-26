"""Orchestrator agent for the full company check pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.agents.domain_agent import DomainAgent
from app.agents.human_review_agent import HumanReviewAgent
from app.agents.name_normalizer_agent import NameNormalizer
from app.agents.registry_agent import RegistryAgent
from app.agents.report_agent import ReportAgent
from app.agents.risk_agent import RiskAgent
from app.agents.web_search_agent import WebSearchAgent
from app.schemas.company_check import (
    CheckStatus,
    CompanyCheckRequest,
    CompanyCheckResponse,
    CompanyCheckResult,
    CompanyInfo,
    RiskInfo,
    SummaryInfo,
)
from app.schemas.name_normalizer import NameNormalizerInput
from app.schemas.risk import RiskLevel, RiskScoreInput
from app.schemas.registry import RegistryCheckResult
from app.schemas.source import ConfidenceLevel, SourceResult
from app.tools.entity_matcher import (
    annotate_relevance,
    source_coverage_flags,
    verified_coverage_sources,
)
from app.tools.web_search import count_negative_snippets, extract_suspicious_keywords


def _new_check_id() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _summary_confidence_from_verification(level: RiskLevel) -> ConfidenceLevel:
    return {
        RiskLevel.low: ConfidenceLevel.low,
        RiskLevel.medium: ConfidenceLevel.medium,
        RiskLevel.high: ConfidenceLevel.high,
    }[level]


def _build_unknowns(registry_check: RegistryCheckResult, sources: list[SourceResult]) -> list[str]:
    has_real_web_sources = bool(verified_coverage_sources(sources))

    if has_real_web_sources:
        unknowns = [
            "Real web search sources were found, but they still require human review and do not prove business safety.",
            "Final risk score requires human review.",
        ]
    else:
        unknowns = [
            "Current web search results are mock results and not verified evidence.",
            "Final risk score requires human review.",
        ]

    if not registry_check.registry_found:
        unknowns.insert(
            0,
            "No official company registry result was confirmed.",
        )
    elif registry_check.is_mock:
        unknowns.insert(
            0,
            "Registry result is based on mock data and must be verified manually.",
        )

    return unknowns


def _build_overall_assessment(has_real_web_sources: bool) -> str:
    if has_real_web_sources:
        return (
            "This is a preliminary check based on DNS data and real web search sources. "
            "Real sources were found, but they still require human review and do not prove business safety. "
            "A higher preliminary score may reflect missing verified data and verification risk, not proof of misconduct. "
            "Final risk assessment requires human review."
        )

    return (
        "This is a preliminary local check based on DNS data and mock web search output. "
        "Mock sources are useful for testing the pipeline but must not be treated as verified evidence. "
        "A higher preliminary score may reflect missing verified data and verification risk, not proof of misconduct. "
        "Final risk assessment requires human review."
    )


class CompanyCheckAgent:
    """Coordinates web search, domain check, risk scoring, reporting, and review."""

    def __init__(
        self,
        name_normalizer: NameNormalizer | None = None,
        web_search_agent: WebSearchAgent | None = None,
        domain_agent: DomainAgent | None = None,
        registry_agent: RegistryAgent | None = None,
        risk_agent: RiskAgent | None = None,
        report_agent: ReportAgent | None = None,
        human_review_agent: HumanReviewAgent | None = None,
    ) -> None:
        self.name_normalizer = name_normalizer or NameNormalizer()
        self.web_search_agent = web_search_agent or WebSearchAgent()
        self.domain_agent = domain_agent or DomainAgent()
        self.registry_agent = registry_agent or RegistryAgent()
        self.risk_agent = risk_agent or RiskAgent()
        self.report_agent = report_agent or ReportAgent()
        self.human_review_agent = human_review_agent or HumanReviewAgent()

    def run(
        self,
        company_name: str,
        country: str,
        domain: Optional[str] = None,
    ) -> CompanyCheckResponse:
        """Run a preliminary company check through all agents."""
        request = CompanyCheckRequest(
            company_name=company_name,
            country=country,
            domain=domain,
        )

        name_normalization = self.name_normalizer.run(
            NameNormalizerInput(
                company_name=request.company_name,
                country=request.country,
                domain=request.domain,
            )
        )

        search_name = name_normalization.normalized_name
        effective_domain = request.domain or (
            name_normalization.domain_candidates[0]
            if name_normalization.domain_candidates
            else None
        )

        check_id = _new_check_id()

        dns_info = self.domain_agent.run(effective_domain)

        sources = self.web_search_agent.run(
            search_names=name_normalization.search_names,
            country=request.country,
        )
        sources = annotate_relevance(
            company_name=request.company_name,
            country=request.country,
            sources=sources,
        )

        registry_check = self.registry_agent.run(
            search_names=name_normalization.search_names,
            country=request.country,
        )

        verified_sources = verified_coverage_sources(sources)
        source_coverage = source_coverage_flags(sources)
        negative_snippets_count = count_negative_snippets(verified_sources)
        suspicious_keywords = extract_suspicious_keywords(verified_sources)

        risk_input = RiskScoreInput(
            has_website=bool(effective_domain) and dns_info.https_available,
            domain_resolves=dns_info.has_a_record,
            has_mx_record=dns_info.has_mx_record,
            https_available=dns_info.https_available,
            negative_snippets_count=negative_snippets_count,
            registry_found=registry_check.registry_found,
            registry_is_mock=registry_check.is_mock,
            multiple_sources_confirm=bool(source_coverage["multiple_sources_confirm"]),
            suspicious_keywords_found=suspicious_keywords,
            source_count=len(sources),
            all_sources_mock=bool(source_coverage["all_sources_mock"]),
            verified_non_mock_source_count=int(source_coverage["verified_non_mock_source_count"]),
            verified_strong_source_count=int(source_coverage["verified_strong_source_count"]),
            has_high_confidence_verified_source=bool(
                source_coverage["has_high_confidence_verified_source"]
            ),
        )

        risk_result = self.risk_agent.run(risk_input)
        human_review_status = self.human_review_agent.run()

        result = CompanyCheckResult(
            check_id=check_id,
            company=CompanyInfo(
                name=request.company_name,
                country=request.country,
                domain=request.domain,
            ),
            name_normalization=name_normalization,
            summary=SummaryInfo(
                short_description=f"Preliminary local check for {search_name}.",
                overall_assessment=_build_overall_assessment(bool(verified_sources)),
                confidence=_summary_confidence_from_verification(
                    risk_result.verification_confidence
                ),
            ),
            sources=sources,
            domain_dns=dns_info,
            registry_check=registry_check,
            risk=RiskInfo(
                preliminary_score=risk_result.score,
                preliminary_level=risk_result.level,
                verification_confidence=risk_result.verification_confidence,
                verification_risk=risk_result.verification_risk,
                business_risk=risk_result.business_risk,
                factors=risk_result.factors,
                requires_human_review=risk_result.requires_human_review,
                final_score=None,
                final_level=None,
                human_review_status=human_review_status,
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
            unknowns=_build_unknowns(registry_check, sources),
            created_at=datetime.now(timezone.utc),
        )

        _, report_path = self.report_agent.save(result)

        return CompanyCheckResponse(
            check_id=check_id,
            status=CheckStatus.completed,
            json_result=result,
            markdown_report_path=str(report_path),
        )
