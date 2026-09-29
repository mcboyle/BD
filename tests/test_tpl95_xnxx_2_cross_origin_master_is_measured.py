"""tpl95-xnxx-2 (LIVE FAIL of tpl95-xnxx-1 on .95 4f9c4c6f, B10-B/tpl-xnxx/RESULT.md + log-0604-scrubbed.txt):
the xnxx HLS master stayed "?p" in the page-media rescue, so min_height dropped it and the job held "Best is 720p
(below 1080p)" while the master lists 1080p.

Cause, measured live (harness-work/FIX/tpl95-xnxx-2-bd-worker-B18-B/cors_probe.out): the master lives on
hls-cdn77.xnxx-cdn.com, cross-origin to www.xnxx.com, and answers with a wildcard CORS header. MANIFEST_TEXT_JS
fetched it with credentials 'include' only -- the browser refuses a credentialed read of a wildcard-CORS response
("TypeError: Failed to fetch") -- and returned ''. The same fetch with credentials 'omit' reads 200, 5 variants.

GREEN: MANIFEST_TEXT_JS tries 'include' first (same-origin / credentialed CDNs unchanged), then 'omit', then a plain
fetch, each only when the previous one failed. Real Chromium, two loopback origins, the real xnxx master text
(A1-A/tpl-xnxx/page-sources-0415.txt); no site, no network beyond 127.0.0.1.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bulk_downloader import runner_extractors, spa_media_extract

BD_GATE_SCOPE = "module"

# The live master body, variant order and attributes as captured (relative URIs, no CODECS).
XNXX_MASTER = (
    "#EXTM3U\n"
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=763904,RESOLUTION=854x480,NAME="480p"\nhls-480p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1327104,RESOLUTION=1280x720,NAME="720p"\nhls-720p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=2760704,RESOLUTION=1920x1080,NAME="1080p"\nhls-1080p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=423936,RESOLUTION=640x360,NAME="360p"\nhls-360p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=155648,RESOLUTION=444x250,NAME="250p"\nhls-250p.m3u8\n'
)


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class _Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><body>scene</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


def _cdn(cors):
    """cors: 'wildcard' (xnxx's CDN), 'credentialed' (echo origin + allow-credentials) or 'none'."""

    class _Cdn(BaseHTTPRequestHandler):
        def do_GET(self):
            body = XNXX_MASTER.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            if cors == "wildcard":
                self.send_header("Access-Control-Allow-Origin", "*")
            elif cors == "credentialed":
                self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "")
                self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a):
            pass

    return _Cdn


class _Runner(runner_extractors.ExtractorsMixin):
    site_id = "xnxx"

    def __init__(self):
        self.config = {"name": "xnxx"}
        self.events = []

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))


@pytest.fixture(scope="module")
def browser_page():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    page_srv = _serve(_Page)
    p = sync_playwright().start()
    try:
        br = p.chromium.launch()
        try:
            page = br.new_context().new_page()
            page.goto(f"http://127.0.0.1:{page_srv.server_address[1]}/video-hbwy3c6/scene")
            yield page
        finally:
            br.close()
    finally:
        p.stop()
        page_srv.shutdown()


def _master_url(cors):
    srv = _serve(_cdn(cors))
    # "localhost" vs the page's "127.0.0.1": a different origin, as hls-cdn77.xnxx-cdn.com is to www.xnxx.com.
    return srv, f"http://localhost:{srv.server_address[1]}/TOKEN/14f9d3a7/0/hls.m3u8"


def _measure(page, master):
    runner = _Runner()
    cands = [{"url": master, "label": "", "height": 0, "size": 0, "source": "page-media", "filename": ""}]
    runner._spa_measure_hls_masters(page, page.url, cands, spa_media_extract)
    return cands[0]


def test_a_wildcard_cors_master_is_read_and_labelled_by_its_tallest_variant(browser_page):
    srv, master = _master_url("wildcard")
    try:
        text = browser_page.evaluate(spa_media_extract.MANIFEST_TEXT_JS, master)
        assert "EXT-X-STREAM-INF" in (text or ""), (
            "TPL95_XNXX2_MASTER_UNREAD: the page could not read a cross-origin wildcard-CORS master "
            f"(xnxx hls-cdn77); got {text!r}")
        cand = _measure(browser_page, master)
    finally:
        srv.shutdown()
    assert cand["height"] == 1080 and cand["label"] == "1080p", cand
    pick = spa_media_extract.hls_variant_for(text, master, 1080)
    assert pick and pick["url"].endswith("/0/hls-1080p.m3u8") and pick["height"] == 1080, pick


def test_a_credentialed_cors_master_still_reads(browser_page):
    """Control: a CDN that allows credentials keeps working (include is still tried first)."""
    srv, master = _master_url("credentialed")
    try:
        cand = _measure(browser_page, master)
    finally:
        srv.shutdown()
    assert cand["height"] == 1080, cand


def test_a_master_the_page_may_not_read_stays_unknown(browser_page):
    """Negative control: no CORS grant at all -> nothing is read, the height stays unknown (never guessed)."""
    srv, master = _master_url("none")
    try:
        cand = _measure(browser_page, master)
    finally:
        srv.shutdown()
    assert int(cand.get("height") or 0) == 0, cand
