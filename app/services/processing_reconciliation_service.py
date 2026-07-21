"""Read-only processing-request reconciliation diagnosis service."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

import app.agents.report_agent as report_agent
from app.db.repositories import get_processing_reconciliation_database_inspection
from app.schemas.processing_reconciliation import (
    ArtifactFileFacts,
    JsonArtifactFacts,
    ProcessingReconciliationDiagnosisError,
    ProcessingReconciliationFacts,
    ProcessingReconciliationResult,
    ReconciliationArtifactFacts,
    ReconciliationConsistency,
    ReconciliationDatabaseFacts,
    ReconciliationDiagnosisErrorReason,
    ReconciliationReportSnapshot,
    is_canonical_processing_check_id,
)
from app.services.processing_reconciliation_classifier import (
    classify_processing_reconciliation,
)


class ProcessingReconciliationRequestNotFoundError(LookupError):
    """Raised when diagnosis is requested for a missing CheckRequest."""

    def __init__(self, request_id: int) -> None:
        self.request_id = request_id
        super().__init__(
            f"Check request {request_id} was not found for reconciliation "
            f"diagnosis."
        )


@dataclass(frozen=True)
class _ArtifactInspectionSnapshot:
    expected_path: Path
    facts: ArtifactFileFacts
    content: str | None
    sha256: str | None


@dataclass(frozen=True)
class _JsonArtifactInspectionSnapshot:
    expected_path: Path
    facts: JsonArtifactFacts
    content: str | None
    sha256: str | None


def diagnose_processing_reconciliation(
    request_id: int,
    *,
    stale_after: timedelta,
    diagnosed_at: datetime | None = None,
) -> ProcessingReconciliationResult:
    """Diagnose one processing request using DB and filesystem facts only."""
    if (
        isinstance(request_id, bool)
        or not isinstance(request_id, int)
        or request_id <= 0
    ):
        raise ValueError("request_id must be a positive integer")
    if not isinstance(stale_after, timedelta) or stale_after <= timedelta(0):
        raise ValueError("stale_after must be a positive timedelta")

    if diagnosed_at is None:
        diagnosed_at_utc = _utc_now()
    else:
        if not isinstance(diagnosed_at, datetime):
            raise ValueError("diagnosed_at must be a datetime")
        if diagnosed_at.tzinfo is None:
            raise ValueError("diagnosed_at must be timezone-aware")
        diagnosed_at_utc = diagnosed_at.astimezone(timezone.utc)

    try:
        inspection = get_processing_reconciliation_database_inspection(
            request_id
        )
    except (SQLAlchemyError, ValidationError) as exc:
        return ProcessingReconciliationDiagnosisError(
            request_id=request_id,
            processing_check_id=None,
            reason=(
                ReconciliationDiagnosisErrorReason.database_inspection_failed
            ),
            detail=str(exc) or "database inspection failed",
            diagnosed_at=diagnosed_at_utc,
        )

    if inspection is None:
        raise ProcessingReconciliationRequestNotFoundError(request_id)

    token = inspection.request.processing_check_id
    if not is_canonical_processing_check_id(token):
        facts = ProcessingReconciliationFacts(
            request=inspection.request,
            database=inspection.database,
            artifacts=ReconciliationArtifactFacts(
                json_artifact=_neutral_json_artifact_facts(),
                markdown_artifact=_neutral_file_artifact_facts(),
            ),
            diagnosed_at=diagnosed_at_utc,
            stale_after=stale_after,
        )
        return classify_processing_reconciliation(facts)

    assert token is not None
    try:
        trusted_root = _trusted_outputs_root()
        expected_json_path = report_agent.json_path_for_check(int(token))
        expected_markdown_path = report_agent.markdown_path_for_check(
            int(token)
        )
        json_snapshot = _inspect_json_artifact(
            expected_json_path,
            trusted_root=trusted_root,
        )
        markdown_snapshot = _inspect_file_artifact(
            expected_markdown_path,
            trusted_root=trusted_root,
        )
    except OSError as exc:
        return ProcessingReconciliationDiagnosisError(
            request_id=request_id,
            processing_check_id=token,
            reason=(
                ReconciliationDiagnosisErrorReason.artifact_inspection_failed
            ),
            detail=str(exc) or "artifact inspection failed",
            diagnosed_at=diagnosed_at_utc,
        )

    database_facts = _with_report_consistency(
        inspection.database,
        reports=inspection.token_report_records,
        expected_json_path=expected_json_path,
        expected_markdown_path=expected_markdown_path,
        json_snapshot=json_snapshot,
        markdown_snapshot=markdown_snapshot,
    )
    facts = ProcessingReconciliationFacts(
        request=inspection.request,
        database=database_facts,
        artifacts=ReconciliationArtifactFacts(
            json_artifact=json_snapshot.facts,
            markdown_artifact=markdown_snapshot.facts,
        ),
        diagnosed_at=diagnosed_at_utc,
        stale_after=stale_after,
    )
    return classify_processing_reconciliation(facts)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _neutral_file_artifact_facts() -> ArtifactFileFacts:
    return ArtifactFileFacts(
        exists=False,
        is_regular_file=False,
        is_symlink=False,
        within_output_root=True,
        utf8_readable=False,
    )


def _neutral_json_artifact_facts() -> JsonArtifactFacts:
    return JsonArtifactFacts(
        exists=False,
        is_regular_file=False,
        is_symlink=False,
        within_output_root=True,
        utf8_readable=False,
        json_valid=False,
        parsed_check_id=None,
    )


def _safe_resolve(path: Path) -> Path:
    """Resolve a path; map symlink-loop RuntimeError to OSError."""
    try:
        return path.resolve(strict=False)
    except RuntimeError as exc:
        # Path.resolve may raise RuntimeError on symlink loops.
        raise OSError(str(exc) or "path resolve failed") from exc


def _trusted_outputs_root() -> Path:
    """Resolve the shared outputs root from report_agent directories at call time."""
    json_root = _safe_resolve(report_agent.JSON_DIR.parent)
    markdown_root = _safe_resolve(report_agent.REPORTS_DIR.parent)
    if json_root != markdown_root:
        raise OSError(
            "JSON_DIR and REPORTS_DIR do not share the same parent root"
        )

    json_dir = _safe_resolve(report_agent.JSON_DIR)
    reports_dir = _safe_resolve(report_agent.REPORTS_DIR)
    if not _is_strict_descendant(json_dir, json_root):
        raise OSError("JSON_DIR is not a strict descendant of outputs root")
    if not _is_strict_descendant(reports_dir, json_root):
        raise OSError(
            "REPORTS_DIR is not a strict descendant of outputs root"
        )
    return json_root


def _is_strict_descendant(path: Path, root: Path) -> bool:
    if path == root:
        return False
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _inspect_file_artifact(
    expected_path: Path,
    *,
    trusted_root: Path,
) -> _ArtifactInspectionSnapshot:
    try:
        file_stat = expected_path.lstat()
    except FileNotFoundError:
        resolved = _safe_resolve(expected_path)
        within_output_root = _is_within_root(resolved, trusted_root)
        return _ArtifactInspectionSnapshot(
            expected_path=expected_path,
            facts=ArtifactFileFacts(
                exists=False,
                is_regular_file=False,
                is_symlink=False,
                within_output_root=within_output_root,
                utf8_readable=False,
            ),
            content=None,
            sha256=None,
        )

    is_symlink = stat.S_ISLNK(file_stat.st_mode)
    is_regular_file = stat.S_ISREG(file_stat.st_mode)
    resolved = _safe_resolve(expected_path)
    within_output_root = _is_within_root(resolved, trusted_root)

    if is_symlink or not is_regular_file or not within_output_root:
        return _ArtifactInspectionSnapshot(
            expected_path=expected_path,
            facts=ArtifactFileFacts(
                exists=True,
                is_regular_file=is_regular_file,
                is_symlink=is_symlink,
                within_output_root=within_output_root,
                utf8_readable=False,
            ),
            content=None,
            sha256=None,
        )

    with expected_path.open("rb") as handle:
        raw = handle.read()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _ArtifactInspectionSnapshot(
            expected_path=expected_path,
            facts=ArtifactFileFacts(
                exists=True,
                is_regular_file=True,
                is_symlink=False,
                within_output_root=True,
                utf8_readable=False,
            ),
            content=None,
            sha256=digest,
        )

    return _ArtifactInspectionSnapshot(
        expected_path=expected_path,
        facts=ArtifactFileFacts(
            exists=True,
            is_regular_file=True,
            is_symlink=False,
            within_output_root=True,
            utf8_readable=True,
        ),
        content=text,
        sha256=digest,
    )


def _inspect_json_artifact(
    expected_path: Path,
    *,
    trusted_root: Path,
) -> _JsonArtifactInspectionSnapshot:
    try:
        file_stat = expected_path.lstat()
    except FileNotFoundError:
        resolved = _safe_resolve(expected_path)
        within_output_root = _is_within_root(resolved, trusted_root)
        return _JsonArtifactInspectionSnapshot(
            expected_path=expected_path,
            facts=JsonArtifactFacts(
                exists=False,
                is_regular_file=False,
                is_symlink=False,
                within_output_root=within_output_root,
                utf8_readable=False,
                json_valid=False,
                parsed_check_id=None,
            ),
            content=None,
            sha256=None,
        )

    is_symlink = stat.S_ISLNK(file_stat.st_mode)
    is_regular_file = stat.S_ISREG(file_stat.st_mode)
    resolved = _safe_resolve(expected_path)
    within_output_root = _is_within_root(resolved, trusted_root)

    if is_symlink or not is_regular_file or not within_output_root:
        return _JsonArtifactInspectionSnapshot(
            expected_path=expected_path,
            facts=JsonArtifactFacts(
                exists=True,
                is_regular_file=is_regular_file,
                is_symlink=is_symlink,
                within_output_root=within_output_root,
                utf8_readable=False,
                json_valid=False,
                parsed_check_id=None,
            ),
            content=None,
            sha256=None,
        )

    with expected_path.open("rb") as handle:
        raw = handle.read()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _JsonArtifactInspectionSnapshot(
            expected_path=expected_path,
            facts=JsonArtifactFacts(
                exists=True,
                is_regular_file=True,
                is_symlink=False,
                within_output_root=True,
                utf8_readable=False,
                json_valid=False,
                parsed_check_id=None,
            ),
            content=None,
            sha256=digest,
        )

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _JsonArtifactInspectionSnapshot(
            expected_path=expected_path,
            facts=JsonArtifactFacts(
                exists=True,
                is_regular_file=True,
                is_symlink=False,
                within_output_root=True,
                utf8_readable=True,
                json_valid=False,
                parsed_check_id=None,
            ),
            content=text,
            sha256=digest,
        )

    parsed_check_id: str | None = None
    if isinstance(parsed, dict):
        check_id = parsed.get("check_id")
        if isinstance(check_id, str) and check_id.strip() != "":
            parsed_check_id = check_id

    return _JsonArtifactInspectionSnapshot(
        expected_path=expected_path,
        facts=JsonArtifactFacts(
            exists=True,
            is_regular_file=True,
            is_symlink=False,
            within_output_root=True,
            utf8_readable=True,
            json_valid=True,
            parsed_check_id=parsed_check_id,
        ),
        content=text,
        sha256=digest,
    )


def _with_report_consistency(
    database: ReconciliationDatabaseFacts,
    *,
    reports: tuple[ReconciliationReportSnapshot, ...],
    expected_json_path: Path,
    expected_markdown_path: Path,
    json_snapshot: _JsonArtifactInspectionSnapshot,
    markdown_snapshot: _ArtifactInspectionSnapshot,
) -> ReconciliationDatabaseFacts:
    if len(reports) == 0:
        return database

    if len(reports) > 1:
        return database.model_copy(
            update={
                "report_json_path_consistency": (
                    ReconciliationConsistency.inconsistent
                ),
                "report_markdown_path_consistency": (
                    ReconciliationConsistency.inconsistent
                ),
                "report_json_content_consistency": (
                    ReconciliationConsistency.inconsistent
                ),
                "report_markdown_content_consistency": (
                    ReconciliationConsistency.inconsistent
                ),
            }
        )

    report = reports[0]
    expected_json = str(expected_json_path)
    expected_markdown = str(expected_markdown_path)

    json_path_consistency = (
        ReconciliationConsistency.consistent
        if report.json_path == expected_json
        else ReconciliationConsistency.inconsistent
    )
    markdown_path_consistency = (
        ReconciliationConsistency.consistent
        if report.markdown_path == expected_markdown
        else ReconciliationConsistency.inconsistent
    )

    if json_snapshot.content is None:
        json_content_consistency = ReconciliationConsistency.not_checked
    elif report.json_content == json_snapshot.content:
        json_content_consistency = ReconciliationConsistency.consistent
    else:
        json_content_consistency = ReconciliationConsistency.inconsistent

    if markdown_snapshot.content is None:
        markdown_content_consistency = ReconciliationConsistency.not_checked
    elif report.markdown_content == markdown_snapshot.content:
        markdown_content_consistency = ReconciliationConsistency.consistent
    else:
        markdown_content_consistency = ReconciliationConsistency.inconsistent

    return database.model_copy(
        update={
            "report_json_path_consistency": json_path_consistency,
            "report_markdown_path_consistency": markdown_path_consistency,
            "report_json_content_consistency": json_content_consistency,
            "report_markdown_content_consistency": (
                markdown_content_consistency
            ),
        }
    )
