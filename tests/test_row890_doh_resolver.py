"""Row 890 -- Async DNS-over-HTTPS (DoH) fallback resolver with pinned EDNS Client Subnet.

WHY THIS GATE EXISTS (Row 890, O870f / O882):
ISP DNS resolvers return stale CDN IP addresses or inject ISP block pages on
third-party media domains. Secure DoH with EDNS Client Subnet (ECS) pins queries
to nearest CDN edge nodes, avoiding middlebox interference and poisoning.

WHAT THIS GATE ASSERTS:
1. DoH query resolution over HTTPS (Cloudflare / Google endpoints) with ECS routing.
2. In-memory cache returns cached DNS responses in <0.5ms.
3. Seamless fallback to system resolver on DoH timeout or unreachable endpoint.
4. Zero site logins touched (Fleet Rule 21).
"""
from __future__ import annotations

import asyncio
import socket

import pytest
import time
from unittest import mock

BD_GATE_SCOPE = "module"


def test_doh_module_exists():
    """Smoke: the public surface. (Behavioural RED for the fixer escapes is
    in the FIXER tests below -- see DONE.md for their result against the
    reviewer-judged module.)"""
    from bulk_downloader import doh_resolver

    assert hasattr(doh_resolver, "DoHResolver")
    assert hasattr(doh_resolver, "resolve")
    assert hasattr(doh_resolver, "resolve_async")


def test_doh_query_resolution_over_https():
    """Acceptance (1): DoH query resolution over HTTPS with ECS parameter."""
    from bulk_downloader import doh_resolver

    resolver = doh_resolver.DoHResolver(
        endpoints=["https://cloudflare-dns.com/dns-query", "https://dns.google/resolve"],
        edns_client_subnet="198.51.100.0/24",
    )

    # Mock DoH HTTPS response
    mock_payload = {
        "Status": 0,
        "Answer": [
            {"name": "cdn.example.com.", "type": 1, "TTL": 300, "data": "198.51.100.42"},
            {"name": "cdn.example.com.", "type": 1, "TTL": 300, "data": "198.51.100.43"},
        ],
    }

    with mock.patch.object(resolver, "_fetch_doh_json", return_value=mock_payload) as mock_fetch:
        result = resolver.resolve("cdn.example.com")

        assert result.domain == "cdn.example.com"
        assert "198.51.100.42" in result.addresses
        assert "198.51.100.43" in result.addresses
        assert result.source.startswith("doh_")
        assert result.ttl == 300

        # Verify ECS was passed to the query
        mock_fetch.assert_called_once()
        args, kwargs = mock_fetch.call_args
        assert "edns_client_subnet" in kwargs or "198.51.100.0/24" in str(args) or kwargs.get("ecs") == "198.51.100.0/24"


def test_in_memory_cache_returns_responses_under_half_millisecond():
    """Acceptance (2): In-memory cache returns responses in <0.5ms."""
    from bulk_downloader import doh_resolver

    resolver = doh_resolver.DoHResolver()
    resolver.clear_cache()

    # Pre-populate cache with an entry
    resolver.cache_put(
        domain="fast.example.org",
        record_type="A",
        addresses=["203.0.113.10"],
        ttl=3600,
    )

    # Measure lookup latency for 100 cache hits
    durations: list[float] = []
    for _ in range(100):
        t0 = time.perf_counter()
        entry = resolver.cache_get("fast.example.org", "A")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        durations.append(elapsed_ms)
        assert entry is not None
        assert entry.addresses == ["203.0.113.10"]

    avg_ms = sum(durations) / len(durations)
    max_ms = max(durations)
    assert avg_ms < 0.5, f"Cache hit average latency {avg_ms:.4f}ms exceeded 0.5ms requirement"
    assert max_ms < 2.0, f"Peak cache latency {max_ms:.4f}ms was unusually slow"


