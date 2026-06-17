"""SQLAlchemy ORM models."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, Integer, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class CompanyCheck(Base):
    __tablename__ = "company_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    final_json_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    markdown_report_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    preliminary_risk_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    preliminary_risk_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    final_risk_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    final_risk_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    human_review_status: Mapped[str] = mapped_column(String(50), default="pending")
    human_review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    sources: Mapped[list["Source"]] = relationship("Source", back_populates="check", cascade="all, delete-orphan")
    tool_calls: Mapped[list["ToolCall"]] = relationship("ToolCall", back_populates="check", cascade="all, delete-orphan")
    reports: Mapped[list["Report"]] = relationship("Report", back_populates="check", cascade="all, delete-orphan")


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    check_id: Mapped[int] = mapped_column(Integer, ForeignKey("company_checks.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), default="search_result")
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[str] = mapped_column(String(20), default="low")

    check: Mapped["CompanyCheck"] = relationship("CompanyCheck", back_populates="sources")


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    check_id: Mapped[int] = mapped_column(Integer, ForeignKey("company_checks.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    check: Mapped["CompanyCheck"] = relationship("CompanyCheck", back_populates="tool_calls")


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    check_id: Mapped[int] = mapped_column(Integer, ForeignKey("company_checks.id"), nullable=False)
    report_type: Mapped[str] = mapped_column(String(50), nullable=False)  # markdown | json
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    check: Mapped["CompanyCheck"] = relationship("CompanyCheck", back_populates="reports")