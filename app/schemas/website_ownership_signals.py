"""Website ownership signals schema."""

from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.source import ConfidenceLevel


class OwnershipSignalsStatus(str, Enum):
    signals_found = "signals_found"
    insufficient_signals = "insufficient_signals"
    not_checked = "not_checked"


class OwnershipSignal(BaseModel):
    name: str
    found: bool
    weight: float
    detail: str


class WebsiteOwnershipSignals(BaseModel):
    status: OwnershipSignalsStatus = OwnershipSignalsStatus.not_checked
    score: float = 0.0
    confidence: ConfidenceLevel = ConfidenceLevel.low
    signals: list[OwnershipSignal] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    is_officially_confirmed: bool = False
