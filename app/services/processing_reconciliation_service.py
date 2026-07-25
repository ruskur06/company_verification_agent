"""Processing-request reconciliation diagnosis and finalize service."""

from __future__ import annotations

import hashlib
import json
import stat
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

import app.agents.report_agent as report_agent
from app.db.repositories import (
    REQUIRED_RECONCILIATION_TOOL_CALLS,
    ProcessingReconciliationFinalizeRequestNotFoundError,
    ProcessingReconciliationFinalizeResult,
    finalize_processing_reconciliation_request,
    get_processing_reconciliation_database_inspection,
    get_processing_reconciliation_database_inspection_for_token,
    list_processing_check_request_records,
    record_processing_reconciliation_audit_only,
)
from app.db.models import ReconciliationActionRecord
from app.schemas.check_request import CheckRequestStatus
from app.schemas.processing_reconciliation import (
    ArtifactFileFacts,
    JsonArtifactFacts,
    ProcessingReconciliationDiagnosis,
    ProcessingReconciliationDiagnosisError,
    ProcessingReconciliationFacts,
    ProcessingReconciliationRequestSummary,
    ProcessingReconciliationResult,
    ProcessingRequestFacts,
    ReconciliationArtifactFacts,
    ReconciliationClassification,
    ReconciliationConsistency,
    ReconciliationDatabaseFacts,
    ReconciliationDiagnosisErrorReason,
    ReconciliationReportSnapshot,
    is_canonical_processing_check_id,
)
from app.services.processing_reconciliation_classifier import (
    classify_processing_reconciliation,
)


PROCESSING_RECONCILIATION_STALE_AFTER = timedelta(minutes=30)
PROCESSING_RECONCILIATION_ACTOR_LABEL = "internal-unauthenticated"


class ProcessingReconciliationRequestNotFoundError(LookupError):
    """Raised when diagnosis is requested for a missing CheckRequest."""

    def __init__(self, request_id: int) -> None:
        self.request_id = request_id
        super().__init__(
            f"Check request {request_id} was not found for reconciliation "
            f"diagnosis."
        )


class ProcessingReconciliationValidationError(ValueError):
    """Raised for known finalize input validation failures."""

    pass


def list_processing_reconciliation_requests(
    limit: int = 50,
) -> list[ProcessingReconciliationRequestSummary]:
    """List processing requests for the internal reconciliation UI."""
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit <= 0
    ):
        raise ValueError("limit must be a positive integer")

    records = list_processing_check_request_records(limit=limit)
    summaries: list[ProcessingReconciliationRequestSummary] = []
    for record in records:
        created_at = _as_aware_utc(record["created_at"])
        if created_at is None:
            raise ValueError("created_at is required")
        summaries.append(
            ProcessingReconciliationRequestSummary(
                id=record["id"],
                company_name=record["company_name"],
                country=record["country"],
                processing_check_id=record["processing_check_id"],
                processing_started_at=_as_aware_utc(
                    record["processing_started_at"]
                ),
                created_at=created_at,
                company_check_id=record["company_check_id"],
            )
        )
    return summaries


