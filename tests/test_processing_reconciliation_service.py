"""Tests for read-only processing reconciliation diagnosis service."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

import app.agents.report_agent as report_agent
from app.schemas.check_request import CheckRequestStatus
from app.schemas.processing_reconciliation import (
    ProcessingReconciliationDatabaseInspection,
    ProcessingReconciliationDiagnosis,
    ProcessingReconciliationDiagnosisError,
    ProcessingRequestFacts,
    ReconciliationClassification,
    ReconciliationConsistency,
    ReconciliationDatabaseFacts,
    ReconciliationDiagnosisErrorReason,
    ReconciliationReportSnapshot,
)
from app.services import processing_reconciliation_service as service
from app.services.processing_reconciliation_service import (
    ProcessingReconciliationRequestNotFoundError,
    diagnose_processing_reconciliation,
)


TOKEN = "1782245999001"
STALE_AFTER = timedelta(hours=1)
FIXED_NOW = datetime(2026, 7, 21, 15, 0, 0, tzinfo=timezone.utc)
STARTED_AT = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _request_facts(
    *,
    processing_check_id: str | None = TOKEN,
    status: CheckRequestStatus | str = CheckRequestStatus.processing,
) -> ProcessingRequestFacts:
    return ProcessingRequestFacts(
        request_id=42,
        status=status,
        company_check_id=None,
        processing_check_id=processing_check_id,
        processing_started_at=STARTED_AT,
    )


def _inspection(
    *,
    request: ProcessingRequestFacts | None = None,
    database: ReconciliationDatabaseFacts | None = None,
    reports: tuple[ReconciliationReportSnapshot, ...] = (),
) -> ProcessingReconciliationDatabaseInspection:
    return ProcessingReconciliationDatabaseInspection(
        request=request or _request_facts(),
        database=database or ReconciliationDatabaseFacts(),
        token_company_checks=(),
        token_report_records=reports,
    )


@pytest.fixture()
def outputs_dirs(tmp_path, monkeypatch):
    json_dir = tmp_path / "outputs" / "json"
    reports_dir = tmp_path / "outputs" / "reports"
    json_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    monkeypatch.setattr(report_agent, "JSON_DIR", json_dir)
    monkeypatch.setattr(report_agent, "REPORTS_DIR", reports_dir)
    return json_dir, reports_dir


def _write_valid_artifacts(json_dir: Path, reports_dir: Path) -> tuple[Path, Path]:
    json_path = json_dir / f"company_check_{TOKEN}.json"
    markdown_path = reports_dir / f"company_check_{TOKEN}.md"
    payload = {
        "check_id": TOKEN,
        "ok": True,
    }
    json_text = json.dumps(payload, indent=2)
    markdown_text = "# Report\n"
    json_path.write_text(json_text, encoding="utf-8")
    markdown_path.write_text(markdown_text, encoding="utf-8")
    return json_path, markdown_path


def test_invalid_request_id_rejected_before_repository(monkeypatch):
    repo = MagicMock(side_effect=AssertionError("repository must not run"))
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        repo,
    )
    for invalid in [True, False, "1", 0, -1, 1.5, None]:
        with pytest.raises(ValueError):
            diagnose_processing_reconciliation(
                invalid,  # type: ignore[arg-type]
                stale_after=STALE_AFTER,
                diagnosed_at=FIXED_NOW,
            )
    repo.assert_not_called()


def test_invalid_stale_after_rejected_before_repository(monkeypatch):
    repo = MagicMock(side_effect=AssertionError("repository must not run"))
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        repo,
    )
    for invalid in [timedelta(0), timedelta(seconds=-1), 5, "1h"]:
        with pytest.raises(ValueError):
            diagnose_processing_reconciliation(
                42,
                stale_after=invalid,  # type: ignore[arg-type]
                diagnosed_at=FIXED_NOW,
            )
    repo.assert_not_called()


def test_naive_diagnosed_at_rejected_before_repository(monkeypatch):
    repo = MagicMock(side_effect=AssertionError("repository must not run"))
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        repo,
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        diagnose_processing_reconciliation(
            42,
            stale_after=STALE_AFTER,
            diagnosed_at=datetime(2026, 7, 21, 15, 0, 0),
        )
    repo.assert_not_called()


def test_diagnosed_at_default_and_normalization(monkeypatch, outputs_dirs):
    repo = MagicMock(return_value=_inspection())
    classifier = MagicMock(
        return_value=ProcessingReconciliationDiagnosis(
            request_id=42,
            processing_check_id=TOKEN,
            classification=(
                ReconciliationClassification.within_processing_window
            ),
            diagnosed_at=FIXED_NOW,
            age_seconds=1.0,
        )
    )
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        repo,
    )
    monkeypatch.setattr(
        service,
        "classify_processing_reconciliation",
        classifier,
    )
    monkeypatch.setattr(service, "_utc_now", lambda: FIXED_NOW)

    diagnose_processing_reconciliation(42, stale_after=STALE_AFTER)
    assert classifier.call_args.args[0].diagnosed_at == FIXED_NOW

    eastern = datetime(
        2026,
        7,
        21,
        11,
        0,
        0,
        tzinfo=timezone(timedelta(hours=-4)),
    )
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=eastern,
    )
    diagnosed = classifier.call_args.args[0].diagnosed_at
    assert diagnosed == eastern.astimezone(timezone.utc)


def test_missing_request_raises(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=None),
    )
    with pytest.raises(ProcessingReconciliationRequestNotFoundError) as exc:
        diagnose_processing_reconciliation(
            42,
            stale_after=STALE_AFTER,
            diagnosed_at=FIXED_NOW,
        )
    assert exc.value.request_id == 42


def test_repository_sqlalchemy_error_becomes_database_diagnosis_error(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(side_effect=SQLAlchemyError("db down")),
    )
    result = diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosisError)
    assert (
        result.reason
        is ReconciliationDiagnosisErrorReason.database_inspection_failed
    )


def test_repository_validation_error_becomes_database_diagnosis_error(
    monkeypatch,
):
    try:
        ProcessingRequestFacts(
            request_id=-1,
            status="processing",
            processing_check_id=TOKEN,
            processing_started_at=STARTED_AT,
        )
    except ValidationError as exc:
        validation_error = exc
    else:
        raise AssertionError("expected ValidationError")

    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(side_effect=validation_error),
    )
    result = diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosisError)
    assert (
        result.reason
        is ReconciliationDiagnosisErrorReason.database_inspection_failed
    )


def test_unexpected_runtime_error_propagates(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError, match="boom"):
        diagnose_processing_reconciliation(
            42,
            stale_after=STALE_AFTER,
            diagnosed_at=FIXED_NOW,
        )


def test_invalid_token_no_filesystem_access(monkeypatch, tmp_path):
    open_mock = MagicMock(side_effect=AssertionError("open must not run"))
    lstat_mock = MagicMock(side_effect=AssertionError("lstat must not run"))
    monkeypatch.setattr(Path, "open", open_mock)
    monkeypatch.setattr(Path, "lstat", lstat_mock)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(
            return_value=_inspection(
                request=_request_facts(processing_check_id="01")
            )
        ),
    )

    result = diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosis)
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
    )
    assert "invalid_processing_check_id_format" in result.reasons
    open_mock.assert_not_called()
    lstat_mock.assert_not_called()


def test_missing_token_no_filesystem_access(monkeypatch):
    open_mock = MagicMock(side_effect=AssertionError("open must not run"))
    monkeypatch.setattr(Path, "open", open_mock)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(
            return_value=_inspection(
                request=_request_facts(processing_check_id=None)
            )
        ),
    )
    result = diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosis)
    assert "missing_processing_check_id" in result.reasons
    open_mock.assert_not_called()


def test_root_computed_at_call_time(monkeypatch, tmp_path, outputs_dirs):
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    roots: list[Path] = []
    original = service._trusted_outputs_root

    def tracking_root():
        root = original()
        roots.append(root)
        return root

    monkeypatch.setattr(service, "_trusted_outputs_root", tracking_root)
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert len(roots) == 1


def test_divergent_roots_return_artifact_error(monkeypatch, tmp_path):
    monkeypatch.setattr(report_agent, "JSON_DIR", tmp_path / "a" / "json")
    monkeypatch.setattr(report_agent, "REPORTS_DIR", tmp_path / "b" / "reports")
    (tmp_path / "a" / "json").mkdir(parents=True)
    (tmp_path / "b" / "reports").mkdir(parents=True)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    result = diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosisError)
    assert (
        result.reason
        is ReconciliationDiagnosisErrorReason.artifact_inspection_failed
    )


def test_unsafe_resolved_directories_return_artifact_error(
    monkeypatch,
    tmp_path,
):
    outer = tmp_path / "outer_json"
    outer.mkdir()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    json_link = outputs / "json"
    json_link.symlink_to(outer)
    reports_dir = outputs / "reports"
    reports_dir.mkdir()
    monkeypatch.setattr(report_agent, "JSON_DIR", json_link)
    monkeypatch.setattr(report_agent, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    result = diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosisError)
    assert (
        result.reason
        is ReconciliationDiagnosisErrorReason.artifact_inspection_failed
    )


def test_root_resolve_oserror_returns_artifact_error(monkeypatch, outputs_dirs):
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    monkeypatch.setattr(
        Path,
        "resolve",
        MagicMock(side_effect=OSError("resolve failed")),
    )
    result = diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosisError)
    assert (
        result.reason
        is ReconciliationDiagnosisErrorReason.artifact_inspection_failed
    )


def test_root_resolve_runtime_error_returns_artifact_error(
    monkeypatch,
    outputs_dirs,
):
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    monkeypatch.setattr(
        Path,
        "resolve",
        MagicMock(side_effect=RuntimeError("symlink loop")),
    )
    result = diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosisError)
    assert (
        result.reason
        is ReconciliationDiagnosisErrorReason.artifact_inspection_failed
    )


def test_open_runtime_error_propagates(monkeypatch, outputs_dirs):
    json_dir, reports_dir = outputs_dirs
    _write_valid_artifacts(json_dir, reports_dir)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    monkeypatch.setattr(
        Path,
        "open",
        MagicMock(side_effect=RuntimeError("open bug")),
    )
    with pytest.raises(RuntimeError, match="open bug"):
        diagnose_processing_reconciliation(
            42,
            stale_after=STALE_AFTER,
            diagnosed_at=FIXED_NOW,
        )


def test_expected_paths_come_from_report_agent_helpers(
    monkeypatch,
    outputs_dirs,
):
    json_dir, reports_dir = outputs_dirs
    _write_valid_artifacts(json_dir, reports_dir)
    json_helper = MagicMock(
        side_effect=report_agent.json_path_for_check
    )
    md_helper = MagicMock(
        side_effect=report_agent.markdown_path_for_check
    )
    monkeypatch.setattr(report_agent, "json_path_for_check", json_helper)
    monkeypatch.setattr(report_agent, "markdown_path_for_check", md_helper)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    json_helper.assert_called_once_with(int(TOKEN))
    md_helper.assert_called_once_with(int(TOKEN))


def test_missing_files_are_normal_facts(monkeypatch, outputs_dirs):
    classifier = MagicMock(
        side_effect=service.classify_processing_reconciliation
    )
    monkeypatch.setattr(service, "classify_processing_reconciliation", classifier)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    result = diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosis)
    artifacts = classifier.call_args.args[0].artifacts
    assert artifacts.json_artifact.exists is False
    assert artifacts.json_artifact.is_regular_file is False
    assert artifacts.json_artifact.is_symlink is False
    assert artifacts.json_artifact.within_output_root is True
    assert artifacts.json_artifact.utf8_readable is False
    assert artifacts.json_artifact.json_valid is False
    assert artifacts.json_artifact.parsed_check_id is None
    assert artifacts.markdown_artifact.exists is False
    assert (
        result.classification
        is ReconciliationClassification.stale_no_result_evidence
    )


def test_regular_valid_json_markdown_happy_path(monkeypatch, outputs_dirs):
    json_dir, reports_dir = outputs_dirs
    json_path, markdown_path = _write_valid_artifacts(json_dir, reports_dir)
    json_text = json_path.read_text(encoding="utf-8")
    markdown_text = markdown_path.read_text(encoding="utf-8")
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(
            return_value=_inspection(
                database=ReconciliationDatabaseFacts(
                    matching_company_check_source_request_ids=(42,),
                    tool_call_names=(
                        "web_search",
                        "domain_dns_check",
                        "registry_search",
                        "risk_score",
                    ),
                    report_record_count=1,
                ),
                reports=(
                    ReconciliationReportSnapshot(
                        record_id=1,
                        check_id=TOKEN,
                        json_path=str(json_path),
                        markdown_path=str(markdown_path),
                        json_content=json_text,
                        markdown_content=markdown_text,
                    ),
                ),
            )
        ),
    )
    result = diagnose_processing_reconciliation(
        42,
        stale_after=timedelta(minutes=1),
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosis)
    assert (
        result.classification
        is ReconciliationClassification.stale_persisted_complete
    )


def test_directory_non_regular_file(monkeypatch, outputs_dirs):
    json_dir, reports_dir = outputs_dirs
    target = json_dir / f"company_check_{TOKEN}.json"
    target.mkdir()
    (reports_dir / f"company_check_{TOKEN}.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    result = diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosis)
    assert (
        result.classification
        is ReconciliationClassification.processing_inconsistent
        or result.classification
        is ReconciliationClassification.stale_artifacts_unpersisted
        or result.classification
        is ReconciliationClassification.stale_persisted_incomplete
    )


def test_symlink_inside_root_never_read(monkeypatch, outputs_dirs):
    json_dir, reports_dir = outputs_dirs
    real = json_dir / "real.json"
    real.write_text('{"check_id":"1782245999001"}', encoding="utf-8")
    link = json_dir / f"company_check_{TOKEN}.json"
    link.symlink_to(real)
    (reports_dir / f"company_check_{TOKEN}.md").write_text(
        "# md\n",
        encoding="utf-8",
    )

    open_calls: list[Path] = []
    real_open = Path.open

    def tracking_open(self, *args, **kwargs):
        open_calls.append(Path(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)
    classifier = MagicMock(
        side_effect=service.classify_processing_reconciliation
    )
    monkeypatch.setattr(service, "classify_processing_reconciliation", classifier)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert link not in open_calls
    json_facts = classifier.call_args.args[0].artifacts.json_artifact
    assert json_facts.exists is True
    assert json_facts.is_symlink is True
    assert json_facts.utf8_readable is False


def test_symlink_outside_root_never_read(monkeypatch, outputs_dirs, tmp_path):
    json_dir, reports_dir = outputs_dirs
    outside = tmp_path / "secret.json"
    outside.write_text('{"check_id":"1782245999001"}', encoding="utf-8")
    link = json_dir / f"company_check_{TOKEN}.json"
    link.symlink_to(outside)
    (reports_dir / f"company_check_{TOKEN}.md").write_text(
        "# md\n",
        encoding="utf-8",
    )

    open_calls: list[Path] = []
    real_open = Path.open

    def tracking_open(self, *args, **kwargs):
        open_calls.append(Path(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)
    classifier = MagicMock(
        side_effect=service.classify_processing_reconciliation
    )
    monkeypatch.setattr(service, "classify_processing_reconciliation", classifier)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert link not in open_calls
    assert outside not in open_calls
    json_facts = classifier.call_args.args[0].artifacts.json_artifact
    assert json_facts.exists is True
    assert json_facts.is_symlink is True
    assert json_facts.within_output_root is False
    assert json_facts.utf8_readable is False


def test_outside_root_expected_path_never_read(monkeypatch, tmp_path, outputs_dirs):
    outside_json = tmp_path / "outside.json"
    outside_md = tmp_path / "outside.md"
    outside_json.write_text("{}", encoding="utf-8")
    outside_md.write_text("md", encoding="utf-8")
    monkeypatch.setattr(
        report_agent,
        "json_path_for_check",
        MagicMock(return_value=outside_json),
    )
    monkeypatch.setattr(
        report_agent,
        "markdown_path_for_check",
        MagicMock(return_value=outside_md),
    )

    opened: list[Path] = []
    original_path_open = Path.open

    def tracking_open(self, *args, **kwargs):
        opened.append(Path(self))
        return original_path_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert outside_json not in opened
    assert outside_md not in opened


def test_missing_expected_paths_outside_root(
    monkeypatch,
    tmp_path,
    outputs_dirs,
):
    outside_json = tmp_path / "missing_outside.json"
    outside_md = tmp_path / "missing_outside.md"
    assert not outside_json.exists()
    assert not outside_md.exists()
    monkeypatch.setattr(
        report_agent,
        "json_path_for_check",
        MagicMock(return_value=outside_json),
    )
    monkeypatch.setattr(
        report_agent,
        "markdown_path_for_check",
        MagicMock(return_value=outside_md),
    )

    opened: list[Path] = []
    original_path_open = Path.open

    def tracking_open(self, *args, **kwargs):
        opened.append(Path(self))
        return original_path_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)
    classifier = MagicMock(
        side_effect=service.classify_processing_reconciliation
    )
    monkeypatch.setattr(service, "classify_processing_reconciliation", classifier)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    result = diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosis)
    assert opened == []
    artifacts = classifier.call_args.args[0].artifacts
    assert artifacts.json_artifact.exists is False
    assert artifacts.json_artifact.within_output_root is False
    assert artifacts.json_artifact.is_regular_file is False
    assert artifacts.json_artifact.is_symlink is False
    assert artifacts.json_artifact.utf8_readable is False
    assert artifacts.json_artifact.json_valid is False
    assert artifacts.json_artifact.parsed_check_id is None
    assert artifacts.markdown_artifact.exists is False
    assert artifacts.markdown_artifact.within_output_root is False
    assert artifacts.markdown_artifact.is_regular_file is False
    assert artifacts.markdown_artifact.is_symlink is False
    assert artifacts.markdown_artifact.utf8_readable is False


def test_invalid_utf8(monkeypatch, outputs_dirs):
    json_dir, reports_dir = outputs_dirs
    (json_dir / f"company_check_{TOKEN}.json").write_bytes(b"\xff\xfe not utf8")
    (reports_dir / f"company_check_{TOKEN}.md").write_text("ok", encoding="utf-8")
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    result = diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosis)


def test_invalid_json(monkeypatch, outputs_dirs):
    json_dir, reports_dir = outputs_dirs
    (json_dir / f"company_check_{TOKEN}.json").write_text(
        "{not-json",
        encoding="utf-8",
    )
    (reports_dir / f"company_check_{TOKEN}.md").write_text("ok", encoding="utf-8")
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    result = diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosis)


def test_json_check_id_missing_or_numeric(monkeypatch, outputs_dirs):
    json_dir, reports_dir = outputs_dirs
    (reports_dir / f"company_check_{TOKEN}.md").write_text("ok", encoding="utf-8")
    classifier = MagicMock(
        side_effect=service.classify_processing_reconciliation
    )
    monkeypatch.setattr(service, "classify_processing_reconciliation", classifier)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )

    (json_dir / f"company_check_{TOKEN}.json").write_text(
        json.dumps({"ok": True}),
        encoding="utf-8",
    )
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert (
        classifier.call_args.args[0].artifacts.json_artifact.parsed_check_id
        is None
    )

    (json_dir / f"company_check_{TOKEN}.json").write_text(
        json.dumps({"check_id": int(TOKEN)}),
        encoding="utf-8",
    )
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert (
        classifier.call_args.args[0].artifacts.json_artifact.parsed_check_id
        is None
    )


def test_mismatched_string_check_id_preserved(monkeypatch, outputs_dirs):
    json_dir, reports_dir = outputs_dirs
    (json_dir / f"company_check_{TOKEN}.json").write_text(
        json.dumps({"check_id": "999"}),
        encoding="utf-8",
    )
    (reports_dir / f"company_check_{TOKEN}.md").write_text("ok", encoding="utf-8")
    classifier = MagicMock(
        side_effect=service.classify_processing_reconciliation
    )
    monkeypatch.setattr(service, "classify_processing_reconciliation", classifier)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert (
        classifier.call_args.args[0].artifacts.json_artifact.parsed_check_id
        == "999"
    )


def test_permission_error_becomes_artifact_error(monkeypatch, outputs_dirs):
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    monkeypatch.setattr(
        Path,
        "lstat",
        MagicMock(side_effect=PermissionError("denied")),
    )
    result = diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert isinstance(result, ProcessingReconciliationDiagnosisError)
    assert (
        result.reason
        is ReconciliationDiagnosisErrorReason.artifact_inspection_failed
    )


def test_sha256_matches_bytes_and_file_read_once(monkeypatch, outputs_dirs):
    json_dir, reports_dir = outputs_dirs
    json_path, markdown_path = _write_valid_artifacts(json_dir, reports_dir)
    expected_json_digest = hashlib.sha256(json_path.read_bytes()).hexdigest()
    expected_md_digest = hashlib.sha256(markdown_path.read_bytes()).hexdigest()

    open_counts: dict[str, int] = {}
    original_path_open = Path.open

    def counting_open(self, *args, **kwargs):
        key = str(self)
        open_counts[key] = open_counts.get(key, 0) + 1
        return original_path_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    snapshots: list = []
    real_inspect_json = service._inspect_json_artifact
    real_inspect_md = service._inspect_file_artifact

    def capture_json(path, *, trusted_root):
        snap = real_inspect_json(path, trusted_root=trusted_root)
        snapshots.append(snap)
        return snap

    def capture_md(path, *, trusted_root):
        snap = real_inspect_md(path, trusted_root=trusted_root)
        snapshots.append(snap)
        return snap

    monkeypatch.setattr(service, "_inspect_json_artifact", capture_json)
    monkeypatch.setattr(service, "_inspect_file_artifact", capture_md)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    assert snapshots[0].sha256 == expected_json_digest
    assert snapshots[1].sha256 == expected_md_digest
    assert open_counts[str(json_path)] == 1
    assert open_counts[str(markdown_path)] == 1


def test_zero_reports_consistency_not_checked(monkeypatch, outputs_dirs):
    json_dir, reports_dir = outputs_dirs
    _write_valid_artifacts(json_dir, reports_dir)
    classifier = MagicMock(
        side_effect=service.classify_processing_reconciliation
    )
    monkeypatch.setattr(service, "classify_processing_reconciliation", classifier)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    database = classifier.call_args.args[0].database
    assert (
        database.report_json_path_consistency
        is ReconciliationConsistency.not_checked
    )
    assert (
        database.report_markdown_path_consistency
        is ReconciliationConsistency.not_checked
    )
    assert (
        database.report_json_content_consistency
        is ReconciliationConsistency.not_checked
    )
    assert (
        database.report_markdown_content_consistency
        is ReconciliationConsistency.not_checked
    )


def test_one_report_exact_path_and_content_consistency(
    monkeypatch,
    outputs_dirs,
):
    json_dir, reports_dir = outputs_dirs
    json_path, markdown_path = _write_valid_artifacts(json_dir, reports_dir)
    json_text = json_path.read_text(encoding="utf-8")
    markdown_text = markdown_path.read_text(encoding="utf-8")
    classifier = MagicMock(
        side_effect=service.classify_processing_reconciliation
    )
    monkeypatch.setattr(service, "classify_processing_reconciliation", classifier)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(
            return_value=_inspection(
                reports=(
                    ReconciliationReportSnapshot(
                        record_id=1,
                        check_id=TOKEN,
                        json_path=str(json_path),
                        markdown_path=str(markdown_path),
                        json_content=json_text,
                        markdown_content=markdown_text,
                    ),
                )
            )
        ),
    )
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    database = classifier.call_args.args[0].database
    assert (
        database.report_json_path_consistency
        is ReconciliationConsistency.consistent
    )
    assert (
        database.report_markdown_path_consistency
        is ReconciliationConsistency.consistent
    )
    assert (
        database.report_json_content_consistency
        is ReconciliationConsistency.consistent
    )
    assert (
        database.report_markdown_content_consistency
        is ReconciliationConsistency.consistent
    )


def test_unreadable_content_keeps_content_not_checked(
    monkeypatch,
    outputs_dirs,
):
    json_dir, reports_dir = outputs_dirs
    json_path = json_dir / f"company_check_{TOKEN}.json"
    markdown_path = reports_dir / f"company_check_{TOKEN}.md"
    json_path.write_bytes(b"\xff\xfe")
    markdown_path.write_text("ok", encoding="utf-8")
    classifier = MagicMock(
        side_effect=service.classify_processing_reconciliation
    )
    monkeypatch.setattr(service, "classify_processing_reconciliation", classifier)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(
            return_value=_inspection(
                reports=(
                    ReconciliationReportSnapshot(
                        record_id=1,
                        check_id=TOKEN,
                        json_path=str(json_path),
                        markdown_path=str(markdown_path),
                        json_content='{"x":1}',
                        markdown_content="ok",
                    ),
                )
            )
        ),
    )
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    database = classifier.call_args.args[0].database
    assert (
        database.report_json_content_consistency
        is ReconciliationConsistency.not_checked
    )
    assert (
        database.report_markdown_content_consistency
        is ReconciliationConsistency.consistent
    )


def test_duplicate_reports_all_four_inconsistent(monkeypatch, outputs_dirs):
    json_dir, reports_dir = outputs_dirs
    json_path, markdown_path = _write_valid_artifacts(json_dir, reports_dir)
    classifier = MagicMock(
        side_effect=service.classify_processing_reconciliation
    )
    monkeypatch.setattr(service, "classify_processing_reconciliation", classifier)
    report = ReconciliationReportSnapshot(
        record_id=1,
        check_id=TOKEN,
        json_path=str(json_path),
        markdown_path=str(markdown_path),
        json_content="{}",
        markdown_content="x",
    )
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(
            return_value=_inspection(
                reports=(
                    report,
                    report.model_copy(update={"record_id": 2}),
                )
            )
        ),
    )
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    database = classifier.call_args.args[0].database
    assert (
        database.report_json_path_consistency
        is ReconciliationConsistency.inconsistent
    )
    assert (
        database.report_markdown_path_consistency
        is ReconciliationConsistency.inconsistent
    )
    assert (
        database.report_json_content_consistency
        is ReconciliationConsistency.inconsistent
    )
    assert (
        database.report_markdown_content_consistency
        is ReconciliationConsistency.inconsistent
    )


def test_repository_and_classifier_called_once(monkeypatch, outputs_dirs):
    json_dir, reports_dir = outputs_dirs
    _write_valid_artifacts(json_dir, reports_dir)
    repo = MagicMock(return_value=_inspection())
    classifier = MagicMock(
        side_effect=service.classify_processing_reconciliation
    )
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        repo,
    )
    monkeypatch.setattr(service, "classify_processing_reconciliation", classifier)
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    repo.assert_called_once_with(42)
    classifier.assert_called_once()


def test_no_filesystem_writes(monkeypatch, outputs_dirs):
    write_text = MagicMock(side_effect=AssertionError("write_text forbidden"))
    mkdir = MagicMock(side_effect=AssertionError("mkdir forbidden"))
    monkeypatch.setattr(Path, "write_text", write_text)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(
        service,
        "get_processing_reconciliation_database_inspection",
        MagicMock(return_value=_inspection()),
    )
    diagnose_processing_reconciliation(
        42,
        stale_after=STALE_AFTER,
        diagnosed_at=FIXED_NOW,
    )
    write_text.assert_not_called()
    mkdir.assert_not_called()
