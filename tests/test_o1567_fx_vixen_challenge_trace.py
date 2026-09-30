"""fx-vixen-challenge-trace (O1567, PM ruling 0604Z on Q-VIXEN-CHALLENGE-ROOT-A4-A.md, option B).

bd4 blacked (T163 04:24Z, T165 05:41Z, operator takeover 04:38Z): the submit lands on
login.vixen.com/i/blacked/login/challenge, Cloudflare challenges it, and the cleared page is "Not found". The
journal cannot say WHICH request Cloudflare challenged (the credential POST, or the GET after a 302) nor what the
origin answered: Chrome History records both shapes identically, and the app logs no response of the submit.

The login now logs every top-level document response from the submit on: method, origin+path (no query), status,
the Location target (origin+path), and Cloudflare's ``cf-mitigated`` header. Never a query string, cookie or body.

Fixture = that site: POST /login answers 302 -> /login/challenge?<token> with a session cookie; the challenge GET
answers 403 ``cf-mitigated: challenge``. The login page carries an iframe (a subframe document: never traced).
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

_PASSWORD = "pw-never-in-the-trace"
_QUERY_SECRET = "qtok-never-in-the-trace"
_COOKIE_SECRET = "sess-never-in-the-trace"

_LOGIN_HTML = b"""<html><body><h1>Members Area</h1>
<form id="login-form" method="POST" action="/login">
  <input id="username" name="username" type="text" placeholder="Email">
  <input id="password" name="password" type="password" placeholder="Password">
  <button id="submit-btn">Login</button>
</form><iframe src="/sub-frame"></iframe></body></html>"""


def _site(hits):
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
            path = self.path.partition("?")[0]
            hits.append(path)
            if path == "/login/challenge":
                self._send(403, b"<html><body>blocked</body></html>",
                           headers=(("cf-mitigated", "challenge"),))
            elif path == "/sub-frame":
                self._send(200, b"<html><body>frame</body></html>")
            else:
                self._send(200, _LOGIN_HTML)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._send(302, headers=(("Location", f"/login/challenge?_gl={_QUERY_SECRET}"),
                                     ("Set-Cookie", f"sess={_COOKIE_SECRET}; Path=/")))

        def log_message(self, *a):
            pass
    return _H


@contextlib.contextmanager
def _serving(hits):
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _site(hits))
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


@pytest.mark.capture_serial
def test_the_login_journal_names_the_challenged_request_and_the_origin_answer(
        monkeypatch, tmp_path, capsys):
    _headless_browsers(monkeypatch)
    from bulk_downloader.login import do_login
    hits = []
    with _serving(hits) as base:
        do_login({"login_url": base + "/login", "username": "member", "password": _PASSWORD,
                  "success_url": "/members", "wait": 1, "use_stealth": False,
                  "use_stealth_library": False, "login_evidence_dir": str(tmp_path / "evidence")},
                 allow_manual_takeover=False)
    err = capsys.readouterr().err
    trace = [ln.split("login trace: ", 1)[1] for ln in err.splitlines() if "login trace: " in ln]
    assert f"POST {base}/login -> 302 Location {base}/login/challenge" in trace, (
        "O1567_VIXEN_CHALLENGE_TRACE_MISSING: the submit's document responses are not in the login journal "
        f"-- the next budgeted blacked login cannot say which request Cloudflare challenged. trace={trace}")
    assert f"GET {base}/login/challenge -> 403 cf-mitigated=challenge" in trace, trace
    # the subframe document is not a navigation of the login page (it WAS loaded: positive control)
    assert "/sub-frame" in hits, hits
    assert not any("/sub-frame" in t for t in trace), trace
    # never a query string, cookie or credential
    for secret in (_PASSWORD, _QUERY_SECRET, _COOKIE_SECRET, "_gl="):
        assert not any(secret in t for t in trace), (secret, trace)
