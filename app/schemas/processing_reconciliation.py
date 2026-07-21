"""Immutable schemas for processing-request reconciliation diagnosis."""

from __future__ import annotations

from datetime import timedelta
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app.schemas.check_request import CheckRequestStatus


PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]


def is_canonical_processing_check_id(value: str | None) -> bool:
    """Return True for a positive ASCII-digit processing token with no padding."""
    if value is None or value == "":
        return False
    if len(value) > 64:
        return False
    if not value.isascii() or not value.isdigit():
        return False
    if value[0] == "0":
        return False
    return True


class ReconciliationClassification(str, Enum):
    """Successful diagnosis classification for a processing request."""

    within_processing_window = "within_processing_window"
    stale_no_result_evidence = "stale_no_result_evidence"
    stale_artifacts_unpersisted = "stale_artifacts_unpersisted"
    stale_persisted_incomplete = "stale_persisted_incomplete"
    stale_persisted_complete = "stale_persisted_complete"
    processing_inconsistent = "processing_inconsistent"


class ReconciliationConsistency(str, Enum):
    """Consistency of DB report path/content versus expected artifacts."""

    not_checked = "not_checked"
    consistent = "consistent"
    inconsistent = "inconsistent"


class ReconciliationDiagnosisErrorReason(str, Enum):
    """Reasons a later diagnosis service could not obtain mandatory facts."""

    database_inspection_failed = "database_inspection_failed"
    artifact_inspection_failed = "artifact_inspection_failed"


def _reject_blank_optional_id(value: StrictStr | None) -> StrictStr | None:
    if value is not None and value.strip() == "":
        raise ValueError("check ID must not be blank")
    return value


class ProcessingRequestFacts(BaseModel):
    """Already-inspected CheckRequest facts used for classification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: PositiveStrictInt
    status: CheckRequestStatus | StrictStr
    company_check_id: StrictStr | None = None
    processing_check_id: StrictStr | None = None
    processing_started_at: AwareDatetime | None = None

    @field_validator("status", mode="before")
    @classmethod
    def coerce_status(
        cls,
        value: object,
    ) -> CheckRequestStatus | str:
        if isinstance(value, CheckRequestStatus):
            return value
        if not isinstance(value, str):
            raise ValueError("status must be a string")
        try:
            return CheckRequestStatus(value)
        except ValueError:
            return value

    @field_validator("company_check_id", "processing_check_id")
    @classmethod
    def reject_blank_check_ids(
        cls,
        value: StrictStr | None,
    ) -> StrictStr | None:
        return _reject_blank_optional_id(value)


class ReconciliationCompanyCheckSnapshot(BaseModel):
    """Raw CompanyCheck row matched by processing token check_id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: PositiveStrictInt
    check_id: StrictStr
    source_check_request_id: StrictInt | None = None
    json_report_path: StrictStr | None = None
    markdown_report_path: StrictStr | None = None


class ReconciliationReportSnapshot(BaseModel):
    """Raw ReportRecord row matched by processing token check_id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: PositiveStrictInt
    check_id: StrictStr
    json_path: StrictStr | None = None
    markdown_path: StrictStr | None = None
    json_content: StrictStr | None = None
    markdown_content: StrictStr | None = None


class ReconciliationDatabaseFacts(BaseModel):
    """Already-inspected database evidence for one processing token."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    matching_company_check_source_request_ids: tuple[
        PositiveStrictInt | None,
        ...,
    ] = ()
    foreign_processing_token_request_ids: tuple[PositiveStrictInt, ...] = ()
    source_record_count: Annotated[StrictInt, Field(ge=0)] = 0
    tool_call_names: tuple[StrictStr, ...] = ()
    report_record_count: Annotated[StrictInt, Field(ge=0)] = 0
    orphan_source_record_count: Annotated[StrictInt, Field(ge=0)] = 0
    orphan_tool_call_record_count: Annotated[StrictInt, Field(ge=0)] = 0
    orphan_report_record_count: Annotated[StrictInt, Field(ge=0)] = 0
    report_json_path_consistency: ReconciliationConsistency = (
        ReconciliationConsistency.not_checked
    )
    report_markdown_path_consistency: ReconciliationConsistency = (
        ReconciliationConsistency.not_checked
    )
    report_json_content_consistency: ReconciliationConsistency = (
        ReconciliationConsistency.not_checked
    )
    report_markdown_content_consistency: ReconciliationConsistency = (
        ReconciliationConsistency.not_checked
    )

    @field_validator("tool_call_names")
    @classmethod
    def reject_blank_tool_call_names(
        cls,
        value: tuple[StrictStr, ...],
    ) -> tuple[StrictStr, ...]:
        for name in value:
            if name.strip() == "":
                raise ValueError("tool-call name must not be blank")
        return value


