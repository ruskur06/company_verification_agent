"""Database repository functions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.db.database import SessionLocal
from app.db.models import CompanyCheckRecord, ReportRecord, SourceRecord, ToolCallRecord


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=str)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


def _read_text_file(path: str | None) -> str | None:
    if not path:
        return None

    file_path = Path(path)
    if not file_path.exists():
        return None

    return file_path.read_text(encoding="utf-8")


def _record_to_dict(record: CompanyCheckRecord) -> dict:
    return {
        "check_id": record.check_id,
        "company_name": record.company_name,
        "country": record.country,
        "domain": record.domain,
        "risk_score": record.risk_score,
        "risk_level": record.risk_level,
        "human_review_status": record.human_review_status,
        "json_report_path": record.json_report_path,
        "markdown_report_path": record.markdown_report_path,
        "registry_check": json.loads(record.registry_check_json) if record.registry_check_json else None,
        "domain_check": json.loads(record.domain_check_json) if record.domain_check_json else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


def _delete_related_records(session, check_id: str) -> None:
    session.query(SourceRecord).filter(SourceRecord.check_id == check_id).delete()
    session.query(ToolCallRecord).filter(ToolCallRecord.check_id == check_id).delete()
    session.query(ReportRecord).filter(ReportRecord.check_id == check_id).delete()


def _save_sources(session, check_id: str, sources: list[dict]) -> None:
    for source in sources:
        session.add(
            SourceRecord(
                check_id=check_id,
                title=source.get("title"),
                url=source.get("url"),
                snippet=source.get("snippet"),
                source_type=source.get("source_type"),
                confidence=source.get("confidence"),
                is_mock=bool(source.get("is_mock", False)),
            )
        )


def _save_tool_calls(session, check_id: str, tool_calls: list[dict]) -> None:
    for tool_call in tool_calls:
        session.add(
            ToolCallRecord(
                check_id=check_id,
                tool_name=tool_call.get("tool_name", "unknown"),
                status=tool_call.get("status", "completed"),
                input_json=_json_dumps(tool_call.get("input")),
                output_json=_json_dumps(tool_call.get("output")),
            )
        )


def _build_default_tool_calls(result: dict) -> list[dict]:
    company = result.get("company") or {}
    domain_check = result.get("domain_check") or result.get("domain_dns")
    registry_check = result.get("registry_check")
    risk = result.get("risk") or {}
    sources = result.get("sources") or []

    return [
        {
            "tool_name": "web_search",
            "status": "completed",
            "input": {
                "company_name": company.get("name") or result.get("company_name"),
                "country": company.get("country") or result.get("country"),
            },
            "output": sources,
        },
        {
            "tool_name": "domain_dns_check",
            "status": "completed",
            "input": {"domain": company.get("domain") or result.get("domain")},
            "output": domain_check,
        },
        {
            "tool_name": "registry_search",
            "status": "completed",
            "input": {
                "company_name": company.get("name") or result.get("company_name"),
                "country": company.get("country") or result.get("country"),
            },
            "output": registry_check,
        },
        {
            "tool_name": "risk_score",
            "status": "completed",
            "input": None,
            "output": risk,
        },
    ]


def save_company_check(result: dict) -> None:
    """Persist one company check and related records."""
    check_id = str(result.get("check_id", "")).strip()
    if not check_id:
        return

    company = result.get("company") or {}
    risk = result.get("risk") or {}
    domain_check = result.get("domain_check") or result.get("domain_dns")
    registry_check = result.get("registry_check")
    sources = result.get("sources") or []
    tool_calls = result.get("tool_calls") or _build_default_tool_calls(result)

    company_name = company.get("name") or result.get("company_name") or "unknown"
    country = company.get("country") or result.get("country") or "unknown"
    domain = company.get("domain") if "domain" in company else result.get("domain")

    risk_score = risk.get("preliminary_score", result.get("risk_score"))
    risk_level = risk.get("preliminary_level", result.get("risk_level"))
    if hasattr(risk_level, "value"):
        risk_level = risk_level.value

    human_review_status = risk.get("human_review_status", result.get("human_review_status", "pending"))
    if hasattr(human_review_status, "value"):
        human_review_status = human_review_status.value

    json_report_path = result.get("json_report_path")
    markdown_report_path = result.get("markdown_report_path")
    created_at = _parse_datetime(result.get("created_at"))

    session = SessionLocal()
    try:
        existing = (
            session.query(CompanyCheckRecord)
            .filter(CompanyCheckRecord.check_id == check_id)
            .first()
        )

        if existing:
            existing.company_name = company_name
            existing.country = country
            existing.domain = domain
            existing.risk_score = risk_score
            existing.risk_level = risk_level
            existing.human_review_status = str(human_review_status)
            existing.json_report_path = json_report_path
            existing.markdown_report_path = markdown_report_path
            existing.registry_check_json = _json_dumps(registry_check)
            existing.domain_check_json = _json_dumps(domain_check)
            if created_at is not None:
                existing.created_at = created_at
            record = existing
            _delete_related_records(session, check_id)
        else:
            record = CompanyCheckRecord(
                check_id=check_id,
                company_name=company_name,
                country=country,
                domain=domain,
                risk_score=risk_score,
                risk_level=risk_level,
                human_review_status=str(human_review_status),
                json_report_path=json_report_path,
                markdown_report_path=markdown_report_path,
                registry_check_json=_json_dumps(registry_check),
                domain_check_json=_json_dumps(domain_check),
                created_at=created_at or datetime.utcnow(),
            )
            session.add(record)

        _save_sources(session, check_id, sources)
        _save_tool_calls(session, check_id, tool_calls)

        session.add(
            ReportRecord(
                check_id=check_id,
                json_path=json_report_path,
                markdown_path=markdown_report_path,
                json_content=_read_text_file(json_report_path),
                markdown_content=_read_text_file(markdown_report_path),
            )
        )

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class CompanyCheckNotFoundError(LookupError):
    """Raised when a company check record does not exist in the database."""


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return value.value
    return str(value)


def _source_record_to_dict(record: SourceRecord) -> dict:
    created_at = record.created_at or datetime.utcnow()
    return {
        "id": record.id,
        "company_check_id": record.check_id,
        "title": record.title or "",
        "url": record.url or "",
        "snippet": record.snippet,
        "source_type": record.source_type or "other",
        "confidence": record.confidence or "low",
        "is_mock": record.is_mock,
        "retrieved_at": created_at,
        "created_at": created_at,
    }


def add_source_to_company_check(company_check_id: str, source_data: dict) -> dict:
    """Attach one human-verified source to an existing company check."""
    check_id = str(company_check_id).strip()
    if not check_id:
        raise ValueError("company_check_id must not be empty")

    session = SessionLocal()
    try:
        existing = (
            session.query(CompanyCheckRecord)
            .filter(CompanyCheckRecord.check_id == check_id)
            .first()
        )
        if existing is None:
            raise CompanyCheckNotFoundError(f"Company check {check_id} was not found.")

        record = SourceRecord(
            check_id=check_id,
            title=source_data.get("title"),
            url=source_data.get("url"),
            snippet=source_data.get("snippet"),
            source_type=_enum_value(source_data.get("source_type")),
            confidence=_enum_value(source_data.get("confidence")),
            is_mock=False,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return _source_record_to_dict(record)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_company_check_by_id(check_id: str) -> dict | None:
    """Load one saved company check by check_id."""
    session = SessionLocal()
    try:
        record = (
            session.query(CompanyCheckRecord)
            .filter(CompanyCheckRecord.check_id == str(check_id))
            .first()
        )
        if record is None:
            return None
        return _record_to_dict(record)
    finally:
        session.close()


def list_company_checks(limit: int = 20) -> list[dict]:
    """List recent saved company checks."""
    session = SessionLocal()
    try:
        records = (
            session.query(CompanyCheckRecord)
            .order_by(CompanyCheckRecord.created_at.desc())
            .limit(limit)
            .all()
        )
        return [_record_to_dict(record) for record in records]
    finally:
        session.close()
