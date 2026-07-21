"""Classifier tests for processing reconciliation diagnosis."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas.check_request import CheckRequestStatus
from app.schemas.processing_reconciliation import (
    ArtifactFileFacts,
    JsonArtifactFacts,
    ProcessingReconciliationFacts,
    ProcessingRequestFacts,
    ReconciliationArtifactFacts,
    ReconciliationClassification,
    ReconciliationConsistency,
    ReconciliationDatabaseFacts,
)
from app.services.processing_reconciliation_classifier import (
    REQUIRED_TOOL_CALLS,
    classify_processing_reconciliation,
)


REQUEST_ID = 42
PROCESSING_CHECK_ID = "1782245999001"
STARTED_AT = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
STALE_AFTER = timedelta(hours=1)
WITHIN_WINDOW_AT = STARTED_AT + timedelta(minutes=30)
STALE_AT = STARTED_AT + timedelta(hours=2)
EXACT_BOUNDARY_AT = STARTED_AT + STALE_AFTER


def _request(
    *,
    status: CheckRequestStatus | str = CheckRequestStatus.processing,
    company_check_id: str | None = None,
    processing_check_id: str | None = PROCESSING_CHECK_ID,
    processing_started_at: datetime | None = STARTED_AT,
) -> ProcessingRequestFacts:
    return ProcessingRequestFacts(
        request_id=REQUEST_ID,
        status=status,
        company_check_id=company_check_id,
        processing_check_id=processing_check_id,
        processing_started_at=processing_started_at,
    )


def _missing_file() -> ArtifactFileFacts:
    return ArtifactFileFacts(
        exists=False,
        is_regular_file=False,
        is_symlink=False,
        within_output_root=True,
        utf8_readable=False,
    )


def _present_file(
    *,
    is_symlink: bool = False,
    within_output_root: bool = True,
    utf8_readable: bool = True,
    is_regular_file: bool = True,
) -> ArtifactFileFacts:
    return ArtifactFileFacts(
        exists=True,
        is_regular_file=is_regular_file,
        is_symlink=is_symlink,
        within_output_root=within_output_root,
        utf8_readable=utf8_readable,
    )


def _missing_json() -> JsonArtifactFacts:
    return JsonArtifactFacts(
        exists=False,
        is_regular_file=False,
        is_symlink=False,
        within_output_root=True,
        utf8_readable=False,
        json_valid=False,
        parsed_check_id=None,
    )


def _present_json(
    *,
    json_valid: bool = True,
    parsed_check_id: str | None = PROCESSING_CHECK_ID,
    is_symlink: bool = False,
    within_output_root: bool = True,
    utf8_readable: bool = True,
    is_regular_file: bool = True,
) -> JsonArtifactFacts:
    return JsonArtifactFacts(
        exists=True,
        is_regular_file=is_regular_file,
        is_symlink=is_symlink,
        within_output_root=within_output_root,
        utf8_readable=utf8_readable,
        json_valid=json_valid,
        parsed_check_id=parsed_check_id if json_valid else None,
    )


def _consistent_db(
    *,
    matching_company_check_source_request_ids: tuple[int | None, ...] = (),
    foreign_processing_token_request_ids: tuple[int, ...] = (),
    source_record_count: int = 0,
    tool_call_names: tuple[str, ...] = (),
    report_record_count: int = 0,
    orphan_source_record_count: int = 0,
    orphan_tool_call_record_count: int = 0,
    orphan_report_record_count: int = 0,
    path_content: ReconciliationConsistency = (
        ReconciliationConsistency.not_checked
    ),
) -> ReconciliationDatabaseFacts:
    return ReconciliationDatabaseFacts(
        matching_company_check_source_request_ids=(
            matching_company_check_source_request_ids
        ),
        foreign_processing_token_request_ids=(
            foreign_processing_token_request_ids
        ),
        source_record_count=source_record_count,
        tool_call_names=tool_call_names,
        report_record_count=report_record_count,
        orphan_source_record_count=orphan_source_record_count,
        orphan_tool_call_record_count=orphan_tool_call_record_count,
        orphan_report_record_count=orphan_report_record_count,
        report_json_path_consistency=path_content,
        report_markdown_path_consistency=path_content,
        report_json_content_consistency=path_content,
        report_markdown_content_consistency=path_content,
    )


def _facts(
    *,
    request: ProcessingRequestFacts | None = None,
    database: ReconciliationDatabaseFacts | None = None,
    json_artifact: JsonArtifactFacts | None = None,
    markdown_artifact: ArtifactFileFacts | None = None,
    diagnosed_at: datetime = STALE_AT,
    stale_after: timedelta = STALE_AFTER,
) -> ProcessingReconciliationFacts:
    return ProcessingReconciliationFacts(
        request=request or _request(),
        database=database or _consistent_db(),
        artifacts=ReconciliationArtifactFacts(
            json_artifact=json_artifact or _missing_json(),
            markdown_artifact=markdown_artifact or _missing_file(),
        ),
        diagnosed_at=diagnosed_at,
        stale_after=stale_after,
    )


def _complete_facts(
    *,
    tool_call_names: tuple[str, ...] = REQUIRED_TOOL_CALLS,
    source_record_count: int = 0,
    diagnosed_at: datetime = STALE_AT,
) -> ProcessingReconciliationFacts:
    return _facts(
        diagnosed_at=diagnosed_at,
        database=_consistent_db(
            matching_company_check_source_request_ids=(REQUEST_ID,),
            source_record_count=source_record_count,
            tool_call_names=tool_call_names,
            report_record_count=1,
            path_content=ReconciliationConsistency.consistent,
        ),
        json_artifact=_present_json(),
        markdown_artifact=_present_file(),
    )


def test_coherent_request_within_processing_window():
    result = classify_processing_reconciliation(
        _facts(diagnosed_at=WITHIN_WINDOW_AT)
    )
    assert (
        result.classification
        is ReconciliationClassification.within_processing_window
    )
    assert result.age_seconds == 1800.0
    assert result.reasons == ()


def test_exact_age_boundary_is_stale_not_within_window():
    result = classify_processing_reconciliation(
        _facts(diagnosed_at=EXACT_BOUNDARY_AT)
    )
    assert (
        result.classification
        is ReconciliationClassification.stale_no_result_evidence
    )
    assert result.age_seconds == STALE_AFTER.total_seconds()


def test_missing_processing_check_id_is_inconsistent():
    result = classify_processing_reconciliation(
        _facts(request=_request(processing_check_id=None))
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "missing_processing_check_id" in result.reasons
    assert result.age_seconds is None


def test_missing_processing_started_at_is_inconsistent():
    result = classify_processing_reconciliation(
        _facts(request=_request(processing_started_at=None))
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "missing_processing_started_at" in result.reasons
    assert result.age_seconds is None


def test_company_check_id_during_processing_is_inconsistent():
    result = classify_processing_reconciliation(
        _facts(
            request=_request(company_check_id=PROCESSING_CHECK_ID),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "company_check_id_set_during_processing" in result.reasons


def test_diagnosed_at_before_started_at_is_inconsistent():
    result = classify_processing_reconciliation(
        _facts(diagnosed_at=STARTED_AT - timedelta(seconds=1))
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "diagnosed_at_before_processing_started_at" in result.reasons
    assert result.age_seconds is None


def test_no_evidence_and_no_files_is_stale_no_result_evidence():
    result = classify_processing_reconciliation(_facts())
    assert (
        result.classification
        is ReconciliationClassification.stale_no_result_evidence
    )


def test_json_only_without_company_check_is_artifacts_unpersisted():
    result = classify_processing_reconciliation(
        _facts(json_artifact=_present_json(json_valid=False))
    )
    assert (
        result.classification
        is ReconciliationClassification.stale_artifacts_unpersisted
    )


def test_markdown_only_without_company_check_is_artifacts_unpersisted():
    result = classify_processing_reconciliation(
        _facts(markdown_artifact=_present_file())
    )
    assert (
        result.classification
        is ReconciliationClassification.stale_artifacts_unpersisted
    )


def test_both_files_invalid_json_without_company_check():
    result = classify_processing_reconciliation(
        _facts(
            json_artifact=_present_json(json_valid=False),
            markdown_artifact=_present_file(),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.stale_artifacts_unpersisted
    )


def test_valid_json_foreign_check_id_is_inconsistent():
    result = classify_processing_reconciliation(
        _facts(
            json_artifact=_present_json(parsed_check_id="999"),
            markdown_artifact=_present_file(),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "json_check_id_mismatch" in result.reasons


def test_matching_company_check_without_report_is_incomplete():
    result = classify_processing_reconciliation(
        _facts(
            database=_consistent_db(
                matching_company_check_source_request_ids=(REQUEST_ID,),
                tool_call_names=REQUIRED_TOOL_CALLS,
                report_record_count=0,
                path_content=ReconciliationConsistency.consistent,
            ),
            json_artifact=_present_json(),
            markdown_artifact=_present_file(),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.stale_persisted_incomplete
    )
    assert "missing_report_record" in result.reasons


def test_missing_required_tool_call_is_incomplete():
    result = classify_processing_reconciliation(
        _facts(
            database=_consistent_db(
                matching_company_check_source_request_ids=(REQUEST_ID,),
                tool_call_names=REQUIRED_TOOL_CALLS[:-1],
                report_record_count=1,
                path_content=ReconciliationConsistency.consistent,
            ),
            json_artifact=_present_json(),
            markdown_artifact=_present_file(),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.stale_persisted_incomplete
    )
    assert "missing_tool_call:risk_score" in result.reasons


def test_duplicate_required_tool_call_is_inconsistent():
    names = REQUIRED_TOOL_CALLS + ("web_search",)
    result = classify_processing_reconciliation(
        _facts(
            database=_consistent_db(
                matching_company_check_source_request_ids=(REQUEST_ID,),
                tool_call_names=names,
                report_record_count=1,
                path_content=ReconciliationConsistency.consistent,
            ),
            json_artifact=_present_json(),
            markdown_artifact=_present_file(),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "duplicate_tool_call:web_search" in result.reasons


def test_unknown_tool_call_is_inconsistent():
    names = REQUIRED_TOOL_CALLS + ("custom_tool",)
    result = classify_processing_reconciliation(
        _facts(
            database=_consistent_db(
                matching_company_check_source_request_ids=(REQUEST_ID,),
                tool_call_names=names,
                report_record_count=1,
                path_content=ReconciliationConsistency.consistent,
            ),
            json_artifact=_present_json(),
            markdown_artifact=_present_file(),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "unknown_tool_call:custom_tool" in result.reasons


def test_complete_evidence_with_zero_sources_is_complete():
    result = classify_processing_reconciliation(
        _complete_facts(source_record_count=0)
    )
    assert (
        result.classification
        is ReconciliationClassification.stale_persisted_complete
    )
    assert result.reasons == ()


def test_complete_db_but_missing_file_is_incomplete():
    result = classify_processing_reconciliation(
        _facts(
            database=_consistent_db(
                matching_company_check_source_request_ids=(REQUEST_ID,),
                tool_call_names=REQUIRED_TOOL_CALLS,
                report_record_count=1,
                path_content=ReconciliationConsistency.consistent,
            ),
            json_artifact=_present_json(),
            markdown_artifact=_missing_file(),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.stale_persisted_incomplete
    )
    assert "missing_markdown_artifact" in result.reasons


def test_db_path_mismatch_is_inconsistent():
    database = _consistent_db(
        matching_company_check_source_request_ids=(REQUEST_ID,),
        tool_call_names=REQUIRED_TOOL_CALLS,
        report_record_count=1,
        path_content=ReconciliationConsistency.consistent,
    )
    database = database.model_copy(
        update={
            "report_json_path_consistency": (
                ReconciliationConsistency.inconsistent
            ),
        }
    )
    result = classify_processing_reconciliation(
        _facts(
            database=database,
            json_artifact=_present_json(),
            markdown_artifact=_present_file(),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "report_json_path_inconsistent" in result.reasons


def test_db_content_mismatch_is_inconsistent():
    database = _consistent_db(
        matching_company_check_source_request_ids=(REQUEST_ID,),
        tool_call_names=REQUIRED_TOOL_CALLS,
        report_record_count=1,
        path_content=ReconciliationConsistency.consistent,
    )
    database = database.model_copy(
        update={
            "report_markdown_content_consistency": (
                ReconciliationConsistency.inconsistent
            ),
        }
    )
    result = classify_processing_reconciliation(
        _facts(
            database=database,
            json_artifact=_present_json(),
            markdown_artifact=_present_file(),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "report_markdown_content_inconsistent" in result.reasons


def test_symlinked_artifact_is_inconsistent_inside_window():
    result = classify_processing_reconciliation(
        _facts(
            diagnosed_at=WITHIN_WINDOW_AT,
            json_artifact=_present_json(is_symlink=True),
            markdown_artifact=_present_file(),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "json_artifact_is_symlink" in result.reasons


def test_artifact_outside_output_root_is_inconsistent_inside_window():
    result = classify_processing_reconciliation(
        _facts(
            diagnosed_at=WITHIN_WINDOW_AT,
            markdown_artifact=_present_file(within_output_root=False),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "markdown_artifact_outside_output_root" in result.reasons


def test_orphan_report_without_company_check_is_inconsistent():
    result = classify_processing_reconciliation(
        _facts(
            database=_consistent_db(
                report_record_count=1,
                orphan_report_record_count=1,
            )
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "orphan_report_records" in result.reasons
    assert "related_db_evidence_without_company_check" in result.reasons


def test_orphan_tool_call_without_company_check_is_inconsistent():
    result = classify_processing_reconciliation(
        _facts(
            database=_consistent_db(
                tool_call_names=("web_search",),
                orphan_tool_call_record_count=1,
            )
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "orphan_tool_call_records" in result.reasons


def test_another_request_uses_processing_token_is_inconsistent():
    result = classify_processing_reconciliation(
        _facts(
            database=_consistent_db(
                foreign_processing_token_request_ids=(99,),
            )
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "foreign_processing_token_owner" in result.reasons


def test_matching_company_check_owned_by_another_request():
    result = classify_processing_reconciliation(
        _facts(
            database=_consistent_db(
                matching_company_check_source_request_ids=(99,),
                tool_call_names=REQUIRED_TOOL_CALLS,
                report_record_count=1,
                path_content=ReconciliationConsistency.consistent,
            ),
            json_artifact=_present_json(),
            markdown_artifact=_present_file(),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "matching_company_check_foreign_owner" in result.reasons


def test_duplicate_matching_company_check_rows_is_inconsistent():
    result = classify_processing_reconciliation(
        _facts(
            database=_consistent_db(
                matching_company_check_source_request_ids=(
                    REQUEST_ID,
                    REQUEST_ID,
                ),
                tool_call_names=REQUIRED_TOOL_CALLS,
                report_record_count=1,
                path_content=ReconciliationConsistency.consistent,
            ),
            json_artifact=_present_json(),
            markdown_artifact=_present_file(),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "duplicate_matching_company_checks" in result.reasons


def test_report_record_count_greater_than_one_is_inconsistent():
    result = classify_processing_reconciliation(
        _facts(
            database=_consistent_db(
                matching_company_check_source_request_ids=(REQUEST_ID,),
                tool_call_names=REQUIRED_TOOL_CALLS,
                report_record_count=2,
                path_content=ReconciliationConsistency.consistent,
            ),
            json_artifact=_present_json(),
            markdown_artifact=_present_file(),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "duplicate_report_records" in result.reasons


def test_consistency_not_checked_is_incomplete():
    result = classify_processing_reconciliation(
        _facts(
            database=_consistent_db(
                matching_company_check_source_request_ids=(REQUEST_ID,),
                tool_call_names=REQUIRED_TOOL_CALLS,
                report_record_count=1,
                path_content=ReconciliationConsistency.not_checked,
            ),
            json_artifact=_present_json(),
            markdown_artifact=_present_file(),
        )
    )
    assert (
        result.classification
        is ReconciliationClassification.stale_persisted_incomplete
    )
    assert "report_json_path_not_checked" in result.reasons


def test_required_manifest_different_order_is_complete():
    reordered = (
        "risk_score",
        "registry_search",
        "domain_dns_check",
        "web_search",
    )
    result = classify_processing_reconciliation(
        _complete_facts(tool_call_names=reordered)
    )
    assert (
        result.classification
        is ReconciliationClassification.stale_persisted_complete
    )


def test_returned_reasons_are_immutable_tuples():
    result = classify_processing_reconciliation(
        _facts(request=_request(processing_check_id=None))
    )
    assert isinstance(result.reasons, tuple)
    assert result.model_config.get("frozen") is True


def test_classifier_does_not_mutate_input_facts():
    facts = _complete_facts()
    before = facts.model_dump()
    classify_processing_reconciliation(facts)
    assert facts.model_dump() == before


def test_classification_is_deterministic():
    facts = _complete_facts()
    first = classify_processing_reconciliation(facts)
    second = classify_processing_reconciliation(facts)
    assert first == second


def test_no_source_record_minimum_for_complete():
    result = classify_processing_reconciliation(
        _complete_facts(source_record_count=0)
    )
    assert (
        result.classification
        is ReconciliationClassification.stale_persisted_complete
    )
    with_sources = classify_processing_reconciliation(
        _complete_facts(source_record_count=3)
    )
    assert (
        with_sources.classification
        is ReconciliationClassification.stale_persisted_complete
    )


def test_unknown_status_is_processing_inconsistent():
    result = classify_processing_reconciliation(
        _facts(request=_request(status="approved-but-weird"))
    )
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "status_not_processing" in result.reasons
