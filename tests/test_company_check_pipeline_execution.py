"""Focused tests for optional check_id and persistence-free pipeline helper."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.agents.company_check_agent import CompanyCheckAgent
from app.agents.name_normalizer_agent import NameNormalizer
from app.agents.report_agent import ReportAgent
from app.agents.risk_agent import RiskAgent
from app.schemas.company_check import (
    CheckStatus,
    CompanyCheckResponse,
    DomainDnsInfo,
    DomainDnsStatus,
)
from app.schemas.name_normalizer import NameNormalizerInput
from app.schemas.registry import RegistryCheckResult, RegistryCheckStatus
from app.schemas.risk import HumanReviewStatus
from app.schemas.source import ConfidenceLevel
from app.services import company_check_service


SUPPLIED_CHECK_ID = 1782245999001
GENERATED_CHECK_ID = 1782245999002


def _build_agent(*, report_agent: ReportAgent | MagicMock | None = None) -> CompanyCheckAgent:
    web_search_agent = MagicMock()
    web_search_agent.run.return_value = []

    registry_agent = MagicMock()
    registry_agent.run.return_value = RegistryCheckResult(
        company_name="Servochron",
        country="Austria",
        status=RegistryCheckStatus.not_found,
        registry_found=False,
        confidence=ConfidenceLevel.low,
        is_mock=True,
    )

    domain_agent = MagicMock()
    domain_agent.run.return_value = DomainDnsInfo(status=DomainDnsStatus.not_provided)

    human_review_agent = MagicMock()
    human_review_agent.run.return_value = HumanReviewStatus.pending

    name_normalizer = MagicMock(spec=NameNormalizer)
    name_normalizer.run.return_value = NameNormalizer().run(
        NameNormalizerInput(
            company_name="Servochron",
            country="Austria",
            domain=None,
        )
    )

    return CompanyCheckAgent(
        name_normalizer=name_normalizer,
        web_search_agent=web_search_agent,
        domain_agent=domain_agent,
        registry_agent=registry_agent,
        risk_agent=RiskAgent(),
        report_agent=report_agent or ReportAgent(),
        human_review_agent=human_review_agent,
    )


def test_supplied_check_id_is_used_exactly_and_skips_id_generation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    new_check_id = MagicMock(return_value=GENERATED_CHECK_ID)
    monkeypatch.setattr(
        "app.agents.company_check_agent._new_check_id",
        new_check_id,
    )

    response = _build_agent().run(
        company_name="Servochron",
        country="Austria",
        check_id=SUPPLIED_CHECK_ID,
    )

    new_check_id.assert_not_called()
    assert response.check_id == SUPPLIED_CHECK_ID
    assert response.json_result is not None
    assert response.json_result.check_id == SUPPLIED_CHECK_ID

    json_path = Path(f"outputs/json/company_check_{SUPPLIED_CHECK_ID}.json")
    markdown_path = Path(f"outputs/reports/company_check_{SUPPLIED_CHECK_ID}.md")
    assert json_path.exists()
    assert markdown_path.exists()
    assert response.markdown_report_path == str(markdown_path)

    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["check_id"] == SUPPLIED_CHECK_ID


def test_omitted_check_id_calls_new_check_id_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    new_check_id = MagicMock(return_value=GENERATED_CHECK_ID)
    monkeypatch.setattr(
        "app.agents.company_check_agent._new_check_id",
        new_check_id,
    )

    response = _build_agent().run(
        company_name="Servochron",
        country="Austria",
    )

    new_check_id.assert_called_once_with()
    assert response.check_id == GENERATED_CHECK_ID
    assert response.json_result is not None
    assert response.json_result.check_id == GENERATED_CHECK_ID
    assert Path(f"outputs/json/company_check_{GENERATED_CHECK_ID}.json").exists()
    assert Path(f"outputs/reports/company_check_{GENERATED_CHECK_ID}.md").exists()


def test_new_check_id_not_called_when_name_normalization_fails(monkeypatch):
    new_check_id = MagicMock(return_value=GENERATED_CHECK_ID)
    monkeypatch.setattr(
        "app.agents.company_check_agent._new_check_id",
        new_check_id,
    )

    agent = _build_agent(report_agent=MagicMock())
    agent.name_normalizer.run.side_effect = RuntimeError("normalization failed")

    with pytest.raises(RuntimeError, match="normalization failed"):
        agent.run(company_name="Servochron", country="Austria")

    new_check_id.assert_not_called()


@pytest.mark.parametrize("invalid_check_id", [True, False, 0, -1, "123"])
def test_invalid_supplied_check_id_raises_value_error(invalid_check_id):
    with pytest.raises(ValueError, match="check_id must be a positive integer when supplied"):
        _build_agent(report_agent=MagicMock()).run(
            company_name="Servochron",
            country="Austria",
            check_id=invalid_check_id,
        )


def test_execute_company_check_pipeline_forwards_check_id_without_persistence(monkeypatch):
    expected = CompanyCheckResponse(
        check_id=SUPPLIED_CHECK_ID,
        status=CheckStatus.completed,
        json_result=None,
        markdown_report_path="mock.md",
    )
    check_agent = MagicMock()
    check_agent.run.return_value = expected
    monkeypatch.setattr(company_check_service, "_check_agent", check_agent)

    def fail_persist(*_args, **_kwargs):
        raise AssertionError("_persist_company_check must not be called")

    def fail_save(*_args, **_kwargs):
        raise AssertionError("save_company_check must not be called")

    monkeypatch.setattr(company_check_service, "_persist_company_check", fail_persist)
    monkeypatch.setattr(company_check_service, "save_company_check", fail_save)

    response = company_check_service.execute_company_check_pipeline(
        company_name="Servochron",
        country="Austria",
        domain="servochron.com",
        check_id=SUPPLIED_CHECK_ID,
    )

    assert response is expected
    check_agent.run.assert_called_once_with(
        company_name="Servochron",
        country="Austria",
        domain="servochron.com",
        check_id=SUPPLIED_CHECK_ID,
    )


def test_run_company_check_delegates_to_pipeline_then_persists(monkeypatch):
    expected = CompanyCheckResponse(
        check_id=GENERATED_CHECK_ID,
        status=CheckStatus.completed,
        json_result=None,
        markdown_report_path="mock.md",
    )
    pipeline = MagicMock(return_value=expected)
    persist = MagicMock()
    monkeypatch.setattr(company_check_service, "execute_company_check_pipeline", pipeline)
    monkeypatch.setattr(company_check_service, "_persist_company_check", persist)

    response = company_check_service.run_company_check(
        company_name="Servochron",
        country="Austria",
        domain=None,
    )

    assert response is expected
    pipeline.assert_called_once_with(
        company_name="Servochron",
        country="Austria",
        domain=None,
    )
    persist.assert_called_once_with(expected)


def test_run_company_check_tolerates_save_company_check_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    expected = _build_agent().run(
        company_name="Servochron",
        country="Austria",
        check_id=SUPPLIED_CHECK_ID,
    )

    monkeypatch.setattr(
        company_check_service,
        "execute_company_check_pipeline",
        MagicMock(return_value=expected),
    )
    save_mock = MagicMock(side_effect=RuntimeError("db unavailable"))
    monkeypatch.setattr(company_check_service, "save_company_check", save_mock)

    response = company_check_service.run_company_check(
        company_name="Servochron",
        country="Austria",
    )

    assert response is expected
    save_mock.assert_called_once()


def test_execute_company_check_pipeline_propagates_exceptions(monkeypatch):
    check_agent = MagicMock()
    check_agent.run.side_effect = RuntimeError("pipeline failed")
    monkeypatch.setattr(company_check_service, "_check_agent", check_agent)

    with pytest.raises(RuntimeError, match="pipeline failed"):
        company_check_service.execute_company_check_pipeline(
            company_name="Servochron",
            country="Austria",
            check_id=SUPPLIED_CHECK_ID,
        )
