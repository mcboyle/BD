"""dl95-beeg-2 (harness-work/DOT95-LANE/live-dl95-beeg-1-live-1/LIVE-RESULT-B6-B.md, HIGH).

LIVE on test2 (.95 build 8189d74f): https://beeg.com/-0920833012505915 logged "chose 1080p from scene-stream" and
closed "done: API/media 1080p (134.8 MB)". The saved file probes h264 426x240, 2152.7 s. The chosen stream was beeg's
"multi=426x240:240p:...,1920x1080:1080p:..." master: its label is the tallest size the URL lists, but the segmented
downloader maps the master's FIRST video stream (-map 0:v:0), which is the 240p variant. Nothing measured what landed,
so a 240p file under min_resolution 1080 closed done under a 1080p label.

Contract after the fix: a master is fetched as the variant its rank named (h264 first), read through the page's own
session. The landed height is then probed, and it replaces the label. A job the runner chose for itself whose landed
height is below min_resolution goes to needs_review, not done. An approved job closes done with its real height.

Hermetic: a 127.0.0.1 CDN serves real HLS renditions that ffmpeg builds at test time, under the live beeg URL shape.
The page is a real chromium page (BD's cloak) on that origin, and the manifest reaches the runner the way the live one
did, through the watcher's manifest_urls. The segmented transfer is the real hls_downloader.download (real ffmpeg),
and the landed height is read with the real ffprobe. Only the egress gate in front of the transfer is bypassed.
"""
from __future__ import annotations

import http.server
import json
import shutil
import subprocess
import threading

import pytest

from bulk_downloader import runner_extractors, spa_media_extract

BD_GATE_SCOPE = "module"

SCENE = "https://beeg.com/-0920833012505915"
BARE = "https://beeg.com/-0920833012505916"   # a master that declares no RESOLUTION
CDN = "/key=K,end=1790739534,limit=10/data=4d30cfc8ea/media=hls4A/"
MULTI = ("multi=426x240:240p:YXZjMS42NDAwMUUsbXA0YS40MC4y,"
         "1920x1080:1080p:YXZjMS42NDAwMkEsbXA0YS40MC4y/_TPL_/")
RANKED_MASTER = CDN + MULTI + "920833012505915.mp4.m3u8"
BARE_MASTER = CDN + MULTI + "920833012505916.mp4.m3u8"
RANKED_BODY = (
    "#EXTM3U\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=426x240,CODECS="avc1.64001E"\nr240/index.m3u8\n'
    '#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080,CODECS="avc1.64002A"\nr1080/index.m3u8\n')
BARE_BODY = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=400000\nr240/index.m3u8\n"


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


@pytest.fixture(scope="module")
def cdn(tmp_path_factory):
    root = tmp_path_factory.mktemp("cdn")
    for name, size in (("r240", "426x240"), ("r1080", "1920x1080")):
        (root / name).mkdir()
        subprocess.run([_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=5",
                        "-t", "2", "-pix_fmt", "yuv420p", "-g", "5", "-f", "hls", "-hls_time", "1",
                        "-hls_segment_filename", str(root / name / "seg%d.ts"), str(root / name / "index.m3u8")],
                       check=True, timeout=120)
    fixed = {"/scene": ("text/html", b"<!doctype html><html><body><video></video></body></html>"),
             RANKED_MASTER: ("application/vnd.apple.mpegurl", RANKED_BODY.encode()),
             BARE_MASTER: ("application/vnd.apple.mpegurl", BARE_BODY.encode())}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            parts = path.split("/")
            if path in fixed:
                ctype, body = fixed[path]
            elif len(parts) > 2 and parts[-2] in ("r240", "r1080") and (root / parts[-2] / parts[-1]).is_file():
                ctype, body = "application/octet-stream", (root / parts[-2] / parts[-1]).read_bytes()
            else:
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
def page(cdn):
    from bulk_downloader import cloak

    with cloak.cloaked_page(headless=True, config={"browser_backend": "playwright"},
                            viewport={"width": 1280, "height": 720}) as p:
        p.goto(cdn + "/scene")
        yield p


