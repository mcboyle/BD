"""JD-3 (v3.66.694) -- JDownloader host-coverage report.

Batch B. Exposes whether JDownloader has a hoster plugin covering a site's host
(i.e. whether the `backend:jd` path can actually handle that site), so an
operator can see coverage before switching a site to JD.

THE PARKED FORK (now resolved): JD2's *deprecated* Remote API (the direct
port-3128 API BD uses) has no single cleanly-documented "supported hosts"
endpoint -- the surface varies across JD builds -- so the exact endpoint is
UNVERIFIABLE offline. Resolution (mirrors every runtime-gated integration in
BD, e.g. GH-2a's yt-dlp binary): the query path is a DOCUMENTED ASSUMPTION,
overridable per-site via the *undeclared* cfg key `jd_supported_hosts_path`, and
the client method + coverage mapping are pure/injectable so the whole feature is
unit-tested offline. A JD that lacks the endpoint degrades to available=False
with a hint -- never an error. Live verification (exact path + response shape
against a real JD) is deferred to on-stash.

RED-first on pristine v3.66.693:
  * `jd_bridge.parse_supported_hosts` / `host_coverage` / `JDClient.supported_hosts`
    do not exist -> import/attr errors.
  * the route `/api/sites/<sid>/jd/coverage` is unregistered -> not in url_map.
"""
from __future__ import annotations


# ── parse_supported_hosts (pure) ──────────────────────────────────────
def test_parse_bare_list_normalizes_lowercase_sorted_deduped():
    from bulk_downloader.jd_bridge import parse_supported_hosts
    out = parse_supported_hosts(["Brazzers.com", "bangbros.com", "BRAZZERS.COM", " "])
    assert out == ["bangbros.com", "brazzers.com"]


def test_parse_unwraps_deprecated_api_data_envelope():
    # the deprecated API wraps values as {"data": <value>}
    # (e.g. /jd/getCoreRevision -> {"data": 43190}).
    from bulk_downloader.jd_bridge import parse_supported_hosts
    assert parse_supported_hosts({"data": ["a.com", "b.com"]}) == ["a.com", "b.com"]


def test_parse_list_of_dicts_reads_host_key():
    from bulk_downloader.jd_bridge import parse_supported_hosts
    payload = [{"host": "a.com"}, {"domain": "b.com"}, {"name": "c.com"}]
    assert parse_supported_hosts(payload) == ["a.com", "b.com", "c.com"]


def test_parse_junk_returns_empty():
    from bulk_downloader.jd_bridge import parse_supported_hosts
    assert parse_supported_hosts(None) == []
    assert parse_supported_hosts("nope") == []
    assert parse_supported_hosts({"data": 42}) == []
    assert parse_supported_hosts([1, 2, {}]) == []


# ── host_coverage (pure) ──────────────────────────────────────────────
def test_coverage_exact_host():
    from bulk_downloader.jd_bridge import host_coverage
    r = host_coverage("brazzers.com", ["brazzers.com", "bangbros.com"])
    assert r["covered"] is True and r["matched"] == "brazzers.com"
    assert r["jd_host_count"] == 2


def test_coverage_subdomain_matches_registrable_domain():
    from bulk_downloader.jd_bridge import host_coverage
    r = host_coverage("www.brazzers.com", ["brazzers.com"])
    assert r["covered"] is True and r["matched"] == "brazzers.com"


def test_coverage_uncovered_host():
    from bulk_downloader.jd_bridge import host_coverage
    r = host_coverage("example.com", ["brazzers.com"])
    assert r["covered"] is False and r["matched"] == ""


def test_coverage_empty_inputs_are_safe():
    from bulk_downloader.jd_bridge import host_coverage
    assert host_coverage("", ["a.com"])["covered"] is False
    assert host_coverage("a.com", [])["covered"] is False
    assert host_coverage("a.com", [])["jd_host_count"] == 0


# ── JDClient.supported_hosts (injected transport) ─────────────────────
def _client_with_handler(handler):
    import httpx
    from bulk_downloader.jd_bridge import JDClient
    c = JDClient(host="127.0.0.1", port=3128)
    c._client = httpx.Client(base_url=c._base,
                             transport=httpx.MockTransport(handler))
    return c


def test_supported_hosts_parses_200_list():
    import httpx
    from bulk_downloader.jd_bridge import DEFAULT_SUPPORTED_HOSTS_PATH
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json=["brazzers.com", "bangbros.com"])
    c = _client_with_handler(handler)
    try:
        assert c.supported_hosts() == ["bangbros.com", "brazzers.com"]
        assert seen["path"] == DEFAULT_SUPPORTED_HOSTS_PATH   # default path used
    finally:
        c.close()


def test_supported_hosts_honors_path_override_and_data_envelope():
    import httpx
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json={"data": ["x.com"]})
    c = _client_with_handler(handler)
    try:
        assert c.supported_hosts("/custom/hosts") == ["x.com"]
        assert seen["path"] == "/custom/hosts"
    finally:
        c.close()


def test_supported_hosts_404_degrades_to_empty():
    import httpx
    c = _client_with_handler(lambda req: httpx.Response(404, text="not found"))
    try:
        assert c.supported_hosts() == []
    finally:
        c.close()


def test_supported_hosts_transport_error_degrades_to_empty():
    import httpx

    def handler(request):
        raise httpx.ConnectError("refused")
    c = _client_with_handler(handler)
    try:
        assert c.supported_hosts() == []
    finally:
        c.close()


def test_supported_hosts_no_httpx_client_is_empty():
    from bulk_downloader.jd_bridge import JDClient
    c = JDClient(host="127.0.0.1", port=3128)
    c._client = None
    assert c.supported_hosts() == []


# ── route /api/sites/<sid>/jd/coverage ────────────────────────────────
def test_coverage_route_is_registered(fresh_app):
    from bulk_downloader.app import app
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/sites/<sid>/jd/coverage" in rules


def test_coverage_route_unknown_site_404(fresh_app):
    r = fresh_app.get("/api/sites/nope/jd/coverage")
    assert r.status_code == 404


def _register_site(sid, cfg):
    import importlib
    st = importlib.import_module("bulk_downloader.app_state")
    st.runners[sid] = object()      # presence is all the route checks
    st.s_cfg[sid] = cfg


def test_coverage_route_reports_covered(fresh_app, monkeypatch):
    from bulk_downloader import jd_bridge

    class _FakeClient:
        def supported_hosts(self, path=None):
            return ["brazzers.com", "bangbros.com"]
        def close(self):
            pass
    monkeypatch.setattr(jd_bridge, "get_client_for_site", lambda cfg: _FakeClient())
    _register_site("bz", {"backend": "jd", "url": "https://www.brazzers.com/"})
    r = fresh_app.get("/api/sites/bz/jd/coverage")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["available"] is True
    assert body["covered"] is True and body["matched"] == "brazzers.com"
    assert body["host"] == "www.brazzers.com"


def test_coverage_route_unavailable_when_jd_returns_nothing(fresh_app, monkeypatch):
    from bulk_downloader import jd_bridge

    class _EmptyClient:
        def supported_hosts(self, path=None):
            return []
        def close(self):
            pass
    monkeypatch.setattr(jd_bridge, "get_client_for_site", lambda cfg: _EmptyClient())
    _register_site("ex", {"backend": "jd", "url": "https://example.com/"})
    r = fresh_app.get("/api/sites/ex/jd/coverage")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["available"] is False
    assert "hint" in body            # tells the operator to set the path
