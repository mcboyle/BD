"""RED-first guard for v3.66.541: /api/jsonapi/probe SSRF host-guard (F-APP05-01).

api_jsonapi_probe takes a request-supplied site_root (and an extra_hosts list) and
passes them to extractors_jsonapi.probe_site, which fetches with
follow_redirects=True -- with no is_global host validation. An authenticated user
could drive the server to probe internal/loopback/CGNAT addresses.

The fix routes site_root and every extra_hosts entry through the canonical
bulk_downloader.app._is_url_public (-> _is_safe_public_host -> _classify_ip, which
rejects RFC 6598 CGNAT since v3.66.524) and refuses a non-public host with 400
BEFORE probe_site.

RED on the pre-541 tree: no guard -> a CGNAT (100.64.0.1) target reaches probe_site
(patched to a sentinel so the test neither hits the network nor hangs) -> 500, not
400. GREEN once the entrypoint refuses it with 400.

Scope note: this cut guards ONLY the jsonapi probe. The sibling request-path SSRF
entrypoints each need individual handling and are deferred:
  - /api/template/sandbox (F-APP03-01): local selector-testing against 127.0.0.1
    fixtures is an intended operator feature -> needs an allowlist / dev-gate, not a
    blanket block.
  - /api/dev/manifest_probe (F-APP04-01): dev-gated; returns 200+{ok:false} for bad
    input, so the guard must match that convention rather than 400.
  - /api/webhooks (F-APP05-03): the real fetch SSRF is fire-time (F-CBD12-01); guard
    add-time + fire-time together with the internal-receiver policy decided.

Runner convention: zero-arg fns; app._check_csrf and probe_site are patched on their
real modules and restored in try/finally.
"""
import bulk_downloader.app as a

_CGNAT = "100.64.0.1"       # RFC 6598 -- not public; must be refused
_PUBLIC = "8.8.8.8"         # public unicast literal (no DNS needed)


class _SinkReached(Exception):
    pass


def _raise_sink(*_a, **_k):
    raise _SinkReached("probe_site reached -- SSRF guard absent")


def test_jsonapi_probe_rejects_non_public_site_root():
    import bulk_downloader.extractors_jsonapi as _j
    orig_csrf, orig_probe = a._check_csrf, _j.probe_site
    a._check_csrf = lambda *x, **k: None
    _j.probe_site = _raise_sink
    try:
        c = a.app.test_client()
        r = c.post("/api/jsonapi/probe", json={"site_root": f"http://{_CGNAT}/"})
        body = r.get_data(as_text=True).lower()
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {body[:200]}"
        assert "public" in body, body[:200]
    finally:
        a._check_csrf, _j.probe_site = orig_csrf, orig_probe


def test_jsonapi_probe_rejects_non_public_extra_host():
    import bulk_downloader.extractors_jsonapi as _j
    orig_csrf, orig_probe = a._check_csrf, _j.probe_site
    a._check_csrf = lambda *x, **k: None
    _j.probe_site = _raise_sink
    try:
        c = a.app.test_client()
        r = c.post("/api/jsonapi/probe",
                   json={"site_root": f"http://{_PUBLIC}/", "extra_hosts": [_CGNAT]})
        body = r.get_data(as_text=True).lower()
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {body[:200]}"
        assert "public" in body, body[:200]
    finally:
        a._check_csrf, _j.probe_site = orig_csrf, orig_probe


def test_jsonapi_probe_allows_public_site_root_reaches_sink():
    # regression: a public site_root must PASS the guard and reach probe_site,
    # proving the guard doesn't over-block. Green on both pre- and post-fix trees.
    import bulk_downloader.extractors_jsonapi as _j
    orig_csrf, orig_probe = a._check_csrf, _j.probe_site
    a._check_csrf = lambda *x, **k: None
    _j.probe_site = _raise_sink
    try:
        c = a.app.test_client()
        r = c.post("/api/jsonapi/probe", json={"site_root": f"http://{_PUBLIC}/"})
        body = r.get_data(as_text=True).lower()
        assert r.status_code == 500 and "guard absent" in body, \
            f"public host should reach the sink, got {r.status_code}: {body[:200]}"
    finally:
        a._check_csrf, _j.probe_site = orig_csrf, orig_probe
