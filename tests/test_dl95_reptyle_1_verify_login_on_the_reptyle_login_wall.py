"""dl95-reptyle-1: Actions > Verify login must pass reptyle's real login wall.

O1513 on test2 (harness-work/UIUX-20260928/download-95/A5-A/RESULT-login.md#L1,
A5-A/reptyle/rp1/result.json): with the operator's working credential,
Actions > Login PASSED (Turnstile clicked, submit, auth.reptyle.com ->
app.reptyle.com), yet Verify login answered "Headless replay failed: login
submitted but URL didn't transition to success_url. Final URL:
https://auth.reptyle.com/oauth/login?referer=spa".  The stripped fill+submit
copy that Verify used posted the form with an EMPTY cf-turnstile-response and
judged the URL 1.5s later; the site answered with its login wall again.

The page served here is the recorded LIVE login wall
(tests/fixtures/row455/reptyle_live_login_wall.html, captured at
https://auth.reptyle.com/oauth/login?referer=spa).  Only two things change:
the form action and <base> say http, not https (a loopback fixture server has
no TLS), and a stand-in for Cloudflare's checkbox widget
fills cf-turnstile-response a moment after the widget box is clicked (live, the
widget sits in a closed shadow root in the capture's empty <div>).  The hosts
auth.reptyle.com and app.reptyle.com resolve to this server through Chromium's
--host-resolver-rules; every other host fails to resolve, so nothing leaves
the machine.  No real credential is used.

PRECONDITION: the worker's do_login completes the wall.  RED (pre-dbcfa0980
Verify): reptyle's live signature.  NEGATIVE CONTROL: a refused password still
fails Verify.
"""
from __future__ import annotations

import contextlib
import http.server
import os
import pathlib
import socketserver
import sys
import threading
import urllib.parse

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

BD_GATE_SCOPE = "module"

_WALL = REPO / "tests" / "fixtures" / "row455" / "reptyle_live_login_wall.html"
_LIVE_ACTION = b'action="https://auth.reptyle.com/oauth/login-user"'
_LIVE_BASE = b'<base href="https://auth.reptyle.com/">'
_AUTH_HOST = "auth.reptyle.com"
_APP_HOST = "app.reptyle.com"
_LOGIN_URL = f"http://{_AUTH_HOST}/oauth/login?referer=spa"
_MEMBER_URL = f"http://{_APP_HOST}/movies/31597"
_USER = "member@example.invalid"
_GOOD_PASSWORD = "right-horse"

# Stand-in for the checkbox-mode widget: the capture keeps its host <div>
# (empty) beside the token field; live, clicking it yields the token seconds
# later (app log: "response in 6.4s").
_WIDGET_JS = b"""<script>
(function(){
  var c = document.getElementById('cf-turnstile-container');
  var host = c.querySelector('div > div');
  host.style.cssText = 'display:block;width:300px;height:65px;';
  c.addEventListener('click', function(){
    setTimeout(function(){
      document.querySelector('input[name="cf-turnstile-response"]').value =
        'fixture-turnstile-token';
    }, 1500);
  });
})();
</script></body>"""

_MEMBER_HTML = (b"<html><head><title>Reptyle</title></head><body>"
                b"<nav><a href='/movies'>Movies</a> <a href='/account'>Account</a>"
                b" <a href='/logout'>Logout</a></nav><h1>Latest movies</h1>"
                b"</body></html>")


def _wall_html():
    raw = _WALL.read_bytes()
    assert raw.count(_LIVE_ACTION) == 1, "recorded wall no longer has its form"
    assert b'name="cf-turnstile-response"' in raw, "recorded wall lost its token field"
    assert raw.count(_LIVE_BASE) == 1, "recorded wall no longer has its <base>"
    raw = raw.replace(_LIVE_ACTION, b'action="/oauth/login-user"')
    raw = raw.replace(_LIVE_BASE, b'<base href="http://auth.reptyle.com/">')
    assert raw.count(b"</body>") == 1
    return raw.replace(b"</body>", _WIDGET_JS)


