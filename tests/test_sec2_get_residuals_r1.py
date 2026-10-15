"""Row sec2-get-residuals r1 (BD-SEC-2, product security HIGH).

Secret VALUES still served on GET after BD-SEC-2 withhold r3 and queue-headers-redact:
  1 /api/status: each job is dict(job) (runner._get_status_impl), so the job's per-URL
    custom_headers VALUES (Authorization, Cookie) reach the client. The same copy feeds the
    SSE status snapshot (app._status_snapshot) and every other get_status(light=False) reader.
  2 the events reads (/api/sites/<sid>/events, /api/events_all) and the SSE initial status
    payload carry no header value either.
  3 an all_stats share token (X-Share-Token and ?ro_token=) on /api/status sees no
    plex_token / stash_api_key / jellyfin_api_key / ha_token (closed on main by
    share-scope-status r2 319e5530f; held here as belt and braces).
  4 diagnostics_bundle.redact_secrets kept a Proxy-Authorization value: the bundle now asks
    the shared secret-header class (capture_redact.is_secret_header) for header names.

A client sees header NAMES with every value db.QUEUE_HEADER_MASK; the job keeps its values for
its own request. Real app (fresh_app: scratch workdir, in-memory SQLite), synthetic canaries in
the BD-SEC-1 shape .gitleaks.toml allowlists. Failure lines carry route names and key names only.
"""
from __future__ import annotations

import json

import pytest

BD_GATE_SCOPE = "module"

AUTH = "bdsec1-canary-getres-auth-GRAH"
COOK = "bdsec1-canary-getres-cookie-GRCK"
CANARIES = (AUTH, COOK)
U1 = "https://h.example/getres/1"
U2 = "https://h.example/getres/2"
ENTRY1 = f"{U1}\tAuthorization=Bearer {AUTH}\tCookie=sess={COOK}"
MASK = "[REDACTED]"  # db.QUEUE_HEADER_MASK, a literal so BASE fails on behaviour

H = {"Host": "127.0.0.1:5000"}
OP_TOKEN = "op-token-sec2-get-residuals"
OP = dict(H, Authorization="Bearer " + OP_TOKEN)
SITE_SECRETS = {"plex_token": "bdsec1-canary-getres-plex-GRPX",
                "stash_api_key": "bdsec1-canary-getres-stash-GRST",
                "jellyfin_api_key": "bdsec1-canary-getres-jf-GRJF",
                "ha_token": "bdsec1-canary-getres-ha-GRHA"}


def _leak_count(text) -> int:
    """How many canaries the text carries. A count, never the strings: a failing assert
    shows pytest's introspection of its operands, so callers bind this to a name first."""
    return sum(1 for c in CANARIES if c in text)


def _names_masked(headers) -> bool:
    """The served map is exactly the job's header names, each value the mask."""
    return (isinstance(headers, dict) and sorted(headers) == ["Authorization", "Cookie"]
            and all(v == MASK for v in headers.values()))


@pytest.fixture
def site(fresh_app):
    from bulk_downloader import app as A
    sid = fresh_app.post("/api/sites", json={"name": "getres"}).get_json()["id"]
    runner = A.runners[sid]
    runner.load_urls([ENTRY1, U2])
    held = runner.jobs[U1].get("custom_headers") or {}
    armed = held.get("Authorization") == f"Bearer {AUTH}"
    assert armed, "GETRES_PROBE_BLIND: no canary header on the job"
    return fresh_app, sid, runner


def _held(runner) -> bool:
    """The job still carries the real values its own request uses."""
    h = runner.jobs[U1].get("custom_headers") or {}
    return h.get("Authorization") == f"Bearer {AUTH}" and h.get("Cookie") == f"sess={COOK}"


def test_api_status_serves_header_names_masked_never_values(site):
    client, sid, runner = site
    reply = client.get("/api/status")
    body = reply.get_data(as_text=True)
    assert reply.status_code == 200
    n = _leak_count(body)
    assert n == 0, ("GETRES-STATUS-HEADER-LEAK", "/api/status", n)
    jobs = reply.get_json()[sid]["jobs"]
    # control: the names stay visible, each value is the mask; the plain job is unchanged
    masked = _names_masked(jobs[U1].get("custom_headers"))
    assert masked, "GETRES-STATUS-NAMES-NOT-MASKED"
    assert "custom_headers" not in jobs[U2] or not jobs[U2]["custom_headers"]
    assert jobs[U1]["status"] == runner.jobs[U1]["status"] and U2 in jobs
    assert reply.get_json()[sid]["total"] == 2
    # the served copy never touched the job's own dict
    assert _held(runner), "GETRES-STATUS-MASKED-THE-JOB"


