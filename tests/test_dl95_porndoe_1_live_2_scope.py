"""dl95-porndoe-1-live-2: an age/consent layer that renders after the gate pass is cleared before the page-media read.

Live on test2 (lane 844de4c4, 07:34:50Z, /watch/pd9v8b2a8c9s) and reproduced on the site's host .183 in the app and with
the real SiteRunner._process_one outside it (fresh profile, no stored cookies): at the one clear_gates call (goto + wait
4 s) the page has NO age/consent layer -- 0 "I Agree" nominees, no modal text, screenshot pdrunner-at-gate.png. The
"Welcome to the World's Best HD Porn Site!" layer (Manage cookies | I AGREE | I disagree & exit) is up by the needs_review
screenshot, so the player never starts, the page-media fallback finds 0 media, and the job goes needs_review.
"I Agree" is already in interstitial.CONSENT; the gate pass ran before the layer existed.

Fix: _fallback_to_page_media clears gates again (same interstitial.clear_gates, same deny rules) while it waits for the
player's media. Hermetic: a 127.0.0.1 scene whose layer appears after load and whose player gets its src on I AGREE.
"""
from __future__ import annotations

import http.server
import threading

import pytest

from bulk_downloader import runner_transport, spa_media_extract

BD_GATE_SCOPE = "module"

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"P" * 2048
SCENE = b"""<!doctype html><html><body>
<video id="player" muted></video>
<div id="age" style="display:none;position:fixed;inset:0;background:#000">
  <p>Welcome to the World's Best HD Porn Site! This website contains age-restricted materials.</p>
  <button class="age-button-aside" onclick="document.body.dataset.managed='1'">Manage cookies</button>
  <button class="age-button-agree" onclick="document.getElementById('age').remove();
     document.getElementById('player').src='/media/pd9v8b2a8c9s-1080p.mp4'">I AGREE</button>
  <button class="age-button-aside" onclick="location.href='https://exit.invalid/'">I disagree &amp; exit</button>
</div>
<script>setTimeout(() => { document.getElementById('age').style.display = 'block'; }, 300);</script>
</body></html>"""
PLAIN = b"""<!doctype html><html><body>
<video id="player" muted src="/media/pd9v8b2a8c9s-1080p.mp4"></video></body></html>"""


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            routes = {"/watch/pd9v8b2a8c9s": (200, "text/html", SCENE), "/watch/plain": (200, "text/html", PLAIN),
                      "/media/pd9v8b2a8c9s-1080p.mp4": (200, "video/mp4", MP4)}
            code, ctype, body = routes.get(self.path, (404, "text/plain", b"nope"))
            self.send_response(code)
            self.send_header("Content-Type", ctype)
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


@pytest.fixture
def page():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


class _Runner(runner_transport.TransportMixin):
    _PAGE_MEDIA_WAIT_S = 3.0

    def __init__(self):
        self.calls, self.events = [], []
        self.config = {"min_resolution": 0}
        self.jobs = {}
        self._lock = threading.Lock()

    def log_event(self, kind, message="", **_kw):
        self.events.append((kind, message))

    def _try_spa_api_media_extractor(self, url, page, min_height=0, hold_below=False, **_kw):
        media = page.evaluate(spa_media_extract.PAGE_MEDIA_JS) or []
        self.calls.append({"url": url, "media": media})
        return any(m.endswith("-1080p.mp4") for m in media)


def test_a_layer_that_rendered_after_the_gate_pass_is_cleared_before_the_media_read(site, page):
    url = f"{site}/watch/pd9v8b2a8c9s"
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("button:has-text('I AGREE')", state="visible")   # the live state at the fallback
    r = _Runner()
    took = r._fallback_to_page_media(page, url, "clicked candidate fired no download")
    assert took is True and r.calls and r.calls[0]["media"], (
        f"DL95_PORNDOE_LATE_AGE_LAYER_LEFT_OVER_THE_PLAYER: calls={r.calls} events={r.events}")
    assert page.url == url
    assert page.evaluate("document.body.dataset.managed") is None, "Manage cookies must never be clicked"
    assert any(k == "gate" and "I Agree" in m for k, m in r.events), r.events


def test_control_a_page_without_a_layer_is_unchanged(site, page):
    url = f"{site}/watch/plain"
    page.goto(url, wait_until="domcontentloaded")
    r = _Runner()
    assert r._fallback_to_page_media(page, url, "x") is True
    assert not [e for e in r.events if e[0] == "gate"], r.events


class _PlayerRunner(_Runner):
    """The lane's dl95-porndoe-1 path: start the player, take its feature media."""
    _try_player_media_extractor = __import__(
        "bulk_downloader.runner_extractors", fromlist=["ExtractorsMixin"]).ExtractorsMixin._try_player_media_extractor

    def _try_spa_api_media_extractor(self, url, page, min_height=0, hold_below=False, page_media=None, **_kw):
        self.calls.append({"url": url, "media": list(page_media or [])})
        return bool(page_media)


def test_the_player_start_runs_after_a_late_layer_is_cleared(site, page, monkeypatch):
    url = f"{site}/watch/pd9v8b2a8c9s"
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("button:has-text('I AGREE')", state="visible")
    seen = []

    def start_player(pg, **_kw):   # the player start click can only land once the layer is gone
        covered = pg.locator("#age").count() > 0 and pg.locator("#age").is_visible()
        seen.append(covered)
        return [] if covered else [f"{site}/media/pd9v8b2a8c9s-1080p.mp4"]

    monkeypatch.setattr(spa_media_extract, "start_player_for_feature_media", start_player)
    r = _PlayerRunner()
    assert r._try_player_media_extractor(url, page) is True, (
        f"DL95_PORNDOE_PLAYER_STARTED_UNDER_THE_AGE_LAYER: covered={seen} events={r.events}")
    assert seen == [False] and r.calls and r.calls[0]["media"], (seen, r.calls)
    assert page.evaluate("document.body.dataset.managed") is None
