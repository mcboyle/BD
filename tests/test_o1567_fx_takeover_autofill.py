"""fx-takeover-autofill (O1567): a manual-takeover window fills the site's login form when the form
APPEARS, not only at open.

test2 reddit 2026-09-30 02:17Z: the takeover opened on reddit's reCAPTCHA page ("Prove your
humanity"); the one-shot autofill ran there, and after the human passed the check the Log In form
sat EMPTY -- only the running app holds the unlocked vault, so nobody could fill it.

Real ManualLoginSession, headless Playwright Chromium, a local HTTP server playing the site:
  /login          a verification page (a text answer box, no password field) that moves on by itself
  /login?form=1   the login form
The pages beacon field LENGTHS (never values) to the server; the test reads them there. Contract:
the form is filled after it appears; the verification box is never touched; nothing is submitted;
a field the operator cleared is not refilled; the password never reaches stderr.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BD_GATE_SCOPE = "module"

_USER = "probe-user"
_SECRET = "probe-secret-7Qz9"

_CAPTCHA_PAGE = """<html><body><h1>Prove your humanity</h1>
<form id="verify"><input type="text" name="captcha_answer" id="captcha"></form>
<script>
setInterval(() => navigator.sendBeacon('/state', JSON.stringify(
  {page: 'captcha', captcha: document.getElementById('captcha').value.length})), 200);
setTimeout(() => { location.href = '/login?form=1'; }, 3000);
</script></body></html>"""

_FORM_PAGE = """<html><body><h1>Log In</h1>
<form id="login" method="post" action="/login">
<input type="text" name="username" autocomplete="username" id="u">
<input type="password" name="password" autocomplete="current-password" id="p">
<button type="submit">Log In</button></form>
<script>
let cleared = false;
setInterval(() => {
  const u = document.getElementById('u'), p = document.getElementById('p');
  navigator.sendBeacon('/state', JSON.stringify(
    {page: 'form', u: u.value.length, p: p.value.length, userOk: u.value === '""" + _USER + """'}));
  if (!cleared && p.value.length) {
    // after reporting the fill, the operator deletes the password (e.g. to type another one)
    cleared = true; p.value = '';
    navigator.sendBeacon('/state', JSON.stringify({page: 'form', event: 'operator-cleared'}));
  }
}, 200);
</script></body></html>"""


class _Site:
    def __init__(self):
        self.events = []
        self.posts = 0
        site = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = _FORM_PAGE if "form=1" in self.path else _CAPTCHA_PAGE
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body.encode())

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(n)
                if self.path.startswith("/state"):
                    site.events.append(json.loads(data or b"{}"))
                else:
                    site.posts += 1
                self.send_response(204)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/login"

    def form_states(self):
        return [e for e in list(self.events) if e.get("page") == "form" and "u" in e]


def test_takeover_fills_the_login_form_that_appears_after_a_verification_page(capfd):
    from bulk_downloader.login_impl import manual

    site = _Site()
    cfg = {"name": "takeover-autofill-probe", "login_url": site.url,
           "username": _USER, "password": _SECRET,
           "use_real_chrome": False, "use_stealth": False, "browser_backend": "playwright"}
    session = manual.ManualLoginSession(cfg, "", headless=True)
    try:
        assert session.ready, f"premise: the takeover browser did not open ({session.error})"
        deadline = time.time() + 20
        while time.time() < deadline and not any(s["u"] and s["p"] for s in site.form_states()):
            time.sleep(0.25)
        states = site.form_states()
        assert states, f"premise: the login form never loaded; events={site.events[-5:]}"
        assert any(s["u"] and s["p"] and s["userOk"] for s in states), (
            "takeover autofill: the login form appeared after the verification page and was never "
            f"filled from the vault -- last form state {states[-1]}")
        # the operator cleared the password: it must stay cleared (fill each field once)
        assert any(e.get("event") == "operator-cleared" for e in site.events), site.events[-5:]
        time.sleep(5)
        tail = site.form_states()[-5:]
        assert all(s["p"] == 0 for s in tail), f"takeover autofill overwrote the operator's edit: {tail}"
    finally:
        session.cancel()
        site.server.shutdown()
    captcha = [e["captcha"] for e in site.events if e.get("page") == "captcha"]
    assert captcha and max(captcha) == 0, (
        f"takeover autofill typed into the verification page's answer box (lengths {sorted(set(captcha))})")
    assert site.posts == 0, f"takeover autofill submitted the form ({site.posts} POST)"
    err = capfd.readouterr().err
    assert _SECRET not in err, "the password reached stderr"
