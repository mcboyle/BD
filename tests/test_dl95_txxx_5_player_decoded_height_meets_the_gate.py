"""dl95-txxx-5 (harness-work/DOT95-LANE/live-dl95-txxx-2/LIVE-RESULT-B7-B.md, LOW).

LIVE on test2 (.95 v3.66.1713, 05:08-05:11Z): the logged-in txxx scene /videos/21752927/flogging-him-as-i-want/ went to
needs_review "Scene player best is unknown quality (minimum 1080p) -- Approve to force". After Approve the app logged
"chose 0p from scene-stream; saw: ?p:unknown". The scene's own stream URL names no rendition, so every txxx job stops
at the gate, whatever the stream really is.

Contract after the fix: a scene candidate of unknown height that the page's own <video> is playing takes the height the
player decoded (videoHeight), before the min_resolution gate. At or above the minimum it downloads; below it, the
hold names the real height. A stream the player has not decoded stays unknown and is held exactly as before.

Hermetic: a 127.0.0.1 origin serves the scene pages and real MP4s that ffmpeg builds at test time. The page is a real
chromium page (BD's cloak), and the direct transfer is a recorder that writes the served bytes.
"""
from __future__ import annotations

import http.server
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from bulk_downloader import runner_extractors

BD_GATE_SCOPE = "module"

SCENES = {  # scene id -> (stream height, <video> preload)
    "21752927": (1080, "auto"),
    "21752928": (720, "auto"),
    "21752929": (1080, "none"),   # never decoded: the height stays unknown
}


def _scene_path(sid):
    return f"/videos/{sid}/flogging-him-as-i-want/"


def _stream_path(sid):
    return f"/cdn/{sid}/{sid}.mp4?e=1"   # the stream URL names the scene, not its rendition


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.fail("ffmpeg is required to build the scene streams")
    root = tmp_path_factory.mktemp("cdn")
    files = {}
    for sid, (height, preload) in SCENES.items():
        mp4 = root / f"{sid}.mp4"
        subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={height * 16 // 9}x{height}:rate=5",
                        "-t", "1", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], check=True, timeout=120)
        files[_stream_path(sid).split("?")[0]] = ("video/mp4", mp4.read_bytes())
        files[_scene_path(sid)] = ("text/html", (
            "<!doctype html><html><head><title>Flogging Him As I Want</title></head><body>"
            f'<video id="player" src="{_stream_path(sid)}" preload="{preload}" muted playsinline></video>'
            "</body></html>").encode())

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            ctype, body = files.get(self.path.split("?")[0], (None, None))
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
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
def page():
    from bulk_downloader import cloak

    with cloak.cloaked_page(headless=True, config={"browser_backend": "playwright"},
                            viewport={"width": 1280, "height": 720}) as p:
        yield p


class _Runner(runner_extractors.ExtractorsMixin):
    site_id = "dl95txxx5"

    def __init__(self, tmp_path, job):
        self.config = {"name": "txxx", "download_dir": str(tmp_path / "dl"), "min_resolution": 1080}
        self.jobs = {job: {}}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.updates, self.events, self.transfers = [], [], []
        self._spa_api_capture = None

    def _update_job(self, url, status, message="", **extra):
        self.updates.append((status, message))

    def log_event(self, kind, message="", url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return ""

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        self.transfers.append(file_url)
        with urllib.request.urlopen(file_url, timeout=10) as r:
            Path(output_path).write_bytes(r.read())
        return True

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


@pytest.fixture
def run(origin, page, tmp_path, monkeypatch):
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})

    def _run(sid):
        job = origin + _scene_path(sid)
        page.goto(job, wait_until="domcontentloaded")
        if SCENES[sid][1] != "none":
            page.wait_for_function("document.getElementById('player').videoHeight > 0", timeout=15000)
        r = _Runner(tmp_path, job)
        assert r._try_spa_api_media_extractor(job, page) is True, (r.updates, r.events)
        return r, origin + _stream_path(sid)

    return _run


def test_a_1080p_stream_the_player_decoded_downloads(run):
    r, stream = run("21752927")
    assert r.updates[-1][0] == "done", f"DL95_TXXX5_DECODED_1080P_HELD: {r.updates}"
    assert r.transfers == [stream], r.transfers
    assert r.updates[-1][1].startswith("API/media 1080p"), r.updates[-1]
    assert ("spa_api_player_height", f"player decoded 1080p: {stream}") in r.events, r.events


def test_a_720p_stream_is_held_naming_its_real_height(run):
    r, _ = run("21752928")
    assert r.transfers == [], r.transfers
    assert r.updates[-1] == ("needs_review", "Scene player best is 720p (minimum 1080p) — Approve to force."), (
        f"DL95_TXXX5_HOLD_WITHOUT_HEIGHT: {r.updates}")


def test_a_stream_the_player_never_decoded_is_held_as_unknown(run):
    r, _ = run("21752929")
    assert r.transfers == [], r.transfers
    assert r.updates[-1] == ("needs_review", "Scene player best is unknown quality (minimum 1080p) — Approve to force."), (
        r.updates)
    assert not [e for e in r.events if e[0] == "spa_api_player_height"], r.events
