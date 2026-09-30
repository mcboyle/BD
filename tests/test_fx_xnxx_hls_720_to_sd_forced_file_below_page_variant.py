"""fx-xnxx-hls-720-to-sd (ISP-SPLIT-O1564/ORDER-FIX-SWEEP-T175.md, Cut C; results/test1/xnxx_T175_evidence-C1-C.json).

LIVE on test1 (T176 v3.66.1748): xnxx video-18g8et87 went to needs_review "Best is 720p (below 1080p) -- Approve to
force" (the player's setVideoHLS master tops at 720p). After Approve, the forced run's learned <video> row matched
nothing (the ad player did not render), the wide sweep clicked the unlabelled Download control, and the quality modal
it revealed was taken at 360p ('MEDIUM'). dl95-xnxx-1's by-file check returns at once for a forced job, so the 640x360
mp4 was saved as XNXX_..._SD.mp4. The prior PASS (T158) saved the scene's hls-720p variant at 1280x720.

Contract after the fix: Approve lifts the min_resolution bar; it never lowers the tier. When a forced job's clicked
file has a KNOWN tier and the scene's own media offer a taller one, the taller variant is downloaded and the clicked
file is cancelled. If the taller variant cannot be fetched, the job fails and names both tiers; it never saves the
lower file as done. No taller option of the scene's own -> the clicked file proceeds unchanged (as before).

Hermetic: a 127.0.0.1 origin serves the scene page (the live WGCZ player shape, tokens blanked) and real HLS
renditions that ffmpeg builds at test time. The page is a real chromium page (BD's cloak). The segmented transfer is
the real hls_downloader.download (real ffmpeg), and the landed height is read with the real ffprobe. Only the egress
gate in front of the transfer is bypassed, and the clicked 360p download is a recorder.
"""
from __future__ import annotations

import http.server
import json
import shutil
import subprocess
import threading

import pytest

from bulk_downloader import runner_extractors, runner_transport

BD_GATE_SCOPE = "module"

UUID = "d58465c8-d9c7-430e-b818-3e7eb318be51"
MASTER_720 = (  # the live master shape: 720p is the tallest rendition this scene has
    "#EXTM3U\n"
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=763904,RESOLUTION=854x480,NAME="480p"\nhls-480p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1327104,RESOLUTION=1280x720,NAME="720p"\nhls-720p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=423936,RESOLUTION=640x360,NAME="360p"\nhls-360p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=155648,RESOLUTION=444x250,NAME="250p"\nhls-250p.m3u8\n')


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
    return ("<!doctype html><html><head><title>Horny redhead - XNXX.COM</title></head><body>"
            '<div id="video-player-bg"><a class="download" href="#">Download</a></div>'
            "<script>var html5player = {setVideoUrlLow() {}, setVideoUrlHigh() {}, setVideoHLS() {}};"
            f"{calls}</script></body></html>").encode()


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    root = tmp_path_factory.mktemp("cdn")
    for name, size in (("480p", "854x480"), ("720p", "1280x720")):
        subprocess.run([_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=5",
                        "-t", "2", "-pix_fmt", "yuv420p", "-g", "5", "-f", "hls", "-hls_time", "1",
                        "-hls_segment_filename", str(root / f"seg-{name}-%d.ts"), str(root / f"hls-{name}.m3u8")],
                       check=True, timeout=120)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), None)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    low, high = f"/mp4/{UUID}/0/video_240p.mp4?e=1", f"/mp4/{UUID}/0/video_360p.mp4?e=1"
    player = [("setVideoUrlLow", base + low), ("setVideoUrlHigh", base + high)]
    fixed = {
        "/video-18g8et87/scene": _scene([*player, ("setVideoHLS", base + f"/tok/{UUID}/0/hls.m3u8")]),
        "/video-18g8et88/no-hls": _scene(player),
        # the master is served, its renditions are not (a CDN token that expired mid-run)
        "/video-18g8et89/dead-720": _scene([*player, ("setVideoHLS", base + f"/dead/{UUID}/0/hls.m3u8")]),
        f"/tok/{UUID}/0/hls.m3u8": MASTER_720.encode(),
        f"/dead/{UUID}/0/hls.m3u8": MASTER_720.encode(),
    }

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            leaf = path.rsplit("/", 1)[-1]
            if path in fixed:
                body = fixed[path]
            elif path.startswith("/tok/") and (root / leaf).is_file():
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
    site_id = "fxxnxx720sd"

    def __init__(self, tmp_path, scene):
        self.config = {"name": "xnxx", "download_dir": str(tmp_path / "dl"), "min_resolution": 1080}
        self.jobs = {scene: {"force_download": True}}     # Approve was pressed
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
def forced(origin, page, tmp_path, monkeypatch):
    for mod in (runner_extractors, runner_transport):
        monkeypatch.setattr(mod, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})

    def _forced(route):
        scene = origin + route
        page.goto(scene, wait_until="domcontentloaded")
        r = _Runner(tmp_path, scene)
        # The live shape: an unlabelled Download trigger (score 0) whose revealed modal label was 360p.
        dl = _Download(f"{origin}/mp4/{UUID}/0/video_360p.mp4?e=1")
        took = r._below_min_resolution_by_file(page, scene, dl, {"score": 0, "_revealed_score": 360},
                                               "video_360p.mp4")
        return r, dl, took, sorted(p for p in (tmp_path / "dl").rglob("*") if p.is_file())

    return _forced


