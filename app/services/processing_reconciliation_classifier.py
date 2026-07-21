"""Pure deterministic classifier for processing-request reconciliation."""

from __future__ import annotations

from collections import Counter

from app.schemas.check_request import CheckRequestStatus
from app.schemas.processing_reconciliation import (
    ProcessingReconciliationDiagnosis,
    ProcessingReconciliationFacts,
    ReconciliationClassification,
    ReconciliationConsistency,
)


REQUIRED_TOOL_CALLS: tuple[str, ...] = (
    "web_search",
    "domain_dns_check",
    "registry_search",
    "risk_score",
)
_REQUIRED_TOOL_CALL_COUNTS = Counter(REQUIRED_TOOL_CALLS)


def _status_value(status: CheckRequestStatus | str) -> str:
    if isinstance(status, CheckRequestStatus):
        return status.value
    return status


def classify_processing_reconciliation(
    facts: ProcessingReconciliationFacts,
) -> ProcessingReconciliationDiagnosis:
    """Classify complete reconciliation facts without I/O or mutation."""
    request = facts.request
    database = facts.database
    artifacts = facts.artifacts

    core_reasons = _core_inconsistency_reasons(facts)
    if core_reasons:
        return _diagnosis(
            facts,
            classification=ReconciliationClassification.processing_inconsistent,
            age_seconds=None,
            reasons=tuple(core_reasons),
        )

    structural_reasons = _structural_inconsistency_reasons(facts)
    if structural_reasons:
        return _diagnosis(
            facts,
            classification=ReconciliationClassification.processing_inconsistent,
            age_seconds=None,
            reasons=tuple(structural_reasons),
        )

    assert request.processing_started_at is not None
    age = facts.diagnosed_at - request.processing_started_at
    age_seconds = age.total_seconds()

    if age < facts.stale_after:
        return _diagnosis(
            facts,
            classification=(
                ReconciliationClassification.within_processing_window
            ),
            age_seconds=age_seconds,
            reasons=(),
        )

    matching_ids = database.matching_company_check_source_request_ids
    if len(matching_ids) == 0:
        if _has_no_result_evidence(facts):
            return _diagnosis(
                facts,
                classification=(
                    ReconciliationClassification.stale_no_result_evidence
                ),
                age_seconds=age_seconds,
                reasons=(),
            )
        return _diagnosis(
            facts,
            classification=(
                ReconciliationClassification.stale_artifacts_unpersisted
            ),
            age_seconds=age_seconds,
            reasons=(),
        )

    incomplete_reasons = _incomplete_reasons(facts)
    if incomplete_reasons:
        return _diagnosis(
            facts,
            classification=(
                ReconciliationClassification.stale_persisted_incomplete
            ),
            age_seconds=age_seconds,
            reasons=tuple(incomplete_reasons),
        )

    return _diagnosis(
        facts,
        classification=ReconciliationClassification.stale_persisted_complete,
        age_seconds=age_seconds,
        reasons=(),
    )


def _diagnosis(
    facts: ProcessingReconciliationFacts,
    *,
    classification: ReconciliationClassification,
    age_seconds: float | None,
    reasons: tuple[str, ...],
) -> ProcessingReconciliationDiagnosis:
    return ProcessingReconciliationDiagnosis(
        request_id=facts.request.request_id,
        processing_check_id=facts.request.processing_check_id,
        classification=classification,
        diagnosed_at=facts.diagnosed_at,
        age_seconds=age_seconds,
        reasons=reasons,
    )


def _core_inconsistency_reasons(
    facts: ProcessingReconciliationFacts,
) -> list[str]:
    request = facts.request
    reasons: list[str] = []

    if _status_value(request.status) != CheckRequestStatus.processing.value:
        reasons.append("status_not_processing")
    if request.processing_check_id is None:
        reasons.append("missing_processing_check_id")
    if request.processing_started_at is None:
        reasons.append("missing_processing_started_at")
    if request.company_check_id is not None:
        reasons.append("company_check_id_set_during_processing")
    if (
        request.processing_started_at is not None
        and facts.diagnosed_at < request.processing_started_at
    ):
        reasons.append("diagnosed_at_before_processing_started_at")

    return reasons


