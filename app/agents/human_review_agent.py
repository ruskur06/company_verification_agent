"""Agent for human review workflow (placeholder)."""

from __future__ import annotations

from app.schemas.risk import HumanReviewStatus


class HumanReviewAgent:
    """Placeholder for future human review integration."""

    def run(self) -> HumanReviewStatus:
        """Return pending review status until a real approval UI exists."""
        return HumanReviewStatus.pending
