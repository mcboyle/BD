"""RED-first guard for v3.66.550: site_weather.probe_http SSRF host-guard (F-COREBD17-02).

probe_http issues requests.head/get on a config-derived URL (weather_url/homepage_url/
base_url) with allow_redirects=True and NO is_global / private-IP guard -- the
F-RUN01-01 SSRF class, unmitigated here. Blind (only status/latency are recorded, the
body is never returned), so this is defense-in-depth: a config URL, or a 30x redirect
FROM an otherwise-legitimate one, pointing at loopback / link-local / private / CGNAT
space (169.254.169.254 cloud metadata, http://localhost:5555 internal API) is fetched by
the scheduled bg probe. The redirect-follow is the amplifier -- even an operator-set
external URL can be redirected inward.

The fix routes the target host -- and every redirect hop -- through the single canonical
predicate bulk_downloader.provider_resolve_impl._common._is_safe_public_host (-> _classify_ip,
which rejects RFC 6598 CGNAT since v3.66.524) and refuses a non-global target BEFORE the
outbound request, returning the module's {"ok": False, "error": "blocked: ..."} shape and
recording a failed http probe. Redirects are followed manually (bounded) with re-validation
at each hop rather than relying on requests' allow_redirects.

RED on the pre-550 tree: no guard -> a loopback / CGNAT / link-local target reaches
requests.head (patched to a spy so the test neither hits the network nor hangs), and a
public URL that redirects to loopback is recorded as ok. GREEN once probe_http refuses the
target (and each redirect hop) before the fetch.

Convention: zero-arg fns; requests.head/get patched on the real requests module and
restored in try/finally; _record patched to a no-op so the test never writes
site_weather_log.
"""
import bulk_downloader.site_weather as sw

# Literal IPs (no DNS needed) -- all non-global, must be refused.
_LOOPBACK = "http://127.0.0.1:5555/api/internal"       # loopback (BD's own API)
_CGNAT = "http://100.64.0.1/"                          # RFC 6598 shared address space
_LINK_LOCAL = "http://169.254.169.254/latest/meta-data/"  # cloud metadata
_PUBLIC = "http://8.8.8.8/"                            # public unicast literal


class _Resp:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}

    def close(self):
        pass


_calls = []


def _spy(url, *_a, **_k):
    """Record the fetch and return a benign 200 -- proves whether the guard let the
    outbound request through."""
    _calls.append(url)
    return _Resp(200)


def _patch(spy=_spy):
    import requests
    orig = (requests.head, requests.get, sw._record)
    requests.head = spy
    requests.get = spy
    sw._record = lambda *a, **k: None

    def restore():
        requests.head, requests.get, sw._record = orig
    return restore


def _assert_blocked(url):
    _calls.clear()
    restore = _patch()
    try:
        res = sw.probe_http("test-site", url)
        assert _calls == [], f"guard must block {url} before the fetch, but fetched: {_calls}"
        assert res.get("ok") is False, f"expected ok=False for {url}, got {res!r}"
        assert "block" in (res.get("error") or "").lower(), \
            f"expected a 'blocked' error for {url}, got {res!r}"
    finally:
        restore()


def test_probe_http_blocks_loopback():
    _assert_blocked(_LOOPBACK)


def test_probe_http_blocks_cgnat():
    _assert_blocked(_CGNAT)


def test_probe_http_blocks_link_local_metadata():
    _assert_blocked(_LINK_LOCAL)


def test_probe_http_blocks_redirect_to_private():
    """A public URL that 302-redirects to a loopback target must NOT have the private
    hop fetched, and the result must be a block -- not a recorded ok. RED pre-fix
    (the 302 is recorded ok, and only requests' own redirect engine -- absent under the
    spy -- would have chased it); GREEN post-fix (manual per-hop re-validation refuses
    the redirect target)."""
    _calls.clear()

    def _redirect_spy(url, *_a, **_k):
        _calls.append(url)
        if "8.8.8.8" in url:
            return _Resp(302, {"Location": "http://127.0.0.1:5555/x"})
        return _Resp(200)

    restore = _patch(_redirect_spy)
    try:
        res = sw.probe_http("test-site", _PUBLIC)
        assert all("127.0.0.1" not in u for u in _calls), \
            f"redirect to loopback must not be fetched, but fetched: {_calls}"
        assert res.get("ok") is False, \
            f"a redirect to loopback must yield ok=False, got {res!r}"
        assert "block" in (res.get("error") or "").lower(), \
            f"expected a 'blocked' error after the redirect, got {res!r}"
    finally:
        restore()


def test_probe_http_allows_public_reaches_fetch():
    """Regression: a public target must PASS the guard and reach the outbound request,
    proving the guard doesn't over-block. Green on both pre- and post-fix trees."""
    _calls.clear()
    restore = _patch()
    try:
        sw.probe_http("test-site", _PUBLIC)
        assert _PUBLIC in _calls, \
            f"public host should reach the fetch, got calls={_calls}"
    finally:
        restore()
