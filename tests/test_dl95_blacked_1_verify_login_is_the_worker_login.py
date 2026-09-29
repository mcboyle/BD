"""dl95-blacked-1: Actions > Verify login must judge a login the way a worker does.

O1513 on test2 (harness-work/UIUX-20260928/download-95/A5-A/RESULT-login.md#L1):
Verify login on blacked answered "Headless replay failed: login submitted but
URL didn't transition to success_url. Final URL:
https://login.vixen.com/i/blacked/login?circle=true" in 5.1s.

``verify_login_replay`` claims to prove "that workers can do it
automatically", but its fresh-login step was a second, stripped copy of the
login (``_attempt_headless_fill_submit``): DOMContentLoaded + 1.5s, then the
URL is judged.  The worker's ``do_login`` learned blacked's post-submit shape
live (row 722): an accepted submit stays on ``/i/<brand>/login?...`` while a
``wait-redirect`` script moves the browser to the members host seconds later
(it also clears post-submit challenges and launches the G23 browser profile,
none of which the copy had).  So Verify failed logins the worker completes.

The fixture below is that shell.  PRECONDITION: the worker's own ``do_login``
completes it.  RED: verify reports the blacked signature on the same site.
The negative control keeps a login the site REFUSES a failure.
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

_GOOD_PASSWORD = "right-horse"

_LOGIN_HTML = b"""<html><body><h1>Sign in</h1>
<form method="GET" action="/submit">
  <input name="username" type="text">
  <input name="password" type="password">
  <button type="submit">Log in</button>
</form></body></html>"""

# blacked's accepted-submit shell: the login URL (with a query), #password left
# in the DOM off-viewport, and a wait-redirect script that moves on later.
_SHELL_HTML = b"""<html><body><p>Signing you in...</p>
<input id="password" type="password" style="position:absolute;left:-9999px">
<script src="/i/brand/wait-redirect.js"></script></body></html>"""

_WAIT_REDIRECT_JS = b"setTimeout(function(){location.href='/members';}, 3000);"

_MEMBERS_HTML = b"<html><body><h1>members area</h1></body></html>"


def _site():
    class _H(http.server.BaseHTTPRequestHandler):
        def _send(self, status, body=b"", ctype="text/html", headers=()):
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            authed = "auth_token=ok" in (self.headers.get("Cookie") or "")
            if path == "/submit":
                if ("password=" + _GOOD_PASSWORD) in query:
                    self._send(302, headers=(
                        ("Location", "/login?circle=true"),
                        ("Set-Cookie", "auth_token=ok; Path=/")))
                else:
                    self._send(302, headers=(("Location", "/login"),))
            elif path == "/login" and authed and "circle=true" in query:
                self._send(200, _SHELL_HTML)
            elif path == "/i/brand/wait-redirect.js":
                self._send(200, _WAIT_REDIRECT_JS, "application/javascript")
            elif path.startswith("/members") and authed:
                self._send(200, _MEMBERS_HTML)
            else:
                self._send(200, _LOGIN_HTML)

        def log_message(self, *a):
            pass
    return _H


@contextlib.contextmanager
def _serving():
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _site())
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d" % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def _require_playwright():
    try:
        import playwright  # noqa: F401
    except ImportError:
        pytest.skip("playwright not installed -- this check cannot run here, "
                    "which is not the same as passing")


def _headless_browsers(monkeypatch):
    """do_login launches HEADED and verify launches the configured backend;
    there is no display here, so both get plain headless chromium. Only the
    window changes -- every assertion is about navigation and cookies."""
    from playwright.sync_api import sync_playwright
    from bulk_downloader import cloak

    def _launch(*, headless=True, args=None, config=None, **kw):
        pw = sync_playwright().start()
        try:
            return pw.chromium.launch(headless=True), pw, "playwright-test"
        except Exception:
            pw.stop()   # rule 45: never orphan a started pw
            raise

    def _persistent(*, user_data_dir, headless=True, args=None,
                    user_agent=None, config=None, **kw):
        pw = sync_playwright().start()
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir), headless=True)
            return ctx, pw, "playwright-test"
        except Exception:
            pw.stop()
            raise

    monkeypatch.setattr(cloak, "launch_browser", _launch)
    monkeypatch.setattr(cloak, "open_persistent_context", _persistent)
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)


def _config(base, password, tmp_path):
    return {"login_url": base + "/login", "username": "member",
            "password": password, "success_url": "/members", "wait": 1,
            "use_stealth": False, "use_stealth_library": False,
            "login_evidence_dir": str(tmp_path / "evidence")}


@pytest.mark.capture_serial
def test_precondition_the_worker_login_completes_the_wait_redirect_shell(
        monkeypatch, tmp_path):
    _require_playwright()
    _headless_browsers(monkeypatch)
    from bulk_downloader.login import do_login
    with _serving() as base:
        ok, why, cookies = do_login(_config(base, _GOOD_PASSWORD, tmp_path),
                                    allow_manual_takeover=False)
    assert ok is True, why
    assert any(c.get("name") == "auth_token" for c in cookies), cookies


@pytest.mark.capture_serial
def test_verify_login_passes_a_login_the_worker_completes(monkeypatch, tmp_path):
    _require_playwright()
    _headless_browsers(monkeypatch)
    from bulk_downloader.login import verify_login_replay
    with _serving() as base:
        res = verify_login_replay(_config(base, _GOOD_PASSWORD, tmp_path),
                                  str(tmp_path / "profile"),
                                  member_url=base + "/members/latest")
    assert res["replay_ok"] is True, (
        "DL95-BLACKED-1: verify failed a login the worker completes: %r"
        % res.get("replay_error"))
    assert res["replay_method"] == "fresh_login", res
    # The worker's session reached verify's own context: the member-only page
    # (which serves the login form without auth_token) opened as a member.
    assert res["member_probe_ok"] is True, res.get("member_probe_error")


@pytest.mark.capture_serial
def test_negative_verify_still_fails_a_login_the_site_refuses(monkeypatch, tmp_path):
    _require_playwright()
    _headless_browsers(monkeypatch)
    from bulk_downloader.login import verify_login_replay
    with _serving() as base:
        res = verify_login_replay(_config(base, "wrong-password", tmp_path),
                                  str(tmp_path / "profile"),
                                  member_url=base + "/members/latest")
    assert res["replay_ok"] is False, res
    assert res["replay_error"], res
    assert res["member_probe_ok"] is None, res
