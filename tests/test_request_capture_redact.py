"""request-capture-redact r1 -- the request_capture ring must not keep or serve secrets.

FINDINGS dev-console-config HIGH-F1 (S59-B) + dev-console-exec HIGH-F1 (S55-B), PM spec
ANSWER-pm-B-HIGH-F1-1210Z items 1-5d:
  1  one shared secret-header class (capture_redact.is_secret_header) masks every
     credential header value, names kept;
  2  credential-route bodies (derived from the app url_map) are never stored; every
     other body has its secret-keyed JSON / form values masked at any depth;
  3  request_capture can be turned ON only from loopback or with the master token,
     and every flag write is logged (name, actor class, ts);
  5  concurrent set_flag / delete_flag lose no acknowledged write, each through its
     own tmp file (FINDINGS F2);
  5b X-BD-API-Token is in the class; 5c list output passes redact_artifact;
  5d a pair_redeem session neither reads canaries from the list nor flips capture.

Secrets are random canaries. Assertions compare COUNTS only, never the strings, so a
failure message never carries a canary.
"""
# The subject is the capture recorder and its flag gate, not the tree.
BD_GATE_SCOPE = "module"

import ast
import importlib
import json
import logging
import secrets
import threading
from pathlib import Path

import pytest

REMOTE = {"REMOTE_ADDR": "10.0.0.5"}


def _mod(name):
    return importlib.import_module(f"bulk_downloader.{name}")


def _canary(prefix):
    return prefix + secrets.token_hex(12)


def _hits(text, sents):
    """{label: count} for every canary present in ``text`` (labels only)."""
    return {k: text.count(v) for k, v in sents.items() if text.count(v)}


@pytest.fixture
def flags(tmp_path, monkeypatch):
    """feature_flags pointed at a tmp file; ring emptied before and after."""
    ff = _mod("feature_flags")
    rr = _mod("request_replay")
    target = Path(tmp_path) / "ff" / "feature_flags.json"
    target.parent.mkdir()
    monkeypatch.setattr(ff, "state_path", lambda: target)
    monkeypatch.setattr(ff, "_CACHE", {})
    monkeypatch.setattr(ff, "_CACHE_MTIME", 0.0)
    rr.reset()
    yield target
    rr.reset()


def _client():
    return _mod("app").app.test_client()


# ── item 1 / 5b: the shared secret-header class ──────────────────────────────

SECRET_HEADERS = ["Authorization", "Proxy-Authorization", "Cookie", "Set-Cookie",
                  "X-BD-Token", "X-BD-API-Token", "X-CSRF-Token", "X-API-Key",
                  "X-Refresh-Token", "X-Client-Secret", "X-Signing-Key", "Token", "Secret"]
PLAIN_HEADERS = ["Content-Type", "Accept", "User-Agent", "X-Requested-With",
                 "Content-Length", "Host", "Referer"]


def test_header_class_names_every_credential_header():
    cr = _mod("capture_redact")
    unmatched = [n for n in SECRET_HEADERS if not cr.SENSITIVE_HEADER.search(n)]
    assert not unmatched, f"RCR-HDR-CLASS secret header names the class misses: {unmatched}"
    over = [n for n in PLAIN_HEADERS if cr.SENSITIVE_HEADER.search(n)]
    assert not over, f"RCR-HDR-CLASS plain header names masked: {over}"
    pred = getattr(cr, "is_secret_header", None)
    assert pred is not None, "RCR-HDR-CLASS capture_redact.is_secret_header missing"
    bad = [n for n in SECRET_HEADERS if not pred(n)] + [n for n in PLAIN_HEADERS if pred(n)]
    assert not bad, f"RCR-HDR-CLASS is_secret_header wrong on: {bad}"


