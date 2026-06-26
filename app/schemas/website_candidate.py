"""Website candidate schema."""

from pydantic import BaseModel

from app.schemas.source import ConfidenceLevel


class WebsiteCandidate(BaseModel):
    """A candidate official website detected from relevant sources (not verified)."""

    candidate_url: str
    candidate_domain: str
    score: float
    confidence: ConfidenceLevel
    reasons: list[str]
    source_title: str
    is_verified: bool = False
