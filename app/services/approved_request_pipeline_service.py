"""Execute and validate an already-claimed approved check request."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from app.agents.report_agent import json_path_for_check, markdown_path_for_check
from app.schemas.approved_request_pipeline import PreparedApprovedRequestCheck
from app.schemas.check_request import ClaimedCheckRequest
from app.schemas.company_check import CheckStatus, CompanyCheckResponse, CompanyCheckResult
from app.services.company_check_service import execute_company_check_pipeline


class ReportFileCollisionError(RuntimeError):
    """Raised when expected report paths are already occupied before execution."""

    def __init__(
        self,
        message: str,
        *,
        source_check_request_id: int,
        processing_check_id: int | str,
        colliding_paths: tuple[Path, ...],
    ) -> None:
        super().__init__(message)
        self.source_check_request_id = source_check_request_id
        self.processing_check_id = processing_check_id
        self.colliding_paths = colliding_paths


class PreparedCheckValidationError(RuntimeError):
    """Raised when pipeline response or report artifacts fail validation."""


def execute_claimed_check_request(
    claimed: ClaimedCheckRequest,
) -> PreparedApprovedRequestCheck:
    """Run the claimed request through the pipeline and prepare persistence artifacts."""
    expected_json_path = json_path_for_check(claimed.processing_check_id)
    expected_markdown_path = markdown_path_for_check(claimed.processing_check_id)

    # Preflight reduces overwrite risk but is not an atomic filesystem reservation.
    colliding_paths: list[Path] = []
    if expected_json_path.exists() or expected_json_path.is_symlink():
        colliding_paths.append(expected_json_path)
    if expected_markdown_path.exists() or expected_markdown_path.is_symlink():
        colliding_paths.append(expected_markdown_path)
    if colliding_paths:
        colliding = tuple(colliding_paths)
        path_text = ", ".join(str(path) for path in colliding)
        raise ReportFileCollisionError(
            (
                f"Report files already exist for check request "
                f"{claimed.request.id} with processing check ID "
                f"{claimed.processing_check_id}: {path_text}"
            ),
            source_check_request_id=claimed.request.id,
            processing_check_id=claimed.processing_check_id,
            colliding_paths=colliding,
        )

    response = execute_company_check_pipeline(
        company_name=claimed.request.company_name,
        country=claimed.request.country,
        domain=claimed.request.website or None,
        check_id=claimed.processing_check_id,
    )

    _validate_pipeline_response(
        response,
        claimed=claimed,
        expected_markdown_path=expected_markdown_path,
    )

    json_content, markdown_content = _read_and_validate_artifacts(
        expected_json_path=expected_json_path,
        expected_markdown_path=expected_markdown_path,
        claimed=claimed,
        response=response,
    )

    result_payload = response.json_result.model_dump(mode="json")
    result_payload["check_id"] = str(claimed.processing_check_id)
    result_payload["json_report_path"] = str(expected_json_path)
    result_payload["markdown_report_path"] = str(expected_markdown_path)

    return PreparedApprovedRequestCheck(
        source_check_request_id=claimed.request.id,
        processing_check_id=str(claimed.processing_check_id),
        processing_started_at=claimed.processing_started_at,
        result_payload=result_payload,
        json_report_path=str(expected_json_path),
        markdown_report_path=str(expected_markdown_path),
        json_content=json_content,
        markdown_content=markdown_content,
    )


def _validate_pipeline_response(
    response: CompanyCheckResponse,
    *,
    claimed: ClaimedCheckRequest,
    expected_markdown_path: Path,
) -> None:
    if response.status != CheckStatus.completed:
        raise PreparedCheckValidationError(
            f"Pipeline response for check request {claimed.request.id} "
            f"must have status completed, got {response.status}."
        )
    if response.check_id != claimed.processing_check_id:
        raise PreparedCheckValidationError(
            f"Pipeline response check_id {response.check_id} does not match "
            f"processing check ID {claimed.processing_check_id}."
        )
    if response.json_result is None:
        raise PreparedCheckValidationError(
            f"Pipeline response for check request {claimed.request.id} "
            "is missing json_result."
        )
    if response.json_result.check_id != claimed.processing_check_id:
        raise PreparedCheckValidationError(
            f"Pipeline json_result.check_id {response.json_result.check_id} "
            f"does not match processing check ID {claimed.processing_check_id}."
        )
    if Path(response.markdown_report_path or "") != expected_markdown_path:
        raise PreparedCheckValidationError(
            f"Pipeline markdown_report_path {response.markdown_report_path!r} "
            f"does not match expected path {expected_markdown_path}."
        )


def _require_regular_file(path: Path, *, label: str, request_id: int) -> None:
    if path.is_symlink() or not path.exists() or not path.is_file():
        raise PreparedCheckValidationError(
            f"{label} artifact for check request {request_id} is missing "
            f"or is not a regular file: {path}"
        )


def _read_and_validate_artifacts(
    *,
    expected_json_path: Path,
    expected_markdown_path: Path,
    claimed: ClaimedCheckRequest,
    response: CompanyCheckResponse,
) -> tuple[str, str]:
    _require_regular_file(
        expected_json_path,
        label="JSON",
        request_id=claimed.request.id,
    )
    _require_regular_file(
        expected_markdown_path,
        label="Markdown",
        request_id=claimed.request.id,
    )

    json_content = expected_json_path.read_text(encoding="utf-8")
    markdown_content = expected_markdown_path.read_text(encoding="utf-8")

    if not json_content.strip():
        raise PreparedCheckValidationError(
            f"JSON artifact for check request {claimed.request.id} is empty."
        )
    if not markdown_content.strip():
        raise PreparedCheckValidationError(
            f"Markdown artifact for check request {claimed.request.id} is empty."
        )

    try:
        parsed_json_result = CompanyCheckResult.model_validate_json(json_content)
    except ValidationError as exc:
        raise PreparedCheckValidationError(
            f"JSON artifact for check request {claimed.request.id} "
            "failed schema validation."
        ) from exc

    if parsed_json_result.check_id != claimed.processing_check_id:
        raise PreparedCheckValidationError(
            f"Parsed JSON check_id {parsed_json_result.check_id} does not match "
            f"processing check ID {claimed.processing_check_id}."
        )

    assert response.json_result is not None
    if parsed_json_result.model_dump(mode="json") != response.json_result.model_dump(
        mode="json"
    ):
        raise PreparedCheckValidationError(
            f"Parsed JSON artifact for check request {claimed.request.id} "
            "does not match the in-memory pipeline result."
        )

    return json_content, markdown_content
