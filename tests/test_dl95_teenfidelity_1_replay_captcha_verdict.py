"""dl95-teenfidelity-1 (harness-work/UIUX-20260928/download-95/A5-A/RESULT-login.md#L1, HIGH).

Measured (B4-B/teenfidelity/): the site's login form carries an invisible reCAPTCHA whose callback submits the form;
headless Chromium gets an image challenge and the form is never posted. captcha_relay.detect_captcha_in_page classified
that .g-recaptcha[data-sitekey][data-callback] mount as "turnstile" (the generic Turnstile heuristic runs first), so any
relay/resolver would pick the wrong solver.

Rebased gen 2 (on b73666ec): the replay-verdict half of gen 1 is dropped -- dl95-blacked-1 replaced the replay's own
fill+submit (and its no-transition verdict) with do_login, so that half has no target on this base.

Hermetic: 127.0.0.1 fixture pages shaped like the measured form (sitekey is a dummy), headless Chromium. No network, no credentials.
"""
from __future__ import annotations

import http.server
import threading

import pytest

from bulk_downloader import captcha_relay

BD_GATE_SCOPE = "module"

FORM = """<form class="spaced" method="POST" action="/login">
<input name="username" type="text"><input name="password" type="password">
{widget}
<button type="submit" class="button">Login</button></form>"""
PAGES = {
    # the measured shape: invisible reCAPTCHA v2, callback submits the form
    "/recaptcha-login": FORM.format(widget='<div class="g-recaptcha" data-sitekey="dummy-key" data-size="invisible" '
                                           'data-callback="_submitForm" data-badge="bottomright"></div>'),
    "/turnstile-login": FORM.format(widget='<div class="cf-turnstile" data-sitekey="dummy-key"></div>'),
    "/bare-mount-login": FORM.format(widget='<div data-sitekey="dummy-key" data-callback="cb"></div>'),
    "/hcaptcha-login": FORM.format(widget='<div class="h-captcha" data-sitekey="dummy-key" data-callback="cb"></div>'),
    "/plain-login": FORM.format(widget='<p class="help is-danger">These credentials do not match our records.</p>'),
}


@pytest.fixture(scope="module")
def server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = f"<!doctype html><html><body>{PAGES.get(self.path, 'nope')}</body></html>".encode()
            self.send_response(200 if self.path in PAGES else 404)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(5)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    b = None
    try:
        b = pw.chromium.launch(headless=True)
        yield b
    finally:
        if b is not None:
            b.close()
        pw.stop()


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    try:
        yield ctx.new_page()
    finally:
        ctx.close()


@pytest.mark.parametrize("path,expected", [
    ("/recaptcha-login", "recaptcha"),     # was "turnstile"
    ("/hcaptcha-login", "hcaptcha"),
    ("/turnstile-login", "turnstile"),     # control: a real Turnstile mount is unchanged
    ("/bare-mount-login", "turnstile"),    # control: the generic heuristic still fires for an unlabelled mount
    ("/plain-login", None),                # control: no widget, no claim
])
def test_captcha_type_is_the_widget_on_the_page(server, page, path, expected):
    page.goto(server + path, wait_until="domcontentloaded")
    assert captcha_relay.detect_captcha_in_page(page) == expected
