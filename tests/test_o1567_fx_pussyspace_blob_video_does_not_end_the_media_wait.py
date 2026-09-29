"""O1567 fx-pussyspace (results/test6/pussyspace.md; live journal 20:45:01Z "spa-api: no download-like options in 0
captured API record(s) and 1 page media URL(s)").

pussyspace's player is MSE: the <video> gets a blob: URL at once, and the HLS manifest is fetched a moment later. The
dl95-pussyspace-1 fallback (``_fallback_to_page_media``) waits for "the player's media requests" but stopped at the first
non-empty ``PAGE_MEDIA_JS`` -- the bare blob: URL -- so the extractor ran before the manifest existed and the job went to
needs_review "set Trigger Selector". A blob:/data: URL names no fetchable media; it must not end the wait.

Hermetic: a 127.0.0.1 page shaped like the measured scene (blob video at once, manifest fetched ~800 ms later).
"""
from __future__ import annotations

import http.server
import threading

import pytest

from bulk_downloader import runner_transport, spa_media_extract

BD_GATE_SCOPE = "module"

SCENE = b"""<!doctype html><html><body>
<video id="player" muted></video>
<a href="/dl/6169126/">Download</a>
<script>
document.getElementById('player').src = URL.createObjectURL(new Blob([new Uint8Array(8)], {type: 'video/mp4'}));
setTimeout(() => fetch('/hls/scene-480p.m3u8'), 800);
</script></body></html>"""
BLOB_ONLY = b"""<!doctype html><html><body>
<video id="player" muted></video>
<script>document.getElementById('player').src = URL.createObjectURL(new Blob([new Uint8Array(8)], {type: 'video/mp4'}));</script>
</body></html>"""
M3U8 = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=854x480\nseg.ts\n"


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            routes = {"/vid-1-scene/": (200, "text/html", SCENE), "/vid-2-blob/": (200, "text/html", BLOB_ONLY),
                      "/hls/scene-480p.m3u8": (200, "application/vnd.apple.mpegurl", M3U8)}
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
def page(browser):
    ctx = browser.new_context()
    try:
        yield ctx.new_page()
    finally:
        ctx.close()


class _Runner(runner_transport.TransportMixin):
    _PAGE_MEDIA_WAIT_S = 4.0

    def __init__(self):
        self.calls = []
        self.config = {"min_resolution": 0}
        self.jobs = {}
        self._lock = threading.Lock()

    def _try_spa_api_media_extractor(self, url, page, min_height=0, hold_below=False):
        media = page.evaluate(spa_media_extract.PAGE_MEDIA_JS) or []
        self.calls.append(media)
        return any(m.endswith(".m3u8") for m in media)


def test_a_blob_video_url_alone_does_not_end_the_wait_for_the_manifest(site, page):
    scene = f"{site}/vid-1-scene/"
    r = _Runner()
    assert r._fallback_to_page_media(page, scene, "clicked candidate fired no download") is True
    assert r.calls and any(m.endswith("/hls/scene-480p.m3u8") for m in r.calls[-1]), r.calls


def test_control_blob_only_page_still_runs_the_extractor_after_the_bounded_wait(site, page):
    r = _Runner()
    r._PAGE_MEDIA_WAIT_S = 1.5
    assert r._fallback_to_page_media(page, f"{site}/vid-2-blob/", "x") is False
    assert len(r.calls) == 1 and all(m.startswith("blob:") for m in r.calls[0]), r.calls
