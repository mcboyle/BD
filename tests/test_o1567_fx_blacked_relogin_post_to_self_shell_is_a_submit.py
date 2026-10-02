"""O1567 fx-blacked-relogin: a submit answered by the wait-redirect shell is a submit.

bd4 live (harness-work/ISP-SPLIT-O1564/results/bd4/blacked.md, app log
20:26:59Z-20:28:08Z): the login form on login.vixen.com/i/blacked/login POSTs
to ITS OWN URL, so a good click on Login leaves ``page.url`` unchanged while
the site answers with the bare wait-redirect shell (redirect to
members.blacked.com several seconds later).  ``_moved()`` reads "no
navigation", the 8s poll expires, and the sweep fires the next methods --
``form.requestSubmit()`` and ``form.submit()`` -- into the accepted login: three
POSTs, the later two without the page script's Castle token and stale CSRF.
The site drops the session; the shell never redirects ("post-submit page did
not redirect within 30s"); every re-login ends in manual takeover.

The fixture is that site: POST-to-self, shell with a wait-redirect script that
redirects after 10s (> the 8s per-method poll), and a ledger that REJECTS any
second POST (clears the session).  RED: the sweep re-submits (POST count 3) and
the login fails.  GREEN: exactly one POST and the login completes.
Negative control: a wrong password is still refused after the sweep tried the
remaining methods (POST answered with the login form again, no shell).

Lens bd-worker-B16-B (REFUTE F1, takeover): the app's captured blacked DOM
(bd4 login_evidence manual-takeover-...-20260929T202808Z) ships
<script src="/i/blacked/wait-redirect"> on the LOGIN PAGE itself, right after
the form, so "a redirect script appeared" never fires live. The shapes below
put that script on the login page from the start ("script-on-login-page") and
cover a page-script login that sends the credentials by fetch() ("xhr").
The sweep now stops once a method has sent the credentials (a POST whose
body carries the password field), whatever the page shows.
"""
from __future__ import annotations

import contextlib
import http.server
import json
import os
import pathlib
import socketserver
import sys
import threading
from urllib.parse import parse_qs

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

BD_GATE_SCOPE = "module"

_GOOD_PASSWORD = "right-horse"

_LOGIN_HTML = b"""<html><body><h1>Members Area</h1>
<form id="login-form" method="POST" action="/login">
  <input id="username" name="username" type="text" placeholder="Email">
  <input id="password" name="password" type="password" placeholder="Password">
  <button id="submit-btn">Login</button>
</form></body></html>"""

# The live shape: the login page itself carries the wait-redirect script
# (it only moves the browser once the session exists).
_LOGIN_WITH_SCRIPT_HTML = _LOGIN_HTML.replace(
    b"</form></body>",
    b'</form><script src="/i/brand/wait-redirect.js"></script></body>')

# A page-script login: the button's handler sends the credentials by fetch()
# and the page moves on 10s later; the form itself is never submitted.
_LOGIN_XHR_HTML = _LOGIN_HTML.replace(b"</form></body>", b"""</form><script>
document.getElementById('login-form').addEventListener('submit', function (e) {
  e.preventDefault();
  fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username: document.getElementById('username').value,
                          password: document.getElementById('password').value})})
  .then(function (r) { if (r.ok) setTimeout(function () { location.href = '/members'; }, 10000); });
});</script></body>""")

# The live blacked shape (bd4 21:52Z + 22:20Z, captured DOM): onSubmit prevents the
# native submit, disables the button for 5s, awaits its Castle + reCAPTCHA v3 tokens
# (here: 20s, longer than two 8s method polls), appends them as hidden
# inputs and calls form.submit() itself. A POST without the token is refused.
_LOGIN_ASYNC_TOKEN_HTML = _LOGIN_HTML.replace(b"</form></body>", b"""</form><script>
var form = document.getElementById('login-form');
function onSubmit(event) {
  event.preventDefault();
  // live: disableSubmitButton() re-enables the button after 5s
  var btn = document.getElementById('submit-btn');
  btn.disabled = true;
  setTimeout(function () { btn.disabled = false; }, 5000);
  new Promise(function (ok) { setTimeout(function () { ok('tok-v3'); }, 20000); })
  .then(function (token) {
    var i = document.createElement('input');
    i.type = 'hidden'; i.name = 'recaptcha-token'; i.value = token;
    form.appendChild(i);
    form.submit();
  });
}
form.addEventListener('submit', onSubmit);
</script></body>""")

