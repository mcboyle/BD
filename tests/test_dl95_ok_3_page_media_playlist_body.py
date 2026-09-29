"""dl95-ok-3 (harness-work/UIUX-20260928/B4-B/ok-playlist-as-mp4.txt, HIGH).

Measured on test2 (ok.xxx/video/785100, an approved job reached through the dud-candidate fallback): the Row 722
page-media path "chose 720p from scene-player", fetched the option's URL (a .mp4 path) by direct HTTP and recorded
"done: API/media 720p (1002 B)". The saved 785100_720p.mp4 is an HLS master playlist (#EXTM3U, EXT-X-STREAM-INF);
_try_spa_api_media_extractor decided HLS vs file from ".m3u8" in the URL only and never looked at what landed.

Contract after the fix: the landed body decides. A playlist body is removed and re-fetched by the segmented
downloader with the HLS demuxer named (ffmpeg >= 6.1 will not probe a playlist at a non-.m3u8 path with a
non-mpegurl type); an HTML/JSON body is removed and the job is not done; real media is untouched.

Hermetic: a 127.0.0.1 server serves a real HLS rendition that ffmpeg builds at test time, with its master playlist
at a .mp4 path; the direct transfer writes the bytes that server returns; the segmented transfer is the real
hls_downloader.download (real ffmpeg), only the egress gate in front of it is bypassed.
"""
from __future__ import annotations

import http.server
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from bulk_downloader import hls_downloader, runner_extractors

BD_GATE_SCOPE = "module"

SCENE = "https://ok.xxx/video/785100/"
MASTER = b"#EXTM3U\n#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=523768,RESOLUTION=320x240\nmedia.m3u8\n"
PAGE = b"<!doctype html><html><body><h1>403 Forbidden</h1></body></html>"


def _ffmpeg():
    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.fail("ffmpeg is required: the segmented downloader under test is ffmpeg")
    return ff


@pytest.fixture(scope="module")
def cdn(tmp_path_factory):
    root = tmp_path_factory.mktemp("cdn")
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "2",
                    "-pix_fmt", "yuv420p", "-g", "10", "-f", "hls", "-hls_time", "1",
                    "-hls_segment_filename", str(root / "seg%d.ts"), str(root / "media.m3u8")],
                   check=True, timeout=60)
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "1",
                    "-pix_fmt", "yuv420p", str(root / "real.mp4")], check=True, timeout=60)
    files = {"/v/785100_720p.mp4": ("video/mp4", MASTER),            # the measured shape: a playlist at a .mp4 path
             "/v/785100_480p.mp4": ("video/mp4", PAGE),              # an error page at a media path
             "/v/785100_360p.mp4": ("video/mp4", (root / "real.mp4").read_bytes())}
    for p in root.iterdir():
        if p.suffix in (".m3u8", ".ts"):
            files[f"/v/{p.name}"] = ("application/octet-stream", p.read_bytes())

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            ctype, body = files.get(self.path, ("text/plain", b"nope"))
            self.send_response(200 if self.path in files else 404)
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


class _Page:
    def __init__(self, media):
        self.media, self.url = media, SCENE

    def evaluate(self, _js):
        return list(self.media)


class _Stub:
    site_id = "dl95ok3"

    def __init__(self, tmp_path):
        self.config = {"name": "ok", "download_dir": str(tmp_path / "dl"), "min_resolution": 1080}
        self.jobs = {SCENE: {"force_download": True}}      # the live job was approved
        self._stop = threading.Event()
        self.updates, self.events, self.transfers, self.segmented = [], [], [], []
        self._spa_api_capture = None

    def _update_job(self, url, status, message, **extra):
        self.updates.append((status, message))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append(kind)

    def _screenshot(self, page, url):
        return ""

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        self.transfers.append(file_url)
        with urllib.request.urlopen(file_url, timeout=10) as r:
            Path(output_path).write_bytes(r.read())
        return True

    def _hls_download_guarded(self, _hls, manifest_url, output_path, **kwargs):
        self.segmented.append((manifest_url, kwargs.get("input_format")))
        return _hls.download(manifest_url, output_path, **kwargs)

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    return lambda: type("StubRunner", (_Stub, runner_extractors.ExtractorsMixin), {})(tmp_path)


