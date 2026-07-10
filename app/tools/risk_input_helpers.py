"""Helpers for building domain-related risk score fields."""

from __future__ import annotations

from app.schemas.company_check import DomainDnsInfo
from app.schemas.website_candidate import WebsiteCandidate
from app.schemas.website_ownership_signals import OwnershipSignalsStatus, WebsiteOwnershipSignals


def candidate_domain_dns_succeeds(candidate_domain_dns: DomainDnsInfo | None) -> bool:
    return (
        candidate_domain_dns is not None
        and candidate_domain_dns.has_a_record
        and candidate_domain_dns.https_available
    )


def build_domain_risk_fields(
    *,
    user_domain: str | None,
    domain_dns: DomainDnsInfo,
    candidate_domain_dns: DomainDnsInfo | None,
    website_candidate: WebsiteCandidate | None,
) -> dict[str, bool]:
    user_domain_provided = bool(user_domain)
    has_website = bool(
        website_candidate is not None
        and website_candidate.is_verified
    )
    has_website_candidate = bool(
        website_candidate is not None
        and not website_candidate.is_verified
    )
    candidate_succeeds = candidate_domain_dns_succeeds(candidate_domain_dns)

    return {
        "has_website": has_website,
        "has_website_candidate": has_website_candidate,
        "user_domain_provided": user_domain_provided,
        "candidate_domain_dns_succeeds": candidate_succeeds,
        "candidate_has_mx_record": bool(
            candidate_domain_dns is not None and candidate_domain_dns.has_mx_record
        ),
        "domain_resolves": domain_dns.has_a_record,
        "has_mx_record": domain_dns.has_mx_record,
        "https_available": domain_dns.https_available,
    }


def build_ownership_risk_fields(
    website_ownership_signals: WebsiteOwnershipSignals,
) -> dict[str, bool | float]:
    has_ownership_signals = (
        website_ownership_signals.status == OwnershipSignalsStatus.signals_found
    )
    return {
        "has_ownership_signals": has_ownership_signals,
        "ownership_signals_score": website_ownership_signals.score,
    }
