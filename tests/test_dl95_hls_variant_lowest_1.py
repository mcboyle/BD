"""dl95-hls-variant-lowest-1 (RESULT-dl95-ok-3-2138Z.md SIDE; NOTE-dispatch-A-hls-lowest-variant-2142Z.md).

Measured live on .95 (build 1e334559): ok.xxx/video/793009, 720p approved; the option URL (.../793009_720p.mp4/)
answered with an HLS MASTER (954 B, captured below verbatim) listing 640x360, 854x480, 1280x720 in that order.
The page-media path logged spa_api_manifest_body, re-fetched the master segmented with no program named, so the
segmented downloader mapped the FIRST video stream: "spa_api_height_mismatch labelled 720p; landed 360p".

Contract after the fix: a master that arrives as the body of a media URL is ranked like a .m3u8 master
(spa_media_extract.hls_variant_for): the variant at the option's height, else the tallest, by its master program.
"""
from __future__ import annotations

import hashlib
import http.server
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from bulk_downloader import runner_extractors, spa_media_extract

BD_GATE_SCOPE = "module"

SCENE = "https://ok.xxx/video/793009/"
# tester-S70-D/ok/master-793009_720p.m3u8, fetched from .95 (200, 954 B, vnd.apple.mpegurl).
CAPTURED_MASTER = (
    b'#EXTM3U\n'
    b'#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=491423,RESOLUTION=640x360,FRAME-RATE=23.974,CODECS="avc1.4d401f,mp4a.40.2"\n'
    b'https://nvms5-b.cdn.privatehost.com/media/sdb/c2bb4a6b-4422-50be-bfa0-f7750b96f67d/793009_360p.mp4/index-v1-a1.m3u8?&c=9&lra=5000k&exp_time=1792021289&tag=ok.xxx&sign=69932ed0811d43af6b6d4b63e40d1b06\n'
    b'#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=703523,RESOLUTION=854x480,FRAME-RATE=23.974,CODECS="avc1.640029,mp4a.40.2"\n'
    b'https://nvms5-b.cdn.privatehost.com/media/sdb/c2bb4a6b-4422-50be-bfa0-f7750b96f67d/793009.mp4/index-v1-a1.m3u8?&c=9&lra=5000k&exp_time=1792021289&tag=ok.xxx&sign=896014ab26f3699a96f99769c7f41554\n'
    b'#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1335072,RESOLUTION=1280x720,FRAME-RATE=23.974,CODECS="avc1.640029,mp4a.40.2"\n'
    b'https://nvms5-b.cdn.privatehost.com/media/sdb/c2bb4a6b-4422-50be-bfa0-f7750b96f67d/793009_720p.mp4/index-v1-a1.m3u8?&c=9&lra=5000k&exp_time=1792021289&tag=ok.xxx&sign=c0efb68e54201fdb9426f5b7d9dd4d59\n'
    b'\n'
)
CAPTURED_SHA256 = "1d0be343772c9e02112b5559ae6dd83b4571666a6de32baf92ad11d062071b12"
HEIGHTS = (360, 480, 720)   # the captured master's order: lowest first


def _ffmpeg():
    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.fail("ffmpeg is required: the segmented downloader under test is ffmpeg")
    return ff


@pytest.fixture(scope="module")
def cdn(tmp_path_factory):
    root = tmp_path_factory.mktemp("cdn")
    widths = {360: 640, 480: 854, 720: 1280}
    stream_inf = b"#EXTM3U\n"
    for h in HEIGHTS:
        d = root / f"{h}"
        d.mkdir()
        subprocess.run([_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={widths[h]}x{h}:rate=10",
                        "-t", "1", "-pix_fmt", "yuv420p", "-g", "10", "-f", "hls", "-hls_time", "1",
                        "-hls_segment_filename", str(d / "seg%d.ts"), str(d / "index.m3u8")],
                       check=True, timeout=60)
        stream_inf += (f"#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH={h * 1000},RESOLUTION={widths[h]}x{h}\n"
                       f"{h}/index.m3u8\n").encode()
    files = {"/v/793009_720p.mp4": ("video/mp4", stream_inf),        # the measured shape: a master at a .mp4 path
             "/cap/793009_720p.mp4": ("video/mp4", CAPTURED_MASTER)}
    for p in root.rglob("*"):
        if p.is_file():
            files[f"/v/{p.relative_to(root).as_posix()}"] = ("application/octet-stream", p.read_bytes())

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

    def evaluate(self, _js, *a):
        return list(self.media)


