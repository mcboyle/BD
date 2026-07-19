"""v3.66.765 -- injected http_get gets an SSRF pre-fetch guard at the edge.

SSRF-REM residual (defense-in-depth): deep_detect's default http_get is fully
guarded (pre-fetch + redirect hook + IP-pinned transport). But an INJECTED
http_get bypasses all of it. The production injection at runner_extractors builds
a RAW `_client.get(url)` closure and relies solely on a downstream resolver guard
(a comment claim). This adds a canonical pre-fetch _is_safe_public_host check at
the injection edge itself, so a page-derived URL to private/loopback/link-local/
CGNAT space is refused before the fetch -- not dependent on the downstream guard.

RED-first on pristine: bulk_downloader.runner_extractors has no
_ssrf_guarded_http_get (ImportError).
"""
import pytest


def _fake_inner(calls):
    def _inner(url):
        calls.append(url)
        return (200, {}, b"ok")
    return _inner


def test_guard_blocks_private_hosts_before_calling_inner():
    from bulk_downloader.runner_extractors import _ssrf_guarded_http_get
    from bulk_downloader.provider_resolve import SSRFBlocked
    calls = []
    guarded = _ssrf_guarded_http_get(_fake_inner(calls))
    for bad in ("http://127.0.0.1/x", "http://10.0.0.1/x",
                "http://169.254.169.254/latest", "http://100.64.0.1/x"):
        with pytest.raises(SSRFBlocked):
            guarded(bad)
    assert calls == [], "inner must NOT be called for a blocked host"


def test_guard_passes_public_hosts_through_to_inner():
    from bulk_downloader.runner_extractors import _ssrf_guarded_http_get
    calls = []
    guarded = _ssrf_guarded_http_get(_fake_inner(calls))
    status, headers, body = guarded("https://example.com/video.m3u8")
    assert status == 200 and body == b"ok"
    assert calls == ["https://example.com/video.m3u8"]
