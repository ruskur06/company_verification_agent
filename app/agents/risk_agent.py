"""Agent that wraps the risk score tool."""

from __future__ import annotations

from app.schemas.risk import RiskScoreInput, RiskScoreResult
from app.tools.risk_score import calculate_risk_score


class RiskAgent:
    """Calculates preliminary risk score from collected check data."""

    def run(self, input_data: RiskScoreInput) -> RiskScoreResult:
        """Calculate risk score using the same input schema as the MVP service."""
        return calculate_risk_score(input_data)