def test_capture_uses_shared_header_class_no_local_list():
    """Grep guard: request_replay imports the class and holds no header-name list."""
    rr = _mod("request_replay")
    src = Path(rr.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {a.name for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("capture_redact")
                for a in n.names}
    assert "is_secret_header" in imported, \
        f"RCR-GREP-GUARD request_replay does not import capture_redact.is_secret_header: {sorted(imported)}"
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "is_secret_header" in called, "RCR-GREP-GUARD is_secret_header imported but never called"
    header_words = {h.lower() for h in SECRET_HEADERS} - {"cookie", "token", "secret"}
    local = sorted({n.value.lower() for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and n.value.lower() in header_words})
    assert not local, f"RCR-GREP-GUARD request_replay holds a local header-name list: {local}"


# ── items 2 / 4: the probe rerun (ring + replay_list) ────────────────────────

def test_ring_and_replay_list_hold_no_secret(flags):
    rr = _mod("request_replay")
    c = _client()
    pw, pw2, tk, tk2, tk3, bk, ck = (_canary(p) for p in ("pw", "pv", "tk", "ta", "tq", "bk", "ck"))
    sents = {"PW": pw, "PW2": pw2, "TK": tk, "TK2": tk2, "TK3": tk3, "BK": bk, "CK": ck}
    plain = {"BODY": _canary("plb"), "QUERY": _canary("plq"), "HDR": _canary("plh")}

    r = c.post("/api/dev/feature_flag_set", json={"name": "request_capture", "value": True})
    assert r.status_code == 200 and rr.is_enabled(), f"RCR-SETUP loopback flip -> {r.status_code}"

    c.post("/api/auth/login", json={"username": "u1", "password": pw})
    c.post("/api/secrets/unlock", json={"password": pw})
    c.get("/api/dev/feature_flags", headers={"X-BD-Token": tk, "X-Probe-Note": plain["HDR"]})
    c.get("/api/dev/feature_flags", headers={"X-BD-API-Token": "bdapi_" + bk})
    c.get("/api/dev/feature_flags", headers={"Authorization": "Bearer " + tk2})
    cc = _client()
    cc.set_cookie("bd_session", ck)
    cc.get("/api/dev/feature_flags")
    c.post("/api/dev/feature_flag_delete",
           json={"name": "zz_absent", "note": plain["BODY"], "token": tk3,
                 "meta": {"inner": {"api_key": pw2, "list": [{"passphrase": pw2},
                                                            {"secret": pw2}]}}})
    c.get(f"/api/dev/feature_flags?token={tk3}&x={plain['QUERY']}")

    with rr._lock:
        stored = list(rr._requests)
    ring = json.dumps(stored)
    resp = c.get("/api/dev/request_replay_list?limit=200")
    listed = resp.get_data(as_text=True)

    leaks = {"ring": _hits(ring, sents), "list": _hits(listed, sents)}
    assert not leaks["ring"] and not leaks["list"], f"RCR-RING-LEAK canary hits {leaks}"
    # Positive controls: the counter can say yes, capture really ran.
    assert resp.status_code == 200, f"RCR-SETUP replay_list -> {resp.status_code}"
    missing = {k for k, v in plain.items() if ring.count(v) < 1 or listed.count(v) < 1}
    assert not missing, f"RCR-CONTROL plain markers absent (capture not proven): {sorted(missing)}"
    assert len(stored) >= 8, f"RCR-CONTROL ring entries={len(stored)}"
    names = {k.lower() for e in stored for k in (e.get("request_headers") or {})}
    kept = {"x-bd-token", "x-bd-api-token", "authorization", "cookie"} - names
    assert not kept, f"RCR-NAMES header names dropped: {sorted(kept)}"


# ── item 2: credential routes derived from url_map ───────────────────────────

def test_withheld_routes_derived_from_url_map(flags):
    rr = _mod("request_replay")
    app = _mod("app").app
    derive = getattr(rr, "withheld_rules", None)
    assert derive is not None, "RCR-WITHHELD request_replay.withheld_rules missing"
    w = derive(app.url_map)
    rules = {r.rule for r in app.url_map.iter_rules()}
    by_prefix = {r for r in rules
                 if r.startswith(("/api/auth/", "/api/secrets/", "/api/pair", "/api/vault"))}
    need = by_prefix | {"/api/sites/<sid>/login", "/api/sites/<sid>/manual_login",
                        "/api/cookie_relogin/check", "/api/secrets/extension/pair"}
    need &= rules
    assert len(by_prefix) >= 20, f"RCR-CONTROL prefix census found {len(by_prefix)} rules"
    gap = sorted(need - w)
    assert not gap, f"RCR-WITHHELD credential rules not withheld: {gap}"
    wrong = sorted({"/api/dev/request_replay_list", "/api/dev/feature_flags",
                    "/api/automation/drift_repair"} & w)
    assert not wrong, f"RCR-WITHHELD plain rules withheld: {wrong}"
    # Derived, not a hand list: a rule the app does not have yet is classified too.
    from werkzeug.routing import Map, Rule
    fresh = derive(Map([Rule("/api/vault/new_thing"), Rule("/api/other/thing")]))
    assert fresh == frozenset({"/api/vault/new_thing"}), f"RCR-WITHHELD fresh map -> {sorted(fresh)}"

    ff = _mod("feature_flags")
    ff.set_flag("request_capture", True)
    pw = _canary("pw")
    rid = rr.record(method="POST", path="/api/sites/s1/login", query="", status=200,
                    request_headers={}, request_body=json.dumps({"pw_field_x": pw}),
                    response_body=json.dumps({"echo": pw}), duration_ms=1.0,
                    rule="/api/sites/<sid>/login")
    e = rr.get(rid) or {}
    leak = _hits(json.dumps(e), {"PW": pw})
    assert not leak and e.get("body_withheld") is True, \
        f"RCR-WITHHELD login body stored: hits={leak} withheld={e.get('body_withheld')}"
    out = rr.replay(rid, base_url="http://127.0.0.1:9")
    assert out.get("ok") is False and "withheld" in (out.get("error") or ""), \
        f"RCR-REPLAY withheld body replayed: ok={out.get('ok')}"


# ── item 2: recursive key masking on every other route ───────────────────────

def test_record_masks_secret_keys_recursively(flags):
    rr = _mod("request_replay")
    _mod("feature_flags").set_flag("request_capture", True)
    pw, tk = _canary("pw"), _canary("tk")
    plain = _canary("pl")
    sents = {"PW": pw, "TK": tk}
    cases = {
        "json-nested": json.dumps({"a": {"b": {"password": pw, "keep": plain}},
                                   "l": [{"passphrase": pw}, {"secret": pw},
                                         {"token": tk}, {"api_key": tk}]}),
        "form": f"user=u1&password={pw}&keep={plain}",
        "unparsed": "{broken password: " + pw,
    }
    leaks = {}
    for label, body in cases.items():
        rid = rr.record(method="POST", path="/api/x", query="", status=200,
                        request_headers={}, request_body=body,
                        response_body=json.dumps({"data": {"token": tk, "keep": plain}}),
                        duration_ms=1.0)
        e = json.dumps(rr.get(rid) or {})
        if _hits(e, sents):
            leaks[label] = _hits(e, sents)
        if label != "unparsed" and plain not in e:
            leaks[label + "-control"] = "plain value dropped"
    # A URL-valued header keeps its URL; the query's secret values are scrubbed.
    rid = rr.record(method="GET", path="/api/x", query="", status=200,
                    request_headers={"Referer": f"http://127.0.0.1:5000/p?token={tk}&keep={plain}"},
                    request_body="", response_body="", duration_ms=1.0)
    e = json.dumps(rr.get(rid) or {})
    if _hits(e, sents) or plain not in e:
        leaks["url-header"] = {"hits": _hits(e, sents), "plain": e.count(plain)}
    assert not leaks, f"RCR-BODY-MASK {leaks}"


# ── 5c: list output passes the canonical artifact floor ──────────────────────

def test_list_output_passes_capture_artifact_floor(flags):
    rr = _mod("request_replay")
    _mod("feature_flags").set_flag("request_capture", True)
    jwt = "eyJ" + secrets.token_hex(10) + "." + "eyJ" + secrets.token_hex(10) + "." + secrets.token_hex(10)
    rr.record(method="GET", path="/api/x", query="", status=200,
              request_headers={"X-Probe-Note": jwt}, request_body="", response_body="",
              duration_ms=1.0)
    with rr._lock:
        raw = json.dumps(list(rr._requests))
    listed = json.dumps(rr.list_recent(50))
    via_diag = json.dumps(_mod("dev_suite").request_replay_list(limit=50))
    counts = {"raw": raw.count(jwt), "list": listed.count(jwt), "diag": via_diag.count(jwt)}
    assert counts["raw"] == 1, f"RCR-CONTROL artifact canary not in the ring: {counts}"
    assert counts["list"] == 0 and counts["diag"] == 0, f"RCR-LIST-FLOOR {counts}"


# ── item 3 / 5d: who may turn request_capture ON ─────────────────────────────

def _session_client(app_mod, sess):
    """A client holding only the session cookie; returns (client, csrf headers)."""
    sc = _client()
    sc.set_cookie("bd_session", sess)
    return sc, {"X-CSRF-Token": app_mod._csrf_token_for(sess)}


def test_session_or_referer_cannot_flip_capture_on(flags, monkeypatch, caplog):
    a = _mod("app")
    ff = _mod("feature_flags")
    rr = _mod("request_replay")
    c = _client()
    mt, bk = _canary("mt"), _canary("bk")
    monkeypatch.setenv("BD_AUTH_TOKEN", mt)
    caplog.set_level(logging.WARNING, logger="bulk_downloader.feature_flags")

    sess = a._session_create("pair_redeem")
    sc, h = _session_client(a, sess)
    ctl = sc.post("/api/dev/feature_flag_set", json={"name": "zz_ctl", "value": True},
                 headers=h, environ_base=REMOTE)
    assert ctl.status_code == 200 and ff.is_on("zz_ctl"), \
        f"RCR-SETUP pair session cannot set a plain flag -> {ctl.status_code}"
    r = sc.post("/api/dev/feature_flag_set", json={"name": "request_capture", "value": True},
                headers=h, environ_base=REMOTE)
    assert r.status_code == 403 and not ff.is_on("request_capture"), \
        f"RCR-FLIP-SESSION pair_redeem session flipped capture: status={r.status_code} on={ff.is_on('request_capture')}"
    off = sc.post("/api/dev/feature_flag_set", json={"name": "request_capture", "value": False},
                 headers=h, environ_base=REMOTE)
    assert off.status_code == 200, f"RCR-CONTROL session turning capture OFF -> {off.status_code}"

    on = c.post("/api/dev/feature_flag_set", json={"name": "request_capture", "value": True},
                headers={"X-BD-Token": mt}, environ_base=REMOTE)
    assert on.status_code == 200 and rr.is_enabled(), f"RCR-CONTROL master token flip -> {on.status_code}"

    # 5d: the pair session reads the list after master / bdapi_ traffic was captured.
    c.get("/api/dev/feature_flags", headers={"X-BD-Token": mt})
    c.get("/api/dev/feature_flags", headers={"X-BD-API-Token": "bdapi_" + bk})
    lst = sc.get("/api/dev/request_replay_list?limit=200", environ_base=REMOTE)
    body = lst.get_data(as_text=True)
    assert lst.status_code == 200 and body.count("X-Bd-Token") >= 1, \
        f"RCR-CONTROL pair session list -> {lst.status_code}, header name present={body.count('X-Bd-Token')}"
    leak = _hits(body, {"MT": mt, "BK": bk})
    assert not leak, f"RCR-PAIR-LIST canaries served to a pair session: {leak}"

    log = caplog.text
    lines = [ln for ln in log.splitlines() if "feature_flag_change" in ln]
    want = {"refused": "actor=session result=refused", "token": "actor=master_token result=ok"}
    absent = [k for k, v in want.items()
              if not any("name='request_capture'" in ln and v in ln and "ts=" in ln for ln in lines)]
    assert not absent, f"RCR-FLAG-LOG missing audit lines: {absent} (lines={len(lines)})"
    assert log.count(mt) == 0, "RCR-FLAG-LOG master token in the log"


def test_same_origin_referer_cannot_flip_capture_on(flags, monkeypatch):
    ff = _mod("feature_flags")
    monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    _auth(monkeypatch, "")
    c = _client()
    h = {"Referer": "http://localhost/dev", "Host": "localhost"}
    # r2: BASE 88ea4929 (raw-editors F1) no longer admits a same-origin Referer to
    # /api/dev/* at all; the control is the same request from loopback.
    ctl = c.post("/api/dev/feature_flag_set", json={"name": "zz_ref", "value": True}, headers=h)
    assert ctl.status_code == 200 and ff.is_on("zz_ref"), \
        f"RCR-SETUP loopback cannot set a plain flag -> {ctl.status_code}"
    r = c.post("/api/dev/feature_flag_set", json={"name": "request_capture", "value": True},
               headers=h, environ_base=REMOTE)
    assert r.status_code == 403 and not ff.is_on("request_capture"), \
        f"RCR-FLIP-REFERER same-origin Referer flipped capture: status={r.status_code}"


# ── item 5: concurrent flag writes (FINDINGS F2 / S09) ───────────────────────

def _run_threads(fn, n=8):
    gate = threading.Barrier(n)
    errs = []

    def body(t):
        gate.wait()
        for i in range(25):
            res = fn(f"cc_{t}_{i}")
            if not res.get("ok"):
                errs.append(t)

    ths = [threading.Thread(target=body, args=(t,)) for t in range(n)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    return len(errs)


def test_concurrent_flag_writes_lose_nothing(flags):
    ff = _mod("feature_flags")
    names = [f"cc_{t}_{i}" for t in range(8) for i in range(25)]
    bad = _run_threads(lambda n: ff.set_flag(n, True))
    disk = json.loads(flags.read_text())
    lost = [n for n in names if n not in disk]
    assert bad == 0 and not lost, f"RCR-FLAG-LOST set: ok_false={bad} lost_on_disk={len(lost)}"
    bad = _run_threads(ff.delete_flag)
    disk = json.loads(flags.read_text())
    kept = [n for n in names if n in disk]
    assert bad == 0 and not kept, f"RCR-FLAG-LOST delete: ok_false={bad} still_on_disk={len(kept)}"
    stray = sorted(p.name for p in flags.parent.iterdir() if p.name.endswith(".tmp"))
    assert not stray, f"RCR-FLAG-TMP stray tmp files: {len(stray)}"
    # Serial control: the same 200 writes, one thread, lose nothing.
    for n in names:
        ff.set_flag(n, True)
    disk = json.loads(flags.read_text())
    assert all(n in disk for n in names), "RCR-CONTROL serial writes lost"


def test_each_flag_write_uses_its_own_tmp(flags, monkeypatch):
    """F2: the tmp a writer renames from is its own (a shared feature_flags.json.tmp
    let one writer's os.replace consume another's file -- the ok:false half of S09)."""
    ff = _mod("feature_flags")
    real = ff.os.replace
    srcs = []

    def spy(src, dst):
        if Path(dst) == flags:
            srcs.append(Path(src).name)
        return real(src, dst)

    monkeypatch.setattr(ff.os, "replace", spy)
    ff.set_flag("tt_a", True)
    ff.set_flag("tt_b", True)
    ff.delete_flag("tt_a")
    assert len(srcs) == 3, f"RCR-CONTROL flag writes renamed {len(srcs)} tmp files, want 3"
    shared = len(srcs) - len(set(srcs))
    fixed = [n for n in srcs if n == flags.name + ".tmp"]
    assert not shared and not fixed, \
        f"RCR-FLAG-TMP-SHARED tmp names reused={shared} fixed-name={len(fixed)}"


# ── r2 (PM ANSWER-pm-B 1352Z): F1 query, F2 replay laundering, A2-A4 ─────────

def _auth(monkeypatch, token=""):
    """Pin the box's accepted tokens: ``token`` alone, or none (auth unset)."""
    a = _mod("app")
    monkeypatch.setattr(a, "_expected_token", lambda: token)
    monkeypatch.setattr(a, "_bd_token_secondary", lambda: "")
    return a


class _Receiver:
    """A loopback HTTP server recording each request (path, headers, body);
    answers 200 {} or, with ``redirect_to``, a 302 to that base + the path."""

    def __init__(self, redirect_to=None):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        seen = self.seen = []

        class H(BaseHTTPRequestHandler):
            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
                seen.append({"path": self.path, "body": body,
                             "headers": {k.lower(): v for k, v in self.headers.items()}})
                if redirect_to:
                    self.send_response(302)
                    self.send_header("Location", redirect_to + self.path)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            do_GET = do_POST = do_PUT = do_DELETE = _any

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _master_ctx(a, mt):
    """A request context presenting the master token (the replay route's caller)."""
    return a.app.test_request_context("/api/dev/request_replay/x", method="POST",
                                      headers={"X-BD-Token": mt})


QUERY_SHAPES = ["ro_token=C&", "&ro_token=C", "ro_token=C&raw", "a=1&&ro_token=C",
                "ro_token=C", "ro_token=C&a=1"]


def test_query_every_shape_masked_in_ring_get_and_replay(flags, monkeypatch):
    """F1 (+A1): every query shape -- including ones a strict parse rejects -- is
    masked by the canonical redact_query in the ring, in get() and at the replay
    receiver."""
    rr = _mod("request_replay")
    mt = _canary("mt")
    a = _auth(monkeypatch, mt)
    _mod("feature_flags").set_flag("request_capture", True)
    c = _client()
    shapes = QUERY_SHAPES + ["sig=C&", "key=C&x", "api_key=C&&", "pass%77ord=C&", "api%5Fkey=C"]
    rx = _Receiver()
    leaks, ids = {}, []
    try:
        for i, shape in enumerate(shapes):
            cn = _canary("q")
            q = shape.replace("C", cn)
            rid = rr.record(method="GET", path="/api/x", query=q, status=200,
                            request_headers={}, request_body="", response_body="",
                            duration_ms=1.0)
            c.get("/api/dev/feature_flags?" + q, headers={"X-BD-Token": mt})
            with rr._lock:
                ring = json.dumps(list(rr._requests))
            got = json.dumps(rr.get(rid) or {})
            listed = json.dumps(rr.list_recent(200))
            with _master_ctx(a, mt):
                out = rr.replay(rid, base_url=rx.base, timeout=5)
            heard = json.dumps(rx.seen[-1]) if rx.seen else ""
            n = {"ring": ring.count(cn), "get": got.count(cn), "list": listed.count(cn),
                 "receiver": heard.count(cn)}
            if any(n.values()) or not out.get("ok"):
                leaks[f"s{i}"] = dict(n, replay_ok=bool(out.get("ok")))
            ids.append(rid)
        # positive control: a plain value DOES reach the ring and the receiver
        plain = _canary("pl")
        rid = rr.record(method="GET", path="/api/x", query=f"keep={plain}&a=1", status=200,
                        request_headers={}, request_body="", response_body="", duration_ms=1.0)
        with _master_ctx(a, mt):
            rr.replay(rid, base_url=rx.base, timeout=5)
        ctl = {"get": json.dumps(rr.get(rid)).count(plain),
               "receiver": json.dumps(rx.seen[-1]).count(plain) if rx.seen else 0,
               "marker": sum(1 for s in rx.seen if "x-bd-replay" in s["headers"])}
    finally:
        rx.close()
    assert ctl["get"] == 1 and ctl["receiver"] == 1, f"RCR-CONTROL plain query value not carried: {ctl}"
    assert not leaks, f"RCR-QUERY-SHAPE query canary kept/sent for {len(leaks)} shapes: {leaks}"
    assert ctl["marker"] == len(shapes) + 1, f"RCR-REPLAY-MARKER replays without the marker header: {ctl}"


def _replay_launder(monkeypatch, mt):
    """R235-C P6: capture ON from loopback, a pair_redeem session turns it OFF, is
    refused turning it back ON, then replays the captured ON request at this box."""
    from werkzeug.serving import make_server
    a = _auth(monkeypatch, mt)
    ff = _mod("feature_flags")
    rr = _mod("request_replay")
    srv = make_server("127.0.0.1", 0, a.app, threaded=True)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        hdr = {"X-BD-Token": mt} if mt else {}
        on = _client().post("/api/dev/feature_flag_set",
                            json={"name": "request_capture", "value": True}, headers=hdr)
        ids = [i["id"] for i in rr.list_recent(50) if i.get("path") == "/api/dev/feature_flag_set"]
        sess = a._session_create("pair_redeem")
        sc, h = _session_client(a, sess)
        off = sc.post("/api/dev/feature_flag_set", json={"name": "request_capture", "value": False},
                      headers=h, environ_base=REMOTE)
        direct = sc.post("/api/dev/feature_flag_set", json={"name": "request_capture", "value": True},
                         headers=h, environ_base=REMOTE)
        assert on.status_code == 200 and ids and off.status_code == 200 and direct.status_code == 403, \
            f"RCR-SETUP P6 on={on.status_code} ids={len(ids)} off={off.status_code} direct={direct.status_code}"
        rp = sc.post(f"/api/dev/request_replay/{ids[0]}",
                     json={"base_url": f"http://127.0.0.1:{srv.server_port}"},
                     headers=h, environ_base=REMOTE)
        return rp.get_json() or {}, ff.is_on("request_capture"), ids[0], a
    finally:
        srv.shutdown()


def test_pair_session_replay_cannot_turn_capture_back_on(flags, monkeypatch):
    """F2 (R235-C P6, auth unset): the replay is refused (self target, no master
    token exists) and capture stays OFF."""
    out, on, _rid, _a = _replay_launder(monkeypatch, "")
    assert not on, f"RCR-REPLAY-LAUNDER capture back ON after a pair-session replay (status={out.get('replay_status')})"
    assert out.get("replay_status") is None and "master token" in str(out.get("error")), \
        f"RCR-REPLAY-SELF self-target replay not refused: status={out.get('replay_status')}"


def test_master_token_replay_of_capture_on_still_refused(flags, monkeypatch):
    """F2 control (auth set): a master-token caller may replay at this box, but the
    replayed request carries no credential and bears the marker: 401, stays OFF."""
    mt = _canary("mt")
    out, on, rid, a = _replay_launder(monkeypatch, mt)
    assert not on and out.get("replay_status") is None, \
        f"RCR-REPLAY-LAUNDER auth set: on={on} status={out.get('replay_status')}"
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", 0, a.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with _master_ctx(a, mt):
            res = _mod("request_replay").replay(rid, base_url=f"http://127.0.0.1:{srv.server_port}",
                                                timeout=5)
    finally:
        srv.shutdown()
    assert res.get("replay_status") == 401 and not _mod("feature_flags").is_on("request_capture"), \
        f"RCR-REPLAY-TOKEN master replay status={res.get('replay_status')} error={res.get('error')}"


def test_replay_marker_refuses_capture_flag_change(flags, monkeypatch, caplog):
    """F2a: a request bearing the replay marker never changes request_capture --
    even from loopback, which may otherwise turn it ON."""
    ff = _mod("feature_flags")
    _auth(monkeypatch, "")
    caplog.set_level(logging.WARNING, logger="bulk_downloader.feature_flags")
    c = _client()
    mk = {"X-BD-Replay": "1"}
    s_on = c.post("/api/dev/feature_flag_set", json={"name": "request_capture", "value": True}, headers=mk)
    on_after = ff.is_on("request_capture")
    plain = c.post("/api/dev/feature_flag_set", json={"name": "zz_mk", "value": True}, headers=mk)
    ctl = c.post("/api/dev/feature_flag_set", json={"name": "request_capture", "value": True})
    d = c.post("/api/dev/feature_flag_delete", json={"name": "request_capture"}, headers=mk)
    still = ff.is_on("request_capture")
    assert plain.status_code == 200 and ctl.status_code == 200, \
        f"RCR-CONTROL marker plain flag={plain.status_code} loopback flip={ctl.status_code}"
    assert s_on.status_code == 403 and not on_after, \
        f"RCR-REPLAY-MARKER-SET marked set -> {s_on.status_code} on={on_after}"
    assert d.status_code == 403 and still, f"RCR-REPLAY-MARKER-DEL marked delete -> {d.status_code} on={still}"
    refused = [ln for ln in caplog.text.splitlines()
               if "feature_flag_change" in ln and "result=refused-replay" in ln]
    assert len(refused) == 2, f"RCR-FLAG-LOG refused-replay audit lines={len(refused)}, want 2"


SELF_URLS = ["http://127.0.0.1:5000", "http://127.1:5000", "http://localhost:5000",
             "http://[::1]:5000", "http://0.0.0.0:5000", "http://2130706433:5000",
             "http://0x7f.1:5000", "http://a.localhost:5000", "http://LOCALHOST.:5000",
             "http://unresolvable-host.invalid:5000"]   # unresolvable = fail closed


def test_replay_to_this_box_needs_master_token(flags, monkeypatch):
    """F2b: a replay aimed at this box (any loopback spelling) needs the master token;
    a non-local target does not."""
    rr = _mod("request_replay")
    mt = _canary("mt")
    a = _auth(monkeypatch, mt)
    _mod("feature_flags").set_flag("request_capture", True)
    rid = rr.record(method="GET", path="/api/x", query="", status=200, request_headers={},
                    request_body="", response_body="", duration_ms=1.0)
    rx = _Receiver()
    try:
        bare = rr.replay(rid, base_url=rx.base, timeout=5)
        heard_bare = len(rx.seen)
        with a.app.test_request_context("/x", headers={"X-BD-Token": _canary("wrong")}):
            wrong = rr.replay(rid, base_url=rx.base, timeout=5)
        heard_wrong = len(rx.seen)
        with _master_ctx(a, mt):
            ok = rr.replay(rid, base_url=rx.base, timeout=5)
    finally:
        rx.close()
    assert heard_bare == 0 and heard_wrong == 0 and "master token" in str(bare.get("error")) \
        and "master token" in str(wrong.get("error")), \
        f"RCR-REPLAY-SELF self replay without the master token sent={heard_bare},{heard_wrong}"
    assert ok.get("ok") and len(rx.seen) == 1, f"RCR-CONTROL master-token replay to self -> {ok.get('replay_status')}"
    is_self = getattr(rr, "_targets_self", lambda u: False)
    not_self = [u for u in SELF_URLS if not is_self(u)]
    assert not not_self, f"RCR-SELF-TARGET loopback spellings read as remote: {not_self}"
    assert not is_self("http://192.0.2.1:5000"), "RCR-CONTROL TEST-NET address read as self"


def test_replay_redirect_to_this_box_needs_master_token(flags, monkeypatch):
    """F2b: a redirect is a new target; one pointing at this box needs the master
    token too (the first hop is treated as remote here)."""
    rr = _mod("request_replay")
    mt = _canary("mt")
    a = _auth(monkeypatch, mt)
    monkeypatch.setattr(rr, "_targets_self", lambda u: "localhost" in u, raising=False)
    _mod("feature_flags").set_flag("request_capture", True)
    inner = _Receiver()
    outer = _Receiver(redirect_to=inner.base.replace("127.0.0.1", "localhost"))
    rid = rr.record(method="GET", path="/api/x", query="", status=200, request_headers={},
                    request_body="", response_body="", duration_ms=1.0)
    try:
        bare = rr.replay(rid, base_url=outer.base, timeout=5)
        heard = len(inner.seen)
        with _master_ctx(a, mt):
            ok = rr.replay(rid, base_url=outer.base, timeout=5)
    finally:
        outer.close()
        inner.close()
    assert heard == 0 and "redirected" in str(bare.get("error")), \
        f"RCR-REPLAY-REDIRECT redirect to self followed without the token: inner={heard}"
    assert len(outer.seen) == 2 and ok.get("ok") and len(inner.seen) == 1, \
        f"RCR-CONTROL redirect hops outer={len(outer.seen)} inner={len(inner.seen)}"


def test_x_auth_headers_masked_x_auth_user_kept(flags):
    """A2: X-Auth, X-Auth-Token and any X-Auth-* are credential headers;
    X-Auth-User (a username) stays visible."""
    rr = _mod("request_replay")
    cr = _mod("capture_redact")
    _mod("feature_flags").set_flag("request_capture", True)
    names = ["X-Auth", "x-auth", "X-Auth-Token", "X-Auth-Foo", "X-AUTH-KEY ", "X-Auth-Session"]
    cans = {n: _canary("ha") for n in names}
    user = _canary("hu")
    hdrs = dict(cans)
    hdrs["X-Auth-User"] = user
    rid = rr.record(method="GET", path="/api/x", query="", status=200, request_headers=hdrs,
                    request_body="", response_body="", duration_ms=1.0)
    e = json.dumps(rr.get(rid) or {})
    kept = [n for n, v in cans.items() if e.count(v)]
    unmasked = [n for n in names if not cr.is_secret_header(n)]
    assert e.count(user) == 1 and not cr.is_secret_header("X-Auth-User"), "RCR-X-AUTH-USER X-Auth-User masked"
    assert not kept and not unmasked, f"RCR-X-AUTH kept={kept} not-in-class={unmasked}"


def test_short_credential_body_keys_masked(flags):
    """A3: pwd / pin / otp / api-key / x-api-key in JSON, form and multipart, '-' and
    '_' alike, any case."""
    rr = _mod("request_replay")
    _mod("feature_flags").set_flag("request_capture", True)
    keys = ["pwd", "PIN", "Otp", "api-key", "API_KEY", "x-api-key", "X_Api_Key"]
    plain = _canary("pl")
    leaks = {}
    for k in keys:
        cn = _canary("bk")
        bnd = "bnd" + secrets.token_hex(4)
        bodies = {
            "json": json.dumps({"outer": {k: cn}, "keep": plain}),
            "form": f"{k}={cn}&keep={plain}",
            "multipart": (f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{cn}\r\n"
                          f"--{bnd}--\r\n"),
        }
        for kind, body in bodies.items():
            rid = rr.record(method="POST", path="/api/x", query="", status=200, request_headers={},
                            request_body=body, response_body="", duration_ms=1.0)
            e = json.dumps(rr.get(rid) or {})
            if e.count(cn) or (kind != "multipart" and not e.count(plain)):
                leaks[f"{k}/{kind}"] = {"hit": e.count(cn), "plain": e.count(plain)}
    assert not leaks, f"RCR-SHORT-KEYS {len(leaks)}: {leaks}"


def test_replay_refuses_masked_body_any_encoding(flags):
    """A4: a body holding the redaction marker raw or URL-encoded (either hex case,
    double-encoded) is never replayed."""
    rr = _mod("request_replay")
    bodies = ["a=1&b=[redacted]", "a=1&b=%5Bredacted%5D", "a=1&b=%5bredacted%5d",
              "a=1&b=%255Bredacted%255D", "a=1&b=%5Bredacted]"]
    rx = _Receiver()
    sent = {}
    try:
        for i, b in enumerate(bodies):
            with rr._lock:
                rr._requests.append({"id": f"zzA4{i}", "method": "POST", "path": "/api/x",
                                     "query": "", "status": 200, "request_body": b,
                                     "body_withheld": False})
            out = rr.replay(f"zzA4{i}", base_url=rx.base, timeout=5)
            if out.get("ok") or "masked" not in str(out.get("error")):
                sent[i] = out.get("replay_status")
        with rr._lock:
            rr._requests.append({"id": "zzA4c", "method": "POST", "path": "/api/x", "query": "",
                                 "status": 200, "request_body": "a=1&b=redacted",
                                 "body_withheld": False})
        ctl = rr.replay("zzA4c", base_url=rx.base, timeout=5)
    finally:
        rx.close()
    assert "masked" not in str(ctl.get("error")), "RCR-CONTROL unmasked body read as masked"
    assert not sent and not rx.seen, f"RCR-MASKED-REPLAY masked bodies replayed: {sent} sent={len(rx.seen)}"