# Lens bd-worker-B3-B (REFUTE F1 of 7d1a4998): a React-style SPA modal login. onSubmit
# prevents the native submit, fetch()es JSON under a key that is NOT the input's name
# ("pass"), and removes the modal without navigating. The form is consumed: that is the
# dl95-scrolller-3 SPA case, not a page script holding the submit.
_LOGIN_SPA_MODAL_HTML = _LOGIN_HTML.replace(b"</form></body>", b"""</form><script>
document.getElementById('login-form').addEventListener('submit', function (e) {
  e.preventDefault();
  fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({user: document.getElementById('username').value,
                          pass: document.getElementById('password').value})})
  .then(function (r) { if (r.ok) document.getElementById('login-form').remove(); });
});</script></body>""")

_LOGIN_PAGES = {"shell-post": _LOGIN_HTML,
                "spa-modal": _LOGIN_SPA_MODAL_HTML,
                "async-token": _LOGIN_ASYNC_TOKEN_HTML,
                "script-on-login-page": _LOGIN_WITH_SCRIPT_HTML,
                "xhr": _LOGIN_XHR_HTML}

# The accepted-submit shell: same URL, the form left in the DOM (off-screen),
# a wait-redirect script that moves the browser on 10s later.
_SHELL_HTML = b"""<html><body><p>Signing you in...</p>
<form id="login-form" method="POST" action="/login"
      style="position:absolute;left:-9999px">
  <input name="username" type="text">
  <input id="password" name="password" type="password">
</form>
<script src="/i/brand/wait-redirect.js"></script></body></html>"""

_WAIT_REDIRECT_JS = b"setTimeout(function(){location.href='/members';}, 10000);"
_WAIT_REDIRECT_IDLE_JS = b"/* no session yet: stay */"

_MEMBERS_HTML = b"<html><body><h1>members area</h1></body></html>"


def _site(posts, shape="shell-post"):
    login_html = _LOGIN_PAGES[shape]

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
            path = self.path.partition("?")[0]
            authed = "auth_token=ok" in (self.headers.get("Cookie") or "")
            if path == "/i/brand/wait-redirect.js":
                self._send(200, _WAIT_REDIRECT_JS if authed else _WAIT_REDIRECT_IDLE_JS,
                           "application/javascript")
            elif path.startswith("/members") and authed:
                self._send(200, _MEMBERS_HTML)
            else:
                self._send(200, login_html)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8", "replace")
            if self.path.partition("?")[0] == "/api/login":
                sent = json.loads(raw or "{}") or {}
                pw = sent.get("password", sent.get("pass", ""))
                posts.append(pw)
                if pw == _GOOD_PASSWORD and len(posts) == 1:
                    self._send(200, b"{}", "application/json", headers=(
                        ("Set-Cookie", "auth_token=ok; Path=/"),))
                else:
                    self._send(401, b"{}", "application/json", headers=(
                        ("Set-Cookie", "auth_token=; Path=/; Max-Age=0"),))
                return
            form = parse_qs(raw)
            posts.append(form.get("password", [""])[0])
            good = form.get("password", [""])[0] == _GOOD_PASSWORD
            if shape == "async-token":
                # the site refuses a form sent without the page script's token
                good = good and form.get("recaptcha-token", [""])[0] == "tok-v3"
            if good and len(posts) == 1:
                self._send(200, _SHELL_HTML, headers=(
                    ("Set-Cookie", "auth_token=ok; Path=/"),))
            else:
                # a refused login, or a duplicate submit: the session goes
                self._send(200, _LOGIN_HTML, headers=(
                    ("Set-Cookie", "auth_token=; Path=/; Max-Age=0"),))

        def log_message(self, *a):
            pass
    return _H


@contextlib.contextmanager
def _serving(posts, shape="shell-post"):
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _site(posts, shape))
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


