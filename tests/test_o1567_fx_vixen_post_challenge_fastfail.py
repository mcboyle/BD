"""fx-vixen-post-challenge-fastfail (O1567, PM RULING 0716Z on Q-VIXEN-CHALLENGE-ROOT-A4-A.md).

bd4 blacked, T167 diagnostic login 07:10:46Z ("login trace:" journal lines):
  POST /i/blacked/login -> 307 -> POST /i/blacked/login/challenge -> 403 cf-mitigated=challenge
  after Turnstile: GET /i/blacked/login/challenge -> 404; the re-submit's POST is challenged again.
Cloudflare challenges the credential POST itself; passing it reloads as a GET and the body is gone. Clearing, re-entering,
re-submitting and the templated-login takeover fallback cannot log in -- they spent ~3 min and a second challenge.

Contract: once a top-level POST of the submit is answered 403 cf-mitigated=challenge, do_login stops with the distinct
settled-challenge-post outcome and the message "log in from a normal browser and import cookies" -- no Turnstile clear,
no re-submit -- and the runner opens no takeover for it.
"""
from __future__ import annotations

import contextlib
import http.server
import os
import pathlib
import socketserver
import sys
import threading
import time
from unittest import mock

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

BD_GATE_SCOPE = "module"

_LOGIN_HTML = b"""<html><body><h1>Members Area</h1>
<form id="login-form" method="POST" action="/login">
  <input id="username" name="username" type="text" placeholder="Email">
  <input id="password" name="password" type="password" placeholder="Password">
  <button id="submit-btn">Login</button>
</form></body></html>"""

# Cloudflare's interstitial, as served for the challenged POST (bd4 shots/a4a-blacked-T165-settled-0544Z.png).
_CF_HTML = b"""<html><head><title>Just a moment...</title></head><body>
<h1>login.example</h1><h2>Performing security verification</h2>
<p>This website uses a security service to protect against malicious bots.</p></body></html>"""


def _site(posts, challenge_header):
    class _H(http.server.BaseHTTPRequestHandler):
        def _send(self, status, body=b"", headers=()):
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.partition("?")[0] == "/login/challenge":
                self._send(404, b"<html><head><title>Not found</title></head><body>Not found</body></html>")
            else:
                self._send(200, _LOGIN_HTML)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            path = self.path.partition("?")[0]
            posts.append(path)
            if path == "/login":
                self._send(307, headers=(("Location", "/login/challenge"),))
            else:
                self._send(403, _CF_HTML,
                           headers=(("cf-mitigated", "challenge"),) if challenge_header else ())

        def log_message(self, *a):
            pass
    return _H


@contextlib.contextmanager
def _serving(posts, challenge_header):
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _site(posts, challenge_header))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d" % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


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


def _login(monkeypatch, tmp_path, capsys, challenge_header):
    _headless_browsers(monkeypatch)
    from bulk_downloader.login import do_login
    posts = []
    with _serving(posts, challenge_header) as base:
        result = do_login({"login_url": base + "/login", "username": "member", "password": "pw",
                           "success_url": "/members", "wait": 1, "use_stealth": False,
                           "use_stealth_library": False, "login_evidence_dir": str(tmp_path / "evidence")},
                          allow_manual_takeover=False)
    return result, posts, capsys.readouterr().err


@pytest.mark.capture_serial
def test_a_challenged_login_post_fails_fast_with_the_cookie_import_message(monkeypatch, tmp_path, capsys):
    (outcome, msg, cookies), posts, err = _login(monkeypatch, tmp_path, capsys, challenge_header=True)
    assert getattr(outcome, "status", None) == "settled-challenge-post", (
        "O1567_VIXEN_POST_CHALLENGE_NOT_FAST_FAILED: Cloudflare challenged the login POST but do_login settled as "
        f"{getattr(outcome, 'status', outcome)!r}: {msg}")
    assert not outcome and cookies == []
    assert "log in from a normal browser and import cookies" in msg, msg
    assert "POST" in msg and "/login/challenge" in msg and "cf-mitigated=challenge" in msg, msg
    # one submit, no clear/re-entry/re-submit
    assert posts == ["/login", "/login/challenge"], posts
    assert "post-submit cloudflare challenge page" not in err, "the Turnstile clear was still attempted"


@pytest.mark.capture_serial
def test_control_a_plain_403_on_the_post_is_not_a_cloudflare_post_challenge(monkeypatch, tmp_path, capsys):
    """Negative control: the same site without the cf-mitigated header keeps the old verdict path."""
    (outcome, msg, _cookies), posts, _err = _login(monkeypatch, tmp_path, capsys, challenge_header=False)
    assert not outcome
    assert getattr(outcome, "status", None) != "settled-challenge-post", msg
    assert "import cookies" not in str(msg), msg
    assert posts[:2] == ["/login", "/login/challenge"], posts


def test_the_runner_opens_no_takeover_for_a_challenged_login_post():
    from bulk_downloader.db import db_init
    from bulk_downloader.login_impl.replay import LoginOutcome
    from bulk_downloader.runner import SiteRunner
    db_init()
    LOGIN_POST_CHALLENGE_STATUS = "settled-challenge-post"   # the public status string (pinned)
    r = SiteRunner("testsite", {"login_url": "https://example.com/login", "username": "u", "password": "p",
                                "auto_teach_first_run": True,
                                "learned": {"login": {"user_field": ["#u"], "pass_field": ["#p"],
                                                      "submit_btn": ["#go"]}}})
    msg = (f"{LOGIN_POST_CHALLENGE_STATUS}: Cloudflare challenges this site's login POST -- "
           "log in from a normal browser and import cookies — NOT success")
    with mock.patch("bulk_downloader.runner_auth.do_login",
                    return_value=(LoginOutcome(LOGIN_POST_CHALLENGE_STATUS, False, None, ""), msg, [])), \
         mock.patch.object(r, "start_manual_login", return_value=(True, "manual window open")) as m_manual:
        r.login_async(allow_manual=True)
        t = getattr(r, "_login_thread", None)
        if t is not None:
            t.join(10)
        deadline = time.time() + 10
        while "import cookies" not in (r._login_status or "") and time.time() < deadline:
            time.sleep(0.1)
    assert not m_manual.called, "a takeover window was opened for a login Cloudflare challenges at the POST"
    assert "import cookies" in r._login_status, r._login_status