class _Stub:
    site_id = "dl95beeg2"

    def __init__(self, tmp_path, job, master, forced):
        self.config = {"name": "beeg", "download_dir": str(tmp_path / "dl"), "min_resolution": 1080}
        self.jobs = {job: {"force_download": forced}}
        self.manifest_urls = [{"url": master}]
        self._stop = threading.Event()
        self.updates, self.events, self.segmented = [], [], []
        self._spa_api_capture = None

    def _update_job(self, url, status, message, **extra):
        self.updates.append((status, message))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return ""

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        raise AssertionError(f"a .m3u8 master must take the segmented transfer: {file_url}")

    def _hls_download_guarded(self, _hls, manifest_url, output_path, **kwargs):
        self.segmented.append(manifest_url)
        return _hls.download(manifest_url, output_path, **kwargs)

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


@pytest.fixture
def run(cdn, page, tmp_path, monkeypatch):
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)

    def _run(job, master, forced=False):
        r = type("StubRunner", (_Stub, runner_extractors.ExtractorsMixin), {})(
            tmp_path, job, cdn + master, forced)
        assert r._try_spa_api_media_extractor(job, page) is True, (r.updates, r.events)
        # Positive control: the rank labelled this master 1080p, as it did live.
        assert any(k == "spa_api_candidate" and m.startswith("chose 1080p from scene-stream")
                   for k, m in r.events), r.events
        return r, sorted(p for p in (tmp_path / "dl").rglob("*") if p.is_file())

    return _run


def test_a_1080p_master_lands_its_1080p_variant(run, cdn):
    r, landed = run(SCENE, RANKED_MASTER)
    assert r.segmented and r.segmented[0].endswith("/r1080/index.m3u8"), (
        f"DL95_BEEG2_MASTER_NOT_RESOLVED: the segmented transfer got {r.segmented}")
    assert len(landed) == 1 and _probe_height(landed[0]) == 1080, (
        f"DL95_BEEG2_LANDED_NOT_RANKED: {[(p.name, _probe_height(p)) for p in landed]} {r.updates[-1]}")
    assert r.updates[-1][0] == "done" and r.updates[-1][1].startswith("API/media 1080p"), r.updates[-1]


def test_a_landed_height_below_the_minimum_goes_to_review_not_done(run):
    r, landed = run(BARE, BARE_MASTER)
    statuses = [s for s, _ in r.updates]
    assert "done" not in statuses, f"DL95_BEEG2_BELOW_MIN_CLOSED_DONE: {r.updates[-1]}"
    assert r.updates[-1][0] == "needs_review" and "240p" in r.updates[-1][1], r.updates[-1]
    assert landed == [], f"a refused landing is not left in the download dir: {landed}"


def test_an_approved_job_closes_done_with_the_real_height(run):
    r, landed = run(BARE, BARE_MASTER, forced=True)
    assert len(landed) == 1 and _probe_height(landed[0]) == 240, landed
    assert r.updates[-1][0] == "done" and r.updates[-1][1].startswith("API/media 240p"), (
        f"DL95_BEEG2_LABEL_NOT_MEASURED: {r.updates[-1]}")


MASTER = "https://video.example/hls/_TPL_/920833012505915.mp4.m3u8"


def test_the_h264_variant_at_the_ranked_height_wins_over_a_higher_bandwidth_av1():
    body = ("#EXTM3U\n"
            '#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=426x240,CODECS="avc1.64001E,mp4a.40.2"\nv240.m3u8\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080,CODECS="av01.0.09M.08,mp4a.40.2"\nav1.m3u8\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080,CODECS="avc1.64002A,mp4a.40.2"\nh264.m3u8\n')
    pick = spa_media_extract.hls_variant_for(body, MASTER, 1080)
    assert pick == {"url": "https://video.example/hls/_TPL_/h264.m3u8", "height": 1080,
                    "codecs": "avc1.64002A,mp4a.40.2"}, pick


def test_without_the_ranked_height_the_tallest_h264_variant_is_taken():
    body = ("#EXTM3U\n"
            '#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080,CODECS="av01.0.09M.08"\nav1.m3u8\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=1280x720,CODECS="avc1.64001F"\nh720.m3u8\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=426x240,CODECS="avc1.64001E"\nh240.m3u8\n')
    pick = spa_media_extract.hls_variant_for(body, MASTER, 2160)
    assert pick and pick["url"].endswith("/h720.m3u8") and pick["height"] == 720, pick


@pytest.mark.parametrize("body", [
    "#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nseg0.ts\n#EXT-X-ENDLIST\n",   # a media playlist
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=400000\nv.m3u8\n",                     # no declared height
    "",                                                                           # the page's fetch refused
], ids=["media-playlist", "no-resolution", "empty"])
def test_nothing_to_pick_keeps_the_master(body):
    assert spa_media_extract.hls_variant_for(body, MASTER, 1080) is None
