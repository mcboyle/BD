"""tpl95-xnxx-1 (harness-work/UIUX-20260928/download-95/A1-A/tpl-xnxx/FINDING-tpl95-xnxx-1.md, MED).

LIVE on test2 (.95 lane): xnxx scene video-hbwy3c6 went to needs_review "below 1080p; got 360p (file video_360p.mp4)".
The page declares its sources in an inline script: setVideoUrlLow = video_240p.mp4, setVideoUrlHigh = video_360p.mp4,
and setVideoHLS = hls.m3u8. That master lists 480p, 720p, 1080p, 360p, 250p (in that order), and every rendition from
480p up is HLS-only. dl95-xnxx-1's by-file hold is right to refuse the 360p file. But nothing read the master, so the
1080p that the page offers was never reached.

Contract after the fix: before the by-file hold refuses, the page's own media are tried at min_resolution. That includes
the WGCZ player's setVideo* sources, and an HLS master of unknown height is labelled by its tallest variant. The rescue
fetches the 1080p VARIANT (dl95-beeg-2), not the master's first stream (480p). If nothing reaches min_resolution, the
hold stands exactly as before.

Hermetic: a 127.0.0.1 origin serves the scene page (the live source shapes, tokens blanked) and real HLS renditions that
ffmpeg builds at test time. The page is a real chromium page (BD's cloak). The segmented transfer is the real
hls_downloader.download (real ffmpeg), and the landed height is read with the real ffprobe. Only the egress gate in
front of the transfer is bypassed, and the clicked 360p download is a recorder.
"""
from __future__ import annotations

import http.server
import json
import shutil
import subprocess
import threading

import pytest

from bulk_downloader import runner_extractors, runner_transport, spa_media_extract

BD_GATE_SCOPE = "module"

