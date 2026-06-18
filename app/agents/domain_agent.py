"""Agent that wraps the domain/DNS check tool."""

from __future__ import annotations

from typing import Optional

from app.schemas.company_check import DomainDnsInfo
from app.tools.domain_dns_check import domain_dns_check


class DomainAgent:
    """Checks domain DNS records and HTTPS availability."""

    def run(self, domain: Optional[str]) -> DomainDnsInfo:
        """Run domain and DNS checks for the given domain."""
        return domain_dns_check(domain)