def test_cache_clear_and_expiration_behavior():
    """Seam catch: test cache_clear and is_expired logic."""
    from bulk_downloader import doh_resolver

    resolver = doh_resolver.DoHResolver()
    resolver.cache_put("temp.example.com", "A", ["1.2.3.4"], ttl=60)
    assert resolver.cache_get("temp.example.com", "A") is not None

    # Test clear_cache
    resolver.clear_cache()
    assert resolver.cache_get("temp.example.com", "A") is None

    # Test is_expired
    entry = doh_resolver.DNSCacheEntry("exp.example.com", "A", ["1.2.3.4"], ttl=1, source="test")
    entry.expires_at = time.monotonic() - 10.0
    assert entry.is_expired is True

    # When put into resolver, expired item returns None on cache_get
    resolver.cache_put("exp.example.com", "A", ["1.2.3.4"], ttl=1)
    cached = resolver._cache[("exp.example.com", "A")]
    cached.expires_at = time.monotonic() - 1.0
    assert resolver.cache_get("exp.example.com", "A") is None


def test_seamless_fallback_to_system_resolver_on_doh_timeout():
    """Acceptance (3): Seamless fallback to system resolver on DoH timeout."""
    from bulk_downloader import doh_resolver

    resolver = doh_resolver.DoHResolver(timeout_s=0.1)
    resolver.clear_cache()

    # Simulate DoH timeout on all HTTPS endpoints
    with (
        mock.patch.object(resolver, "_fetch_doh_json", side_effect=TimeoutError("DoH request timed out")),
        mock.patch("socket.getaddrinfo") as mock_gai,
    ):
        mock_gai.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ]
        result = resolver.resolve("fallback.example.net")

        assert result.success is True
        assert result.source == "system_fallback"
        assert "93.184.216.34" in result.addresses
        mock_gai.assert_called_once()


def test_async_resolution_executes_cleanly():
    """Verifies async resolution path (resolve_async)."""
    from bulk_downloader import doh_resolver

    resolver = doh_resolver.DoHResolver()
    resolver.clear_cache()

    mock_payload = {
        "Status": 0,
        "Answer": [
            {"name": "async.example.com.", "type": 1, "TTL": 120, "data": "192.0.2.1"},
        ],
    }

    with mock.patch.object(resolver, "_fetch_doh_json_async", return_value=mock_payload):
        loop = asyncio.new_event_loop()
        try:
            res = loop.run_until_complete(resolver.resolve_async("async.example.com"))
            assert res.success is True
            assert "192.0.2.1" in res.addresses
        finally:
            loop.close()


def test_module_level_convenience_functions_and_repr():
    """Seam catch: test module-level resolve / resolve_async and ResolutionResult.__repr__."""
    from bulk_downloader import doh_resolver

    # ResolutionResult repr
    res = doh_resolver.ResolutionResult("repr.example.com", ["1.1.1.1"], 300, "test", True)
    r_str = repr(res)
    assert "ResolutionResult" in r_str
    assert "repr.example.com" in r_str
    assert "1.1.1.1" in r_str

    # Module-level resolve
    with mock.patch.object(doh_resolver.get_default_resolver(), "resolve") as mock_res:
        mock_res.return_value = res
        val = doh_resolver.resolve("repr.example.com")
        assert val is res
        mock_res.assert_called_once_with("repr.example.com", record_type="A")

    # Module-level resolve_async
    with mock.patch.object(doh_resolver.get_default_resolver(), "resolve_async") as mock_res_async:
        mock_res_async.return_value = res
        loop = asyncio.new_event_loop()
        try:
            val_async = loop.run_until_complete(doh_resolver.resolve_async("repr.example.com"))
            assert val_async is res
            mock_res_async.assert_called_once_with("repr.example.com", record_type="A")
        finally:
            loop.close()


def test_negative_control_unresolvable_domain():
    """Negative control: domain that fails DoH and system resolution returns empty / failed result."""
    from bulk_downloader import doh_resolver

    resolver = doh_resolver.DoHResolver()
    resolver.clear_cache()

    with (
        mock.patch.object(resolver, "_fetch_doh_json", side_effect=TimeoutError("Timeout")),
        mock.patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")),
    ):
        res = resolver.resolve("definitely-does-not-exist-123456789.invalid")
        assert res.success is False
        assert res.addresses == []
        assert res.source == "failed"


# ---- fixer (O928) controls for the correctness REFUTE E1/E2/E3 ----------

