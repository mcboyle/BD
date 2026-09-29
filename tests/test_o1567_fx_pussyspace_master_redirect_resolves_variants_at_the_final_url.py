"""O1567 fx-pussyspace live verify (results/test6/pussyspace.md; app log 21:20:07Z "hls failed: ffmpeg_failed -- ffmpeg
exited 8; last stderr: [https] HTTP error 403 Forbidden").

pussyspace's player asks /reversebuffer?u64hash=... for its HLS master; that URL answers 301 to the CDN master
(hls-cdn77.others-cdn.com/<token>/hls.m3u8), whose variants are RELATIVE ("hls-1080p.m3u8"). ``_spa_hls_ranked_variant``
read the master text through the page's fetch (which follows the redirect) but resolved the relative variant against the
PRE-redirect URL, so ffmpeg was handed https://<site>/hls-1080p.m3u8 -> 403. Relative URIs resolve against the URL the
playlist was actually served from.

Hermetic: a 127.0.0.1 site with /rb -> 301 -> /cdn/tok/master.m3u8, headless Chromium.
"""
from __future__ import annotations

import http.server
import threading

import pytest

from bulk_downloader import runner_extractors, spa_media_extract

BD_GATE_SCOPE = "module"

MASTER = (b"#EXTM3U\n"
          b"#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=763904,RESOLUTION=854x480,NAME=\"480p\"\nhls-480p.m3u8\n"
          b"#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=2763904,RESOLUTION=1920x1080,NAME=\"1080p\"\nhls-1080p.m3u8\n")
DIRECT_MASTER = MASTER   # served with no redirect at /plain/master.m3u8 (control)


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/rb"):
                self.send_response(301)
                self.send_header("Location", "/cdn/tok/master.m3u8")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            routes = {"/cdn/tok/master.m3u8": (200, "application/vnd.apple.mpegurl", MASTER),
                      "/plain/master.m3u8": (200, "application/vnd.apple.mpegurl", DIRECT_MASTER),
                      "/": (200, "text/html", b"<html><body>scene</body></html>")}
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
def page(browser, site):
    ctx = browser.new_context()
    try:
        p = ctx.new_page()
        p.goto(site + "/", wait_until="domcontentloaded")
        yield p
    finally:
        ctx.close()


class _R(runner_extractors.ExtractorsMixin):
    def __init__(self):
        self.events = []

    def log_event(self, kind, msg, url=""):
        self.events.append((kind, msg))


def test_a_redirected_master_resolves_its_relative_variant_at_the_final_url(site, page):
    r = _R()
    url, height, program = r._spa_hls_ranked_variant(page, "scene", f"{site}/rb?u64hash=abc", 1080, spa_media_extract)
    assert height == 1080 and program is None, (url, height, program)
    assert url == f"{site}/cdn/tok/hls-1080p.m3u8", url


def test_control_an_unredirected_master_still_resolves_at_its_own_url(site, page):
    r = _R()
    url, height, _ = r._spa_hls_ranked_variant(page, "scene", f"{site}/plain/master.m3u8", 1080, spa_media_extract)
    assert height == 1080 and url == f"{site}/plain/hls-1080p.m3u8", url