def _structural_inconsistency_reasons(
    facts: ProcessingReconciliationFacts,
) -> list[str]:
    request = facts.request
    database = facts.database
    artifacts = facts.artifacts
    reasons: list[str] = []

    if database.foreign_processing_token_request_ids:
        reasons.append("foreign_processing_token_owner")

    matching_ids = database.matching_company_check_source_request_ids
    if len(matching_ids) > 1:
        reasons.append("duplicate_matching_company_checks")
    elif (
        len(matching_ids) == 1
        and matching_ids[0] is not None
        and matching_ids[0] != request.request_id
    ):
        reasons.append("matching_company_check_foreign_owner")

    if database.orphan_source_record_count > 0:
        reasons.append("orphan_source_records")
    if database.orphan_tool_call_record_count > 0:
        reasons.append("orphan_tool_call_records")
    if database.orphan_report_record_count > 0:
        reasons.append("orphan_report_records")

    if database.report_record_count > 1:
        reasons.append("duplicate_report_records")

    reasons.extend(_tool_call_structural_reasons(database.tool_call_names))

    if (
        artifacts.json_artifact.json_valid
        and artifacts.json_artifact.parsed_check_id is not None
        and artifacts.json_artifact.parsed_check_id != request.processing_check_id
    ):
        reasons.append("json_check_id_mismatch")

    if artifacts.json_artifact.is_symlink:
        reasons.append("json_artifact_is_symlink")
    if artifacts.markdown_artifact.is_symlink:
        reasons.append("markdown_artifact_is_symlink")

    if not artifacts.json_artifact.within_output_root:
        reasons.append("json_artifact_outside_output_root")
    if not artifacts.markdown_artifact.within_output_root:
        reasons.append("markdown_artifact_outside_output_root")

    if (
        database.report_json_path_consistency
        is ReconciliationConsistency.inconsistent
    ):
        reasons.append("report_json_path_inconsistent")
    if (
        database.report_markdown_path_consistency
        is ReconciliationConsistency.inconsistent
    ):
        reasons.append("report_markdown_path_inconsistent")
    if (
        database.report_json_content_consistency
        is ReconciliationConsistency.inconsistent
    ):
        reasons.append("report_json_content_inconsistent")
    if (
        database.report_markdown_content_consistency
        is ReconciliationConsistency.inconsistent
    ):
        reasons.append("report_markdown_content_inconsistent")

    if len(matching_ids) == 0 and (
        database.source_record_count > 0
        or len(database.tool_call_names) > 0
        or database.report_record_count > 0
    ):
        reasons.append("related_db_evidence_without_company_check")

    return reasons


def _tool_call_structural_reasons(names: tuple[str, ...]) -> list[str]:
    reasons: list[str] = []
    counts = Counter(names)

    for name in sorted(counts):
        count = counts[name]
        if name not in _REQUIRED_TOOL_CALL_COUNTS:
            reasons.append(f"unknown_tool_call:{name}")
        elif count > _REQUIRED_TOOL_CALL_COUNTS[name]:
            reasons.append(f"duplicate_tool_call:{name}")

    return reasons


def _has_no_result_evidence(facts: ProcessingReconciliationFacts) -> bool:
    database = facts.database
    artifacts = facts.artifacts
    return (
        database.source_record_count == 0
        and len(database.tool_call_names) == 0
        and database.report_record_count == 0
        and not artifacts.json_artifact.exists
        and not artifacts.markdown_artifact.exists
    )


def _incomplete_reasons(facts: ProcessingReconciliationFacts) -> list[str]:
    database = facts.database
    artifacts = facts.artifacts
    reasons: list[str] = []

    matching_ids = database.matching_company_check_source_request_ids
    if matching_ids and matching_ids[0] is None:
        reasons.append("matching_company_check_missing_source_request_id")

    if database.report_record_count == 0:
        reasons.append("missing_report_record")

    counts = Counter(database.tool_call_names)
    for name in REQUIRED_TOOL_CALLS:
        if counts[name] < 1:
            reasons.append(f"missing_tool_call:{name}")

    if not artifacts.json_artifact.exists:
        reasons.append("missing_json_artifact")
    if not artifacts.markdown_artifact.exists:
        reasons.append("missing_markdown_artifact")

    if artifacts.json_artifact.exists and not artifacts.json_artifact.is_regular_file:
        reasons.append("json_artifact_not_regular_file")
    if artifacts.markdown_artifact.exists and not artifacts.markdown_artifact.is_regular_file:
        reasons.append("markdown_artifact_not_regular_file")

    if artifacts.json_artifact.exists and not artifacts.json_artifact.utf8_readable:
        reasons.append("json_artifact_not_utf8_readable")
    if artifacts.markdown_artifact.exists and not artifacts.markdown_artifact.utf8_readable:
        reasons.append("markdown_artifact_not_utf8_readable")

    if artifacts.json_artifact.exists and not artifacts.json_artifact.json_valid:
        reasons.append("json_artifact_invalid")
    if (
        artifacts.json_artifact.exists
        and artifacts.json_artifact.json_valid
        and artifacts.json_artifact.parsed_check_id is None
    ):
        reasons.append("json_check_id_missing")

    if (
        database.report_json_path_consistency
        is ReconciliationConsistency.not_checked
    ):
        reasons.append("report_json_path_not_checked")
    if (
        database.report_markdown_path_consistency
        is ReconciliationConsistency.not_checked
    ):
        reasons.append("report_markdown_path_not_checked")
    if (
        database.report_json_content_consistency
        is ReconciliationConsistency.not_checked
    ):
        reasons.append("report_json_content_not_checked")
    if (
        database.report_markdown_content_consistency
        is ReconciliationConsistency.not_checked
    ):
        reasons.append("report_markdown_content_not_checked")

    return reasons