UUID = "14f9d3a7-e94c-4aee-9127-64ad7b6aa820"
MASTER_1080 = (  # the live master, verbatim order and attributes
    "#EXTM3U\n"
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=763904,RESOLUTION=854x480,NAME="480p"\nhls-480p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1327104,RESOLUTION=1280x720,NAME="720p"\nhls-720p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=2760704,RESOLUTION=1920x1080,NAME="1080p"\nhls-1080p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=423936,RESOLUTION=640x360,NAME="360p"\nhls-360p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=155648,RESOLUTION=444x250,NAME="250p"\nhls-250p.m3u8\n')
MASTER_720 = "\n".join(ln for ln in MASTER_1080.split("\n") if "1080" not in ln) + "\n"


def _tool(name):
    path = shutil.which(name)
    if not path:
        pytest.fail(f"{name} is required: the segmented transfer and the landed probe under test use it")
    return path


def _probe_height(path):
    out = subprocess.run([_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=height", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True, timeout=30).stdout
    return int(json.loads(out)["streams"][0]["height"])


def _scene(sources):
    calls = "".join(f"html5player.{fn}('{u}');" for fn, u in sources)
    return ("<!doctype html><html><head><title>Scene - XNXX.COM</title></head><body>"
            '<div id="video-player-bg"><a class="download" href="#">Download</a></div>'
            "<script>var html5player = {setVideoUrlLow() {}, setVideoUrlHigh() {}, setVideoHLS() {}};"
            f"{calls}</script></body></html>").encode()


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    root = tmp_path_factory.mktemp("cdn")
    for name, size in (("480p", "854x480"), ("720p", "1280x720"), ("1080p", "1920x1080")):
        subprocess.run([_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=5",
                        "-t", "2", "-pix_fmt", "yuv420p", "-g", "5", "-f", "hls", "-hls_time", "1",
                        "-hls_segment_filename", str(root / f"seg-{name}-%d.ts"), str(root / f"hls-{name}.m3u8")],
                       check=True, timeout=120)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), None)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    hls = f"/tok/{UUID}/0/hls.m3u8"
    low, high = f"/mp4/{UUID}/0/video_240p.mp4?e=1", f"/mp4/{UUID}/0/video_360p.mp4?e=1"
    player = [("setVideoUrlLow", base + low), ("setVideoUrlHigh", base + high)]
    fixed = {
        "/video-hbwy3c6/scene": _scene([*player, ("setVideoHLS", base + hls)]),
        "/video-hbwy3c7/no-hls": _scene(player),
        "/video-hbwy3c8/tops-at-720": _scene([*player, ("setVideoHLS", base + f"/tokb/{UUID}/0/hls.m3u8")]),
        hls: MASTER_1080.encode(),
        f"/tokb/{UUID}/0/hls.m3u8": MASTER_720.encode(),
    }

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            leaf = path.rsplit("/", 1)[-1]
            if self.path in fixed or path in fixed:
                body = fixed.get(self.path) or fixed[path]
            elif path.startswith(("/tok/", "/tokb/")) and (root / leaf).is_file():
                body = (root / leaf).read_bytes()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html" if body.startswith(b"<!doctype") else "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv.RequestHandlerClass = H
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield base
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


class _Download:
    def __init__(self, url):
        self.url, self.cancelled = url, 0

    def cancel(self):
        self.cancelled += 1


class _Runner(runner_transport.TransportMixin, runner_extractors.ExtractorsMixin):
    _PAGE_MEDIA_WAIT_S = 1.0
    site_id = "tpl95xnxx1"

    def __init__(self, tmp_path):
        self.config = {"name": "xnxx", "download_dir": str(tmp_path / "dl"), "min_resolution": 1080}
        self.jobs = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.updates, self.events, self.segmented = [], [], []
        self._spa_api_capture = None

    def _update_job(self, url, status, message="", **extra):
        self.updates.append((status, message))

    def log_event(self, kind, message="", url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return ""

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        raise AssertionError(f"only the HLS variant may be transferred here: {file_url}")

    def _hls_download_guarded(self, _hls, manifest_url, output_path, **kwargs):
        self.segmented.append(manifest_url)
        return _hls.download(manifest_url, output_path, **kwargs)

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


@pytest.fixture
def held(origin, page, tmp_path, monkeypatch):
    for mod in (runner_extractors, runner_transport):
        monkeypatch.setattr(mod, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})

    def _held(route):
        scene = origin + route
        page.goto(scene, wait_until="domcontentloaded")
        r = _Runner(tmp_path)
        dl = _Download(f"{origin}/mp4/{UUID}/0/video_360p.mp4?e=1")
        took = r._below_min_resolution_by_file(page, scene, dl, {"score": 0}, "video_360p.mp4")
        # Positive control: the by-file judgement did fire on the clicked 360p file.
        assert took is True and dl.cancelled >= 1, (took, r.updates)
        return r, sorted(p for p in (tmp_path / "dl").rglob("*") if p.is_file())

    return _held


def test_the_players_hls_master_lands_the_1080p_variant_instead_of_holding_360p(held):
    r, landed = held("/video-hbwy3c6/scene")
    assert r.updates and r.updates[-1][0] == "done", f"TPL95_XNXX1_HELD_WITH_1080P_OFFERED: {r.updates}"
    assert r.segmented and r.segmented[0].endswith("/hls-1080p.m3u8"), (
        f"TPL95_XNXX1_NOT_THE_1080P_VARIANT: {r.segmented}")
    assert len(landed) == 1 and _probe_height(landed[0]) == 1080, [(p.name, _probe_height(p)) for p in landed]
    assert r.updates[-1][1].startswith("API/media 1080p"), r.updates[-1]


def test_without_a_player_master_the_hold_stands(held):
    r, landed = held("/video-hbwy3c7/no-hls")
    assert r.updates[-1][0] == "needs_review" and "360p" in r.updates[-1][1], r.updates
    assert r.segmented == [] and landed == []


def test_a_master_that_tops_out_below_the_minimum_does_not_rescue(held):
    r, landed = held("/video-hbwy3c8/tops-at-720")
    assert any(k == "spa_api_hls_measured" and "720p" in m for k, m in r.events), r.events
    assert r.updates[-1][0] == "needs_review" and "360p" in r.updates[-1][1], r.updates
    assert r.segmented == [] and landed == []


def test_wgcz_player_sources_are_read_from_the_inline_script():
    html = ("<script>html5player.setVideoTitle('Scene 1080p');"
            "html5player.setVideoUrlLow('https://mp4.example/v/video_240p.mp4?e=1');"
            "html5player.setVideoUrlHigh(\"https://mp4.example/v/video_360p.mp4?e=1\");"
            "html5player.setVideoHLS('https://hls.example/t/v/0/hls.m3u8');"
            "html5player.setThumbUrl('https://img.example/t.jpg');"
            "html5player.setVideoHLS('/relative/hls.m3u8');</script>")
    assert spa_media_extract.wgcz_player_media(html) == [
        "https://mp4.example/v/video_240p.mp4?e=1", "https://mp4.example/v/video_360p.mp4?e=1",
        "https://hls.example/t/v/0/hls.m3u8"]
