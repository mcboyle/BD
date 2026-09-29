"""fx-xnxx-preview (O1567, LIVE test1 .194 2026-09-29 20:32Z-20:38Z, results/test1/xnxx.md).

xnxx scene pages carry an ad player: a real <video><source src=".../<hash>.mp4"> whose file is a 15 s 640x360 or a
1 s 300x250 clip. The site's learned row selector `video source[src*='.mp4']` matched it, the learned direct-URL fast
path (a learned url_attribute, score 0) fetched it, and the job closed "Saved: celestial_x-15.mp4" done -- 861 KB
for a scene whose own player (setVideoHLS master) offers 720p. After Approve the same clip landed again (48 KB, 300x250).
The scene's own media was never consulted because the learned fast path has no height to judge.

Contract: a learned direct URL of UNKNOWN height is not taken over the scene's OWN player media. When the learned element is
a player's own <video>/<source>, the page-media path runs first: unforced it takes an option at min_resolution, else holds "Approve to force" naming the
scene's tallest; forced it takes the tallest scene-own option. The ad clip is transferred in none of the three cases.
When the scene has no media of its own the learned URL proceeds unchanged.

Hermetic: 127.0.0.1 origin serves the scene page (live shapes: inline setVideo* script + a DOM ad <video><source>) and
real HLS renditions built by ffmpeg. Real chromium page (BD's cloak); real hls_downloader + ffprobe. Only the direct
HTTP transfer is a recorder.
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

UUID = "c36c20ec-00a3-4de0-a813-b5e958d53c6f"
MASTER_1080 = (
    "#EXTM3U\n"
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=763904,RESOLUTION=854x480,NAME="480p"\nhls-480p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1327104,RESOLUTION=1280x720,NAME="720p"\nhls-720p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=2760704,RESOLUTION=1920x1080,NAME="1080p"\nhls-1080p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=423936,RESOLUTION=640x360,NAME="360p"\nhls-360p.m3u8\n')
MASTER_720 = "\n".join(ln for ln in MASTER_1080.split("\n") if "1080" not in ln) + "\n"
AD_LEAF = "995b7ae9e56f17cbf0e73f52dce0e06032105de3.mp4"


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


def _scene(base, hls_path):
    player = ""
    if hls_path:
        player = (f"html5player.setVideoUrlLow('{base}/mp4/{UUID}/0/video_240p.mp4?e=1');"
                  f"html5player.setVideoUrlHigh('{base}/mp4/{UUID}/0/video_360p.mp4?e=1');"
                  f"html5player.setVideoHLS('{base}{hls_path}');")
    return ("<!doctype html><html><head><title>Scene - XNXX.COM</title></head><body>"
            f'<div id="ad"><video muted><source src="{base}/ads/{AD_LEAF}" type="video/mp4"></video></div>'
            '<div id="video-player-bg"><a class="download" href="#">Download</a></div>'
            "<script>var html5player = {setVideoUrlLow() {}, setVideoUrlHigh() {}, setVideoHLS() {}};"
            f"{player}</script></body></html>").encode()


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    root = tmp_path_factory.mktemp("cdn")
    for name, size in (("480p", "854x480"), ("720p", "1280x720"), ("1080p", "1920x1080"), ("360p", "640x360")):
        subprocess.run([_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=5",
                        "-t", "2", "-pix_fmt", "yuv420p", "-g", "5", "-f", "hls", "-hls_time", "1",
                        "-hls_segment_filename", str(root / f"seg-{name}-%d.ts"), str(root / f"hls-{name}.m3u8")],
                       check=True, timeout=120)
    subprocess.run([_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=300x250:rate=5",
                    "-t", "1", "-pix_fmt", "yuv420p", str(root / "ad.mp4")], check=True, timeout=60)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), None)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    fixed = {
        "/video-a1080/scene": _scene(base, f"/tok/{UUID}/0/hls.m3u8"),
        "/video-a720/scene": _scene(base, f"/tokb/{UUID}/0/hls.m3u8"),
        "/video-anone/scene": _scene(base, None),
        # A paysite shape: a learned download LINK to the full file beside a player streaming a trailer.
        "/pay/scene": ("<!doctype html><html><body><div id=\"player\"><script>var html5player = "
                       f"{{setVideoHLS() {{}}}}; html5player.setVideoHLS('{base}/tok/{UUID}/0/hls.m3u8');</script></div>"
                       f"<a class=\"dl\" href=\"{base}/full/scene_4k_full.mp4\">Download Full Movie</a></body></html>").encode(),
        f"/tok/{UUID}/0/hls.m3u8": MASTER_1080.encode(),
        # GEN 3 (lens B13-B F1): a tube scene whose OWN player <source> is the full file (no height in its name)
        # beside a related-rail preview <video> of ANOTHER scene labelled 1080p.
        "/tube/scene": ("<!doctype html><html><body><div id=\"player\"><video id=\"main\" controls>"
                        f"<source src=\"{base}/v/scene_main_file.mp4\" type=\"video/mp4\"></video></div>"
                        f"<aside class=\"related\"><video muted preload=\"none\" src=\"{base}/rel/other-scene-1080p.mp4\"></video></aside>"
                        "</body></html>").encode(),
        f"/tokb/{UUID}/0/hls.m3u8": MASTER_720.encode(),
    }

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            leaf = path.rsplit("/", 1)[-1]
            if path in fixed:
                body = fixed[path]
            elif path == f"/ads/{AD_LEAF}":
                body = (root / "ad.mp4").read_bytes()
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


class _Runner(runner_transport.TransportMixin, runner_extractors.ExtractorsMixin):
    _PAGE_MEDIA_WAIT_S = 1.0
    site_id = "fxxnxxpreview"

    def __init__(self, tmp_path, forced):
        self.config = {"name": "xnxx", "download_dir": str(tmp_path / "dl"), "min_resolution": 1080,
                       "learned": {"download": {"row_selectors": ["video source"], "url_attribute": "src"}}}
        self.jobs = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.updates, self.events, self.segmented, self.direct = [], [], [], []
        self._spa_api_capture = None
        self._forced = forced

    def _update_job(self, url, status, message="", **extra):
        self.updates.append((status, message))

    def log_event(self, kind, message="", url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return ""

    def _build_mirror_urls(self, file_url):
        return []

    def _probe_for_higher_tier(self, file_url, referer=""):
        return file_url

    def _run_http_attempts_with_resume(self, page_url, page, ctx, file_url, attempt_urls, final_path):
        self.direct.append(file_url)   # the learned fast path's transfer: recorded, never performed
        raise RuntimeError(f"DIRECT_TRANSFER {file_url}")

    def _do_direct_http_download(self, page_url, file_url, output_path, referer="", **kw):
        self.direct.append(file_url)
        raise RuntimeError(f"DIRECT_TRANSFER {file_url}")

    def _hls_download_guarded(self, _hls, manifest_url, output_path, **kwargs):
        self.segmented.append(manifest_url)
        return _hls.download(manifest_url, output_path, **kwargs)

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


@pytest.fixture
def run(origin, page, tmp_path, monkeypatch):
    for mod in (runner_extractors, runner_transport):
        monkeypatch.setattr(mod, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})

    def _run(route, forced=False):
        scene = origin + route
        page.goto(scene, wait_until="domcontentloaded")
        r = _Runner(tmp_path, forced)
        if forced:
            r.jobs[scene] = {"force_download": True}
        loc = page.locator("video source").first
        # Positive control: the learned selector really matches the ad <source>.
        assert (loc.get_attribute("src") or "").endswith(AD_LEAF)
        best = {"_via_learned": True, "_learned_sel": "video source", "score": 0, "locator": loc, "text": ""}
        try:
            r._do_download(page, None, scene, best, tmp_path / "dl", "")
        except Exception as e:  # noqa: BLE001 -- the recorder raises on any ad transfer
            r.raised = repr(e)
        return r, sorted(p for p in (tmp_path / "dl").rglob("*") if p.is_file())

    return _run


def test_the_scenes_own_1080p_variant_lands_not_the_learned_ad_clip(run):
    r, landed = run("/video-a1080/scene")
    assert r.direct == [] and not any(_probe_height(p) == 250 for p in landed), (
        f"FX_XNXX_PREVIEW_AD_CLIP_TRANSFERRED: {r.direct} {[p.name for p in landed]}")
    assert r.segmented and r.segmented[0].endswith("/hls-1080p.m3u8"), (r.segmented, r.updates, r.events)
    assert len(landed) == 1 and _probe_height(landed[0]) == 1080, landed


def test_a_scene_that_tops_out_at_720_holds_for_approval_and_transfers_nothing(run):
    r, landed = run("/video-a720/scene")
    assert r.direct == [] and not any(_probe_height(p) == 250 for p in landed), (
        f"FX_XNXX_PREVIEW_AD_CLIP_TRANSFERRED: {r.direct} {[p.name for p in landed]}")
    assert r.updates and r.updates[-1][0] == "needs_review" and "720p" in r.updates[-1][1], r.updates
    assert r.segmented == [] and landed == []


def test_approved_it_takes_the_scenes_720p_not_the_ad_clip(run):
    r, landed = run("/video-a720/scene", forced=True)
    assert r.direct == [] and not any(_probe_height(p) == 250 for p in landed), (
        f"FX_XNXX_PREVIEW_AD_CLIP_TRANSFERRED: {r.direct} {[p.name for p in landed]}")
    assert r.segmented and r.segmented[0].endswith("/hls-720p.m3u8"), (r.segmented, r.updates, r.events)
    assert len(landed) == 1 and _probe_height(landed[0]) == 720, landed


def test_a_page_without_scene_media_keeps_the_learned_url(run):
    """Negative control: nothing of the scene's own -> the learned fast path is unchanged (it fetches the leaf)."""
    r, landed = run("/video-anone/scene")
    assert r.direct and r.direct[0].endswith(AD_LEAF), (r.direct, r.updates, r.events, getattr(r, "raised", None), [p.name for p in landed])
    assert r.segmented == []


