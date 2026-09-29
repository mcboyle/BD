"""dl95-scrolller-2 -- an SPA login modal that is not mounted until clicked.

Measured on test2 (scrolller, 2026-09-29T02:32:27Z): app login returned
"Couldn't find username field: could not fill username; tried 25 selectors;
skipped 2 search box(es)".  scrolller.com is a public SPA whose header carries
``<button><svg/><div>Login</div></button>``; the login form does not exist in
the DOM until that button is clicked (tests/corpus/recognizer/scroller.cap.json
has the Search input and the Login button, and no password input).  No
``login_trigger`` is configured for the site, so the row 373 trigger path never
ran and the only text input on the page was the search box.

The fixture mirrors that shape without a live request.  Row 373's contract is
kept: a hidden-but-mounted form with no configured trigger is still zero clicks
(tests/test_row373_login_trigger.py pins that), and an ambiguous page (two
visible login controls) is not guessed at.
"""

from __future__ import annotations

import contextlib
import http.server
import threading
from urllib.parse import parse_qs, urlsplit

import pytest


BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.capture_serial


def _spa_html(*, login_controls: int = 1) -> bytes:
    buttons = "\n".join(
        '<button class="nav-login" onclick="openLogin()"><svg width="10" '
        'height="10"></svg><div class="title">Login</div></button>'
        for _ in range(login_controls)
    )
    return f"""<!doctype html>
<html><body>
<script>
window.loginTriggerFired = 0;
function openLogin() {{
  window.loginTriggerFired += 1;
  if (document.getElementById('loginModal')) return;
  // Mount asynchronously, like a React portal after a state update.
  setTimeout(function () {{
    document.body.insertAdjacentHTML('beforeend',
      '<div id="loginModal" role="dialog"><form onsubmit="submitLogin(event)">'
      + '<input name="username" type="text" placeholder="Username">'
      + '<input name="password" type="password" placeholder="Password">'
      + '<button id="submit" type="submit">Login</button></form></div>');
  }}, 50);
}}
function submitLogin(event) {{
  event.preventDefault();
  window.location.href = '/members?trigger_fired=' + window.loginTriggerFired;
}}
</script>
<nav>
  <form role="search"><input type="text" placeholder="Search" autocomplete="off"></form>
  <button aria-label="Enable NSFW filter">NSFW</button>
  {buttons}
</nav>
<main><a href="/r/some_gallery">some gallery</a></main>
</body></html>""".encode("utf-8")