class _Refused:
    ok, error, error_detail, bytes_written = False, "stub", "", 0


class _Stub:
    site_id = "dl95hlsvar"

    def __init__(self, tmp_path, transfer=True):
        self.config = {"name": "ok", "download_dir": str(tmp_path / "dl"), "min_resolution": 1080}
        self.jobs = {SCENE: {"force_download": True}}      # the live job was approved
        self._stop = threading.Event()
        self.updates, self.events, self.segmented = [], [], []
        self._spa_api_capture = None
        self._transfer = transfer

    def _update_job(self, url, status, message, **extra):
        self.updates.append((status, message))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return ""

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        with urllib.request.urlopen(file_url, timeout=10) as r:
            Path(output_path).write_bytes(r.read())
        return True

    def _hls_download_guarded(self, _hls, manifest_url, output_path, **kwargs):
        self.segmented.append((manifest_url, kwargs.get("input_format"), kwargs.get("program")))
        if not self._transfer:                 # the captured master's CDN is not reachable from a test
            return _Refused()
        return _hls.download(manifest_url, output_path, **kwargs)

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    return lambda **kw: type("StubRunner", (_Stub, runner_extractors.ExtractorsMixin), {})(tmp_path, **kw)


def _landed(tmp_path):
    return sorted(p for p in (tmp_path / "dl").rglob("*") if p.is_file())


def test_captured_master_is_the_measured_artefact():
    assert len(CAPTURED_MASTER) == 954
    assert hashlib.sha256(CAPTURED_MASTER).hexdigest() == CAPTURED_SHA256


def test_control_the_ranker_names_the_720p_entry_of_the_captured_master():
    """Positive control: the chooser can say yes on the live bytes (program 2 = third STREAM-INF, 1280x720)."""
    pick = spa_media_extract.hls_variant_for(CAPTURED_MASTER.decode(), "https://ok.xxx/get_file/x/793009_720p.mp4/", 720)
    assert pick and pick["height"] == 720 and pick["program"] == 2, pick


def test_captured_master_body_is_transferred_with_its_720p_program(cdn, make):
    r = make(transfer=False)
    option = f"{cdn}/cap/793009_720p.mp4"
    r._try_spa_api_media_extractor(SCENE, _Page([option]))
    assert [k for k, _ in r.events].count("spa_api_manifest_body") == 1, r.events
    assert len(r.segmented) == 1, r.segmented
    url, fmt, program = r.segmented[0]
    assert (url, fmt) == (option, "hls"), r.segmented
    assert program == 2, f"DL95_HLS_VARIANT_LOWEST: master body went to the transfer with program={program!r} (first variant = 360p)"


def test_master_body_at_a_mp4_path_lands_its_720p_rendition(cdn, make, tmp_path):
    r = make()
    option = f"{cdn}/v/793009_720p.mp4"
    assert r._try_spa_api_media_extractor(SCENE, _Page([option])) is True, r.updates
    [f] = _landed(tmp_path)
    landed = runner_extractors._landed_video_height(str(f))
    assert landed == 720, f"DL95_HLS_VARIANT_LOWEST: labelled 720p, landed {landed}p; events={r.events}"
    assert "spa_api_height_mismatch" not in [k for k, _ in r.events], r.events
    status, msg = r.updates[-1]
    assert status == "done" and msg.startswith("API/media 720p"), msg


