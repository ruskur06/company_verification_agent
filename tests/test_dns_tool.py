import httpx

from app.schemas.company_check import DomainDnsStatus
from app.tools.domain_dns_check import domain_dns_check, normalize_domain_input


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


def test_normalize_domain_input_strips_scheme_www_path():
    assert normalize_domain_input("https://munchy.at/de") == "munchy.at"
    assert normalize_domain_input("http://munchy.at/de") == "munchy.at"
    assert normalize_domain_input("www.munchy.at/de") == "munchy.at"
    assert normalize_domain_input("munchy.at/de") == "munchy.at"
    assert normalize_domain_input("munchy.at") == "munchy.at"


def test_dns_lookup_uses_normalized_domain_for_url_inputs(monkeypatch):
    import dns.resolver

    normalized_domain = "munchy.at"
    original_input = "https://munchy.at/de"

    def fake_resolve(domain, record_type, lifetime=5):
        # The DNS lookups must be executed against the normalized domain,
        # not against munchy.at/de.
        assert domain == normalized_domain

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

    def fake_head(url, *args, **kwargs):
        assert url == f"https://{normalized_domain}"
        return FakeHttpResponse()

    monkeypatch.setattr(dns.resolver, "resolve", fake_resolve)
    monkeypatch.setattr("app.tools.domain_dns_check.httpx.head", fake_head)

    result = domain_dns_check(original_input)

    assert result.status == DomainDnsStatus.checked
    assert result.domain == normalized_domain
    assert result.has_a_record is True
    assert result.has_mx_record is True
    assert result.has_txt_record is True
    assert result.https_available is True