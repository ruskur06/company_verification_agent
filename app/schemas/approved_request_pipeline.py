"""Schemas for the approved-request strict execution pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class PreparedApprovedRequestCheck(BaseModel):
    """Validated pipeline artifacts awaiting strict persistence."""

    model_config = ConfigDict(frozen=True)

    source_check_request_id: int
    processing_check_id: str
    processing_started_at: datetime
    result_payload: dict[str, Any]
    json_report_path: str
    markdown_report_path: str
    json_content: str
    markdown_content: str
