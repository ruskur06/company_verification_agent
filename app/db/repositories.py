"""Database repository functions — thin data-access layer."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import CompanyCheck, Source, ToolCall, Report
from app.schemas.source import SourceResult
from app.schemas.risk import HumanReviewInput


# ── Company checks ──────────────────────────────────────────────────────────

def create_company_check(
    db: Session,
    company_name: str,
    country: str,
    domain: Optional[str] = None,
) -> CompanyCheck:
    check = CompanyCheck(
        company_name=company_name,
        country=country,
        domain=domain,
        status="pending",
    )
    db.add(check)
    db.commit()
    db.refresh(check)
    return check


def get_company_check(db: Session, check_id: int) -> Optional[CompanyCheck]:
    return db.query(CompanyCheck).filter(CompanyCheck.id == check_id).first()


def list_company_checks(db: Session, limit: int = 50) -> list[CompanyCheck]:
    return db.query(CompanyCheck).order_by(CompanyCheck.created_at.desc()).limit(limit).all()


def update_check_status(db: Session, check_id: int, status: str) -> None:
    db.query(CompanyCheck).filter(CompanyCheck.id == check_id).update({"status": status})
    db.commit()


def update_check_results(
    db: Session,
    check_id: int,
    preliminary_risk_score: int,
    preliminary_risk_level: str,
    final_json_path: Optional[str] = None,
    markdown_report_path: Optional[str] = None,
) -> None:
    db.query(CompanyCheck).filter(CompanyCheck.id == check_id).update({
        "status": "completed",
        "preliminary_risk_score": preliminary_risk_score,
        "preliminary_risk_level": preliminary_risk_level,
        "final_json_path": final_json_path,
        "markdown_report_path": markdown_report_path,
        "human_review_status": "pending",
    })
    db.commit()


def update_human_review(
    db: Session,
    check_id: int,
    review: HumanReviewInput,
) -> Optional[CompanyCheck]:
    check = get_company_check(db, check_id)
    if not check:
        return None
    check.human_review_status = review.decision.value
    check.human_review_notes = review.notes
    if review.final_score is not None:
        check.final_risk_score = review.final_score
    if review.final_level is not None:
        check.final_risk_level = review.final_level.value
    db.commit()
    db.refresh(check)
    return check


# ── Sources ──────────────────────────────────────────────────────────────────

def save_source(db: Session, check_id: int, source: SourceResult) -> Source:
    row = Source(
        check_id=check_id,
        title=source.title,
        url=source.url,
        snippet=source.snippet,
        source_type=source.source_type.value,
        retrieved_at=source.retrieved_at,
        is_mock=source.is_mock,
        confidence=source.confidence.value,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ── Tool calls ───────────────────────────────────────────────────────────────

def save_tool_call(
    db: Session,
    check_id: int,
    tool_name: str,
    input_data: dict,
    output_data: dict | None,
    success: bool,
    error_message: Optional[str] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
) -> ToolCall:
    row = ToolCall(
        check_id=check_id,
        tool_name=tool_name,
        input_json=json.dumps(input_data, default=str),
        output_json=json.dumps(output_data, default=str) if output_data else None,
        success=success,
        error_message=error_message,
        started_at=started_at or datetime.utcnow(),
        finished_at=finished_at or datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ── Reports ──────────────────────────────────────────────────────────────────

def save_report(
    db: Session,
    check_id: int,
    report_type: str,
    content: str,
    file_path: Optional[str] = None,
) -> Report:
    row = Report(
        check_id=check_id,
        report_type=report_type,
        content=content,
        file_path=file_path,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row