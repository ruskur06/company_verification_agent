from datetime import datetime, timezone
from pathlib import Path

from app.agents.report_agent import ReportAgent
from app.schemas.company_check import CompanyCheckResult
from app.schemas.source import RelevanceLevel, SourceResult, SourceType
from tests.test_json_schema import valid_company_check_data


def test_report_top_level_shows_verification_and_business_risk_labels():
    result = CompanyCheckResult.model_validate(valid_company_check_data())
    markdown = ReportAgent().build_markdown(result)

    assert "Verification confidence: **LOW**" in markdown
    assert "Verification risk: **MEDIUM**" in markdown
    assert "Business risk: **UNKNOWN**" in markdown
    assert "Preliminary verification score (legacy):" in markdown
    assert "Preliminary Risk Score" not in markdown
    assert "Preliminary risk score" not in markdown.lower()


def test_report_source_section_shows_relevance_fields():
    data = valid_company_check_data()
    data["sources"] = [
        SourceResult(
            title="Avron GmbH profile",
            url="https://example.com/avron-gmbh",
            snippet="Avron GmbH company information.",
            source_type=SourceType.search_result,
            retrieved_at=datetime.now(timezone.utc),
            relevance=RelevanceLevel.irrelevant,
            relevance_score=0.1,
            relevance_reasons=["country_mentioned", "no_company_name_overlap"],
        ).model_dump(mode="json")
    ]

    markdown = ReportAgent().build_markdown(CompanyCheckResult.model_validate(data))

    assert "Relevance: `irrelevant`" in markdown
    assert "Relevance score: `0.1`" in markdown
    assert "Relevance reasons: country_mentioned, no_company_name_overlap" in markdown


def test_result_template_shows_source_relevance_fields():
    template = Path("app/web/templates/result.html").read_text(encoding="utf-8")

    assert "relevance:" in template
    assert "relevance_score:" in template
    assert "Relevance reasons:" in template


def test_result_template_does_not_use_misleading_preliminary_risk_score_label():
    template = Path("app/web/templates/result.html").read_text(encoding="utf-8")

    assert "Preliminary Risk Score" not in template
    assert "Verification Confidence" in template
    assert "Verification Risk" in template
    assert "Business Risk" in template
    assert "Preliminary verification score (legacy)" in template


def test_report_includes_candidate_domain_dns_section_when_present():
    data = valid_company_check_data()
    data["website_candidate"] = {
        "candidate_url": "https://servochron.com",
        "candidate_domain": "servochron.com",
        "score": 0.8,
        "confidence": "medium",
        "reasons": ["domain_contains_company_name", "https_scheme"],
        "source_title": "SERVOCHRON GmbH official website",
        "is_verified": False,
    }
    data["candidate_domain_dns"] = {
        "status": "checked",
        "domain": "servochron.com",
        "has_a_record": True,
        "has_mx_record": False,
        "has_txt_record": False,
        "https_available": True,
        "warnings": [],
    }

    markdown = ReportAgent().build_markdown(CompanyCheckResult.model_validate(data))

    assert "Candidate Domain DNS/HTTPS (pending ownership verification)" in markdown
    assert "official ownership is not confirmed" in markdown


def test_report_includes_website_ownership_signals_section():
    data = valid_company_check_data()
    data["website_ownership_signals"] = {
        "status": "signals_found",
        "score": 0.75,
        "confidence": "high",
        "signals": [
            {
                "name": "candidate_domain_resolves",
                "found": True,
                "weight": 0.15,
                "detail": "Candidate domain resolves to an A record.",
            }
        ],
        "warnings": ["Official website status still requires human verification."],
        "is_officially_confirmed": False,
    }

    markdown = ReportAgent().build_markdown(CompanyCheckResult.model_validate(data))

    assert "Website Ownership Signals (pending verification)" in markdown
    assert "official website status still requires human verification" in markdown.lower()


def test_result_template_shows_website_ownership_signals_section():
    template = Path("app/web/templates/result.html").read_text(encoding="utf-8")

    assert "Website Ownership Signals (pending verification)" in template
    assert "website_ownership_signals" in template


def test_result_template_shows_candidate_domain_dns_section():
    template = Path("app/web/templates/result.html").read_text(encoding="utf-8")

    assert "Candidate Domain DNS/HTTPS (pending ownership verification)" in template
    assert "candidate_domain_dns" in template


def test_result_template_shows_website_candidate_section():
    template = Path("app/web/templates/result.html").read_text(encoding="utf-8")

    assert "Website Candidate (pending verification)" in template
    assert "not a confirmed official website" in template


def test_report_includes_website_candidate_section_when_present():
    data = valid_company_check_data()
    data["website_candidate"] = {
        "candidate_url": "https://servochron.com",
        "candidate_domain": "servochron.com",
        "score": 0.8,
        "confidence": "medium",
        "reasons": ["domain_contains_company_name", "https_scheme"],
        "source_title": "SERVOCHRON GmbH official website",
        "is_verified": False,
    }

    markdown = ReportAgent().build_markdown(CompanyCheckResult.model_validate(data))

    assert "Website Candidate (pending verification)" in markdown
    assert "servochron.com" in markdown
    assert "candidate pending human verification" in markdown
    assert (
        "This is a candidate website from relevant web search results, "
        "not a confirmed official website."
    ) in markdown


def test_report_website_candidate_from_provided_domain_uses_provided_domain_text():
    data = valid_company_check_data()
    data["website_candidate"] = {
        "candidate_url": "https://munchy.at",
        "candidate_domain": "munchy.at",
        "score": 0.5,
        "confidence": "medium",
        "reasons": ["provided_domain"],
        "source_title": "User-provided domain",
        "is_verified": False,
    }

    markdown = ReportAgent().build_markdown(CompanyCheckResult.model_validate(data))

    assert (
        "This is a candidate website from the user-provided domain, "
        "not a confirmed official website."
    ) in markdown
    assert "relevant web search results" not in markdown


def test_result_template_website_candidate_text_varies_by_source():
    template = Path("app/web/templates/result.html").read_text(encoding="utf-8")

    assert "user-provided domain" in template
    assert "relevant web search results" in template
    assert "provided_domain" in template


def test_cli_source_does_not_use_misleading_preliminary_risk_score_label():
    cli_source = Path("app/cli.py").read_text(encoding="utf-8")

    assert "Preliminary risk score" not in cli_source
    assert "verification_confidence" in cli_source
    assert "business_risk" in cli_source
