"""fx-wowgirls-relogin-root-success (O1567, operator override 2026-09-30; ORDER-HARDEN-T165.md wowgirls row).

test7 wowgirls (ddcde6ee) 06:10-06:14Z: session expired -> worker re-login. The app logged in on auth.wowgirls.com and the
site landed on venus.wowgirls.com/search/?query= -- a member page (it shows "Log out"). success_url is the ROOT
https://venus.wowgirls.com/, and row 722 made a root success URL match only the root page (bangbros: its login page sits on
the success host, so "any path" matched the login page). The member landing was judged "Expected URL contains ..., got
.../search/", the login handed off to a manual takeover, and every later worker re-login answered "manual login already in
progress" until the job dead-lettered: "Session expired -- re-login retries exhausted". No captcha was ever involved.

Contract: a root success URL on a host OTHER than the login host means "landed on the member host" -- any path there
counts (error-query landings still do not). A root success URL on the login host keeps the row-722 root-only rule.
"""
from __future__ import annotations

import contextlib
import http.server
import os
import pathlib
import socketserver
import sys
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

BD_GATE_SCOPE = "module"

AUTH_LOGIN = "https://auth.wowgirls.test/login"
MEMBER_ROOT = "https://venus.wowgirls.test/"


@pytest.mark.parametrize("final,expect", [
    ("https://venus.wowgirls.test/search/?query=", True),            # THE ROW (live landing)
    ("https://venus.wowgirls.test/film/a76bf6b9/stunned", True),
    ("https://venus.wowgirls.test/", True),
    ("https://venus.wowgirls.test/search/?error=1", False),          # an error landing is never success
    ("https://auth.wowgirls.test/login", False),                     # still on the login host
    ("https://auth.wowgirls.test/", False),
    ("https://www.wowgirls.test/", False),                           # another host
])
def test_a_root_success_url_on_another_host_than_the_login_matches_that_host(final, expect):
    from bulk_downloader.login_impl import replay
    assert replay.success_url_reached(MEMBER_ROOT, final, AUTH_LOGIN) is expect, (
        "O1567_WOWGIRLS_MEMBER_LANDING_REJECTED" if expect else "over-accepted", final)


@pytest.mark.parametrize("final,expect", [
    ("https://site-ma.bangbros.test/login", False),
    ("https://site-ma.bangbros.test/store", False),
    ("https://site-ma.bangbros.test/", True),
])
def test_control_row722_a_root_success_url_on_the_login_host_is_still_root_only(final, expect):
    from bulk_downloader.login_impl import replay
    assert replay.success_url_reached("https://site-ma.bangbros.test/", final,
                                      "https://site-ma.bangbros.test/login") is expect, final


# -- the live shape end to end: login on one host, member landing on another -------------------------------------

_LOGIN_HTML = b"""<html><body><h1>Member Login</h1>
<form id="login-form" method="post" action="/login">
  <input type="text" name="website_url" style="display:none">
  <input type="text" name="username" id="user-email" placeholder="E-Mail">
  <input type="password" name="password" id="user-password" placeholder="Password">
  <button type="submit">Get inside</button>
</form></body></html>"""

_MEMBER_HTML = (b"<html><head><title>Wow Girls</title></head><body><nav><a href='/'>Home</a> "
                b"<a href='/logout'>Log out</a></nav><form method='get' action='/search/'>"
                b"<input name='query'></form><h1>Search results</h1><p>member area</p></body></html>")
_ANON_HTML = b"<html><body><a href='/login'>Log in</a> <a href='/join'>Join now</a></body></html>"


def _auth(member_base_ref):
    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(_LOGIN_HTML)))
            self.end_headers()
            self.wfile.write(_LOGIN_HTML)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(302)
            # cookies ignore the port: the member host (same IP, other port) sees it
            self.send_header("Set-Cookie", "auth_token=ok; Path=/")
            self.send_header("Location", member_base_ref[0] + "/search/?query=")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass
    return _H


class _Member(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = _MEMBER_HTML if "auth_token=ok" in (self.headers.get("Cookie") or "") else _ANON_HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@contextlib.contextmanager
def _two_hosts():
    ref = [""]
    servers = [socketserver.ThreadingTCPServer(("127.0.0.1", 0), _auth(ref)),
               socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Member)]
    for s in servers:
        s.daemon_threads = True
        threading.Thread(target=s.serve_forever, daemon=True).start()
    auth, member = ("http://127.0.0.1:%d" % s.server_address[1] for s in servers)
    ref[0] = member
    try:
        yield auth, member
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()


def _headless_browsers(monkeypatch):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed -- this check cannot run here, "
                    "which is not the same as passing")
    from bulk_downloader import cloak

    def _launch(*, headless=True, args=None, config=None, **kw):
        pw = sync_playwright().start()
        try:
            return pw.chromium.launch(headless=True), pw, "playwright-test"
        except Exception:
            pw.stop()   # rule 45: never orphan a started pw
            raise

    monkeypatch.setattr(cloak, "launch_browser", _launch)
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)


@pytest.mark.capture_serial
def test_the_app_logs_in_when_the_site_lands_on_a_member_page_of_the_success_host(monkeypatch, tmp_path):
    _headless_browsers(monkeypatch)
    from bulk_downloader.login import do_login
    with _two_hosts() as (auth, member):
        ok, why, cookies = do_login({"login_url": auth + "/login", "username": "member", "password": "pw",
                                     "success_url": member + "/", "wait": 1, "use_stealth": False,
                                     "use_stealth_library": False,
                                     "login_evidence_dir": str(tmp_path / "evidence")},
                                    allow_manual_takeover=False)
    assert ok is True, (
        f"O1567_WOWGIRLS_MEMBER_LANDING_REJECTED: login landed on {member}/search/?query= (member page) "
        f"but the app said: {why}")
    assert any(c.get("name") == "auth_token" and c.get("value") == "ok" for c in cookies), cookies
