"""dl95-porndoe-1-live-1: the generic consent "OK" never clicks "Manage cookies".

Live on test2 (lane e1432a60, 06:34:50Z, /watch/pd9v8b2a8c9s): the journal
says "consent: cleared via button:has-text('OK')", and the app's screenshot
shows a "Customize Consent Preferences" layer with a "SAVE MY PREFERENCES"
button over the player. The player never loaded, so the job went needs_review.

Measured on the page from its HOSTS-O1528 host (.183, plain Chromium): the
page has no "OK" button. ``:has-text`` is a case-insensitive SUBSTRING match,
and both "Manage cookies" buttons (the age modal's aside and the footer's)
contain "ok". Once the age/consent modal has been agreed (the app's persistent
profile), the only visible nominee is the footer's "Manage cookies". Clicking
it OPENS the preferences layer that then blocks the player.

The cases run ``interstitial.clear_gates`` against real Chromium pages served
from loopback, the same shape as the dl95-hoopladigital-3 age tests.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bulk_downloader import interstitial

BD_GATE_SCOPE = "module"

_PREFS = """<div class="prefs" style="display:none">
  <p>Customize Consent Preferences</p>
  <button class="age-button-agree" onclick="this.closest('.prefs').style.display='none'">SAVE MY PREFERENCES</button>
</div>"""

# The scene page once the age/consent modal was agreed (persistent profile).
_AGREED = f"""<!doctype html><html><body>
<div class="player"><video></video></div>
{_PREFS}
<footer><button class="-footer-group-link"
  onclick="document.querySelector('.prefs').style.display='block'">Manage cookies</button></footer>
</body></html>"""

# The site itself put the preferences layer up.
_PREFS_OPEN = _AGREED.replace('class="prefs" style="display:none"', 'class="prefs"')

_OK_BANNER = """<!doctype html><html><body>
<div class="cookie-bar"><p>We use cookies.</p>
  <button onclick="this.closest('.cookie-bar').remove()">OK</button></div>
</body></html>"""


class _Server:
    def __init__(self, pages):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = pages.get(self.path.split("?")[0], "<html><body>other</body></html>")
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def site():
    s = _Server({"/watch/agreed": _AGREED, "/watch/prefs": _PREFS_OPEN, "/ok": _OK_BANNER})
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def page():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            yield browser.new_page()
        finally:
            browser.close()


def _clear(page, url):
    page.goto(url)
    return interstitial.clear_gates(page, url=url, sleep=lambda _s: page.wait_for_timeout(200))


def test_manage_cookies_is_never_clicked_as_the_consent_ok(site, page):
    url = f"{site.base}/watch/agreed"
    page.goto(url)
    # Precondition: the substring selector DOES nominate "Manage cookies".
    assert page.locator("button:has-text('OK')").first.inner_text() == "Manage cookies"
    result = _clear(page, url)
    assert not page.locator(".prefs").is_visible(), (
        f"DL95_PORNDOE_PREFS_OPENED_BY_CONSENT_OK: {result}")
    assert not [m for m in result if m.startswith("consent:")], result


def test_a_preferences_layer_the_site_shows_is_saved_away(site, page):
    url = f"{site.base}/watch/prefs"
    result = _clear(page, url)
    assert not page.locator(".prefs").is_visible(), (
        f"DL95_PORNDOE_PREFS_LAYER_LEFT_OVER_THE_PLAYER: {result}")
    assert "consent: cleared via button:has-text('Save my preferences')" in result, result


def test_control_a_real_ok_consent_button_is_still_cleared(site, page):
    url = f"{site.base}/ok"
    result = _clear(page, url)
    assert [m for m in result if m.startswith("consent: cleared via")], result
    assert page.locator(".cookie-bar").count() == 0
