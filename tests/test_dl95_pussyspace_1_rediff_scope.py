"""dl95-pussyspace-1-rediff: ONE page-media fallback in _do_download that keeps the brazzers trailer filter.

Lane 819b62889 (dl95-pussyspace-1) added _fallback_to_page_media at _do_download's click-miss exit, where main (T147,
tpl95-site-ma-brazzers-1) already falls back to page media through click_miss_candidates. Stacked, the lane's fallback
(min_height path, no trailer filter) runs first and takes the trailer brazzers refuses (brazzers lens R1;
REDIFF-NEEDED-bd-integrator-A.md). Re-diff: the one fallback returns to the scene, waits for the player's media and
calls the extractor as a click miss at both needs_review exits (nav-gate rejection and no download event).

Hermetic: a 127.0.0.1 pussyspace-shaped scene in headless Chromium and the REAL Row 722 extractor; only the byte
transfer is recorded.
"""
from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from bulk_downloader import runner_extractors, runner_transport

BD_GATE_SCOPE = "module"

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"S" * 2048
TRAILER_SCENE = b"""<!doctype html><html><body>
<video id="player" muted autoplay src="/trailers/6167743/trailer_1080p.mp4"></video>
<a href="/1080p/">1080p</a></body></html>"""
FULL_SCENE = TRAILER_SCENE.replace(b"/trailers/6167743/trailer_1080p.mp4", b"/media/scene-6167743-1080p.mp4")
LISTING = b"<!doctype html><html><body><h1>1080p videos</h1></body></html>"


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            routes = {"/vid-6167743-trailer/": (200, "text/html", TRAILER_SCENE),
                      "/vid-6167743-full/": (200, "text/html", FULL_SCENE),
                      "/1080p/": (200, "text/html", LISTING),
                      "/trailers/6167743/trailer_1080p.mp4": (200, "video/mp4", MP4),
                      "/media/scene-6167743-1080p.mp4": (200, "video/mp4", MP4)}
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


class _Runner(runner_transport.TransportMixin, runner_extractors.ExtractorsMixin):
    _PAGE_MEDIA_WAIT_S = 3.0
    site_id = "dl95ps1r"

    def __init__(self, tmp_path):
        self.config = {"name": "dl95-ps1r", "download_dir": str(tmp_path), "min_resolution": 1080}
        self.jobs = {}
        self._lock = threading.Lock()
        self.updates, self.transfers = [], []
        self._spa_api_capture = None

    def _update_job(self, url, status, message="", **extra):
        self.updates.append((status, message))

    def log_event(self, *a, **k):
        pass

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        self.transfers.append(file_url)
        Path(output_path).write_bytes(MP4)
        return True

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


def _after_dud_click(site, page, tmp_path, monkeypatch, scene_path):
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    scene = f"{site}{scene_path}"
    page.goto(scene, wait_until="domcontentloaded")
    page.click("a[href='/1080p/']")                # the dud winner: a category listing
    page.wait_for_load_state("domcontentloaded")
    r = _Runner(tmp_path)
    return r, r._fallback_to_page_media(page, scene, "winner rejected: navigation URL")


def test_a_trailer_is_never_taken_by_the_pussyspace_fallback(site, page, tmp_path, monkeypatch):
    r, took = _after_dud_click(site, page, tmp_path, monkeypatch, "/vid-6167743-trailer/")
    assert took is False and r.transfers == [], (
        f"DL95_PUSSYSPACE_REDIFF_TOOK_THE_TRAILER: transfers={r.transfers} updates={r.updates}")


def test_control_the_same_scene_with_its_feature_file_is_taken(site, page, tmp_path, monkeypatch):
    r, took = _after_dud_click(site, page, tmp_path, monkeypatch, "/vid-6167743-full/")
    assert took is True, r.updates
    assert len(r.transfers) == 1 and r.transfers[0].endswith("/media/scene-6167743-1080p.mp4"), r.transfers
