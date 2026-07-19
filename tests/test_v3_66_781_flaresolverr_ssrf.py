"""RED-first guard for v3.66.781: /api/flaresolverr/test SSRF host-guard.

api_flaresolverr_test takes a request-supplied `endpoint` and passes it straight
to `_flare_client.solve_cloudflare(url, endpoint=endpoint, ...)`, which makes an
HTTP request to that endpoint -- with no host validation. A CSRF-authenticated
user could drive the server to hit internal/loopback/link-local addresses (e.g.
cloud metadata at 169.254.169.254). This is the lone MEDIUM SSRF residual recorded
at 780 (SSRF_INTERPROCEDURAL_TRIAGE): the only untrusted-reachable +
destination-controlling flow without a host pre-check.

The fix routes a USER-SUPPLIED endpoint through the canonical
bulk_downloader.provider_resolve_impl._common._is_safe_public_host (-> _classify_ip,
which rejects loopback/private/link-local/CGNAT/reserved) and refuses a non-public
host BEFORE solve_cloudflare, matching this route's own error convention
(200 + {"ok": false, "error": ...}, as used by its "missing url" / "unavailable"
paths). The hardcoded DEFAULT_ENDPOINT (localhost) is operator-configured and
trusted, so it is exempt -- only a user-overridable endpoint is guarded.

RED on the pre-781 tree: no guard -> an internal endpoint reaches solve_cloudflare
(a stub records the call), so `called_with` is non-empty. GREEN once the entrypoint
refuses it (200 + ok false + "public") before the sink and `called_with` stays [].

Runner convention: zero-arg fns; app._check_csrf, app._FLARE_AVAILABLE and
app._flare_client are patched on the real module and restored in try/finally.
"""
import bulk_downloader.app as a

_METADATA = "169.254.169.254"   # link-local (cloud metadata) -- must be refused
_LOOPBACK = "127.0.0.1"         # loopback -- must be refused
_PUBLIC = "8.8.8.8"             # public unicast literal (no DNS needed)


class _StubResult:
    ok = True
    elapsed_s = 0.0
    cookies: list = []
    user_agent = ""
    status_code = 200
    html = ""
    error = None


class _StubFlare:
    DEFAULT_ENDPOINT = "http://localhost:8191/v1"

    def __init__(self):
        self.called_with = []

    def solve_cloudflare(self, url, *, endpoint=None, timeout_s=None):
        # Records that the fetch sink was reached and with which endpoint.
        self.called_with.append(endpoint)
        return _StubResult()


def _install():
    orig = (a._check_csrf, a._FLARE_AVAILABLE, a._flare_client)
    stub = _StubFlare()
    a._check_csrf = lambda *x, **k: None
    a._FLARE_AVAILABLE = True
    a._flare_client = stub
    return orig, stub


def _restore(orig):
    a._check_csrf, a._FLARE_AVAILABLE, a._flare_client = orig


def test_flaresolverr_test_rejects_metadata_endpoint():
    orig, stub = _install()
    try:
        c = a.app.test_client()
        r = c.post("/api/flaresolverr/test",
                   json={"url": "http://example.com/", "endpoint": f"http://{_METADATA}/"})
        body = r.get_data(as_text=True).lower()
        assert stub.called_with == [], \
            f"SSRF: solve_cloudflare reached with {stub.called_with!r} -- guard absent"
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {body[:200]}"
        assert '"ok": false' in body or '"ok":false' in body, body[:200]
        assert "public" in body, body[:200]
    finally:
        _restore(orig)


def test_flaresolverr_test_rejects_loopback_endpoint():
    orig, stub = _install()
    try:
        c = a.app.test_client()
        r = c.post("/api/flaresolverr/test",
                   json={"url": "http://example.com/", "endpoint": f"http://{_LOOPBACK}:8191/v1"})
        body = r.get_data(as_text=True).lower()
        assert stub.called_with == [], \
            f"SSRF: solve_cloudflare reached with {stub.called_with!r} -- guard absent"
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {body[:200]}"
        assert "public" in body, body[:200]
    finally:
        _restore(orig)


def test_flaresolverr_test_allows_public_endpoint_reaches_sink():
    # Regression: a public endpoint must PASS the guard and reach solve_cloudflare,
    # proving the guard doesn't over-block. Green on both pre- and post-fix trees.
    orig, stub = _install()
    try:
        c = a.app.test_client()
        r = c.post("/api/flaresolverr/test",
                   json={"url": "http://example.com/", "endpoint": f"http://{_PUBLIC}/"})
        assert r.status_code == 200, r.status_code
        assert stub.called_with == [f"http://{_PUBLIC}/"], \
            f"public endpoint should reach the sink, got {stub.called_with!r}"
    finally:
        _restore(orig)


def test_flaresolverr_test_default_endpoint_is_exempt():
    # No user-supplied endpoint -> the hardcoded DEFAULT_ENDPOINT (localhost) is
    # operator config, not attacker-controlled: it must remain usable (reach sink).
    orig, stub = _install()
    try:
        c = a.app.test_client()
        r = c.post("/api/flaresolverr/test", json={"url": "http://example.com/"})
        assert r.status_code == 200, r.status_code
        assert stub.called_with == [_StubFlare.DEFAULT_ENDPOINT], \
            f"default endpoint should reach the sink, got {stub.called_with!r}"
    finally:
        _restore(orig)
