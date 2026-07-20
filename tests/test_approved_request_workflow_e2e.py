"""Application integration test for the approved-request happy path.

External company-data collection is mocked. Routes, claim, validation, and
strict persistence are real. SQLite does not prove PostgreSQL concurrency.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import sessionmaker

from app.agents.report_agent import json_path_for_check, markdown_path_for_check
from app.db import database
from app.db.models import (
    CheckRequestRecord,
    CompanyCheckRecord,
    ReportRecord,
    SourceRecord,
    ToolCallRecord,
)
from app.schemas.company_check import (
    CheckStatus,
    CompanyCheckResponse,
    CompanyCheckResult,
)
from app.services.public_request_guard import public_request_rate_limiter


COMPANY_NAME = "Workflow GmbH"
COUNTRY = "Austria"
EMAIL = "workflow@example.com"
WEBSITE = "https://workflow.example.com"
MARKDOWN_TEXT = "# Company Verification Report\nWorkflow e2e.\n"
FIXED_CREATED_AT = "2026-01-01T00:00:00"
SOURCE_TITLE = "Workflow registry entry"
SOURCE_URL = "https://example.com/workflow-registry"
DEFAULT_TOOL_NAMES = {
    "web_search",
    "domain_dns_check",
    "registry_search",
    "risk_score",
}


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    """Isolated SQLite database for the approved-request workflow e2e test."""
    database_url = f"sqlite:///{tmp_path / 'approved_request_workflow_e2e.db'}"
    database.configure_engine(database_url)
    database.init_db()

    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=database.engine,
    )
    monkeypatch.setattr("app.db.repositories.SessionLocal", session_factory)

    yield session_factory

    database.engine.dispose()


@pytest.fixture(autouse=True)
def _clear_public_rate_limiter():
    public_request_rate_limiter.clear()
    yield
    public_request_rate_limiter.clear()


def _company_check_data(
    *,
    check_id: int,
    company_name: str,
    country: str,
    domain: str | None,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "company": {
            "name": company_name,
            "country": country,
            "domain": domain,
        },
        "summary": {
            "short_description": "Workflow e2e preliminary check.",
            "overall_assessment": "Needs human review.",
            "confidence": "low",
        },
        "sources": [
            {
                "title": SOURCE_TITLE,
                "url": SOURCE_URL,
                "snippet": "Mocked source for the workflow e2e test.",
                "source_type": "search_result",
                "retrieved_at": FIXED_CREATED_AT,
                "confidence": "medium",
                "is_mock": True,
                "relevance": "relevant",
                "relevance_score": 0.9,
                "relevance_reasons": ["Exact company name match."],
            }
        ],
        "domain_dns": {
            "status": "not_provided",
            "domain": None,
            "has_a_record": False,
            "has_mx_record": False,
            "has_txt_record": False,
            "https_available": False,
            "warnings": [],
        },
        "registry_check": {
            "company_name": company_name,
            "country": country,
            "status": "not_found",
            "registry_found": False,
            "registry_name": None,
            "source_url": None,
            "confidence": "low",
            "notes": [],
            "is_mock": True,
        },
        "risk": {
            "preliminary_score": 55,
            "preliminary_level": "medium",
            "verification_confidence": "low",
            "verification_risk": "medium",
            "business_risk": "unknown",
            "factors": [],
            "requires_human_review": True,
            "final_score": None,
            "final_level": None,
            "human_review_status": "pending",
        },
        "manual_verification_checklist": [
            "Confirm the company in the official registry."
        ],
        "unknowns": ["No official registry result was confirmed."],
        "created_at": FIXED_CREATED_AT,
    }


def _build_pipeline_mock(captured_calls: list[dict[str, Any]]) -> MagicMock:
    """Mock pipeline that writes valid artifacts using the reserved check ID.

    CompanyCheckResult has no explicit tool_calls field, so strict persistence
    inserts the four default tool-call records derived from the result payload.
    """

    def _run(
        *,
        company_name: str,
        country: str,
        domain: str | None,
        check_id: int,
    ) -> CompanyCheckResponse:
        if type(check_id) is not int:
            raise AssertionError(
                f"check_id must be a non-boolean int, got {type(check_id)!r}"
            )
        if check_id <= 0:
            raise AssertionError(f"check_id must be > 0, got {check_id!r}")

        captured_calls.append(
            {
                "company_name": company_name,
                "country": country,
                "domain": domain,
                "check_id": check_id,
            }
        )

        result = CompanyCheckResult.model_validate(
            _company_check_data(
                check_id=check_id,
                company_name=company_name,
                country=country,
                domain=domain,
            )
        )
        json_path = json_path_for_check(check_id)
        markdown_path = markdown_path_for_check(check_id)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        markdown_path.write_text(MARKDOWN_TEXT, encoding="utf-8")

        return CompanyCheckResponse(
            check_id=check_id,
            status=CheckStatus.completed,
            json_result=result,
            markdown_report_path=str(markdown_path),
        )

    return MagicMock(side_effect=_run)


def test_complete_approved_request_workflow(
    sqlite_db,
    client,
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)

    captured_calls: list[dict[str, Any]] = []
    pipeline = _build_pipeline_mock(captured_calls)
    monkeypatch.setattr(
        "app.services.approved_request_pipeline_service.execute_company_check_pipeline",
        pipeline,
    )

    # Step 1 — public submission
    public_response = client.post(
        "/en/request-check",
        data={
            "company_name": COMPANY_NAME,
            "country": COUNTRY,
            "email": EMAIL,
            "website": WEBSITE,
            "transaction_type": "procurement",
            "additional_context": "Full approved-request workflow test.",
            "company_website": "",
        },
    )

    assert public_response.status_code == 200
    assert "Your request has been received" in public_response.text
    pipeline.assert_not_called()

    session = sqlite_db()
    try:
        requests = session.query(CheckRequestRecord).all()
        assert len(requests) == 1
        request_row = requests[0]
        assert request_row.status == "pending"
        assert request_row.company_check_id is None
        assert request_row.processing_check_id is None
        assert request_row.processing_started_at is None
        assert session.query(CompanyCheckRecord).count() == 0
        request_id = request_row.id
    finally:
        session.close()

    # Step 2 — pending detail
    pending_detail = client.get(f"/internal/requests/{request_id}")
    assert pending_detail.status_code == 200
    pending_text = pending_detail.text
    assert f'action="/internal/requests/{request_id}/approve"' in pending_text
    assert f'action="/internal/requests/{request_id}/reject"' in pending_text
    assert f'action="/internal/requests/{request_id}/run"' not in pending_text
    assert "Run check" not in pending_text

    # Step 3 — approval
    approve_response = client.post(
        f"/internal/requests/{request_id}/approve",
        follow_redirects=False,
    )
    assert approve_response.status_code == 303
    assert (
        approve_response.headers["location"]
        == f"/internal/requests/{request_id}"
    )

    session = sqlite_db()
    try:
        request_row = session.get(CheckRequestRecord, request_id)
        assert request_row is not None
        assert request_row.status == "approved"
        assert session.query(CompanyCheckRecord).count() == 0
    finally:
        session.close()
    pipeline.assert_not_called()

    # Step 4 — approved detail
    approved_detail = client.get(f"/internal/requests/{request_id}")
    assert approved_detail.status_code == 200
    approved_text = approved_detail.text
    assert (
        f'action="/internal/requests/{request_id}/run"'
        in approved_text
    )
    assert 'method="post"' in approved_text
    assert f'action="/internal/requests/{request_id}/approve"' not in approved_text
    assert f'action="/internal/requests/{request_id}/reject"' not in approved_text

    # Step 5 — manual run
    run_response = client.post(
        f"/internal/requests/{request_id}/run",
        follow_redirects=False,
    )
    assert run_response.status_code == 303
    pipeline.assert_called_once()
    assert len(captured_calls) == 1

    check_id = captured_calls[0]["check_id"]
    assert run_response.headers["location"] == f"/internal/result/{check_id}"
    assert captured_calls[0] == {
        "company_name": COMPANY_NAME,
        "country": COUNTRY,
        "domain": WEBSITE,
        "check_id": check_id,
    }
    pipeline.assert_called_once_with(
        company_name=COMPANY_NAME,
        country=COUNTRY,
        domain=WEBSITE,
        check_id=check_id,
    )

    expected_json_path = json_path_for_check(check_id)
    expected_markdown_path = markdown_path_for_check(check_id)
    expected_json_text = expected_json_path.read_text(encoding="utf-8")
    expected_markdown_text = expected_markdown_path.read_text(encoding="utf-8")
    check_id_str = str(check_id)

    # Step 6 — final CheckRequest state
    session = sqlite_db()
    try:
        request_row = session.get(CheckRequestRecord, request_id)
        assert request_row is not None
        assert request_row.status == "processed"
        assert request_row.company_check_id == check_id_str
        assert request_row.processing_check_id is None
        assert request_row.processing_started_at is None
    finally:
        session.close()

    # Step 7 — CompanyCheckRecord
    session = sqlite_db()
    try:
        checks = (
            session.query(CompanyCheckRecord)
            .filter(CompanyCheckRecord.check_id == check_id_str)
            .all()
        )
        assert len(checks) == 1
        check_row = checks[0]
        assert check_row.source_check_request_id == request_id
        assert check_row.company_name == COMPANY_NAME
        assert check_row.country == COUNTRY
        assert check_row.domain == WEBSITE
        assert check_row.human_review_status == "pending"
        assert check_row.json_report_path == str(expected_json_path)
        assert check_row.markdown_report_path == str(expected_markdown_path)
    finally:
        session.close()

    # Step 8 — SourceRecord
    session = sqlite_db()
    try:
        sources = (
            session.query(SourceRecord)
            .filter(SourceRecord.check_id == check_id_str)
            .all()
        )
        assert len(sources) == 1
        source = sources[0]
        assert source.title == SOURCE_TITLE
        assert source.url == SOURCE_URL
        assert source.source_type == "search_result"
        assert source.confidence == "medium"
        assert source.is_mock is True
        assert source.relevance == "relevant"
        assert source.relevance_score == 0.9
    finally:
        session.close()

    # Step 9 — ToolCallRecord
    # CompanyCheckResult has no tool_calls field; strict persistence therefore
    # inserts the four default derived tool calls.
    session = sqlite_db()
    try:
        tools = (
            session.query(ToolCallRecord)
            .filter(ToolCallRecord.check_id == check_id_str)
            .order_by(ToolCallRecord.id.asc())
            .all()
        )
        assert len(tools) == 4
        assert {tool.tool_name for tool in tools} == DEFAULT_TOOL_NAMES
        by_name = {tool.tool_name: tool for tool in tools}

        web_search = by_name["web_search"]
        assert web_search.status == "completed"
        assert json.loads(web_search.input_json) == {
            "company_name": COMPANY_NAME,
            "country": COUNTRY,
        }
        assert isinstance(json.loads(web_search.output_json), list)
        assert len(json.loads(web_search.output_json)) == 1

        domain_tool = by_name["domain_dns_check"]
        assert domain_tool.status == "completed"
        assert json.loads(domain_tool.input_json) == {"domain": WEBSITE}
        assert isinstance(json.loads(domain_tool.output_json), dict)

        registry_tool = by_name["registry_search"]
        assert registry_tool.status == "completed"
        assert json.loads(registry_tool.input_json) == {
            "company_name": COMPANY_NAME,
            "country": COUNTRY,
        }
        assert isinstance(json.loads(registry_tool.output_json), dict)

        risk_tool = by_name["risk_score"]
        assert risk_tool.status == "completed"
        assert risk_tool.input_json is None
        assert isinstance(json.loads(risk_tool.output_json), dict)
    finally:
        session.close()

    # Step 10 — ReportRecord
    session = sqlite_db()
    try:
        reports = (
            session.query(ReportRecord)
            .filter(ReportRecord.check_id == check_id_str)
            .all()
        )
        assert len(reports) == 1
        report = reports[0]
        assert report.json_path == str(expected_json_path)
        assert report.markdown_path == str(expected_markdown_path)
        assert report.json_content == expected_json_text
        assert report.markdown_content == expected_markdown_text
    finally:
        session.close()

    # Step 11 — filesystem
    assert expected_json_path.is_file()
    assert expected_markdown_path.is_file()
    assert not expected_json_path.is_symlink()
    assert not expected_markdown_path.is_symlink()

    tmp_root = tmp_path.resolve()
    json_resolved = expected_json_path.resolve()
    markdown_resolved = expected_markdown_path.resolve()
    assert json_resolved.is_relative_to(tmp_root)
    assert markdown_resolved.is_relative_to(tmp_root)

    parsed_result = CompanyCheckResult.model_validate_json(expected_json_text)
    assert parsed_result.check_id == check_id
    assert expected_markdown_text == MARKDOWN_TEXT

    # Step 12 — result page
    result_page = client.get(run_response.headers["location"])
    assert result_page.status_code == 200
    assert str(check_id) in result_page.text
    assert COMPANY_NAME in result_page.text
    assert COUNTRY in result_page.text

    # Step 13 — processed detail page
    processed_detail = client.get(f"/internal/requests/{request_id}")
    assert processed_detail.status_code == 200
    processed_text = processed_detail.text
    assert "<strong>processed</strong>" in processed_text
    assert f'href="/internal/result/{check_id}"' in processed_text
    assert "Verification complete." in processed_text
    assert f'action="/internal/requests/{request_id}/run"' not in processed_text
    assert f'action="/internal/requests/{request_id}/approve"' not in processed_text
    assert f'action="/internal/requests/{request_id}/reject"' not in processed_text