def test_every_get_status_reader_gets_the_masked_copy(site):
    from bulk_downloader import app as A
    client, sid, runner = site
    st = runner.get_status(light=False)
    masked = _names_masked(st["jobs"][U1].get("custom_headers"))
    assert masked, "GETRES-READER-NAMES-NOT-MASKED"
    for light in (False, True):
        snap = json.dumps(A._status_snapshot(light=light), default=str)
        n = _leak_count(snap)
        assert n == 0, ("GETRES-SSE-STATUS-LEAK", light, n)
    assert _held(runner)


def test_events_reads_and_sse_initial_payload_carry_no_header_value(site):
    from bulk_downloader import app as A
    client, sid, runner = site
    runner.log_event("state", "getres probe event", url=U1)
    for path in (f"/api/sites/{sid}/events", "/api/events_all"):
        reply = client.get(path)
        assert reply.status_code == 200, path
        n = _leak_count(reply.get_data(as_text=True))
        assert n == 0, ("GETRES-EVENTS-LEAK", path, n)
    gen = client.get("/api/stream", buffered=False)
    try:
        first = b""
        for chunk in gen.response:
            first += chunk if isinstance(chunk, bytes) else chunk.encode()
            if b"event: status" in first:
                break
    finally:
        gen.close()
    assert b"event: status" in first, "GETRES_PROBE_BLIND: no SSE status event"
    n = _leak_count(first.decode("utf-8", "replace"))
    assert n == 0, ("GETRES-SSE-INITIAL-LEAK", n)
    assert _held(runner)


@pytest.fixture
def authed(fresh_app, monkeypatch):
    import bulk_downloader.app as A
    monkeypatch.setattr(A, "_expected_token", lambda: OP_TOKEN)
    monkeypatch.setattr(A, "_bd_token_secondary", lambda: "")
    return fresh_app


def test_all_stats_share_token_reads_no_integration_key_on_api_status(authed):
    from bulk_downloader import app_state as ST
    from bulk_downloader import shares
    client = authed
    r = client.post("/api/sites", json={"name": "GetresShare"}, headers=OP)
    assert r.status_code == 200
    sid = next(k for k, v in ST.s_cfg.items() if v.get("name") == "GetresShare")
    for key, value in SITE_SECRETS.items():
        assert client.put(f"/api/sites/{sid}", json={key: value}, headers=OP).status_code == 200, key
        stored = ST.s_cfg[sid].get(key) == value
        assert stored, key
    tok = shares.create_token(scopes=["all_stats"], label="getres")["token"]
    for reply in (client.get("/api/status", headers=dict(H, **{"X-Share-Token": tok})),
                  client.get("/api/status?ro_token=" + tok, headers=H)):
        # share-scope-status r2 (319e5530f) took /api/status out of SHARE_ROUTES: a refusal is the
        # closure; a 200 must carry no integration key
        assert reply.status_code in (200, 401, 403), ("GETRES-SHARE-STATUS-CODE", reply.status_code)
        body = reply.get_data(as_text=True)
        leaked = sorted(k for k, v in SITE_SECRETS.items() if v in body)
        assert leaked == [], ("GETRES-SHARE-STATUS-LEAK", reply.status_code, leaked)
    # positive control: the same token opens a route its scope names
    assert client.get("/api/capacity", headers=dict(H, **{"X-Share-Token": tok})).status_code == 200


SECRET_HEADERS = ["Proxy-Authorization", "proxy-authorization", "Authorization", "Cookie",
                  "Set-Cookie", "X-Api-Key", "X-BD-Token", "X-CSRF-Token"]
PLAIN = {"User-Agent": "Mozilla/5.0 getres", "Accept": "text/html", "Referer": "https://h.example/"}


