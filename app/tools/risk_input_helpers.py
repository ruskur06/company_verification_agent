"""Helpers for building domain-related risk score fields."""

from __future__ import annotations

from app.schemas.company_check import DomainDnsInfo
from app.schemas.website_candidate import WebsiteCandidate


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
    has_website = user_domain_provided and domain_dns.https_available
    candidate_succeeds = candidate_domain_dns_succeeds(candidate_domain_dns)

    return {
        "has_website": has_website,
        "has_website_candidate": website_candidate is not None and not has_website,
        "user_domain_provided": user_domain_provided,
        "candidate_domain_dns_succeeds": candidate_succeeds,
        "candidate_has_mx_record": bool(
            candidate_domain_dns is not None and candidate_domain_dns.has_mx_record
        ),
        "domain_resolves": domain_dns.has_a_record,
        "has_mx_record": domain_dns.has_mx_record,
        "https_available": domain_dns.https_available,
    }