def test_aaaa_system_fallback_uses_af_inet6(monkeypatch):
    """E1: an AAAA query that falls back to the system resolver must ask for
    AF_INET6 and return the IPv6 answer, never an A-record address."""
    from bulk_downloader import doh_resolver

    resolver = doh_resolver.DoHResolver(timeout_s=0.1)
    resolver.clear_cache()
    seen: dict = {}

    def _gai(host, port, family=0, *a, **k):
        seen["family"] = family
        if family == socket.AF_INET6:
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 0, 0, 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 0))]

    with (
        mock.patch.object(resolver, "_fetch_doh_json", side_effect=TimeoutError("DoH timed out")),
        mock.patch("socket.getaddrinfo", side_effect=_gai),
    ):
        result = resolver.resolve("v6.example.net", record_type="AAAA")

    assert seen["family"] == socket.AF_INET6
    assert result.addresses == ["2001:db8::1"]
    assert result.source == "system_fallback"
    cached = resolver.cache_get("v6.example.net", "AAAA")
    assert cached is not None and cached.addresses == ["2001:db8::1"]


def test_zero_ttl_answer_is_never_served_from_cache():
    """E2: TTL=0 means use-once; a second query must hit HTTPS again."""
    from bulk_downloader import doh_resolver

    resolver = doh_resolver.DoHResolver(timeout_s=0.1)
    resolver.clear_cache()
    payload = {"Status": 0, "Answer": [
        {"name": "ttl0.example.com.", "type": 1, "TTL": 0, "data": "198.51.100.7"},
    ]}
    with mock.patch.object(resolver, "_fetch_doh_json", return_value=payload) as fetch:
        first = resolver.resolve("ttl0.example.com")
        second = resolver.resolve("ttl0.example.com")
    assert first.addresses == second.addresses == ["198.51.100.7"]
    assert first.source.startswith("doh_") and second.source.startswith("doh_")
    assert fetch.call_count == 2
    assert resolver.cache_get("ttl0.example.com") is None
    resolver.cache_put("ttl0.example.com", "A", ["198.51.100.7"], ttl=-5)
    assert resolver.cache_get("ttl0.example.com") is None


@pytest.mark.parametrize("bad_payload", [
    {"Status": 0, "Answer": None},
    {"Status": 0},
    {"Status": 0, "Answer": [None]},
    {"Status": 0, "Answer": [{"type": 1, "TTL": None, "data": None}]},
    None,
    [],
])
def test_malformed_doh_answer_continues_to_next_endpoint_and_system(bad_payload):
    """E3: Answer:null (and friends) must not raise; the resolver continues
    to the next endpoint and finally the system resolver."""
    from bulk_downloader import doh_resolver

    resolver = doh_resolver.DoHResolver(timeout_s=0.1)
    resolver.clear_cache()
    good = {"Status": 0, "Answer": [
        {"name": "shape.example.com.", "type": 1, "TTL": 60, "data": "198.51.100.9"},
    ]}
    with mock.patch.object(resolver, "_fetch_doh_json", side_effect=[bad_payload, good]) as fetch:
        result = resolver.resolve("shape.example.com")
    assert fetch.call_count == 2
    assert result.success is True and result.addresses == ["198.51.100.9"]

    resolver.clear_cache()
    with (
        mock.patch.object(resolver, "_fetch_doh_json", return_value=bad_payload),
        mock.patch("socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ]),
    ):
        result = resolver.resolve("shape.example.com")
    assert result.source == "system_fallback" and result.addresses == ["93.184.216.34"]


# ── FIXER (row890 REFUTE E1/E2/E4; E3 FOUND NONE) ────────────────────────


def test_record_type_is_case_insensitive_when_extracting_answers():
    """E1: record_type="a" must select type-1 answers, not silently AAAA."""
    from bulk_downloader.doh_resolver import DoHResolver

    payload = {"Answer": [{"type": 1, "data": "93.184.216.34", "TTL": 60},
                          {"type": 28, "data": "2606:2800::1", "TTL": 60}]}
    resolver = DoHResolver()
    for rt in ("A", "a"):
        assert resolver._extract_addresses(payload, rt)[0] == ["93.184.216.34"], rt
    for rt in ("AAAA", "aaaa"):
        assert resolver._extract_addresses(payload, rt)[0] == ["2606:2800::1"], rt
    with mock.patch.object(resolver, "_fetch_doh_json", return_value=payload):
        assert resolver.resolve("example.test", "a").addresses == ["93.184.216.34"]


def test_doh_fetch_goes_through_the_pinned_opener_under_the_doh_policy(monkeypatch):
    """E2 (hermetic: no socket): _open builds a PinnedUrlOpener with the
    DoH public-only policy and opens the request through it -- never a bare
    urlopen, never the hooks (LAN-admitting) opener."""
    from bulk_downloader import doh_resolver, urllib_ssrf

    src = open(doh_resolver.__file__, encoding="utf-8").read()
    assert "urllib.request.urlopen(" not in src
    assert "_hook_urlopen" not in src
    seen = []

    class Recorder:
        def __init__(self, allowed):
            seen.append(("policy", allowed))

        def open(self, req, timeout=None):
            seen.append(("open", req.full_url, timeout))
            raise OSError("fixture: no network")

    monkeypatch.setattr(urllib_ssrf, "PinnedUrlOpener", Recorder)
    resolver = doh_resolver.DoHResolver(endpoints=["https://doh.fixture.invalid/dns-query"], timeout_s=3.0)
    with pytest.raises(OSError):
        resolver._fetch_doh_json("https://doh.fixture.invalid/dns-query", "example.test", "A", None, 3.0)
    assert seen[0] == ("policy", doh_resolver._doh_address_allowed)
    assert seen[1][0] == "open" and seen[1][1].startswith("https://doh.fixture.invalid/dns-query?") and seen[1][2] == 3.0


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.0.70.5", "192.168.1.1", "172.16.0.9",
                                  "169.254.169.254", "::1", "fd00::1", "0.0.0.0"])