@pytest.mark.parametrize("name", SECRET_HEADERS)
def test_diagnostics_bundle_redacts_every_secret_header_class_name(name):
    from bulk_downloader.capture_redact import is_secret_header
    from bulk_downloader.diagnostics_bundle import redact_secrets
    assert is_secret_header(name), ("GETRES_PROBE_BLIND: not in the secret-header class", name)
    out = redact_secrets({"request": {"headers": {name: "Basic " + AUTH, **PLAIN}}})
    text = json.dumps(out)
    hit = AUTH in text
    assert not hit, ("GETRES-DIAG-HEADER-LEAK", name)
    # control: non-secret header names AND values are still shown
    shown = {k: out["request"]["headers"][k] for k in PLAIN}
    assert shown == PLAIN, ("GETRES-DIAG-PLAIN-HEADER-HIDDEN", sorted(k for k in PLAIN if shown[k] != PLAIN[k]))


# r2 (C6 F1): a header map is redacted by the UNION of is_secret_header and the per-key
# _is_sensitive_key class BASE applied by recursing into it -- never narrower than BASE.
# X-Twocaptcha-Key is out of the denominator: BASE served it too (C6 r1).
BASE_KEY_CLASS = ["X-Password", "Username", "Email", "Passwd", "X-Vpn-Password"]
OLD = "bdsec1-canary-getres-oldclass-GROC"


@pytest.mark.parametrize("name", BASE_KEY_CLASS)
def test_diagnostics_header_map_keeps_base_key_class(name):
    from bulk_downloader.diagnostics_bundle import _is_sensitive_key, redact_secrets
    assert _is_sensitive_key(name), ("GETRES_PROBE_BLIND: BASE class lost the name", name)
    out = redact_secrets({"request": {"headers": {name: OLD, **PLAIN}}})
    hit = OLD in json.dumps(out)
    assert not hit, ("GETRES-DIAG-OLD-CLASS-REGRESSION", name)
    shown = {k: out["request"]["headers"][k] for k in PLAIN}
    assert shown == PLAIN, "GETRES-DIAG-PLAIN-HEADER-HIDDEN"


@pytest.mark.parametrize("name", BASE_KEY_CLASS)
def test_put_site_probe_headers_never_reach_the_bundle(fresh_app, name):
    from bulk_downloader import app_state as ST
    from bulk_downloader import diagnostics_bundle as DB
    sid = fresh_app.post("/api/sites", json={"name": "getres-r2"}).get_json()["id"]
    fresh_app.put(f"/api/sites/{sid}", json={"probe_headers": {name: OLD, "Accept": "text/html"}})
    stored = (ST.s_cfg[sid].get("probe_headers") or {}).get(name) == OLD
    assert stored, "GETRES_PROBE_BLIND: probe_headers not stored"
    out = json.dumps(DB.bundle(ST.s_cfg, include_history=False), default=str)
    assert "text/html" in out, "GETRES-BUNDLE-PLAIN-HIDDEN"
    hit = OLD in out
    assert not hit, ("GETRES-BUNDLE-PROBE-HEADER-LEAK", name)


def test_diagnostics_mixed_case_headers_parent_uses_secret_header_class():
    from bulk_downloader.diagnostics_bundle import _is_sensitive_key, redact_secrets
    # the per-key class alone misses this name: only the *headers branch withholds it
    assert not _is_sensitive_key("Proxy-Authorization"), "GETRES_PROBE_BLIND: key class covers it"
    out = redact_secrets({"Request_Headers": {"Proxy-Authorization": OLD, **PLAIN}})
    hit = OLD in json.dumps(out)
    assert not hit, "GETRES-DIAG-MIXED-CASE-PARENT-LEAK"
    assert out["Request_Headers"]["Accept"] == "text/html"


@pytest.mark.parametrize("state", ["pending", "queued", "running", "done", "failed",
                                   "paused", "stopped", "needs_review"])
def test_api_status_masks_header_values_in_every_job_state(site, state):
    from bulk_downloader import app as A
    client, sid, runner = site
    with runner._lock:
        runner.jobs[U1]["status"] = state
    body = client.get("/api/status").get_data(as_text=True)
    snap = json.dumps(A._status_snapshot(light=False), default=str)
    n = _leak_count(body) + _leak_count(snap)
    assert n == 0, ("GETRES-STATE-HEADER-LEAK", state, n)
    assert _held(runner), "GETRES-STATUS-MASKED-THE-JOB"
