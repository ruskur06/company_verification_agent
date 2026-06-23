from pathlib import Path

from app.agents.report_agent import ReportAgent
from app.schemas.company_check import CompanyCheckResult
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


def test_result_template_does_not_use_misleading_preliminary_risk_score_label():
    template = Path("app/web/templates/result.html").read_text(encoding="utf-8")

    assert "Preliminary Risk Score" not in template
    assert "Verification Confidence" in template
    assert "Verification Risk" in template
    assert "Business Risk" in template
    assert "Preliminary verification score (legacy)" in template


def test_cli_source_does_not_use_misleading_preliminary_risk_score_label():
    cli_source = Path("app/cli.py").read_text(encoding="utf-8")

    assert "Preliminary risk score" not in cli_source
    assert "verification_confidence" in cli_source
    assert "business_risk" in cli_source
