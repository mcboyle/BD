"""fx-pornhoarder-login-trigger (O1567) -- the sole Login control is an
``<a href="/login/">`` whose click never lands.

Measured on bd1 (pornhoarder, 2026-09-29T20:26Z): the app's login returned
"login form is hidden behind a trigger: could not click the page's Login
control (auto): Locator.click: Timeout 2500ms exceeded ... waiting for
locator("button, a, [role='button']").nth(13)".  pornhoarder.tv's home page has
no password input; its only visible Log In control is ``<a href="/login/">`` in
the off-canvas aside nav (index 13), and /login/ carries the real form
(username / password).  Playwright's actionability check (an element
intercepting pointer events) times the click out, and the helper stopped
there, so the seat fell through to manual login on a page that was one
navigation from the form.

The fixture covers the link with a full-screen overlay, the same failure class
without a live request.  A control with no ``href`` has nowhere to navigate and
keeps the historical (needed, not fired) result.
"""

from __future__ import annotations

import contextlib
import http.server
import threading

import pytest


BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.capture_serial

_OVERLAY = ('<div style="position:fixed;left:0;top:0;width:100%;height:100%;'
            'background:transparent;z-index:9999"></div>')


def _home(control: str) -> bytes:
    return (f"<!doctype html><html><body><nav>{control}</nav>{_OVERLAY}"
            "</body></html>").encode("utf-8")


_LOGIN_PAGE = (b"<!doctype html><html><body><form>"
               b"<input type='text' name='username'>"
               b"<input type='password' name='password'></form></body></html>")


@contextlib.contextmanager
def _serving(home_html: bytes):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = _LOGIN_PAGE if self.path.startswith("/login") else home_html
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _fire(base_url):
    from playwright.sync_api import sync_playwright
    from bulk_downloader.login_impl._common import _fire_auto_login_trigger

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(base_url + "/")
            result = _fire_auto_login_trigger(page)
            page.wait_for_timeout(300)
            return result, page.url, page.locator("input[type='password']").count()
        finally:
            browser.close()


def test_login_link_whose_click_is_intercepted_is_followed_by_its_href():
    with _serving(_home('<a href="/login/">Log In</a>')) as base_url:
        (needed, fired, detail), url, passwords = _fire(base_url)

    assert (needed, fired) == (True, True), (
        "fx-pornhoarder-login-trigger: the Login link was not followed; "
        f"helper said {detail!r}")
    assert url.endswith("/login/"), url
    assert passwords == 1


def test_uncovered_login_link_is_still_clicked_not_navigated():
    home = b'<nav><a href="/login/">Log In</a></nav>'
    with _serving(home) as base_url:
        (needed, fired, detail), url, passwords = _fire(base_url)

    assert (needed, fired) == (True, True), detail
    assert "clicked" in detail and url.endswith("/login/")


def test_login_button_without_href_whose_click_is_intercepted_stays_unfired():
    with _serving(_home("<button>Log In</button>")) as base_url:
        (needed, fired, detail), url, passwords = _fire(base_url)

    assert (needed, fired) == (True, False), detail
    assert "could not click" in detail
    assert not url.endswith("/login/") and passwords == 0
