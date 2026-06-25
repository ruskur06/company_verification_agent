"""SQLAlchemy ORM models for company check persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class CompanyCheckRecord(Base):
    __tablename__ = "company_check_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    check_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    risk_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    risk_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    human_review_status: Mapped[str] = mapped_column(String(50), default="pending")
    json_report_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    markdown_report_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    registry_check_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    domain_check_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class ToolCallRecord(Base):
    __tablename__ = "tool_call_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    check_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="completed")
    input_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class SourceRecord(Base):
    __tablename__ = "source_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    check_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    confidence: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    relevance: Mapped[str] = mapped_column(String(20), default="uncertain", nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class ReportRecord(Base):
    __tablename__ = "report_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    check_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    json_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    markdown_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    json_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    markdown_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class HumanReviewRecord(Base):
    __tablename__ = "human_review_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    check_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    decision: Mapped[str] = mapped_column(String(50), nullable=False)
    reviewer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewer_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    final_verification_confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    final_verification_risk: Mapped[str] = mapped_column(String(20), nullable=False)
    final_business_risk: Mapped[str] = mapped_column(String(20), nullable=False)
    overrides_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
