"""Focused tests for claimed approved-request pipeline execution."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.agents.report_agent import json_path_for_check, markdown_path_for_check
from app.schemas.approved_request_pipeline import PreparedApprovedRequestCheck
from app.schemas.check_request import (
    CheckRequestLanguage,
    CheckRequestResponse,
    CheckRequestStatus,
    ClaimedCheckRequest,
)
from app.schemas.company_check import (
    CheckStatus,
    CompanyCheckResponse,
    CompanyCheckResult,
)
from app.services import approved_request_pipeline_service
from app.services.approved_request_pipeline_service import (
    PreparedCheckValidationError,
    ReportFileCollisionError,
    execute_claimed_check_request,
)


PROCESSING_CHECK_ID = 1782245999101
REQUEST_ID = 42
FIXED_STARTED_AT = datetime(2026, 7, 17, 12, 0, 0)


def _company_check_data(*, check_id: int = PROCESSING_CHECK_ID) -> dict:
    return {
        "check_id": check_id,
        "company": {
            "name": "Pipeline GmbH",
            "country": "Austria",
            "domain": "pipeline.example.com",
        },
        "summary": {
            "short_description": "Preliminary check.",
            "overall_assessment": "Needs human review.",
            "confidence": "low",
        },
        "sources": [],
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
            "company_name": "Pipeline GmbH",
            "country": "Austria",
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
        "manual_verification_checklist": ["Check official company registry."],
        "unknowns": ["No official registry result was confirmed."],
        "created_at": "2026-01-01T00:00:00",
    }


def _claimed(
    *,
    website: str | None = "https://pipeline.example.com",
    processing_check_id: int = PROCESSING_CHECK_ID,
) -> ClaimedCheckRequest:
    return ClaimedCheckRequest(
        request=CheckRequestResponse(
            id=REQUEST_ID,
            company_name="Pipeline GmbH",
            country="Austria",
            email="pipeline@example.com",
            website=website,
            preferred_language=CheckRequestLanguage.en,
            status=CheckRequestStatus.processing,
            created_at=FIXED_STARTED_AT,
        ),
        processing_check_id=processing_check_id,
        processing_started_at=FIXED_STARTED_AT,
    )


def _result(*, check_id: int = PROCESSING_CHECK_ID) -> CompanyCheckResult:
    return CompanyCheckResult.model_validate(_company_check_data(check_id=check_id))


def _write_matching_artifacts(
    *,
    result: CompanyCheckResult,
    markdown_text: str = "# Company Verification Report\n",
) -> tuple[Path, Path, str, str]:
    json_path = json_path_for_check(result.check_id)
    markdown_path = markdown_path_for_check(result.check_id)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_content = result.model_dump_json(indent=2)
    json_path.write_text(json_content, encoding="utf-8")
    markdown_path.write_text(markdown_text, encoding="utf-8")
    return json_path, markdown_path, json_content, markdown_text


def _completed_response(
    *,
    result: CompanyCheckResult,
    markdown_path: Path | None = None,
) -> CompanyCheckResponse:
    path = markdown_path or markdown_path_for_check(result.check_id)
    return CompanyCheckResponse(
        check_id=result.check_id,
        status=CheckStatus.completed,
        json_result=result,
        markdown_report_path=str(path),
    )


def _pipeline_that_writes_artifacts(
    *,
    result: CompanyCheckResult,
    markdown_text: str = "# Company Verification Report\n",
) -> MagicMock:
    def _run(**_kwargs):
        json_path, markdown_path, _, _ = _write_matching_artifacts(
            result=result,
            markdown_text=markdown_text,
        )
        return _completed_response(result=result, markdown_path=markdown_path)

    return MagicMock(side_effect=_run)


def test_markdown_path_for_check_returns_expected_relative_path():
    assert markdown_path_for_check(PROCESSING_CHECK_ID) == Path(
        f"outputs/reports/company_check_{PROCESSING_CHECK_ID}.md"
    )


def test_existing_json_file_raises_collision(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    json_path = json_path_for_check(PROCESSING_CHECK_ID)
    json_path.parent.mkdir(parents=True)
    json_path.write_text("{}", encoding="utf-8")
    pipeline = MagicMock(side_effect=AssertionError("pipeline must not run"))
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        pipeline,
    )

    with pytest.raises(ReportFileCollisionError) as exc_info:
        execute_claimed_check_request(_claimed())

    error = exc_info.value
    assert error.source_check_request_id == REQUEST_ID
    assert error.processing_check_id == PROCESSING_CHECK_ID
    assert error.colliding_paths == (json_path,)
    assert str(json_path) in str(error)
    assert json_path.read_text(encoding="utf-8") == "{}"
    pipeline.assert_not_called()


def test_existing_markdown_file_raises_collision(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    markdown_path = markdown_path_for_check(PROCESSING_CHECK_ID)
    markdown_path.parent.mkdir(parents=True)
    markdown_path.write_text("existing", encoding="utf-8")
    pipeline = MagicMock(side_effect=AssertionError("pipeline must not run"))
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        pipeline,
    )

    with pytest.raises(ReportFileCollisionError) as exc_info:
        execute_claimed_check_request(_claimed())

    assert exc_info.value.colliding_paths == (markdown_path,)
    assert markdown_path.read_text(encoding="utf-8") == "existing"
    pipeline.assert_not_called()


def test_both_existing_files_reported_in_deterministic_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    json_path = json_path_for_check(PROCESSING_CHECK_ID)
    markdown_path = markdown_path_for_check(PROCESSING_CHECK_ID)
    json_path.parent.mkdir(parents=True)
    markdown_path.parent.mkdir(parents=True)
    json_path.write_text("{}", encoding="utf-8")
    markdown_path.write_text("md", encoding="utf-8")
    pipeline = MagicMock(side_effect=AssertionError("pipeline must not run"))
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        pipeline,
    )

    with pytest.raises(ReportFileCollisionError) as exc_info:
        execute_claimed_check_request(_claimed())

    assert exc_info.value.colliding_paths == (json_path, markdown_path)
    assert str(exc_info.value).index(str(json_path)) < str(exc_info.value).index(
        str(markdown_path)
    )
    pipeline.assert_not_called()


def test_dangling_json_symlink_is_collision(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    json_path = json_path_for_check(PROCESSING_CHECK_ID)
    json_path.parent.mkdir(parents=True)
    json_path.symlink_to(tmp_path / "missing-target.json")
    pipeline = MagicMock(side_effect=AssertionError("pipeline must not run"))
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        pipeline,
    )

    with pytest.raises(ReportFileCollisionError) as exc_info:
        execute_claimed_check_request(_claimed())

    assert exc_info.value.colliding_paths == (json_path,)
    assert json_path.is_symlink()
    pipeline.assert_not_called()


def test_successful_execution_forwards_kwargs_and_builds_prepared(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    result = _result()
    expected_json = json_path_for_check(PROCESSING_CHECK_ID)
    expected_markdown = markdown_path_for_check(PROCESSING_CHECK_ID)
    pipeline = _pipeline_that_writes_artifacts(result=result)
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        pipeline,
    )
    monkeypatch.setattr(
        "app.services.company_check_service.run_company_check",
        MagicMock(side_effect=AssertionError("run_company_check must not run")),
    )
    monkeypatch.setattr(
        "app.services.check_request_service.claim_approved_check_request",
        MagicMock(side_effect=AssertionError("claim must not run")),
    )

    prepared = execute_claimed_check_request(_claimed())

    pipeline.assert_called_once_with(
        company_name="Pipeline GmbH",
        country="Austria",
        domain="https://pipeline.example.com",
        check_id=PROCESSING_CHECK_ID,
    )
    assert isinstance(prepared, PreparedApprovedRequestCheck)
    assert prepared.source_check_request_id == REQUEST_ID
    assert prepared.processing_check_id == str(PROCESSING_CHECK_ID)
    assert isinstance(prepared.processing_check_id, str)
    assert prepared.processing_started_at == FIXED_STARTED_AT
    assert prepared.json_report_path == str(expected_json)
    assert prepared.markdown_report_path == str(expected_markdown)
    assert prepared.json_content == expected_json.read_text(encoding="utf-8")
    assert prepared.markdown_content == expected_markdown.read_text(encoding="utf-8")
    assert prepared.result_payload["check_id"] == str(PROCESSING_CHECK_ID)
    assert isinstance(prepared.result_payload["check_id"], str)
    assert prepared.result_payload["json_report_path"] == str(expected_json)
    assert prepared.result_payload["markdown_report_path"] == str(expected_markdown)
    assert "source_check_request_id" not in prepared.result_payload


def test_website_none_forwards_domain_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result()
    pipeline = _pipeline_that_writes_artifacts(result=result)
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        pipeline,
    )

    execute_claimed_check_request(_claimed(website=None))

    pipeline.assert_called_once_with(
        company_name="Pipeline GmbH",
        country="Austria",
        domain=None,
        check_id=PROCESSING_CHECK_ID,
    )


def test_pipeline_exceptions_propagate_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(side_effect=RuntimeError("pipeline exploded")),
    )

    with pytest.raises(RuntimeError, match="pipeline exploded"):
        execute_claimed_check_request(_claimed())


def test_non_completed_response_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result()

    def _run(**_kwargs):
        _, markdown_path, _, _ = _write_matching_artifacts(result=result)
        return CompanyCheckResponse(
            check_id=PROCESSING_CHECK_ID,
            status=CheckStatus.failed,
            json_result=result,
            markdown_report_path=str(markdown_path),
        )

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(side_effect=_run),
    )

    with pytest.raises(PreparedCheckValidationError, match="status completed"):
        execute_claimed_check_request(_claimed())


def test_response_check_id_mismatch_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result()

    def _run(**_kwargs):
        _, markdown_path, _, _ = _write_matching_artifacts(result=result)
        return CompanyCheckResponse(
            check_id=PROCESSING_CHECK_ID + 1,
            status=CheckStatus.completed,
            json_result=result,
            markdown_report_path=str(markdown_path),
        )

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(side_effect=_run),
    )

    with pytest.raises(PreparedCheckValidationError, match="does not match"):
        execute_claimed_check_request(_claimed())


def test_missing_json_result_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    response = CompanyCheckResponse(
        check_id=PROCESSING_CHECK_ID,
        status=CheckStatus.completed,
        json_result=None,
        markdown_report_path=str(markdown_path_for_check(PROCESSING_CHECK_ID)),
    )
    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(return_value=response),
    )

    with pytest.raises(PreparedCheckValidationError, match="missing json_result"):
        execute_claimed_check_request(_claimed())


def test_json_result_check_id_mismatch_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result(check_id=PROCESSING_CHECK_ID + 7)

    def _run(**_kwargs):
        _, markdown_path, _, _ = _write_matching_artifacts(result=result)
        return CompanyCheckResponse(
            check_id=PROCESSING_CHECK_ID,
            status=CheckStatus.completed,
            json_result=result,
            markdown_report_path=str(markdown_path),
        )

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(side_effect=_run),
    )

    with pytest.raises(
        PreparedCheckValidationError,
        match="json_result.check_id",
    ):
        execute_claimed_check_request(_claimed())


def test_markdown_path_mismatch_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result()

    def _run(**_kwargs):
        _write_matching_artifacts(result=result)
        return CompanyCheckResponse(
            check_id=PROCESSING_CHECK_ID,
            status=CheckStatus.completed,
            json_result=result,
            markdown_report_path="outputs/reports/wrong.md",
        )

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(side_effect=_run),
    )

    with pytest.raises(
        PreparedCheckValidationError,
        match="markdown_report_path",
    ):
        execute_claimed_check_request(_claimed())


def test_missing_json_artifact_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result()

    def _run(**_kwargs):
        markdown_path = markdown_path_for_check(PROCESSING_CHECK_ID)
        markdown_path.parent.mkdir(parents=True)
        markdown_path.write_text("# ok\n", encoding="utf-8")
        return _completed_response(result=result, markdown_path=markdown_path)

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(side_effect=_run),
    )

    with pytest.raises(PreparedCheckValidationError, match="JSON artifact"):
        execute_claimed_check_request(_claimed())


def test_missing_markdown_artifact_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result()

    def _run(**_kwargs):
        json_path = json_path_for_check(PROCESSING_CHECK_ID)
        json_path.parent.mkdir(parents=True)
        json_path.write_text(result.model_dump_json(), encoding="utf-8")
        return _completed_response(result=result)

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(side_effect=_run),
    )

    with pytest.raises(PreparedCheckValidationError, match="Markdown artifact"):
        execute_claimed_check_request(_claimed())


def test_symlink_artifact_after_execution_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result()

    def _run(**_kwargs):
        real_json = tmp_path / "real.json"
        real_json.write_text(result.model_dump_json(), encoding="utf-8")
        json_path = json_path_for_check(PROCESSING_CHECK_ID)
        markdown_path = markdown_path_for_check(PROCESSING_CHECK_ID)
        json_path.parent.mkdir(parents=True)
        markdown_path.parent.mkdir(parents=True)
        json_path.symlink_to(real_json)
        markdown_path.write_text("# ok\n", encoding="utf-8")
        return _completed_response(result=result, markdown_path=markdown_path)

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(side_effect=_run),
    )

    with pytest.raises(PreparedCheckValidationError, match="JSON artifact"):
        execute_claimed_check_request(_claimed())


def test_empty_json_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result()

    def _run(**_kwargs):
        json_path, markdown_path, _, _ = _write_matching_artifacts(result=result)
        json_path.write_text("   \n", encoding="utf-8")
        return _completed_response(result=result, markdown_path=markdown_path)

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(side_effect=_run),
    )

    with pytest.raises(PreparedCheckValidationError, match="JSON artifact .* empty"):
        execute_claimed_check_request(_claimed())


def test_empty_markdown_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result()

    def _run(**_kwargs):
        _, markdown_path, _, _ = _write_matching_artifacts(result=result)
        markdown_path.write_text("\n\t  ", encoding="utf-8")
        return _completed_response(result=result, markdown_path=markdown_path)

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(side_effect=_run),
    )

    with pytest.raises(
        PreparedCheckValidationError,
        match="Markdown artifact .* empty",
    ):
        execute_claimed_check_request(_claimed())


def test_malformed_json_raises_with_chaining(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result()

    def _run(**_kwargs):
        json_path, markdown_path, _, _ = _write_matching_artifacts(result=result)
        json_path.write_text("{not-json", encoding="utf-8")
        return _completed_response(result=result, markdown_path=markdown_path)

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(side_effect=_run),
    )

    with pytest.raises(
        PreparedCheckValidationError, match="failed schema validation"
    ) as exc_info:
        execute_claimed_check_request(_claimed())

    assert exc_info.value.__cause__ is not None


def test_parsed_json_check_id_mismatch_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result()
    mismatched = _result(check_id=PROCESSING_CHECK_ID + 9)

    def _run(**_kwargs):
        json_path = json_path_for_check(PROCESSING_CHECK_ID)
        markdown_path = markdown_path_for_check(PROCESSING_CHECK_ID)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(mismatched.model_dump_json(indent=2), encoding="utf-8")
        markdown_path.write_text("# Company Verification Report\n", encoding="utf-8")
        return _completed_response(result=result, markdown_path=markdown_path)

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(side_effect=_run),
    )

    with pytest.raises(PreparedCheckValidationError, match="Parsed JSON check_id"):
        execute_claimed_check_request(_claimed())


def test_parsed_json_semantic_mismatch_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result()
    altered = result.model_copy(
        update={
            "summary": result.summary.model_copy(
                update={"short_description": "Different summary."}
            )
        }
    )

    def _run(**_kwargs):
        _, markdown_path, _, _ = _write_matching_artifacts(result=altered)
        return _completed_response(result=result, markdown_path=markdown_path)

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(side_effect=_run),
    )

    with pytest.raises(
        PreparedCheckValidationError,
        match="does not match the in-memory pipeline result",
    ):
        execute_claimed_check_request(_claimed())


def test_filesystem_read_oserror_propagates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result()
    json_path = json_path_for_check(PROCESSING_CHECK_ID)

    def _run(**_kwargs):
        written_json, markdown_path, _, _ = _write_matching_artifacts(result=result)
        assert written_json == json_path
        return _completed_response(result=result, markdown_path=markdown_path)

    monkeypatch.setattr(
        approved_request_pipeline_service,
        "execute_company_check_pipeline",
        MagicMock(side_effect=_run),
    )

    original_read_text = Path.read_text

    def fake_read_text(self, *args, **kwargs):
        if self == json_path:
            raise PermissionError("denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    with pytest.raises(PermissionError, match="denied"):
        execute_claimed_check_request(_claimed())