class ArtifactFileFacts(BaseModel):
    """Already-inspected filesystem facts for one expected artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    exists: bool
    is_regular_file: bool
    is_symlink: bool
    within_output_root: bool
    utf8_readable: bool

    @model_validator(mode="after")
    def reject_incoherent_missing_file(self) -> ArtifactFileFacts:
        if not self.exists:
            if self.is_regular_file:
                raise ValueError(
                    "missing artifact cannot be a regular file"
                )
            if self.utf8_readable:
                raise ValueError(
                    "missing artifact cannot be UTF-8 readable"
                )
        return self


class JsonArtifactFacts(ArtifactFileFacts):
    """Already-inspected filesystem and JSON parse facts."""

    json_valid: bool
    parsed_check_id: StrictStr | None = None

    @field_validator("parsed_check_id")
    @classmethod
    def reject_blank_parsed_check_id(
        cls,
        value: StrictStr | None,
    ) -> StrictStr | None:
        return _reject_blank_optional_id(value)

    @model_validator(mode="after")
    def reject_incoherent_json_facts(self) -> JsonArtifactFacts:
        if self.json_valid:
            if not self.exists:
                raise ValueError("valid JSON artifact must exist")
            if not self.is_regular_file:
                raise ValueError(
                    "valid JSON artifact must be a regular file"
                )
            if not self.utf8_readable:
                raise ValueError(
                    "valid JSON artifact must be UTF-8 readable"
                )
        elif self.parsed_check_id is not None:
            raise ValueError(
                "invalid JSON artifact cannot include parsed_check_id"
            )
        return self


class ReconciliationArtifactFacts(BaseModel):
    """Already-inspected expected JSON and Markdown artifact facts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    json_artifact: JsonArtifactFacts
    markdown_artifact: ArtifactFileFacts


class ProcessingReconciliationFacts(BaseModel):
    """Complete immutable input for pure reconciliation classification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: ProcessingRequestFacts
    database: ReconciliationDatabaseFacts
    artifacts: ReconciliationArtifactFacts
    diagnosed_at: AwareDatetime
    stale_after: timedelta

    @model_validator(mode="after")
    def reject_non_positive_stale_after(
        self,
    ) -> ProcessingReconciliationFacts:
        if self.stale_after <= timedelta(0):
            raise ValueError("stale_after must be greater than zero")
        return self


class ProcessingReconciliationDatabaseInspection(BaseModel):
    """Advisory read-only DB inspection bundle for one CheckRequest.

    token_company_checks contains every CompanyCheckRecord whose check_id
    equals the normalized processing token, regardless of ownership.
    token_report_records contains every ReportRecord for that token, even
    when no CompanyCheck exists. Tuple order is record ID ascending.
    This bundle cannot authorize mutation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: ProcessingRequestFacts
    database: ReconciliationDatabaseFacts
    token_company_checks: tuple[ReconciliationCompanyCheckSnapshot, ...] = ()
    token_report_records: tuple[ReconciliationReportSnapshot, ...] = ()


class ProcessingReconciliationDiagnosis(BaseModel):
    """Successful classification result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["diagnosis"] = "diagnosis"
    request_id: PositiveStrictInt
    processing_check_id: StrictStr | None = None
    classification: ReconciliationClassification
    diagnosed_at: AwareDatetime
    age_seconds: Annotated[float, Field(ge=0)] | None = None
    reasons: tuple[str, ...] = ()

    @field_validator("processing_check_id")
    @classmethod
    def reject_blank_processing_check_id(
        cls,
        value: StrictStr | None,
    ) -> StrictStr | None:
        return _reject_blank_optional_id(value)


class ProcessingReconciliationDiagnosisError(BaseModel):
    """Diagnosis could not be completed because mandatory facts failed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["diagnosis_error"] = "diagnosis_error"
    request_id: PositiveStrictInt | None = None
    processing_check_id: StrictStr | None = None
    reason: ReconciliationDiagnosisErrorReason
    detail: StrictStr
    diagnosed_at: AwareDatetime

    @field_validator("processing_check_id")
    @classmethod
    def reject_blank_processing_check_id(
        cls,
        value: StrictStr | None,
    ) -> StrictStr | None:
        return _reject_blank_optional_id(value)

    @field_validator("detail")
    @classmethod
    def reject_blank_detail(cls, value: StrictStr) -> StrictStr:
        if value.strip() == "":
            raise ValueError("detail must not be blank")
        return value


ProcessingReconciliationResult = Annotated[
    ProcessingReconciliationDiagnosis
    | ProcessingReconciliationDiagnosisError,
    Field(discriminator="kind"),
]

processing_reconciliation_result_adapter: TypeAdapter[
    ProcessingReconciliationResult
] = TypeAdapter(ProcessingReconciliationResult)