def test_a_learned_download_link_is_not_replaced_by_the_players_stream(origin, page, tmp_path, monkeypatch):
    """Control (lens B9-B, O1568): the page-media-first step is for a learned PLAYER source only. A learned
    download link (<a href>, score 0) to the full file keeps the learned fast path even when the page's player
    streams a 1080p trailer -- the trailer is not the scene file."""
    for mod in (runner_extractors, runner_transport):
        monkeypatch.setattr(mod, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    scene = origin + "/pay/scene"
    page.goto(scene, wait_until="domcontentloaded")
    r = _Runner(tmp_path, False)
    r.config["learned"] = {"download": {"row_selectors": ["a.dl"], "url_attribute": "href"}}
    loc = page.locator("a.dl").first
    assert (loc.get_attribute("href") or "").endswith("scene_4k_full.mp4")
    best = {"_via_learned": True, "_learned_sel": "a.dl", "score": 0, "locator": loc, "text": "Download Full Movie"}
    try:
        r._do_download(page, None, scene, best, tmp_path / "dl", "")
    except Exception as e:  # noqa: BLE001 -- the recorder raises on the learned transfer
        r.raised = repr(e)
    assert r.direct and r.direct[0].endswith("scene_4k_full.mp4") and r.segmented == [], (
        f"FX_XNXX_PREVIEW_LEARNED_LINK_REPLACED_BY_PLAYER_STREAM direct={r.direct} segmented={r.segmented} "
        f"updates={r.updates[-2:]}")



def test_a_related_scenes_preview_never_replaces_a_learned_scene_source(origin, page, tmp_path, monkeypatch):
    """GEN 3 (lens B13-B F1, O1568): the page-media-first step asks only the scene's OWN player (scene_own_only:
    a KVS flashvars config or the page's WGCZ html5player). A related rail's <video> labelled 1080p is another
    scene; the learned player <source> (the scene file, no height in its name) is fetched instead."""
    for mod in (runner_extractors, runner_transport):
        monkeypatch.setattr(mod, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    scene = origin + "/tube/scene"
    page.goto(scene, wait_until="domcontentloaded")
    r = _Runner(tmp_path, False)
    r.config["learned"] = {"download": {"row_selectors": ["#main source"], "url_attribute": "src"}}
    loc = page.locator("#main source").first
    assert (loc.get_attribute("src") or "").endswith("scene_main_file.mp4")
    best = {"_via_learned": True, "_learned_sel": "#main source", "score": 0, "locator": loc, "text": ""}
    try:
        r._do_download(page, None, scene, best, tmp_path / "dl", "")
    except Exception as e:  # noqa: BLE001 -- the recorder raises on the learned transfer
        r.raised = repr(e)
    assert r.direct and r.direct[0].endswith("scene_main_file.mp4") and r.segmented == [], (
        f"FX_XNXX_PREVIEW_RELATED_PREVIEW_TAKEN direct={r.direct} segmented={r.segmented} updates={r.updates[-2:]}")