def _site(wall, posts):
    class _H(http.server.BaseHTTPRequestHandler):
        def _send(self, status, body=b"", headers=()):
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _host(self):
            return (self.headers.get("Host") or "").split(":")[0].lower()

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            form = urllib.parse.parse_qs(self.rfile.read(n).decode())
            path = self.path.partition("?")[0]
            if self._host() != _AUTH_HOST or path != "/oauth/login-user":
                self._send(404)
                return
            token = (form.get("cf-turnstile-response") or [""])[0]
            posts.append({"token": bool(token)})
            if ((form.get("email") or [""])[0] == _USER
                    and (form.get("password") or [""])[0] == _GOOD_PASSWORD
                    and token):
                self._send(302, headers=(
                    ("Location", f"http://{_APP_HOST}/oauth/callback?code=fixture"),
                    ("Set-Cookie", "auth_sso=ok; Path=/")))
            else:
                # The site's answer to a refused post: its login wall again.
                self._send(302, headers=(("Location", "/oauth/login?referer=spa"),))

        def do_GET(self):
            path = self.path.partition("?")[0]
            host = self._host()
            member = "rp_session=ok" in (self.headers.get("Cookie") or "")
            if host == _AUTH_HOST and path == "/oauth/login":
                self._send(200, wall)
            elif host == _APP_HOST and path == "/oauth/callback":
                self._send(302, headers=(
                    ("Location", f"http://{_APP_HOST}/"),
                    ("Set-Cookie", "rp_session=ok; Path=/")))
            elif host == _APP_HOST and member:
                self._send(200, _MEMBER_HTML)
            elif host == _APP_HOST:
                self._send(302, headers=(("Location", _LOGIN_URL),))
            else:
                self._send(404)

        def log_message(self, *a):
            pass
    return _H


@contextlib.contextmanager
def _serving(posts):
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0),
                                            _site(_wall_html(), posts))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def _require_playwright():
    try:
        import playwright  # noqa: F401
    except ImportError:
        pytest.skip("playwright not installed -- this check cannot run here, "
                    "which is not the same as passing")


def _headless_browsers(monkeypatch, port):
    """do_login launches HEADED and Verify launches the configured backend;
    both get plain headless chromium whose resolver sends the two reptyle
    hosts to this server and fails every other host."""
    from playwright.sync_api import sync_playwright

    from bulk_downloader import cloak

    rules = (f"--host-resolver-rules=MAP {_AUTH_HOST} 127.0.0.1:{port},"
             f"MAP {_APP_HOST} 127.0.0.1:{port},MAP * ~NOTFOUND")
    args = ["--no-sandbox", rules]

    def _launch(*, headless=True, config=None, **kw):
        pw = sync_playwright().start()
        try:
            return pw.chromium.launch(headless=True, args=args), pw, "playwright-test"
        except Exception:
            pw.stop()   # rule 45: never orphan a started pw
            raise

    def _persistent(*, user_data_dir, headless=True, user_agent=None,
                    config=None, **kw):
        pw = sync_playwright().start()
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir), headless=True, args=args)
            return ctx, pw, "playwright-test"
        except Exception:
            pw.stop()
            raise

    monkeypatch.setattr(cloak, "launch_browser", _launch)
    monkeypatch.setattr(cloak, "open_persistent_context", _persistent)
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)


def _config(password, tmp_path):
    return {"login_url": _LOGIN_URL, "username": _USER,
            "password": password, "success_url": f"http://{_APP_HOST}/",
            "wait": 1, "use_real_chrome": False,
            "use_stealth": False, "use_stealth_library": False,
            "login_evidence_dir": str(tmp_path / "evidence")}


@pytest.mark.capture_serial
def test_precondition_the_worker_login_completes_the_recorded_wall(
        monkeypatch, tmp_path):
    _require_playwright()
    posts = []
    from bulk_downloader.login import do_login
    with _serving(posts) as port:
        _headless_browsers(monkeypatch, port)
        ok, why, cookies = do_login(_config(_GOOD_PASSWORD, tmp_path),
                                    allow_manual_takeover=False)
    assert ok is True, why
    assert posts and posts[-1]["token"] is True, posts
    assert any(c.get("name") == "rp_session" for c in cookies), cookies


@pytest.mark.capture_serial
def test_verify_login_passes_the_reptyle_wall_the_worker_completes(
        monkeypatch, tmp_path):
    _require_playwright()
    posts = []
    from bulk_downloader.login import verify_login_replay
    with _serving(posts) as port:
        _headless_browsers(monkeypatch, port)
        res = verify_login_replay(_config(_GOOD_PASSWORD, tmp_path),
                                  str(tmp_path / "profile"),
                                  member_url=_MEMBER_URL)
    assert res["replay_ok"] is True, (
        "DL95-REPTYLE-1: Verify failed the reptyle login the worker "
        f"completes: {res.get('replay_error')!r} (posts: {posts!r})")
    assert res["replay_method"] == "fresh_login", res
    # The worker's app.reptyle.com session reached Verify's own profile.
    assert res["member_probe_ok"] is True, res.get("member_probe_error")


@pytest.mark.capture_serial
def test_negative_verify_still_fails_a_password_the_site_refuses(
        monkeypatch, tmp_path):
    _require_playwright()
    posts = []
    from bulk_downloader.login import verify_login_replay
    with _serving(posts) as port:
        _headless_browsers(monkeypatch, port)
        res = verify_login_replay(_config("stale-password", tmp_path),
                                  str(tmp_path / "profile"),
                                  member_url=_MEMBER_URL)
    assert res["replay_ok"] is False, res
    assert res["replay_error"], res
    assert res["member_probe_ok"] is None, res