def _landed(tmp_path):
    return sorted(p for p in (tmp_path / "dl").rglob("*") if p.is_file())


def test_a_playlist_served_at_a_mp4_path_is_fetched_segmented_and_lands_video(cdn, make, tmp_path):
    r = make()
    option = f"{cdn}/v/785100_720p.mp4"
    assert r._try_spa_api_media_extractor(SCENE, _Page([option])) is True, r.updates
    assert r.transfers == [option]
    assert r.segmented == [(option, "hls")], f"DL95_OK3_PLAYLIST_NOT_SEGMENTED: {r.segmented}"
    assert "spa_api_manifest_body" in r.events, r.events
    status, msg = r.updates[-1]
    assert status == "done" and msg.startswith("API/media 720p"), msg
    [f] = _landed(tmp_path)
    head = f.read_bytes()[:16]
    assert head[4:8] == b"ftyp" and not head.startswith(b"#EXTM3U"), f"DL95_OK3_PLAYLIST_SAVED_AS_VIDEO: {head!r}"
    assert runner_extractors._landed_video_height(str(f)) == 240, "the landed file is the rendition's video"


def test_a_page_body_at_a_media_path_is_not_done_and_is_removed(cdn, make, tmp_path):
    r = make()
    assert r._try_spa_api_media_extractor(SCENE, _Page([f"{cdn}/v/785100_480p.mp4"])) is False
    assert "spa_api_not_media" in r.events, r.events
    assert not [u for u in r.updates if u[0] == "done"], f"DL95_OK3_PAGE_RECORDED_DONE: {r.updates}"
    assert r.segmented == [] and _landed(tmp_path) == []


def test_control_real_media_at_a_mp4_path_is_untouched(cdn, make, tmp_path):
    r = make()
    option = f"{cdn}/v/785100_360p.mp4"
    assert r._try_spa_api_media_extractor(SCENE, _Page([option])) is True
    assert r.segmented == [] and r.updates[-1][0] == "done", r.updates
    [f] = _landed(tmp_path)
    assert f.read_bytes()[4:8] == b"ftyp"
    assert not {"spa_api_manifest_body", "spa_api_not_media"} & set(r.events), r.events


def test_premise_ffmpeg_needs_the_named_demuxer_for_a_playlist_at_a_mp4_path(cdn, tmp_path):
    """Positive control on why input_format exists: without it the real downloader fails on the measured shape."""
    url = f"{cdn}/v/785100_720p.mp4"
    plain = hls_downloader.download(url, str(tmp_path / "plain.mp4"))
    named = hls_downloader.download(url, str(tmp_path / "named.mp4"), input_format="hls")
    assert not plain.ok, "fixture: ffmpeg probed the playlist by itself -- the demuxer name would be untested"
    assert named.ok and (tmp_path / "named.mp4").read_bytes()[4:8] == b"ftyp", named


def test_input_format_is_an_input_option_before_dash_i():
    cmd = hls_downloader._build_ffmpeg_cmd("ffmpeg", "https://x.test/a.mp4", "/o.mp4", input_format="hls")
    i = cmd.index("-i")
    assert cmd[i - 2:i] == ["-f", "hls"], cmd
    assert "-f" not in hls_downloader._build_ffmpeg_cmd("ffmpeg", "https://x.test/a.m3u8", "/o.mp4")


@pytest.mark.parametrize("body,kind", [
    (MASTER, "manifest"),
    (b"\xef\xbb\xbf\n#EXTM3U\n#EXT-X-VERSION:3\n", "manifest"),
    (PAGE, "page"),
    (b"<HTML><body>login</body></HTML>", "page"),
    (b'{"error": "expired"}', "page"),
    (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64, "media"),
    (b"\x47\x40\x00\x10" + b"\xff" * 184, "media"),        # MPEG-TS: unfamiliar is not refused
    (b"\x1a\x45\xdf\xa3" + b"\x00" * 32, "media"),
])
def test_landed_body_kind(tmp_path, body, kind):
    p = tmp_path / "f.mp4"
    p.write_bytes(body)
    assert runner_extractors._landed_body_kind(str(p)) == kind