def test_doh_policy_refuses_every_non_public_address(addr):
    """Round-2 item 2: the DoH policy is public-only -- a redirect (or a DNS
    answer for the endpoint host) landing on loopback/LAN/link-local is
    refused, unlike the hooks policy which admits LAN for local webhooks."""
    from bulk_downloader import doh_resolver
    ok, why = doh_resolver._doh_address_allowed(addr, "doh.fixture.invalid")
    assert ok is False and why


def test_doh_policy_admits_public_addresses_and_refuses_garbage():
    from bulk_downloader import doh_resolver
    assert doh_resolver._doh_address_allowed("1.1.1.1", "cloudflare-dns.com")[0] is True
    assert doh_resolver._doh_address_allowed("2606:4700:4700::1111", "cloudflare-dns.com")[0] is True
    assert doh_resolver._doh_address_allowed("not-an-ip", "x")[0] is False


def test_non_address_record_types_keep_their_own_answers(monkeypatch):
    """Round-2 item 1: CNAME/MX/TXT queries used to collapse to type 28 and
    discard their answers; an unknown type matches nothing."""
    from bulk_downloader import doh_resolver
    r = doh_resolver.DoHResolver(endpoints=["https://doh.fixture.invalid/dns-query"])
    payload = {"Answer": [{"type": 5, "data": "alias.example.test.", "TTL": 60},
                          {"type": 28, "data": "2001:db8::1", "TTL": 60},
                          {"type": 16, "data": "\"v=spf1 -all\"", "TTL": 30},
                          {"type": 15, "data": "10 mx.example.test.", "TTL": 30}]}
    assert r._extract_addresses(payload, "cname")[0] == ["alias.example.test."]
    assert r._extract_addresses(payload, "TXT")[0] == ["\"v=spf1 -all\""]
    assert r._extract_addresses(payload, "MX")[0] == ["10 mx.example.test."]
    assert r._extract_addresses(payload, "AAAA")[0] == ["2001:db8::1"]
    assert r._extract_addresses(payload, "BOGUS")[0] == []
    # the system resolver cannot serve non-address types: no getaddrinfo
    monkeypatch.setattr(doh_resolver.socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("getaddrinfo called")))
    assert r._system_resolve("example.test", "CNAME") == []