def _handler(page_html: bytes):
    class Handler(http.server.BaseHTTPRequestHandler):
        requests: list[str] = []

        def do_GET(self):
            type(self).requests.append(self.path)
            body = (
                b"<html><body><h1>members area</h1></body></html>"
                if self.path.startswith("/members")
                else page_html
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    return Handler


@contextlib.contextmanager
def _serving(handler_cls):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _submitted_trigger_counts(handler_cls) -> list[int]:
    counts = []
    for path in handler_cls.requests:
        parsed = urlsplit(path)
        if parsed.path != "/members":
            continue
        raw = parse_qs(parsed.query).get("trigger_fired", [])
        assert len(raw) == 1, f"submit did not carry one trigger count: {path!r}"
        counts.append(int(raw[0]))
    return counts


def _headless_launch(monkeypatch):
    from playwright.sync_api import sync_playwright
    from bulk_downloader import cloak

    def launch_for_test(*, headless=True, args=None, config=None, **kwargs):
        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception:
            # Rule 45: never orphan a started playwright on launch failure.
            playwright.stop()
            raise
        return browser, playwright, "playwright-test"

    monkeypatch.setattr(cloak, "launch_browser", launch_for_test)


def _do_login(monkeypatch, base_url, *, user_field="input[name='username']"):
    from bulk_downloader.login_impl import submit as submit_impl

    _headless_launch(monkeypatch)
    # Two generic fallbacks: the real field's, and the one that matches
    # scrolller's header search (it sits in a <form>).  Walking all of them
    # would add minutes to the RED without exercising a different behavior.
    monkeypatch.setattr(submit_impl, "USER_FIELD_FALLBACKS", [
        "input[name='username']", "form input[type='text']"])
    monkeypatch.setattr(submit_impl, "PASS_FIELD_FALLBACKS", [])
    monkeypatch.setattr(submit_impl, "SUBMIT_FALLBACKS", [])
    monkeypatch.setattr(submit_impl, "_try_check_remember_me", lambda page: False)
    monkeypatch.setattr(submit_impl.time, "sleep", lambda seconds: None)
    config = {
        "login_url": base_url + "/",
        "username": "u",
        "password": "p",
        "user_field": user_field,
        "pass_field": "input[name='password']",
        "submit_btn": "#submit",
        "success_url": "/members",
        "wait": 0,
        "use_real_chrome": False,
        "use_stealth": False,
        "use_stealth_library": False,
    }
    return submit_impl.do_login(config, allow_manual_takeover=False)


# "" is the measured scrolller shape: no user_field configured, so the
# generic fallbacks -- which match the header search box -- decide whether a
# login field is already visible.
@pytest.mark.parametrize("user_field", ["input[name='username']", ""])
def test_unmounted_spa_login_modal_is_opened_by_its_sole_login_control(
    monkeypatch, user_field
):
    handler = _handler(_spa_html())
    with _serving(handler) as base_url:
        ok, reason, _cookies = _do_login(
            monkeypatch, base_url, user_field=user_field)

    assert _submitted_trigger_counts(handler) == [1], (
        "dl95-scrolller-2: the SPA's Login control was not clicked, so the "
        f"login modal never mounted; do_login said {reason!r}"
    )
    assert ok is True, reason


def test_two_visible_login_controls_are_not_guessed_at(monkeypatch):
    handler = _handler(_spa_html(login_controls=2))
    with _serving(handler) as base_url:
        ok, reason, _cookies = _do_login(monkeypatch, base_url)

    assert ok is False, reason
    assert _submitted_trigger_counts(handler) == []


def test_auto_trigger_helper_skips_a_page_with_a_mounted_password_field():
    from playwright.sync_api import sync_playwright
    from bulk_downloader.login_impl._common import _fire_login_trigger_if_needed

    html = (
        "<nav><button onclick='window.clicked=(window.clicked||0)+1'>Log in"
        "</button></nav><form style='display:none'><input name='username'>"
        "<input type='password'></form>"
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(html)
            result = _fire_login_trigger_if_needed(
                page, None, ["input[name='username']"])
            clicked = page.evaluate("window.clicked || 0")
        finally:
            browser.close()
    assert result == (False, False, "")
    assert clicked == 0


# cx1 R1: opacity:0 passes the box, display and visibility checks, but no
# operator sees the control -- on itself or on an ancestor it is not "the
# page's sole visible Login control".  The visible twin must still open once.
@pytest.mark.parametrize("wrap, clicks", [
    ("<button onclick='window.clicked=(window.clicked||0)+1'>Login</button>", 1),
    ("<button style='opacity:0' "
     "onclick='window.clicked=(window.clicked||0)+1'>Login</button>", 0),
    ("<div style='opacity:0'><button "
     "onclick='window.clicked=(window.clicked||0)+1'>Login</button></div>", 0),
], ids=["visible", "transparent", "transparent-ancestor"])
def test_auto_trigger_ignores_a_transparent_login_control(wrap, clicks):
    from playwright.sync_api import sync_playwright
    from bulk_downloader.login_impl._common import _fire_login_trigger_if_needed

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(f"<nav>{wrap}</nav>")
            result = _fire_login_trigger_if_needed(
                page, None, ["input[name='username']"])
            clicked = page.evaluate("window.clicked || 0")
        finally:
            browser.close()
    assert clicked == clicks, f"CX1_INVISIBLE_LOGIN_CLICKED: {result!r}"
    assert result[1] is bool(clicks)