# r2 (ROUTE-lensrouter-A-dl95-hls-variant-lowest-1-r1-REFUTE-0024Z.md F1; RULING-pm-A4-A-hls-chooser-cap-0024Z.md).
M = "#EXTM3U\n"
V360 = '#EXT-X-STREAM-INF:BANDWIDTH=360000,RESOLUTION=640x360,CODECS="avc1.4d401f"\n360/index.m3u8\n'
V480 = '#EXT-X-STREAM-INF:BANDWIDTH=480000,RESOLUTION=854x480,CODECS="avc1.4d401f"\n480/index.m3u8\n'
V720 = '#EXT-X-STREAM-INF:BANDWIDTH=720000,RESOLUTION=1280x720,CODECS="avc1.4d401f"\n720/index.m3u8\n'
# The lens's F1-input.m3u8 (.review/ev of the r1 lens copy): a valid master whose 720p entry sits past byte 65536.
F1_MASTER = (M + V360 + V480 + "#" + "padding" * 10000 + "\n" + V720).encode()
F1_SHA256 = "52f8321fd253540a51fe12c34bdbd08701a66f3c4a781b6fabfaa77ad12ed70c"


def _program_for_body(make, payload, height):
    r = make(transfer=False)

    def direct(page_url, file_url, output_path, referer=""):
        Path(output_path).write_bytes(payload)
        return True

    r._do_direct_http_download = direct
    r._try_spa_api_media_extractor(SCENE, _Page([f"http://127.0.0.1:9/v/scene_{height}p.mp4"]))
    assert len(r.segmented) == 1, r.events
    return r, r.segmented[0][2]


def test_whole_master_is_ranked_not_its_first_64k(make):
    assert len(F1_MASTER) == 70281 and hashlib.sha256(F1_MASTER).hexdigest() == F1_SHA256
    assert F1_MASTER.find(V720.encode()) > 65536
    r, program = _program_for_body(make, F1_MASTER, 720)
    assert program == 2, f"DL95_HLS_VARIANT_LOWEST_R2 TRUNCATED-MASTER: wanted 720p program 2, got {program}; {r.events}"


def test_master_past_the_hard_cap_is_never_ranked_partially(make, monkeypatch):
    """Over the cap: no partial ranking -- program None (today's unnamed transfer), never the prefix's best."""
    monkeypatch.setattr(runner_extractors, "_HLS_MASTER_BODY_CAP", 1000, raising=False)
    payload = (M + V360 + V480 + "#" + "x" * 1000 + "\n" + V720).encode()
    r, program = _program_for_body(make, payload, 720)
    assert program is None, f"DL95_HLS_VARIANT_LOWEST_R2 CAP: a master past the cap was ranked (program {program}); {r.events}"


def test_chooser_takes_the_highest_variant_under_the_cap():
    """PM chooser: want 480, have 360/720 -> 360 (the lowest above only when none is <= the cap)."""
    pick = spa_media_extract.hls_variant_for(M + V360 + V720, "https://cdn.invalid/master.mp4", 480)
    assert pick and pick["height"] == 360 and pick["program"] == 0, f"DL95_HLS_VARIANT_LOWEST_R2 OVER-CAP: {pick}"
    pick = spa_media_extract.hls_variant_for(M + V720 + V480.replace("480000", "990000"), "https://cdn.invalid/m.mp4", 360)
    assert pick and pick["height"] == 480 and pick["program"] == 1, f"DL95_HLS_VARIANT_LOWEST_R2 NONE-UNDER: {pick}"
    pick = spa_media_extract.hls_variant_for(M + V360 + V480 + V720, "https://cdn.invalid/m.mp4", 0)
    assert pick and pick["height"] == 720, f"an unknown height (0) still names the tallest: {pick}"


def test_master_body_at_480p_option_is_transferred_with_its_360p_program(make):
    r, program = _program_for_body(make, (M + V360 + V720).encode(), 480)
    assert program == 0, f"DL95_HLS_VARIANT_LOWEST_R2 OVER-CAP: wanted 360p program 0 under a 480p cap, got {program}; {r.events}"