def _as_aware_utc(value: datetime | None) -> datetime | None:
    """Treat naive timestamps as UTC and normalize aware values to UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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


@dataclass(frozen=True)
class _PreparedFinalization:
    """Immutable prepared finalization snapshot for one POST attempt."""

    request_id: int
    expected_processing_check_id: str
    request: ProcessingRequestFacts
    database: ReconciliationDatabaseFacts
    diagnosis: ProcessingReconciliationDiagnosis
    json_snapshot: _JsonArtifactInspectionSnapshot
    markdown_snapshot: _ArtifactInspectionSnapshot
    artifact_json_sha256: str | None
    artifact_markdown_sha256: str | None
    diagnosis_snapshot_json: str


def finalize_processing_reconciliation(
    request_id: int,
    *,
    expected_processing_check_id: str,
    operator_note: str | None = None,
) -> ProcessingReconciliationFinalizeResult:
    """Freshly verify and finalize one processing reconciliation request."""
    validated_request_id = _validate_finalize_request_id(request_id)
    token = _validate_finalize_expected_token(expected_processing_check_id)
    note = _normalize_operator_note(operator_note)

    prepared = _prepare_finalization(
        validated_request_id,
        expected_processing_check_id=token,
    )

    try:
        if _is_candidate_new_finalization(prepared):
            # The filesystem can change between the service read and the
            # database UPDATE. This is an explicitly accepted residual race
            # for the current single-operator, tailnet-only deployment. The
            # fenced UPDATE still protects DB ownership and DB evidence.
            return finalize_processing_reconciliation_request(
                prepared.request_id,
                expected_processing_check_id=prepared.expected_processing_check_id,
                actor_label=PROCESSING_RECONCILIATION_ACTOR_LABEL,
                diagnosis_snapshot_json=prepared.diagnosis_snapshot_json,
                operator_note=note,
                artifact_json_sha256=prepared.artifact_json_sha256,
                artifact_markdown_sha256=prepared.artifact_markdown_sha256,
            )

        if _is_candidate_already_processed(prepared):
            # The filesystem can change between the service read and the
            # database UPDATE. This is an explicitly accepted residual race
            # for the current single-operator, tailnet-only deployment. The
            # fenced UPDATE still protects DB ownership and DB evidence.
            return finalize_processing_reconciliation_request(
                prepared.request_id,
                expected_processing_check_id=prepared.expected_processing_check_id,
                actor_label=PROCESSING_RECONCILIATION_ACTOR_LABEL,
                diagnosis_snapshot_json=prepared.diagnosis_snapshot_json,
                operator_note=note,
                artifact_json_sha256=prepared.artifact_json_sha256,
                artifact_markdown_sha256=prepared.artifact_markdown_sha256,
            )

        if _is_known_conflict(prepared):
            return record_processing_reconciliation_audit_only(
                prepared.request_id,
                expected_processing_check_id=prepared.expected_processing_check_id,
                outcome="conflict",
                actor_label=PROCESSING_RECONCILIATION_ACTOR_LABEL,
                diagnosis_snapshot_json=prepared.diagnosis_snapshot_json,
                operator_note=note,
                artifact_json_sha256=prepared.artifact_json_sha256,
                artifact_markdown_sha256=prepared.artifact_markdown_sha256,
            )

        return record_processing_reconciliation_audit_only(
            prepared.request_id,
            expected_processing_check_id=prepared.expected_processing_check_id,
            outcome="precondition_failed",
            actor_label=PROCESSING_RECONCILIATION_ACTOR_LABEL,
            diagnosis_snapshot_json=prepared.diagnosis_snapshot_json,
            operator_note=note,
            artifact_json_sha256=prepared.artifact_json_sha256,
            artifact_markdown_sha256=prepared.artifact_markdown_sha256,
        )
    except ProcessingReconciliationFinalizeRequestNotFoundError as exc:
        raise ProcessingReconciliationRequestNotFoundError(
            exc.request_id
        ) from exc


def _validate_finalize_request_id(request_id: int) -> int:
    if (
        isinstance(request_id, bool)
        or not isinstance(request_id, int)
        or request_id <= 0
    ):
        raise ProcessingReconciliationValidationError(
            "request_id must be a positive integer"
        )
    return request_id


def _validate_finalize_expected_token(token: str) -> str:
    if not isinstance(token, str) or not is_canonical_processing_check_id(token):
        raise ProcessingReconciliationValidationError(
            "expected_processing_check_id must be a canonical processing "
            "check id"
        )
    return token


def _normalize_operator_note(operator_note: str | None) -> str | None:
    if operator_note is None:
        return None
    if not isinstance(operator_note, str):
        raise ProcessingReconciliationValidationError(
            "operator_note must be a string or None"
        )
    if operator_note.strip() == "":
        return None
    column = ReconciliationActionRecord.__table__.c.operator_note
    max_length = getattr(column.type, "length", None)
    if max_length is not None and len(operator_note) > max_length:
        raise ProcessingReconciliationValidationError(
            "operator_note exceeds the audit-column length constraint"
        )
    return operator_note


def _prepare_finalization(
    request_id: int,
    *,
    expected_processing_check_id: str,
) -> _PreparedFinalization:
    inspection = get_processing_reconciliation_database_inspection_for_token(
        request_id,
        expected_processing_check_id,
    )
    if inspection is None:
        raise ProcessingReconciliationRequestNotFoundError(request_id)

    token = expected_processing_check_id
    trusted_root = _trusted_outputs_root()
    expected_json_path = report_agent.json_path_for_check(int(token))
    expected_markdown_path = report_agent.markdown_path_for_check(int(token))
    json_snapshot = _inspect_json_artifact(
        expected_json_path,
        trusted_root=trusted_root,
    )
    markdown_snapshot = _inspect_file_artifact(
        expected_markdown_path,
        trusted_root=trusted_root,
    )

    database_facts = _with_report_consistency(
        inspection.database,
        reports=inspection.token_report_records,
        expected_json_path=expected_json_path,
        expected_markdown_path=expected_markdown_path,
        json_snapshot=json_snapshot,
        markdown_snapshot=markdown_snapshot,
    )
    diagnosed_at = _utc_now()
    facts = ProcessingReconciliationFacts(
        request=inspection.request,
        database=database_facts,
        artifacts=ReconciliationArtifactFacts(
            json_artifact=json_snapshot.facts,
            markdown_artifact=markdown_snapshot.facts,
        ),
        diagnosed_at=diagnosed_at,
        stale_after=PROCESSING_RECONCILIATION_STALE_AFTER,
    )
    diagnosis = classify_processing_reconciliation(facts)
    snapshot_json = _build_audit_snapshot_json(
        request_id=request_id,
        expected_processing_check_id=token,
        request=inspection.request,
        database=database_facts,
        diagnosis=diagnosis,
        json_facts=json_snapshot.facts,
        markdown_facts=markdown_snapshot.facts,
        artifact_json_sha256=json_snapshot.sha256,
        artifact_markdown_sha256=markdown_snapshot.sha256,
    )
    return _PreparedFinalization(
        request_id=request_id,
        expected_processing_check_id=token,
        request=inspection.request,
        database=database_facts,
        diagnosis=diagnosis,
        json_snapshot=json_snapshot,
        markdown_snapshot=markdown_snapshot,
        artifact_json_sha256=json_snapshot.sha256,
        artifact_markdown_sha256=markdown_snapshot.sha256,
        diagnosis_snapshot_json=snapshot_json,
    )


def _build_audit_snapshot_json(
    *,
    request_id: int,
    expected_processing_check_id: str,
    request: ProcessingRequestFacts,
    database: ReconciliationDatabaseFacts,
    diagnosis: ProcessingReconciliationDiagnosis,
    json_facts: JsonArtifactFacts,
    markdown_facts: ArtifactFileFacts,
    artifact_json_sha256: str | None,
    artifact_markdown_sha256: str | None,
) -> str:
    payload = {
        "request_id": request_id,
        "expected_processing_check_id": expected_processing_check_id,
        "request": request.model_dump(mode="json"),
        "database": database.model_dump(mode="json"),
        "artifacts": {
            "json_artifact": json_facts.model_dump(mode="json"),
            "markdown_artifact": markdown_facts.model_dump(mode="json"),
        },
        "diagnosis": diagnosis.model_dump(mode="json"),
        "artifact_json_sha256": artifact_json_sha256,
        "artifact_markdown_sha256": artifact_markdown_sha256,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _status_value(status: CheckRequestStatus | str) -> str:
    if isinstance(status, CheckRequestStatus):
        return status.value
    return status


def _db_evidence_complete(
    prepared: _PreparedFinalization,
) -> bool:
    database = prepared.database
    request_id = prepared.request_id
    if database.foreign_processing_token_request_ids:
        return False
    if database.matching_company_check_source_request_ids != (request_id,):
        return False
    if database.report_record_count != 1:
        return False
    if len(database.tool_call_names) != len(REQUIRED_RECONCILIATION_TOOL_CALLS):
        return False
    counts = Counter(database.tool_call_names)
    expected = Counter(REQUIRED_RECONCILIATION_TOOL_CALLS)
    return counts == expected


def _filesystem_evidence_complete_and_consistent(
    prepared: _PreparedFinalization,
) -> bool:
    token = prepared.expected_processing_check_id
    database = prepared.database
    json_facts = prepared.json_snapshot.facts
    markdown_facts = prepared.markdown_snapshot.facts

    if not (
        json_facts.exists
        and json_facts.is_regular_file
        and not json_facts.is_symlink
        and json_facts.within_output_root
        and json_facts.utf8_readable
        and json_facts.json_valid
        and json_facts.parsed_check_id == token
    ):
        return False
    if not (
        markdown_facts.exists
        and markdown_facts.is_regular_file
        and not markdown_facts.is_symlink
        and markdown_facts.within_output_root
        and markdown_facts.utf8_readable
    ):
        return False
    if (
        database.report_json_path_consistency
        is not ReconciliationConsistency.consistent
    ):
        return False
    if (
        database.report_markdown_path_consistency
        is not ReconciliationConsistency.consistent
    ):
        return False
    if (
        database.report_json_content_consistency
        is not ReconciliationConsistency.consistent
    ):
        return False
    if (
        database.report_markdown_content_consistency
        is not ReconciliationConsistency.consistent
    ):
        return False
    return True


def _is_candidate_new_finalization(prepared: _PreparedFinalization) -> bool:
    request = prepared.request
    token = prepared.expected_processing_check_id
    return (
        _status_value(request.status) == CheckRequestStatus.processing.value
        and request.processing_check_id == token
        and request.processing_started_at is not None
        and request.company_check_id is None
        and prepared.diagnosis.classification
        is ReconciliationClassification.stale_persisted_complete
        and _db_evidence_complete(prepared)
        and _filesystem_evidence_complete_and_consistent(prepared)
    )


def _is_candidate_already_processed(prepared: _PreparedFinalization) -> bool:
    request = prepared.request
    token = prepared.expected_processing_check_id
    return (
        _status_value(request.status) == CheckRequestStatus.processed.value
        and request.company_check_id == token
        and request.processing_check_id is None
        and request.processing_started_at is None
        and _db_evidence_complete(prepared)
        and _filesystem_evidence_complete_and_consistent(prepared)
        and prepared.json_snapshot.facts.parsed_check_id == token
    )


def _is_known_conflict(prepared: _PreparedFinalization) -> bool:
    request = prepared.request
    token = prepared.expected_processing_check_id
    status = _status_value(request.status)
    if prepared.database.foreign_processing_token_request_ids:
        return True
    for source_id in prepared.database.matching_company_check_source_request_ids:
        if (
            isinstance(source_id, int)
            and not isinstance(source_id, bool)
            and source_id > 0
            and source_id != prepared.request_id
        ):
            return True
    if (
        status == CheckRequestStatus.processing.value
        and request.processing_check_id is not None
        and request.processing_check_id != token
    ):
        return True
    if (
        status == CheckRequestStatus.processed.value
        and request.company_check_id is not None
        and request.company_check_id != token
    ):
        return True
    return False
