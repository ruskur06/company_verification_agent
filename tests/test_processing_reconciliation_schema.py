"""Schema tests for processing reconciliation diagnosis models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.check_request import CheckRequestStatus
from app.schemas.processing_reconciliation import (
    ArtifactFileFacts,
    JsonArtifactFacts,
    ProcessingReconciliationDiagnosis,
    ProcessingReconciliationDiagnosisError,
    ProcessingReconciliationFacts,
    ProcessingRequestFacts,
    ReconciliationArtifactFacts,
    ReconciliationClassification,
    ReconciliationConsistency,
    ReconciliationDatabaseFacts,
    ReconciliationDiagnosisErrorReason,
    processing_reconciliation_result_adapter,
)


FIXED_STARTED_AT = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
FIXED_DIAGNOSED_AT = datetime(2026, 7, 20, 13, 0, 0, tzinfo=timezone.utc)


def _missing_artifact() -> ArtifactFileFacts:
    return ArtifactFileFacts(
        exists=False,
        is_regular_file=False,
        is_symlink=False,
        within_output_root=True,
        utf8_readable=False,
    )


def _missing_json_artifact() -> JsonArtifactFacts:
    return JsonArtifactFacts(
        exists=False,
        is_regular_file=False,
        is_symlink=False,
        within_output_root=True,
        utf8_readable=False,
        json_valid=False,
        parsed_check_id=None,
    )


def _valid_request_facts() -> ProcessingRequestFacts:
    return ProcessingRequestFacts(
        request_id=42,
        status=CheckRequestStatus.processing,
        company_check_id=None,
        processing_check_id="1782245999001",
        processing_started_at=FIXED_STARTED_AT,
    )


def test_classification_enum_values():
    assert [member.value for member in ReconciliationClassification] == [
        "within_processing_window",
        "stale_no_result_evidence",
        "stale_artifacts_unpersisted",
        "stale_persisted_incomplete",
        "stale_persisted_complete",
        "processing_inconsistent",
    ]


def test_consistency_enum_values():
    assert [member.value for member in ReconciliationConsistency] == [
        "not_checked",
        "consistent",
        "inconsistent",
    ]


def test_diagnosis_error_reason_enum_values():
    assert [
        member.value for member in ReconciliationDiagnosisErrorReason
    ] == [
        "database_inspection_failed",
        "artifact_inspection_failed",
    ]


@pytest.mark.parametrize(
    "model",
    [
        ProcessingRequestFacts(
            request_id=1,
            status=CheckRequestStatus.processing,
            processing_check_id="1",
            processing_started_at=FIXED_STARTED_AT,
        ),
        ReconciliationDatabaseFacts(),
        _missing_artifact(),
        _missing_json_artifact(),
        ReconciliationArtifactFacts(
            json_artifact=_missing_json_artifact(),
            markdown_artifact=_missing_artifact(),
        ),
        ProcessingReconciliationFacts(
            request=_valid_request_facts(),
            database=ReconciliationDatabaseFacts(),
            artifacts=ReconciliationArtifactFacts(
                json_artifact=_missing_json_artifact(),
                markdown_artifact=_missing_artifact(),
            ),
            diagnosed_at=FIXED_DIAGNOSED_AT,
            stale_after=timedelta(hours=1),
        ),
        ProcessingReconciliationDiagnosis(
            request_id=1,
            processing_check_id="1",
            classification=(
                ReconciliationClassification.within_processing_window
            ),
            diagnosed_at=FIXED_DIAGNOSED_AT,
            age_seconds=0.0,
        ),
        ProcessingReconciliationDiagnosisError(
            request_id=1,
            processing_check_id="1",
            reason=(
                ReconciliationDiagnosisErrorReason.database_inspection_failed
            ),
            detail="database unavailable",
            diagnosed_at=FIXED_DIAGNOSED_AT,
        ),
    ],
)
def test_fact_models_are_frozen(model):
    field_name = next(iter(type(model).model_fields))
    with pytest.raises(ValidationError):
        setattr(model, field_name, getattr(model, field_name))


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        ProcessingRequestFacts(
            request_id=1,
            status=CheckRequestStatus.processing,
            processing_check_id="1",
            processing_started_at=FIXED_STARTED_AT,
            unexpected="nope",
        )


def test_integer_processing_check_id_rejected():
    with pytest.raises(ValidationError):
        ProcessingRequestFacts(
            request_id=1,
            status=CheckRequestStatus.processing,
            processing_check_id=1782245999001,
            processing_started_at=FIXED_STARTED_AT,
        )


def test_integer_company_check_id_rejected():
    with pytest.raises(ValidationError):
        ProcessingRequestFacts(
            request_id=1,
            status=CheckRequestStatus.processing,
            company_check_id=1782245999001,
            processing_check_id="1782245999001",
            processing_started_at=FIXED_STARTED_AT,
        )


def test_naive_processing_started_at_rejected():
    with pytest.raises(ValidationError):
        ProcessingRequestFacts(
            request_id=1,
            status=CheckRequestStatus.processing,
            processing_check_id="1",
            processing_started_at=datetime(2026, 7, 20, 12, 0, 0),
        )


def test_naive_diagnosed_at_rejected():
    with pytest.raises(ValidationError):
        ProcessingReconciliationFacts(
            request=_valid_request_facts(),
            database=ReconciliationDatabaseFacts(),
            artifacts=ReconciliationArtifactFacts(
                json_artifact=_missing_json_artifact(),
                markdown_artifact=_missing_artifact(),
            ),
            diagnosed_at=datetime(2026, 7, 20, 13, 0, 0),
            stale_after=timedelta(hours=1),
        )


def test_zero_stale_after_rejected():
    with pytest.raises(ValidationError):
        ProcessingReconciliationFacts(
            request=_valid_request_facts(),
            database=ReconciliationDatabaseFacts(),
            artifacts=ReconciliationArtifactFacts(
                json_artifact=_missing_json_artifact(),
                markdown_artifact=_missing_artifact(),
            ),
            diagnosed_at=FIXED_DIAGNOSED_AT,
            stale_after=timedelta(0),
        )


def test_negative_stale_after_rejected():
    with pytest.raises(ValidationError):
        ProcessingReconciliationFacts(
            request=_valid_request_facts(),
            database=ReconciliationDatabaseFacts(),
            artifacts=ReconciliationArtifactFacts(
                json_artifact=_missing_json_artifact(),
                markdown_artifact=_missing_artifact(),
            ),
            diagnosed_at=FIXED_DIAGNOSED_AT,
            stale_after=timedelta(seconds=-1),
        )


def test_blank_tool_call_name_rejected():
    with pytest.raises(ValidationError):
        ReconciliationDatabaseFacts(tool_call_names=("web_search", ""))


def test_whitespace_tool_call_name_rejected():
    with pytest.raises(ValidationError):
        ReconciliationDatabaseFacts(tool_call_names=("   ",))


def test_negative_record_counts_rejected():
    with pytest.raises(ValidationError):
        ReconciliationDatabaseFacts(source_record_count=-1)


def test_diagnosis_result_union_round_trip():
    diagnosis = ProcessingReconciliationDiagnosis(
        request_id=42,
        processing_check_id="1782245999001",
        classification=ReconciliationClassification.stale_persisted_complete,
        diagnosed_at=FIXED_DIAGNOSED_AT,
        age_seconds=3600.0,
        reasons=(),
    )
    payload = diagnosis.model_dump(mode="json")
    restored = processing_reconciliation_result_adapter.validate_python(
        payload
    )
    assert isinstance(restored, ProcessingReconciliationDiagnosis)
    assert restored.kind == "diagnosis"
    assert restored == diagnosis


def test_diagnosis_error_structurally_distinct():
    error = ProcessingReconciliationDiagnosisError(
        request_id=42,
        processing_check_id="1782245999001",
        reason=ReconciliationDiagnosisErrorReason.artifact_inspection_failed,
        detail="artifact root unavailable",
        diagnosed_at=FIXED_DIAGNOSED_AT,
    )
    payload = error.model_dump(mode="json")
    restored = processing_reconciliation_result_adapter.validate_python(
        payload
    )
    assert isinstance(restored, ProcessingReconciliationDiagnosisError)
    assert restored.kind == "diagnosis_error"
    assert not isinstance(restored, ProcessingReconciliationDiagnosis)
    assert "classification" not in payload


def test_result_kinds_serialize_exactly():
    diagnosis = ProcessingReconciliationDiagnosis(
        request_id=1,
        classification=ReconciliationClassification.within_processing_window,
        diagnosed_at=FIXED_DIAGNOSED_AT,
        age_seconds=1.0,
    )
    error = ProcessingReconciliationDiagnosisError(
        reason=ReconciliationDiagnosisErrorReason.database_inspection_failed,
        detail="query failed",
        diagnosed_at=FIXED_DIAGNOSED_AT,
    )
    assert diagnosis.model_dump()["kind"] == "diagnosis"
    assert error.model_dump()["kind"] == "diagnosis_error"


@pytest.mark.parametrize("blank_id", ["", " ", "   "])
def test_whitespace_only_processing_check_id_rejected(blank_id: str):
    with pytest.raises(ValidationError):
        ProcessingRequestFacts(
            request_id=1,
            status=CheckRequestStatus.processing,
            processing_check_id=blank_id,
            processing_started_at=FIXED_STARTED_AT,
        )


@pytest.mark.parametrize("blank_id", ["", " ", "   "])
def test_whitespace_only_company_check_id_rejected(blank_id: str):
    with pytest.raises(ValidationError):
        ProcessingRequestFacts(
            request_id=1,
            status=CheckRequestStatus.processing,
            company_check_id=blank_id,
            processing_check_id="1782245999001",
            processing_started_at=FIXED_STARTED_AT,
        )


@pytest.mark.parametrize("blank_id", ["", " ", "   "])
def test_whitespace_only_parsed_check_id_rejected(blank_id: str):
    with pytest.raises(ValidationError):
        JsonArtifactFacts(
            exists=True,
            is_regular_file=True,
            is_symlink=False,
            within_output_root=True,
            utf8_readable=True,
            json_valid=True,
            parsed_check_id=blank_id,
        )


@pytest.mark.parametrize("blank_detail", ["", " ", "   "])
def test_whitespace_only_diagnosis_error_detail_rejected(blank_detail: str):
    with pytest.raises(ValidationError):
        ProcessingReconciliationDiagnosisError(
            reason=ReconciliationDiagnosisErrorReason.database_inspection_failed,
            detail=blank_detail,
            diagnosed_at=FIXED_DIAGNOSED_AT,
        )


@pytest.mark.parametrize(
    "invalid_id",
    [0, -1, True, False, "42"],
)
def test_related_request_ids_reject_invalid_values(invalid_id):
    with pytest.raises(ValidationError):
        ReconciliationDatabaseFacts(
            matching_company_check_source_request_ids=(invalid_id,),
        )
    with pytest.raises(ValidationError):
        ReconciliationDatabaseFacts(
            foreign_processing_token_request_ids=(invalid_id,),
        )


def test_tool_call_names_reject_non_string_input():
    with pytest.raises(ValidationError):
        ReconciliationDatabaseFacts(tool_call_names=(1,))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ReconciliationDatabaseFacts(tool_call_names=(True,))  # type: ignore[arg-type]


def test_missing_artifact_cannot_be_regular():
    with pytest.raises(ValidationError):
        ArtifactFileFacts(
            exists=False,
            is_regular_file=True,
            is_symlink=False,
            within_output_root=True,
            utf8_readable=False,
        )


def test_missing_artifact_cannot_be_utf8_readable():
    with pytest.raises(ValidationError):
        ArtifactFileFacts(
            exists=False,
            is_regular_file=False,
            is_symlink=False,
            within_output_root=True,
            utf8_readable=True,
        )


def test_dangling_symlink_facts_remain_valid():
    artifact = ArtifactFileFacts(
        exists=False,
        is_symlink=True,
        is_regular_file=False,
        utf8_readable=False,
        within_output_root=True,
    )
    assert artifact.exists is False
    assert artifact.is_symlink is True


def test_json_valid_true_with_exists_false_rejected():
    with pytest.raises(ValidationError):
        JsonArtifactFacts(
            exists=False,
            is_regular_file=False,
            is_symlink=False,
            within_output_root=True,
            utf8_readable=False,
            json_valid=True,
            parsed_check_id=None,
        )


def test_json_valid_true_with_non_regular_file_rejected():
    with pytest.raises(ValidationError):
        JsonArtifactFacts(
            exists=True,
            is_regular_file=False,
            is_symlink=False,
            within_output_root=True,
            utf8_readable=True,
            json_valid=True,
            parsed_check_id=None,
        )


def test_json_valid_true_with_unreadable_utf8_rejected():
    with pytest.raises(ValidationError):
        JsonArtifactFacts(
            exists=True,
            is_regular_file=True,
            is_symlink=False,
            within_output_root=True,
            utf8_readable=False,
            json_valid=True,
            parsed_check_id=None,
        )


def test_json_valid_false_with_parsed_check_id_rejected():
    with pytest.raises(ValidationError):
        JsonArtifactFacts(
            exists=True,
            is_regular_file=True,
            is_symlink=False,
            within_output_root=True,
            utf8_readable=True,
            json_valid=False,
            parsed_check_id="1782245999001",
        )


def test_json_valid_true_with_parsed_check_id_none_remains_valid():
    artifact = JsonArtifactFacts(
        exists=True,
        is_regular_file=True,
        is_symlink=False,
        within_output_root=True,
        utf8_readable=True,
        json_valid=True,
        parsed_check_id=None,
    )
    assert artifact.json_valid is True
    assert artifact.parsed_check_id is None
