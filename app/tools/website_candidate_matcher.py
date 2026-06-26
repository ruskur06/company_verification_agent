"""Deterministic website candidate detection from relevant sources."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from app.schemas.source import ConfidenceLevel, RelevanceLevel, SourceResult
from app.schemas.website_candidate import WebsiteCandidate
from app.tools.entity_matcher import company_tokens, normalize_text

CANDIDATE_THRESHOLD = 0.5

EXCLUDED_PLATFORM_DOMAINS = (
    "linkedin.com",
    "facebook.com",
    "wikipedia.org",
    "bloomberg.com",
    "northdata.com",
    "crunchbase.com",
)

OFFICIAL_WEBSITE_HINTS = (
    "official",
    "company",
    "homepage",
    "home page",
    "website",
    "site",
)


@dataclass(frozen=True)
class CandidateScoreResult:
    candidate_url: str
    candidate_domain: str
    score: float
    confidence: ConfidenceLevel
    reasons: list[str]


def extract_domain(url: str) -> str | None:
    """Extract normalized registrable domain from a URL."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return None

    host = parsed.netloc.lower()
    if not host:
        return None

    if host.startswith("www."):
        host = host[4:]

    return host or None


def is_excluded_platform_domain(domain: str) -> bool:
    for excluded in EXCLUDED_PLATFORM_DOMAINS:
        if domain == excluded or domain.endswith(f".{excluded}"):
            return True
    return False


def _confidence_from_score(score: float) -> ConfidenceLevel:
    if score >= 0.75:
        return ConfidenceLevel.high
    if score >= 0.55:
        return ConfidenceLevel.medium
    return ConfidenceLevel.low


def _normalized_candidate_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "https"
    path = parsed.path or ""
    host = parsed.netloc.lower()
    if path in ("", "/"):
        return f"{scheme}://{host}"
    if path.endswith("/"):
        path = path.rstrip("/")
    return f"{scheme}://{host}{path}"


def score_website_candidate(company_name: str, source: SourceResult) -> CandidateScoreResult | None:
    """Score one source as a website candidate. Returns None when not eligible."""
    if source.is_mock or source.relevance != RelevanceLevel.relevant:
        return None

    domain = extract_domain(source.url)
    if domain is None or is_excluded_platform_domain(domain):
        return None

    tokens = company_tokens(company_name)
    if not tokens:
        return None

    parsed = urlparse(source.url.strip())
    score = 0.0
    reasons: list[str] = []
    domain_compact = domain.replace("-", "").replace(".", "")

    if any(token in domain_compact for token in tokens):
        score += 0.4
        reasons.append("domain_contains_company_name")

    if parsed.scheme == "https":
        score += 0.2
        reasons.append("https_scheme")

    path = parsed.path.strip("/")
    if not path or path.count("/") == 0:
        score += 0.2
        reasons.append("short_url_path")

    text = normalize_text(f"{source.title} {source.snippet}")
    if any(hint in text for hint in OFFICIAL_WEBSITE_HINTS):
        score += 0.15
        reasons.append("official_website_hint_in_text")

    if score < CANDIDATE_THRESHOLD:
        return None

    return CandidateScoreResult(
        candidate_url=_normalized_candidate_url(source.url),
        candidate_domain=domain,
        score=round(score, 3),
        confidence=_confidence_from_score(score),
        reasons=reasons,
    )


def find_website_candidate(
    company_name: str,
    sources: list[SourceResult],
) -> WebsiteCandidate | None:
    """Return the best website candidate from relevant non-mock sources."""
    best: WebsiteCandidate | None = None
    best_score = 0.0

    for source in sources:
        scored = score_website_candidate(company_name, source)
        if scored is None or scored.score <= best_score:
            continue

        best_score = scored.score
        best = WebsiteCandidate(
            candidate_url=scored.candidate_url,
            candidate_domain=scored.candidate_domain,
            score=scored.score,
            confidence=scored.confidence,
            reasons=scored.reasons,
            source_title=source.title,
            is_verified=False,
        )

    return best
