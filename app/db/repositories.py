"""Database repository functions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.database import SessionLocal
from app.db.models import (
    CheckRequestRecord,
    CompanyCheckRecord,
    HumanReviewRecord,
    ReportRecord,
    SourceRecord,
    ToolCallRecord,
)
from app.schemas.processing_reconciliation import (
    ProcessingReconciliationDatabaseInspection,
    ProcessingRequestFacts,
    ReconciliationCompanyCheckSnapshot,
    ReconciliationDatabaseFacts,
    ReconciliationReportSnapshot,
)


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
        "is_locked": bool(record.is_locked),
        "official_website_review": {
            "decision": record.official_website_review_decision or "pending",
            "note": record.official_website_review_note,
            "reviewed_by": record.official_website_review_reviewed_by,
            "reviewed_at": (
                record.official_website_review_reviewed_at.isoformat()
                if record.official_website_review_reviewed_at
                else None
            ),
        },
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
                relevance=_enum_value(source.get("relevance")) or "uncertain",
                relevance_score=float(source.get("relevance_score", 0.0)),
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


class CompanyCheckLockedError(RuntimeError):
    """Raised when a company check is finalized and cannot be modified."""


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
        "relevance": record.relevance or "uncertain",
        "relevance_score": float(record.relevance_score or 0.0),
        "retrieved_at": created_at,
        "created_at": created_at,
    }


def _human_review_record_to_dict(record: HumanReviewRecord, *, is_locked: bool) -> dict:
    return {
        "id": record.id,
        "company_check_id": record.check_id,
        "decision": record.decision,
        "reviewer_name": record.reviewer_name,
        "reviewer_notes": record.reviewer_notes,
        "final_verification_confidence": record.final_verification_confidence,
        "final_verification_risk": record.final_verification_risk,
        "final_business_risk": record.final_business_risk,
        "overrides": json.loads(record.overrides_json) if record.overrides_json else {},
        "is_locked": is_locked,
        "created_at": record.created_at or datetime.utcnow(),
    }


def is_company_check_locked(company_check_id: str) -> bool:
    """Return whether a company check has been finalized by human review."""
    check_id = str(company_check_id).strip()
    session = SessionLocal()
    try:
        record = (
            session.query(CompanyCheckRecord)
            .filter(CompanyCheckRecord.check_id == check_id)
            .first()
        )
        if record is None:
            return False
        return bool(record.is_locked)
    finally:
        session.close()


def get_human_reviews_for_company_check(company_check_id: str) -> list[dict]:
    """Load all human review records for a company check."""
    check_id = str(company_check_id).strip()
    session = SessionLocal()
    try:
        company_check = (
            session.query(CompanyCheckRecord)
            .filter(CompanyCheckRecord.check_id == check_id)
            .first()
        )
        is_locked = bool(company_check.is_locked) if company_check is not None else False

        records = (
            session.query(HumanReviewRecord)
            .filter(HumanReviewRecord.check_id == check_id)
            .order_by(HumanReviewRecord.id.asc())
            .all()
        )
        return [_human_review_record_to_dict(record, is_locked=is_locked) for record in records]
    finally:
        session.close()


def create_human_review_record(company_check_id: str, review_data: dict) -> dict:
    """Create a new human review record and lock the company check."""
    check_id = str(company_check_id).strip()
    if not check_id:
        raise ValueError("company_check_id must not be empty")

    session = SessionLocal()
    try:
        company_check = (
            session.query(CompanyCheckRecord)
            .filter(CompanyCheckRecord.check_id == check_id)
            .first()
        )
        if company_check is None:
            raise CompanyCheckNotFoundError(f"Company check {check_id} was not found.")
        if company_check.is_locked:
            raise CompanyCheckLockedError(f"Company check {check_id} is already finalized.")

        decision = _enum_value(review_data.get("decision"))
        if decision is None:
            raise ValueError("decision must not be empty")

        review_record = HumanReviewRecord(
            check_id=check_id,
            decision=decision,
            reviewer_name=review_data.get("reviewer_name"),
            reviewer_notes=review_data.get("reviewer_notes"),
            final_verification_confidence=_enum_value(
                review_data.get("final_verification_confidence")
            ),
            final_verification_risk=_enum_value(review_data.get("final_verification_risk")),
            final_business_risk=_enum_value(review_data.get("final_business_risk")),
            overrides_json=_json_dumps(review_data.get("overrides") or {}),
        )
        session.add(review_record)

        company_check.is_locked = True
        company_check.human_review_status = decision

        session.commit()
        session.refresh(review_record)
        return _human_review_record_to_dict(review_record, is_locked=True)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


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
        if existing.is_locked:
            raise CompanyCheckLockedError(f"Company check {check_id} is already finalized.")

        record = SourceRecord(
            check_id=check_id,
            title=source_data.get("title"),
            url=source_data.get("url"),
            snippet=source_data.get("snippet"),
            source_type=_enum_value(source_data.get("source_type")),
            confidence=_enum_value(source_data.get("confidence")),
            is_mock=False,
            relevance="relevant",
            relevance_score=1.0,
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


def get_sources_for_company_check(company_check_id: str) -> list[dict]:
    """Load all sources linked to a company check."""
    check_id = str(company_check_id).strip()
    session = SessionLocal()
    try:
        records = (
            session.query(SourceRecord)
            .filter(SourceRecord.check_id == check_id)
            .order_by(SourceRecord.id.asc())
            .all()
        )
        return [_source_record_to_dict(record) for record in records]
    finally:
        session.close()


def update_official_website_review(company_check_id: str, review_data: dict) -> None:
    """Persist official website review decision for a company check."""
    check_id = str(company_check_id).strip()
    if not check_id:
        raise ValueError("company_check_id must not be empty")

    session = SessionLocal()
    try:
        record = (
            session.query(CompanyCheckRecord)
            .filter(CompanyCheckRecord.check_id == check_id)
            .first()
        )
        if record is None:
            raise CompanyCheckNotFoundError(f"Company check {check_id} was not found.")

        record.official_website_review_decision = str(review_data.get("decision", "pending"))
        record.official_website_review_note = review_data.get("note")
        record.official_website_review_reviewed_by = review_data.get("reviewed_by")
        record.official_website_review_reviewed_at = _parse_datetime(
            review_data.get("reviewed_at")
        )

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def update_final_risk_review(company_check_id: str, review_data: dict) -> None:
    """Persist final risk human review status for a company check."""
    check_id = str(company_check_id).strip()
    if not check_id:
        raise ValueError("company_check_id must not be empty")

    session = SessionLocal()
    try:
        record = (
            session.query(CompanyCheckRecord)
            .filter(CompanyCheckRecord.check_id == check_id)
            .first()
        )
        if record is None:
            raise CompanyCheckNotFoundError(f"Company check {check_id} was not found.")

        record.human_review_status = str(review_data.get("human_review_status", "pending"))

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def update_company_check_after_refresh(
    result: dict,
    *,
    official_website_review_data: dict | None = None,
) -> None:
    """Update company check metadata and append a refreshed report record."""
    check_id = str(result.get("check_id", "")).strip()
    if not check_id:
        raise ValueError("check_id must not be empty")

    risk = result.get("risk") or {}
    risk_score = risk.get("preliminary_score", result.get("risk_score"))
    risk_level = risk.get("preliminary_level", result.get("risk_level"))
    if hasattr(risk_level, "value"):
        risk_level = risk_level.value

    human_review_status = risk.get("human_review_status", result.get("human_review_status", "pending"))
    if hasattr(human_review_status, "value"):
        human_review_status = human_review_status.value

    json_report_path = result.get("json_report_path")
    markdown_report_path = result.get("markdown_report_path")

    session = SessionLocal()
    try:
        record = (
            session.query(CompanyCheckRecord)
            .filter(CompanyCheckRecord.check_id == check_id)
            .first()
        )
        if record is None:
            raise CompanyCheckNotFoundError(f"Company check {check_id} was not found.")
        if record.is_locked:
            raise CompanyCheckLockedError(f"Company check {check_id} is already finalized.")

        if official_website_review_data is not None:
            record.official_website_review_decision = str(
                official_website_review_data.get("decision", "pending")
            )
            record.official_website_review_note = official_website_review_data.get("note")
            record.official_website_review_reviewed_by = official_website_review_data.get(
                "reviewed_by"
            )
            record.official_website_review_reviewed_at = _parse_datetime(
                official_website_review_data.get("reviewed_at")
            )

        record.risk_score = risk_score
        record.risk_level = risk_level
        record.human_review_status = str(human_review_status)
        record.json_report_path = json_report_path
        record.markdown_report_path = markdown_report_path

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


def _check_request_record_to_dict(
    record: CheckRequestRecord,
) -> dict:
    """Convert one public check request record to a dictionary."""
    return {
        "id": record.id,
        "company_name": record.company_name,
        "country": record.country,
        "email": record.email,
        "website": record.website,
        "transaction_type": record.transaction_type,
        "additional_context": record.additional_context,
        "preferred_language": record.preferred_language,
        "status": record.status,
        "company_check_id": record.company_check_id,
        "processing_check_id": record.processing_check_id,
        "processing_started_at": record.processing_started_at,
        "created_at": record.created_at or datetime.utcnow(),
    }


def create_check_request_record(
    request_data: dict,
) -> dict:
    """Persist one public request without starting verification."""
    company_name = str(
        request_data.get("company_name") or ""
    ).strip()
    country = str(
        request_data.get("country") or ""
    ).strip()
    email = str(
        request_data.get("email") or ""
    ).strip()
    preferred_language = _enum_value(
        request_data.get("preferred_language")
    )

    if not company_name:
        raise ValueError("company_name must not be empty")
    if not country:
        raise ValueError("country must not be empty")
    if not email:
        raise ValueError("email must not be empty")
    if not preferred_language:
        raise ValueError(
            "preferred_language must not be empty"
        )

    record = CheckRequestRecord(
        company_name=company_name,
        country=country,
        email=email,
        website=request_data.get("website"),
        transaction_type=_enum_value(
            request_data.get("transaction_type")
        ),
        additional_context=request_data.get(
            "additional_context"
        ),
        preferred_language=preferred_language,
        status="pending",
        company_check_id=None,
    )

    session = SessionLocal()
    try:
        session.add(record)
        session.commit()
        session.refresh(record)

        return _check_request_record_to_dict(record)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_check_request_by_id(
    request_id: int,
) -> dict | None:
    """Load one saved public check request."""
    session = SessionLocal()
    try:
        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .first()
        )

        if record is None:
            return None

        return _check_request_record_to_dict(record)
    finally:
        session.close()


def list_check_requests(limit: int = 50) -> list[dict]:
    """List recent public check requests, newest first."""
    session = SessionLocal()
    try:
        records = (
            session.query(CheckRequestRecord)
            .order_by(
                CheckRequestRecord.created_at.desc(),
                CheckRequestRecord.id.desc(),
            )
            .limit(limit)
            .all()
        )
        return [
            _check_request_record_to_dict(record)
            for record in records
        ]
    finally:
        session.close()


def list_processing_check_request_records(
    limit: int = 50,
) -> list[dict]:
    """List processing CheckRequest rows for reconciliation UI, newest first."""
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit <= 0
    ):
        raise ValueError("limit must be a positive integer")

    session = SessionLocal()
    try:
        records = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.status == "processing")
            .order_by(
                CheckRequestRecord.processing_started_at.desc().nullslast(),
                CheckRequestRecord.id.desc(),
            )
            .limit(limit)
            .all()
        )
        return [
            _check_request_record_to_dict(record)
            for record in records
        ]
    finally:
        session.close()


def update_check_request_status(
    request_id: int,
    *,
    expected_status: str,
    new_status: str,
) -> dict | None:
    """Conditionally update one request status and return the new record."""
    session = SessionLocal()
    try:
        updated_rows = (
            session.query(CheckRequestRecord)
            .filter(
                CheckRequestRecord.id == request_id,
                CheckRequestRecord.status == expected_status,
            )
            .update(
                {"status": new_status},
                synchronize_session=False,
            )
        )

        if updated_rows == 0:
            session.rollback()
            return None

        session.commit()

        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .first()
        )
        if record is None:
            return None

        return _check_request_record_to_dict(record)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def company_check_id_exists(check_id: str) -> bool:
    """Return whether a CompanyCheckRecord already uses this check_id."""
    normalized = str(check_id).strip()
    if not normalized:
        raise ValueError("check_id must not be empty")

    session = SessionLocal()
    try:
        record = (
            session.query(CompanyCheckRecord.id)
            .filter(CompanyCheckRecord.check_id == normalized)
            .first()
        )
        return record is not None
    finally:
        session.close()


def processing_check_id_exists(processing_check_id: str) -> bool:
    """Return whether a CheckRequestRecord already uses this processing_check_id."""
    normalized = str(processing_check_id).strip()
    if not normalized:
        raise ValueError("processing_check_id must not be empty")

    session = SessionLocal()
    try:
        record = (
            session.query(CheckRequestRecord.id)
            .filter(CheckRequestRecord.processing_check_id == normalized)
            .first()
        )
        return record is not None
    finally:
        session.close()


def claim_approved_check_request_record(
    request_id: int,
    *,
    processing_check_id: str,
    processing_started_at: datetime,
) -> dict | None:
    """Atomically claim an approved request for processing."""
    session = SessionLocal()
    try:
        updated_rows = (
            session.query(CheckRequestRecord)
            .filter(
                CheckRequestRecord.id == request_id,
                CheckRequestRecord.status == "approved",
                CheckRequestRecord.company_check_id.is_(None),
                CheckRequestRecord.processing_check_id.is_(None),
                CheckRequestRecord.processing_started_at.is_(None),
            )
            .update(
                {
                    "status": "processing",
                    "processing_check_id": processing_check_id,
                    "processing_started_at": processing_started_at,
                },
                synchronize_session=False,
            )
        )

        if updated_rows == 0:
            session.rollback()
            return None

        session.commit()

        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .first()
        )
        if record is None:
            return None

        return _check_request_record_to_dict(record)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class ApprovedRequestPersistenceFenceError(RuntimeError):
    """Raised when strict request finalization loses its processing fence."""

    def __init__(
        self,
        message: str,
        *,
        source_check_request_id: int,
        processing_check_id: str,
    ) -> None:
        super().__init__(message)
        self.source_check_request_id = source_check_request_id
        self.processing_check_id = processing_check_id


def _map_strict_company_check_fields(result: dict[str, Any]) -> dict[str, Any]:
    """Map prepared result_payload fields using the legacy save_company_check rules."""
    company = result.get("company") or {}
    risk = result.get("risk") or {}

    company_name = company.get("name") or result.get("company_name") or "unknown"
    country = company.get("country") or result.get("country") or "unknown"
    domain = company.get("domain") if "domain" in company else result.get("domain")

    risk_score = risk.get("preliminary_score", result.get("risk_score"))
    risk_level = risk.get("preliminary_level", result.get("risk_level"))
    if hasattr(risk_level, "value"):
        risk_level = risk_level.value

    human_review_status = risk.get(
        "human_review_status",
        result.get("human_review_status", "pending"),
    )
    if hasattr(human_review_status, "value"):
        human_review_status = human_review_status.value

    registry_check = result.get("registry_check")
    domain_check = result.get("domain_check") or result.get("domain_dns")
    created_at = _parse_datetime(result.get("created_at")) or datetime.utcnow()

    return {
        "company_name": company_name,
        "country": country,
        "domain": domain,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "human_review_status": str(human_review_status),
        "registry_check_json": _json_dumps(registry_check),
        "domain_check_json": _json_dumps(domain_check),
        "created_at": created_at,
    }


def persist_prepared_approved_request_check_record(
    *,
    source_check_request_id: int,
    processing_check_id: str,
    result_payload: dict[str, Any],
    json_report_path: str,
    markdown_report_path: str,
    json_content: str,
    markdown_content: str,
) -> None:
    """Insert CompanyCheck artifacts and fence processing → processed in one transaction."""
    mapped_fields = _map_strict_company_check_fields(result_payload)
    sources = result_payload.get("sources") or []
    tool_calls = result_payload.get("tool_calls") or _build_default_tool_calls(
        result_payload
    )

    session = SessionLocal()
    try:
        session.add(
            CompanyCheckRecord(
                check_id=processing_check_id,
                source_check_request_id=source_check_request_id,
                json_report_path=json_report_path,
                markdown_report_path=markdown_report_path,
                **mapped_fields,
            )
        )
        _save_sources(session, processing_check_id, sources)
        _save_tool_calls(session, processing_check_id, tool_calls)
        session.add(
            ReportRecord(
                check_id=processing_check_id,
                json_path=json_report_path,
                markdown_path=markdown_report_path,
                json_content=json_content,
                markdown_content=markdown_content,
            )
        )

        session.flush()

        updated_rows = (
            session.query(CheckRequestRecord)
            .filter(
                CheckRequestRecord.id == source_check_request_id,
                CheckRequestRecord.status == "processing",
                CheckRequestRecord.processing_check_id == processing_check_id,
                CheckRequestRecord.company_check_id.is_(None),
                CheckRequestRecord.processing_started_at.is_not(None),
            )
            .update(
                {
                    "status": "processed",
                    "company_check_id": processing_check_id,
                    "processing_check_id": None,
                    "processing_started_at": None,
                },
                synchronize_session=False,
            )
        )
        if updated_rows != 1:
            raise ApprovedRequestPersistenceFenceError(
                (
                    f"Strict persistence fence failed for check request "
                    f"{source_check_request_id} with processing check ID "
                    f"{processing_check_id}."
                ),
                source_check_request_id=source_check_request_id,
                processing_check_id=processing_check_id,
            )

        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        raise
    finally:
        session.close()


def _as_aware_utc(value: datetime | None) -> datetime | None:
    """Treat naive DB timestamps as UTC and normalize aware values to UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_optional_check_id(value: str | None) -> str | None:
    """Strip linkage/token query values without mutating stored DB fields."""
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def get_processing_reconciliation_database_inspection(
    request_id: int,
) -> ProcessingReconciliationDatabaseInspection | None:
    """Read advisory DB facts for one CheckRequest reconciliation diagnosis.

    Multiple SELECTs under PostgreSQL READ COMMITTED may observe read-skew.
    This diagnostic result is advisory; Commit 7 must freshly revalidate
    everything before mutation.
    """
    if (
        isinstance(request_id, bool)
        or not isinstance(request_id, int)
        or request_id <= 0
    ):
        raise ValueError("request_id must be a positive integer")

    session = SessionLocal()
    try:
        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one_or_none()
        )
        if record is None:
            return None

        request_facts = ProcessingRequestFacts(
            request_id=record.id,
            status=record.status,
            company_check_id=_normalize_optional_check_id(
                record.company_check_id
            ),
            processing_check_id=_normalize_optional_check_id(
                record.processing_check_id
            ),
            processing_started_at=_as_aware_utc(
                record.processing_started_at
            ),
        )

        token = request_facts.processing_check_id
        if token is None:
            return ProcessingReconciliationDatabaseInspection(
                request=request_facts,
                database=ReconciliationDatabaseFacts(),
                token_company_checks=(),
                token_report_records=(),
            )

        company_check_rows = (
            session.query(CompanyCheckRecord)
            .filter(CompanyCheckRecord.check_id == token)
            .order_by(CompanyCheckRecord.id.asc())
            .all()
        )
        token_company_checks = tuple(
            ReconciliationCompanyCheckSnapshot(
                record_id=row.id,
                check_id=row.check_id,
                source_check_request_id=row.source_check_request_id,
                json_report_path=row.json_report_path,
                markdown_report_path=row.markdown_report_path,
            )
            for row in company_check_rows
        )
        # Malformed raw source_check_request_id values remain in
        # token_company_checks; the classifier-facing facts schema
        # supports only positive IDs or None.
        matching_source_ids: list[int | None] = []
        for row in company_check_rows:
            source_id = row.source_check_request_id
            if (
                isinstance(source_id, int)
                and not isinstance(source_id, bool)
                and source_id > 0
            ):
                matching_source_ids.append(source_id)
            else:
                matching_source_ids.append(None)

        foreign_rows = (
            session.query(CheckRequestRecord.id)
            .filter(
                CheckRequestRecord.processing_check_id == token,
                CheckRequestRecord.id != request_id,
            )
            .order_by(CheckRequestRecord.id.asc())
            .all()
        )
        foreign_processing_token_request_ids = tuple(
            row_id for (row_id,) in foreign_rows
        )

        source_record_count = (
            session.query(SourceRecord)
            .filter(SourceRecord.check_id == token)
            .count()
        )

        tool_call_rows = (
            session.query(ToolCallRecord)
            .filter(ToolCallRecord.check_id == token)
            .order_by(ToolCallRecord.id.asc())
            .all()
        )
        tool_call_names = tuple(row.tool_name for row in tool_call_rows)

        report_rows = (
            session.query(ReportRecord)
            .filter(ReportRecord.check_id == token)
            .order_by(ReportRecord.id.asc())
            .all()
        )
        token_report_records = tuple(
            ReconciliationReportSnapshot(
                record_id=row.id,
                check_id=row.check_id,
                json_path=row.json_path,
                markdown_path=row.markdown_path,
                json_content=row.json_content,
                markdown_content=row.markdown_content,
            )
            for row in report_rows
        )

        has_token_company_check = bool(token_company_checks)
        if has_token_company_check:
            orphan_source_record_count = 0
            orphan_tool_call_record_count = 0
            orphan_report_record_count = 0
        else:
            orphan_source_record_count = source_record_count
            orphan_tool_call_record_count = len(tool_call_names)
            orphan_report_record_count = len(token_report_records)

        return ProcessingReconciliationDatabaseInspection(
            request=request_facts,
            database=ReconciliationDatabaseFacts(
                matching_company_check_source_request_ids=tuple(
                    matching_source_ids
                ),
                foreign_processing_token_request_ids=(
                    foreign_processing_token_request_ids
                ),
                source_record_count=source_record_count,
                tool_call_names=tool_call_names,
                report_record_count=len(token_report_records),
                orphan_source_record_count=orphan_source_record_count,
                orphan_tool_call_record_count=orphan_tool_call_record_count,
                orphan_report_record_count=orphan_report_record_count,
            ),
            token_company_checks=token_company_checks,
            token_report_records=token_report_records,
        )
    finally:
        session.close()
