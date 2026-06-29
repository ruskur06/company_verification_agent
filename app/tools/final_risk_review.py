"""Final risk human review report wording helpers."""

from app.schemas.risk import HumanReviewStatus


def final_risk_review_status_message(status: HumanReviewStatus) -> str:
    if status == HumanReviewStatus.approved:
        return "Final risk approved by human reviewer."
    if status == HumanReviewStatus.edited:
        return "Final risk edited by human reviewer."
    if status == HumanReviewStatus.rejected:
        return "Risk assessment rejected by human reviewer."
    return "Final risk assessment requires human review."