def test_forced_360p_file_gives_way_to_the_scenes_720p_hls_variant(forced):
    r, dl, took, landed = forced("/video-18g8et87/scene")
    assert took is True, f"FX_XNXX_720_TO_SD_FORCED_FILE_SAVED_AT_360P: the forced 360p file proceeds; {r.updates}"
    assert dl.cancelled >= 1, "the clicked 360p download was not cancelled"
    assert r.updates and r.updates[-1][0] == "done", r.updates
    assert r.segmented and r.segmented[0].endswith("/hls-720p.m3u8"), (
        f"FX_XNXX_720_TO_SD_NOT_THE_720P_VARIANT: {r.segmented}")
    assert len(landed) == 1 and _probe_height(landed[0]) == 720, [(p.name, _probe_height(p)) for p in landed]


def test_a_taller_variant_that_cannot_be_fetched_fails_the_job_loudly(forced):
    r, dl, took, landed = forced("/video-18g8et89/dead-720")
    assert took is True, f"FX_XNXX_720_TO_SD_SILENT_SD_FALLBACK: the 360p file proceeds past a 720p offer; {r.updates}"
    assert dl.cancelled >= 1
    assert r.updates and r.updates[-1][0] == "failed", r.updates
    assert "720p" in r.updates[-1][1] and "360p" in r.updates[-1][1], r.updates[-1]
    assert landed == []


def test_negative_control_no_taller_own_media_lets_the_forced_file_proceed(forced):
    r, dl, took, landed = forced("/video-18g8et88/no-hls")
    assert took is False and dl.cancelled == 0, (took, dl.cancelled, r.updates)
    assert not [u for u in r.updates if u[0] in ("done", "failed", "needs_review")], r.updates
    assert r.segmented == [] and landed == []


def test_negative_control_unknown_tier_file_is_not_second_guessed(origin, page, tmp_path, monkeypatch):
    for mod in (runner_extractors, runner_transport):
        monkeypatch.setattr(mod, "db_log", lambda *a, **k: None)
    scene = origin + "/video-18g8et87/scene"
    page.goto(scene, wait_until="domcontentloaded")
    r = _Runner(tmp_path, scene)
    dl = _Download(f"{origin}/mp4/{UUID}/0/clip.mp4?e=1")
    assert r._below_min_resolution_by_file(page, scene, dl, {"score": 0}, "clip.mp4") is False
    assert dl.cancelled == 0 and r.segmented == [] and r.updates == []


# Lens C2-C REFUTE (.review/VERDICT-bd-worker-C2-C.md): the same defect one selector away.

