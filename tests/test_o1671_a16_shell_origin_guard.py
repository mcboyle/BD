"""O1671 AUDIT-16 (HIGH SEC): the cockpit web-shell origin guard trusted a
non-loopback request whenever its Referer host:port equalled its Host header.
Both are client-controlled, so a remote non-browser client reached
/cockpit/api/shell/* (arbitrary commands) by sending two matching headers.

tools.cockpit_shell.shell_open is stubbed in every case: the shell is default-on
and a test that reached the real handler would spawn a pty.
"""
import pytest

BD_GATE_SCOPE = "module"

_REMOTE = {"REMOTE_ADDR": "10.0.0.99"}   # a non-loopback network client
_FORGED = {"Host": "x:1", "Referer": "http://x:1/"}


@pytest.fixture
def opened(monkeypatch):
    from tools import cockpit_shell as sh
    calls = []

    def _stub_open():
        calls.append(1)
        return {"ok": True, "session": "stub-a16"}

    monkeypatch.setattr(sh, "shell_open", _stub_open)
    return calls


def _bp_client():
    from flask import Flask
    from tools.cockpit_console import bp
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


def test_forged_host_referer_cannot_open_shell(opened):
    r = _bp_client().post("/cockpit/api/shell/open", environ_base=_REMOTE,
                          headers=_FORGED, json={})
    assert r.status_code == 403, (
        f"A16: remote Host==Referer-only request reached the shell "
        f"(status {r.status_code}, shell_open calls {len(opened)})")
    assert opened == []


def test_forged_host_referer_refused_through_host_app(opened):
    # The real wiring: bulk_downloader.app registers tools.cockpit_console's bp,
    # behind the host app's own before_request hooks (no session -> CSRF skips).
    from bulk_downloader.app import app
    r = app.test_client().post("/cockpit/api/shell/open", environ_base=_REMOTE,
                               headers=_FORGED, json={})
    assert r.status_code == 403, (
        f"A16: remote Host==Referer-only request reached the shell via the host "
        f"app (status {r.status_code}, shell_open calls {len(opened)})")
    assert opened == []


def test_loopback_still_opens_shell(opened):
    # Positive control: the standalone cockpit's 127.0.0.1 path still opens the
    # shell for its own browser page. O1807 R2: loopback needs an exact same-origin Origin (test client Host: localhost).
    r = _bp_client().post("/cockpit/api/shell/open", headers={"Origin": "http://localhost"}, json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert opened == [1]


# ---- ruling B (operator, O1671 a16) as directed by PM order O1672 a16 r2 (lens D5 BOUNCE):
# a valid session always; POSTs add X-CSRF-Token + a same-origin signal (Origin or
# Sec-Fetch-Site); GETs over plain HTTP carry neither, so Referer must name Host (or be
# absent: the SameSite=Lax session cookie is the same-site proof). Present cross-site refuses. ----
_HOST = {"Host": "bd.local:5555"}


def _session():
    from bulk_downloader import app as host
    sess = host._session_create(source="csrf_bootstrap")
    return host.app, sess, host._csrf_token_for(sess)


def _ui_post(path, extra=None, token=True, cookie=True, full=False):
    # full=False: the bare blueprint, so a 403 is the shell guard's own (the host
    # app's _check_csrf would otherwise refuse some of these first).
    app, sess, tok = _session()
    c = app.test_client() if full else _bp_client()
    if cookie:  # scoped to the Host the request names (the jar defaults to "localhost")
        c.set_cookie("bd_session", sess, domain="bd.local")
    h = dict(_HOST, **{"Sec-Fetch-Site": "same-origin"})
    if token:
        h["X-CSRF-Token"] = tok
    h.update(extra or {})
    h = {k: v for k, v in h.items() if v is not None}
    return c.post(path, environ_base=_REMOTE, headers=h, json={})


@pytest.mark.parametrize("full", [False, True])
def test_same_origin_ui_session_opens_shell(opened, full):
    r = _ui_post("/cockpit/api/shell/open", full=full)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert opened == [1]


def test_same_origin_ui_by_origin_without_fetch_metadata(opened):
    r = _ui_post("/cockpit/api/shell/open",
                 {"Sec-Fetch-Site": None, "Origin": "http://bd.local:5555"})
    assert r.status_code == 200, r.get_data(as_text=True)


# What a browser sends over plain HTTP (the deployed transport, lens a16 BOUNCE): no
# Sec-Fetch-*, and a same-origin GET carries no Origin -- cookie, Host and Referer only.
_REF = "http://bd.local:5555/cockpit"


def _http_get(path, referer=_REF, cookie=True, extra=None):
    app, sess, _ = _session()
    c = app.test_client()
    if cookie:
        c.set_cookie("bd_session", sess, domain="bd.local")
    h = dict(_HOST, **({"Referer": referer} if referer else {}))
    h.update(extra or {})
    return c.get(path, environ_base=_REMOTE, headers=h)


def test_lens_d5_http_get_probe(opened):
    # The lens's own probe (harness-work/O1671/lens-bd-worker-D5/a16/test_lens_a16_http_probe.py
    # ::test_http_browser_get_status), verbatim headers: the positive control for the bounce.
    r = _http_get("/cockpit/api/shell/status")
    assert r.status_code == 200, f"LENS-A16-HTTP-GET-REFUSED: {r.status_code} {r.get_data(as_text=True)[:120]}"


def test_http_ui_poll_get_admitted(opened):
    # The handler itself answers an unknown session with 403 "no such shell session";
    # what matters is that the origin guard let the request through to it.
    r = _http_get("/cockpit/api/shell/poll?session=x&offset=0")
    body = r.get_data(as_text=True)
    assert "shell endpoints require" not in body and "no such shell session" in body, body


def test_http_get_without_referer_admitted_on_lax_session(opened):
    r = _http_get("/cockpit/api/shell/status", referer=None)
    assert r.status_code == 200, r.get_data(as_text=True)


@pytest.mark.parametrize("why,kw", [
    ("no session cookie (Referer==Host only)", {"cookie": False}),
    ("cross-site Referer", {"referer": "http://evil.example/page"}),
    ("cross-site fetch metadata", {"extra": {"Sec-Fetch-Site": "cross-site"}}),
    ("cross-origin Origin", {"extra": {"Origin": "http://evil.example"}}),
])
def test_http_get_refusals(opened, why, kw):
    r = _http_get("/cockpit/api/shell/status", **kw)
    assert r.status_code == 403, f"A16 r2: GET with {why} must refuse, got {r.status_code}"


@pytest.mark.parametrize("why,kw", [
    ("no session cookie", {"cookie": False}),
    ("no X-CSRF-Token", {"token": False}),
    ("wrong X-CSRF-Token", {"extra": {"X-CSRF-Token": "forged"}}),
    ("cross-site fetch metadata", {"extra": {"Sec-Fetch-Site": "cross-site"}}),
    ("cross-origin Origin", {"extra": {"Origin": "http://evil.example"}}),
    ("POST without Origin or Sec-Fetch-Site", {"extra": {"Sec-Fetch-Site": None}}),
])
def test_each_missing_signal_refuses(opened, why, kw):
    r = _ui_post("/cockpit/api/shell/open", **kw)
    assert r.status_code == 403, f"A16 B: {why} must refuse, got {r.status_code}"
    assert opened == [], f"A16 B: {why} reached shell_open"
