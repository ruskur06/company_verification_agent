import httpx

from app.schemas.company_check import DomainDnsStatus
from app.tools.domain_dns_check import domain_dns_check


class FakeHttpResponse:
    status_code = 200


class FakeMxRecord:
    exchange = "mail.example.com."


def test_missing_domain_returns_not_provided():
    result = domain_dns_check(None)

    assert result.status == DomainDnsStatus.not_provided
    assert result.domain is None


def test_invalid_domain_format_does_not_crash(monkeypatch):
    import dns.resolver

    def fake_resolve(*args, **kwargs):
        raise RuntimeError("DNS failure")

    def fake_head(*args, **kwargs):
        raise httpx.ConnectError("Connection failed")

    monkeypatch.setattr(dns.resolver, "resolve", fake_resolve)
    monkeypatch.setattr("app.tools.domain_dns_check.httpx.head", fake_head)

    result = domain_dns_check("not a valid domain")

    assert result.status == DomainDnsStatus.failed
    assert result.warnings


def test_valid_domain_with_mocked_dns(monkeypatch):
    import dns.resolver

    def fake_resolve(domain, record_type, lifetime=5):
        if record_type == "A":
            return [object()]
        if record_type == "MX":
            return [FakeMxRecord()]
        if record_type == "TXT":
            return ['"v=spf1 include:example.com ~all"']
        if record_type == "NS":
            return ["ns1.example.com."]
        if record_type == "AAAA":
            return []
        return []

    def fake_head(*args, **kwargs):
        return FakeHttpResponse()

    monkeypatch.setattr(dns.resolver, "resolve", fake_resolve)
    monkeypatch.setattr("app.tools.domain_dns_check.httpx.head", fake_head)

    result = domain_dns_check("example.com")

    assert result.status == DomainDnsStatus.checked
    assert result.domain == "example.com"
    assert result.has_a_record is True
    assert result.has_mx_record is True
    assert result.has_txt_record is True
    assert result.https_available is True


def test_dns_exception_is_handled(monkeypatch):
    import dns.resolver

    def fake_resolve(*args, **kwargs):
        raise RuntimeError("DNS timeout")

    def fake_head(*args, **kwargs):
        raise httpx.TimeoutException("HTTP timeout")

    monkeypatch.setattr(dns.resolver, "resolve", fake_resolve)
    monkeypatch.setattr("app.tools.domain_dns_check.httpx.head", fake_head)

    result = domain_dns_check("example.com")

    assert result.status == DomainDnsStatus.checked
    assert result.warnings