def _config(base, password, tmp_path):
    return {"login_url": base + "/login", "username": "member",
            "password": password, "success_url": "/members", "wait": 1,
            "use_stealth": False, "use_stealth_library": False,
            "login_evidence_dir": str(tmp_path / "evidence")}


@pytest.mark.capture_serial
@pytest.mark.parametrize("shape", ["shell-post", "script-on-login-page", "xhr",
                                   "async-token"])
def test_a_post_to_self_answered_by_the_shell_is_submitted_once(
        monkeypatch, tmp_path, shape):
    _headless_browsers(monkeypatch)
    from bulk_downloader.login import do_login
    posts = []
    with _serving(posts, shape) as base:
        ok, why, cookies = do_login(_config(base, _GOOD_PASSWORD, tmp_path),
                                    allow_manual_takeover=False)
    assert len(posts) == 1, (
        "O1567 fx-blacked-relogin: the sweep re-submitted a login the site had "
        "already accepted (shape %s, POST count %d)" % (shape, len(posts)))
    assert ok is True, why
    assert any(c.get("name") == "auth_token" and c.get("value") == "ok"
               for c in cookies), cookies


@pytest.mark.capture_serial
@pytest.mark.parametrize("shape", ["shell-post", "xhr", "async-token"])
def test_negative_a_refused_login_is_still_refused(monkeypatch, tmp_path, shape):
    _headless_browsers(monkeypatch)
    from bulk_downloader.login import do_login
    posts = []
    with _serving(posts, shape) as base:
        ok, why, cookies = do_login(_config(base, "wrong-password", tmp_path),
                                    allow_manual_takeover=False)
    assert ok is False, (why, cookies)
    assert len(posts) >= 1


def test_the_credential_post_detector_reads_names_not_values():
    from bulk_downloader.login_impl.submit import _carries_password_field
    assert _carries_password_field("username=a&password=x", "password")
    assert _carries_password_field('{"username":"a","password":"x"}', "password")
    assert _carries_password_field('Content-Disposition: form-data; name="password"', "password")
    # negative controls: another field that merely contains the name, no body, no name
    assert not _carries_password_field("newpassword=x&user=a", "password")
    assert not _carries_password_field("event=click&page=login", "password")
    assert not _carries_password_field(None, "password")
    assert not _carries_password_field("password=x", "")
    # O1634: an empty password field sent no credentials (row 722 hidden twin)
    assert not _carries_password_field("username=&password=", "password")
    assert not _carries_password_field("password=&username=a", "password")
    assert not _carries_password_field('{"username":"a","password":""}', "password")
    assert _carries_password_field('{"password": "x"}', "password")
    # r2 (lens rc-D2-D): json.dumps spacing, and an empty multipart part
    assert not _carries_password_field('{"password": ""}', "password")
    assert not _carries_password_field('{"password" : ""}', "password")
    assert not _carries_password_field('{"password": null}', "password")
    part = '--b\r\nContent-Disposition: form-data; name="password"\r\n\r\n%s\r\n--b--\r\n'
    assert not _carries_password_field(part % "", "password")
    assert _carries_password_field(part % "x", "password")


@pytest.mark.capture_serial
def test_an_spa_modal_login_that_consumed_its_form_is_not_a_held_submit(
        monkeypatch, tmp_path, capsys):
    """REFUTE F1 (lens bd-worker-B3-B): the hold wait must not pre-empt the
    SPA-consumed check. BASE and the fixed cut settle in ~50 s; the refuted cut
    waited 30 s more on a submit nobody was holding."""
    import time
    _headless_browsers(monkeypatch)
    from bulk_downloader.login import do_login
    posts = []
    t0 = time.time()
    with _serving(posts, "spa-modal") as base:
        do_login(_config(base, _GOOD_PASSWORD, tmp_path), allow_manual_takeover=False)
    took = time.time() - t0
    err = capsys.readouterr().err
    assert len(posts) == 1, posts
    assert "holds the submit" not in err, (
        "O1567_BLACKED_SPA_CONSUMED_READ_AS_HELD: an SPA login that removed its form "
        "was waited on as a held submit (%.1fs)" % took)
