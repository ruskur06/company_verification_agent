"""Domain and DNS check tool.

Performs DNS lookups, validates domain format, and checks HTTP/HTTPS availability.
All errors are caught and returned as structured warnings.
"""

from __future__ import annotations

import re
import socket
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.schemas.company_check import DomainDnsInfo, DomainDnsStatus

logger = get_logger(__name__)

# Simple domain format regex (not exhaustive — just catches obvious invalids)
_DOMAIN_RE = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
)


def domain_dns_check(domain: Optional[str]) -> DomainDnsInfo:
    """
    Check DNS records and HTTP availability for a domain.

    Returns a DomainDnsInfo with status 'not_provided' when domain is None/empty.
    Never raises — all errors become warnings in the returned structure.
    """
    if not domain:
        return DomainDnsInfo(status=DomainDnsStatus.not_provided)

    domain = domain.strip().lower()
    # Strip scheme if accidentally included
    domain = re.sub(r"^https?://", "", domain).rstrip("/")

    warnings: list[str] = []

    # 1. Format validation
    is_valid_format = bool(_DOMAIN_RE.match(domain))
    if not is_valid_format:
        warnings.append(f"Domain '{domain}' does not appear to be a valid format.")

    # 2. DNS lookups
    has_a_record = False
    has_mx_record = False
    has_txt_record = False
    nameservers: list[str] = []
    mx_records: list[str] = []
    txt_records: list[str] = []

    try:
        import dns.resolver

        # A records
        try:
            answers = dns.resolver.resolve(domain, "A", lifetime=5)
            has_a_record = bool(answers)
        except Exception:
            warnings.append("No A record found.")

        # AAAA records (IPv6) — informational only
        try:
            dns.resolver.resolve(domain, "AAAA", lifetime=5)
        except Exception:
            pass

        # MX records
        try:
            answers = dns.resolver.resolve(domain, "MX", lifetime=5)
            mx_records = [str(r.exchange).rstrip(".") for r in answers]
            has_mx_record = bool(mx_records)
        except Exception:
            warnings.append("No MX record found — email infrastructure may be absent.")

        # NS records
        try:
            answers = dns.resolver.resolve(domain, "NS", lifetime=5)
            nameservers = [str(r).rstrip(".") for r in answers]
        except Exception:
            warnings.append("Could not retrieve NS records.")

        # TXT records
        try:
            answers = dns.resolver.resolve(domain, "TXT", lifetime=5)
            txt_records = [str(r) for r in answers]
            has_txt_record = bool(txt_records)
        except Exception:
            pass

    except ImportError:
        logger.warning("dnspython not installed — falling back to socket-based check.")
        try:
            socket.getaddrinfo(domain, None)
            has_a_record = True
        except socket.gaierror:
            warnings.append("Domain does not resolve (socket check).")

    except Exception as e:
        warnings.append(f"DNS lookup error: {e}")

    # 3. HTTP/HTTPS availability
    http_status: Optional[int] = None
    https_available = False

    for scheme in ("https", "http"):
        try:
            resp = httpx.head(
                f"{scheme}://{domain}",
                timeout=8,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 CompanyVerificationBot/1.0"},
            )
            http_status = resp.status_code
            if scheme == "https":
                https_available = True
            break  # stop at first success
        except httpx.ConnectError:
            warnings.append(f"{scheme.upper()} connection refused or unreachable.")
        except httpx.TimeoutException:
            warnings.append(f"{scheme.upper()} request timed out.")
        except Exception as e:
            warnings.append(f"{scheme.upper()} check error: {e}")

    status = DomainDnsStatus.checked if is_valid_format else DomainDnsStatus.failed

    return DomainDnsInfo(
        status=status,
        domain=domain,
        has_a_record=has_a_record,
        has_mx_record=has_mx_record,
        has_txt_record=has_txt_record,
        https_available=https_available,
        warnings=warnings,
    )