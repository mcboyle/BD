"""O1567 fx-manual-banner-iframe: the manual-login banner must stay out of iframes.

Live (wrk-191, o1513-a6-hustler1-live, shots/op-start-display.png): the app's
manual-login window injects its "finish logging in" banner with
ctx.add_init_script, which runs in EVERY frame. Inside the 300x65 Cloudflare
Turnstile iframe the banner replaced the widget (and padded the frame body by
52px), so the human who is asked to "solve any captcha" saw the banner where
the checkbox should be. The banner belongs to the top-level page only.
"""
BD_GATE_SCOPE = "module"

import http.server
import threading

import pytest

from bulk_downloader.login_impl.manual import _MANUAL_LOGIN_BANNER_JS

_TOP = (b"<html><body><h1>login</h1>"
        b"<iframe id=w src='http://localhost:%d/widget' width=300 height=65>"
        b"</iframe></body></html>")
_WIDGET = b"<html><body><input type=checkbox id=cb> Verify you are human</body></html>"


def _server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = _WIDGET if self.path.startswith("/widget") else (
                _TOP % self.server.server_port)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_banner_in_top_frame_but_not_in_cross_origin_iframe():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    srv = _server()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context()
            ctx.add_init_script(_MANUAL_LOGIN_BANNER_JS)
            page = ctx.new_page()
            page.goto("http://127.0.0.1:%d/top" % srv.server_port)  # O805 DP-12: loopback fixture, not a path build
            page.wait_for_selector("#bd-manual-banner", timeout=5000)
            child = page.frame_locator("#w")
            child.locator("#cb").wait_for(timeout=5000)
            page.wait_for_timeout(1500)  # banner re-install interval is 1s
            frames = [f for f in page.frames if f != page.main_frame]
            assert frames, "fixture broken: no child frame"
            in_child = frames[0].evaluate(
                "!!document.getElementById('bd-manual-banner')")
            pad = frames[0].evaluate("document.body.style.paddingTop")
            browser.close()
    finally:
        srv.shutdown()
    assert not in_child, "manual-login banner injected INTO the iframe (Turnstile widget covered)"
    assert pad in ("", None), f"iframe body padded by the banner: {pad!r}"
