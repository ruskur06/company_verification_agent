"""Official website review report wording helpers."""

from app.schemas.official_website_review import OfficialWebsiteReview, OfficialWebsiteReviewDecision
from app.schemas.website_candidate import WebsiteCandidate


def official_website_review_status_message(review: OfficialWebsiteReview) -> str:
    if review.decision == OfficialWebsiteReviewDecision.approved:
        return "Official website verified by human reviewer"
    if review.decision == OfficialWebsiteReviewDecision.rejected:
        return "Candidate website rejected by human reviewer"
    if review.decision == OfficialWebsiteReviewDecision.uncertain:
        return "Candidate website remains uncertain after human review"
    return "Candidate official website pending human verification"


def apply_official_website_review_to_candidate(
    website_candidate: WebsiteCandidate | None,
    review: OfficialWebsiteReview,
) -> WebsiteCandidate | None:
    if website_candidate is None:
        return None

    is_verified = review.decision == OfficialWebsiteReviewDecision.approved
    return website_candidate.model_copy(update={"is_verified": is_verified})