def test_forced_scored_360p_row_gives_way_to_the_720p_hls_variant(origin, page, tmp_path, monkeypatch):
    """E2: a learned/labelled 360p row (score > 0) that the forced tier fallback clicked."""
    for mod in (runner_extractors, runner_transport):
        monkeypatch.setattr(mod, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    scene = origin + "/video-18g8et87/scene"
    page.goto(scene, wait_until="domcontentloaded")
    r = _Runner(tmp_path, scene)
    dl = _Download(f"{origin}/mp4/{UUID}/0/video_360p.mp4?e=1")
    took = r._below_min_resolution_by_file(page, scene, dl, {"score": 360}, "video_360p.mp4")
    assert took is True, f"FX_XNXX_720_TO_SD_FORCED_SCORED_ROW_SAVED_AT_360P: {r.updates}"
    assert dl.cancelled >= 1 and r.updates[-1][0] == "done", (dl.cancelled, r.updates)
    assert r.segmented and r.segmented[0].endswith("/hls-720p.m3u8"), r.segmented


def test_unforced_file_at_a_low_bar_gives_way_to_the_720p_hls_variant(origin, page, tmp_path, monkeypatch):
    """E1: min_resolution 360 -- the unlabelled 360p file passes the bar, the scene offers 720p."""
    for mod in (runner_extractors, runner_transport):
        monkeypatch.setattr(mod, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    scene = origin + "/video-18g8et87/scene"
    page.goto(scene, wait_until="domcontentloaded")
    r = _Runner(tmp_path, scene)
    r.jobs = {}                                   # not forced
    r.config["min_resolution"] = 360
    dl = _Download(f"{origin}/mp4/{UUID}/0/video_360p.mp4?e=1")
    took = r._below_min_resolution_by_file(page, scene, dl, {"score": 0, "_revealed_score": 360}, "video_360p.mp4")
    assert took is True, f"FX_XNXX_720_TO_SD_UNFORCED_FILE_SAVED_AT_360P: {r.updates}"
    assert dl.cancelled >= 1 and r.updates[-1][0] == "done", (dl.cancelled, r.updates)
    assert r.segmented and r.segmented[0].endswith("/hls-720p.m3u8"), r.segmented


def test_negative_control_unforced_scored_winner_is_not_second_guessed(origin, page, tmp_path, monkeypatch):
    """The pre-click gate judged an unforced scored winner; this check does not reopen it (unchanged)."""
    for mod in (runner_extractors, runner_transport):
        monkeypatch.setattr(mod, "db_log", lambda *a, **k: None)
    scene = origin + "/video-18g8et87/scene"
    page.goto(scene, wait_until="domcontentloaded")
    r = _Runner(tmp_path, scene)
    r.jobs = {}
    dl = _Download(f"{origin}/mp4/{UUID}/0/video_360p.mp4?e=1")
    assert r._below_min_resolution_by_file(page, scene, dl, {"score": 360}, "video_360p.mp4") is False
    assert dl.cancelled == 0 and r.segmented == [] and r.updates == []


# Rework r3 (bd-worker-A11-A): E1's scored twin. An unforced LABELLED row at or above a low bar passed the pre-click gate
# and returned before the own-media check, though the watcher had already seen the scene's 720p HLS on the wire (the live
# journal: "hls manifest detected: .../hls-720p-c262f.m3u8"). The check runs only when a detected manifest is taller than
# the file or of unknown tier, so a page with no adaptive stream pays nothing.
def _unforced_scored(origin, page, tmp_path, monkeypatch, manifests):
    for mod in (runner_extractors, runner_transport):
        monkeypatch.setattr(mod, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    scene = origin + "/video-18g8et87/scene"
    page.goto(scene, wait_until="domcontentloaded")
    r = _Runner(tmp_path, scene)
    r.jobs = {}                                   # not forced
    r.config["min_resolution"] = 360
    r.manifest_urls = [{"url": origin + m, "kind": "hls", "redirect_chain": []} for m in manifests]
    dl = _Download(f"{origin}/mp4/{UUID}/0/video_360p.mp4?e=1")
    took = r._below_min_resolution_by_file(page, scene, dl, {"score": 360}, "video_360p.mp4")
    return r, dl, took


@pytest.mark.parametrize("seen", [f"/tok/{UUID}/0/hls-720p-c262f.m3u8", f"/tok/{UUID}/0/hls.m3u8"])
def test_unforced_scored_row_gives_way_to_a_detected_taller_hls(origin, page, tmp_path, monkeypatch, seen):
    r, dl, took = _unforced_scored(origin, page, tmp_path, monkeypatch, [seen])
    assert took is True, f"FX_XNXX_720_TO_SD_UNFORCED_SCORED_ROW_SAVED_AT_360P: {r.updates}"
    assert dl.cancelled >= 1 and r.updates[-1][0] == "done", (dl.cancelled, r.updates)
    assert r.segmented and r.segmented[0].endswith("/hls-720p.m3u8"), r.segmented


@pytest.mark.parametrize("seen", [[], [f"/tok/{UUID}/0/hls-360p-59626.m3u8", f"/tok/{UUID}/0/hls-250p-e4f89.m3u8"]])
def test_negative_control_unforced_scored_row_with_no_taller_manifest_is_not_probed(
        origin, page, tmp_path, monkeypatch, seen):
    """Nothing taller (or nothing at all) was detected: the pre-click gate's verdict stands and no page probe runs."""
    probed = []
    monkeypatch.setattr(_Runner, "_fallback_to_page_media", lambda self, *a, **k: probed.append(a) or False,
                        raising=False)
    r, dl, took = _unforced_scored(origin, page, tmp_path, monkeypatch, seen)
    assert took is False and dl.cancelled == 0 and r.updates == [] and r.segmented == [], (took, r.updates)
    assert probed == [], f"a page with no taller adaptive stream paid for a page-media probe: {probed}"
