"""RED-first regression test for the /api/scrape_listing httpx fix (v3.66.446).

PRE-EXISTING BUG (surfaced @442, present in 437-445): api_scrape_listing calls
httpx.Client(...) but neither app.py nor (post-extraction) app_scrape_listing.py
imported httpx -> a public-URL POST that passes the _is_url_public SSRF gate
raised NameError -> 500. No test covered the path, so it was invisible to the
suite. This file is the RED (fails with 500 on the unfixed tree, passes once
`import httpx` is added to app_scrape_listing.py).

Runner notes: zero-arg fns; module globals restored in try/finally (monkeypatch
is unreliable in the custom runner). httpx.Client is patched on the REAL httpx
module -- on the unfixed tree the handler has no `httpx` name at all, so it still
NameErrors regardless of the patch; only the `import httpx` fix makes the patched
Client reachable.
"""
import httpx

import bulk_downloader.app as a
import bulk_downloader.app_scrape_listing as listing
from bulk_downloader.provider_resolve_impl._common import SSRFBlocked


class _FakeResp:
    status_code = 200
    text = (
        '<a href="https://pub.example.com/video/123.mp4">vid</a>'
        '<a href="https://pub.example.com/category/">listing</a>'
        '<a href="#frag">frag</a>'
        '<a href="javascript:void(0)">js</a>'
    )

    def raise_for_status(self):
        return None


class _FakeClient:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None):
        return _FakeResp()


def test_scrape_listing_returns_links_when_fetch_succeeds():
    """The happy path: SSRF gate open + a real (mocked) fetch -> 200 with the
    video-looking links extracted. RED on the unfixed tree (NameError -> 500)."""
    orig_pub = a._is_url_public
    orig_client = httpx.Client
    a._is_url_public = lambda url: True
    httpx.Client = _FakeClient
    try:
        c = a.app.test_client()
        r = c.post("/api/scrape_listing", json={"url": "https://pub.example.com/list"})
        assert r.status_code == 200, f"expected 200, got {r.status_code}"
        body = r.get_json()
        assert body.get("ok") is True, body
        assert "https://pub.example.com/video/123.mp4" in body.get("found", []), body
        # listing/fragment/js anchors must be filtered out
        assert all("category" not in u for u in body.get("found", [])), body
        assert body.get("count") == len(body.get("found", []))
    finally:
        a._is_url_public = orig_pub
        httpx.Client = orig_client


def test_scrape_listing_rejects_private_host():
    """SSRF guard: a URL whose host is NOT public is rejected with 400 before any
    fetch. Passes on both trees (never reaches httpx) -- a permanent guard that
    the fix must not regress."""
    orig_pub = a._is_url_public
    a._is_url_public = lambda url: False
    try:
        c = a.app.test_client()
        r = c.post("/api/scrape_listing", json={"url": "http://169.254.169.254/latest/meta-data/"})
        assert r.status_code == 400, f"expected 400, got {r.status_code}"
        assert r.get_json().get("ok") is False
    finally:
        a._is_url_public = orig_pub


def test_scrape_listing_returns_redacted_502_for_ssrf_refusal():
    """A transport refusal is actionable without reflecting its sensitive text."""
    sensitive = "https://token.example/?token=secret 127.0.0.1"

    class _BlockedClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            raise SSRFBlocked(sensitive)

    orig_pub, orig_client = listing._is_url_public, httpx.Client
    listing._is_url_public, httpx.Client = lambda url: True, _BlockedClient
    try:
        response = a.app.test_client().post(
            "/api/scrape_listing", json={"url": "https://public.example/list"})
        assert response.status_code == 502
        assert response.get_json() == {"ok": False, "error": "fetch blocked by SSRF policy"}
        assert sensitive not in response.get_data(as_text=True)
    finally:
        listing._is_url_public, httpx.Client = orig_pub, orig_client


def test_scrape_listing_keeps_httpx_failure_behavior_distinct():
    class _HTTPErrorClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            raise httpx.ConnectError("transport detail")

    orig_pub, orig_client = listing._is_url_public, httpx.Client
    listing._is_url_public, httpx.Client = lambda url: True, _HTTPErrorClient
    try:
        response = a.app.test_client().post(
            "/api/scrape_listing", json={"url": "https://public.example/list"})
        assert response.status_code == 502
        assert response.get_json()["error"] == "fetch failed: ConnectError: transport detail"
    finally:
        listing._is_url_public, httpx.Client = orig_pub, orig_client


def test_scrape_listing_does_not_relabel_generic_exceptions_as_ssrf():
    class _GenericErrorClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            raise RuntimeError("not an SSRF refusal")

    orig_pub, orig_client = listing._is_url_public, httpx.Client
    orig_propagate = a.app.config.get("PROPAGATE_EXCEPTIONS")
    listing._is_url_public, httpx.Client = lambda url: True, _GenericErrorClient
    a.app.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        response = a.app.test_client().post(
            "/api/scrape_listing", json={"url": "https://public.example/list"})
        assert response.status_code == 500
    finally:
        listing._is_url_public, httpx.Client = orig_pub, orig_client
        a.app.config["PROPAGATE_EXCEPTIONS"] = orig_propagate


def test_scrape_listing_rejects_non_http_url():
    """A missing/invalid scheme is a 400 (input validation, pre-SSRF)."""
    c = a.app.test_client()
    r = c.post("/api/scrape_listing", json={"url": "ftp://example.com/x"})
    assert r.status_code == 400, f"expected 400, got {r.status_code}"
