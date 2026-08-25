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

WHY THIS FILE NO LONGER SKIPS ITSELF (v3.66.1229, backlog row 215).
probe_http soft-imports `requests` INSIDE the function and returns
{"ok": False, "error": "requests not installed"} without it. `requests` is declared in no
requirements manifest -- it arrives only transitively, through requirements-cloak.txt's
cloakbrowser[geoip] -> geoip2 -> requests, and that install step is NON-FATAL by design --
so its absence is a SUPPORTED posture, not a broken box. This file used to answer that
posture with a module-level `pytest.importorskip("requests")`, which meant every SSRF
assertion below vanished on exactly the install where nobody is watching: a check
reporting nothing at all over a denominator that structurally excluded its own subject
(CLAUDE.md A7). NOTHING here ever needed the real distribution -- every HTTP call was
already a spy, and the only thing the package supplied was an object for `import requests`
to bind. So the minimal API the seam actually uses (head, get, and a response carrying
status_code/headers/close) is INJECTED into sys.modules for the duration of each test, and
all seven nodeids execute with or without the package installed. The genuinely
requests-dependent behaviour -- the missing-dependency return itself -- is asserted
directly instead of being skipped past.

Convention: zero-arg fns except where the pytest `monkeypatch` fixture is needed to
restore sys.modules and module attributes; _record patched to a no-op so the test never
writes site_weather_log.
"""
import sys
from types import SimpleNamespace

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


def _install_requests(monkeypatch, spy=_spy):
    """Inject the MINIMAL requests API probe_http actually uses.

    probe_http does `import requests` in its own body, so sys.modules is the seam --
    not a module attribute (that is selector_playground's shape, not this one).
    monkeypatch.setitem restores the previous entry whether the real package was
    present or absent, which is what makes this file posture-independent."""
    fake = SimpleNamespace(head=spy, get=spy)
    monkeypatch.setitem(sys.modules, "requests", fake)
    monkeypatch.setattr(sw, "_record", lambda *a, **k: None)
    # PRECONDITION: the seam is really the injected object. Without this a typo in
    # the module name would leave the ambient package in place and every assertion
    # below would be measuring the wrong thing on a host that has requests.
    assert sys.modules["requests"] is fake
    return fake


def _hide_requests(monkeypatch):
    """Reproduce the supported posture in which the distribution is absent.

    A None entry in sys.modules makes `import requests` raise ImportError, which is
    exactly what probe_http catches."""
    monkeypatch.setitem(sys.modules, "requests", None)
    monkeypatch.setattr(sw, "_record", lambda *a, **k: None)


def _assert_blocked(url, monkeypatch):
    _calls.clear()
    _install_requests(monkeypatch)
    res = sw.probe_http("test-site", url)
    assert _calls == [], f"guard must block {url} before the fetch, but fetched: {_calls}"
    assert res.get("ok") is False, f"expected ok=False for {url}, got {res!r}"
    assert "block" in (res.get("error") or "").lower(), \
        f"expected a 'blocked' error for {url}, got {res!r}"
    # The missing-dependency return carries the SAME ok=False, so distinguish them:
    # a blocked target must not be laundered by an absent package.
    assert "requests not installed" not in (res.get("error") or ""), \
        f"{url} was refused for the wrong reason -- the seam never ran: {res!r}"


def test_probe_http_blocks_loopback(monkeypatch):
    _assert_blocked(_LOOPBACK, monkeypatch)


def test_probe_http_blocks_cgnat(monkeypatch):
    _assert_blocked(_CGNAT, monkeypatch)


def test_probe_http_blocks_link_local_metadata(monkeypatch):
    _assert_blocked(_LINK_LOCAL, monkeypatch)


def test_probe_http_blocks_redirect_to_private(monkeypatch):
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

    _install_requests(monkeypatch, _redirect_spy)
    res = sw.probe_http("test-site", _PUBLIC)
    # EXACT count, not merely "no loopback": the public first hop must have been
    # fetched exactly once, so the refusal is the redirect policy and not an early
    # bail-out that never reached the network at all.
    assert _calls == [_PUBLIC], \
        f"only the public first hop may be fetched, got: {_calls}"
    assert res.get("ok") is False, \
        f"a redirect to loopback must yield ok=False, got {res!r}"
    assert "block" in (res.get("error") or "").lower(), \
        f"expected a 'blocked' error after the redirect, got {res!r}"


def test_probe_http_allows_public_reaches_fetch(monkeypatch):
    """Regression / over-sensitivity control: a public target must PASS the guard and
    reach the outbound request, proving the guard doesn't over-block.

    Also the catcher for the laundering direction: if probe_http returned its
    missing-dependency result despite the injected API, ok would not be True and no
    call would be recorded."""
    _calls.clear()
    _install_requests(monkeypatch)
    res = sw.probe_http("test-site", _PUBLIC)
    assert _calls == [_PUBLIC], \
        f"public host should reach the fetch exactly once, got calls={_calls}"
    assert res.get("ok") is True, f"public probe should succeed, got {res!r}"
    assert res.get("status_code") == 200, res


def test_probe_http_reports_supported_missing_requests_posture(monkeypatch):
    """The one genuinely requests-dependent behaviour, asserted rather than skipped.

    This is what the module-level importorskip used to stand in for, and it is a
    weaker claim than the file pretended: it says the seam DEGRADES, not that any
    guard holds."""
    _hide_requests(monkeypatch)
    _calls.clear()
    res = sw.probe_http("test-site", _PUBLIC)
    assert res == {"ok": False, "error": "requests not installed"}, res
    assert _calls == [], f"nothing may be fetched without the package: {_calls}"


def test_requests_seam_restores_sys_modules_in_either_order(monkeypatch):
    """The injection must not leak into the rest of the session, in EITHER order.

    A leaked sys.modules['requests'] is a suite-wide hazard: every later test that
    imports requests would silently get a two-method SimpleNamespace."""
    sentinel = object()
    original_present = "requests" in sys.modules
    original_value = sys.modules.get("requests", sentinel)

    def assert_restored():
        assert ("requests" in sys.modules) is original_present, \
            "sys.modules['requests'] presence was not restored"
        assert sys.modules.get("requests", sentinel) is original_value, \
            "sys.modules['requests'] was replaced beyond the test that injected it"

    def available():
        with monkeypatch.context() as scoped:
            _calls.clear()
            _install_requests(scoped)
            res = sw.probe_http("test-site", _PUBLIC)
            assert res.get("ok") is True and _calls == [_PUBLIC], (res, _calls)
        assert_restored()

    def missing():
        with monkeypatch.context() as scoped:
            _hide_requests(scoped)
            res = sw.probe_http("test-site", _PUBLIC)
            assert res == {"ok": False, "error": "requests not installed"}, res
        assert_restored()

    assert_restored()
    available()
    missing()
    missing()
    available()
