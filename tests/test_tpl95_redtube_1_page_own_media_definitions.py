"""tpl95-redtube-1 (queues/APP-DL-FIX-QUEUE.tsv, HIGH).

Evidence: harness-work/UIUX-20260928/download-95/B4-B/tpl-redtube/events.jsonl#run_done and
harness-work/UIUX-20260928/B4-B/tpl95/redtube-template-request.json. With the redtube template applied
(row_selectors ``video.mgp_videoElement source[type='video/mp4']``, ``video.mgp_videoElement``) both scene
jobs ended needs_review "no dl event; scored ok but no download fired; saw: 4K(?):BRAZZERS 4K
https://a.adtng.co | 720p(?):HD /redtube/hd | ...": the DOM scorer picked an ad, then a nav link.

Measured cause (bd-worker-A9-A live probe, harness-work/FIX/tpl95-cumlouder-1-bd-worker-A9-A/REDTUBE-HANDBACK.md):
the <video> has no src until played and then only a ~4 MB preview (pre_videos/.../mp4_720.mp4); the scene's
files are in the page's player config ``mediaDefinitions`` as INDIRECT entries (``/media/mp4?s=``,
``/media/hls?s=``) that answer a JSON list of per-quality files. BD's Aylo page extractor (which runs before
find_best_download) never ran: redtube.com was not an Aylo host, the config is not a ``flashvars_<id>``
object, and an indirect entry would have been downloaded as a JSON document.

Rule: on a redtube scene page the page's own mediaDefinitions are resolved through the page's session and
the preferred-quality scene file is dispatched before the DOM scorer can pick an ad or nav link.

Hermetic: a local HTTP server; real headless Chromium with --host-resolver-rules mapping the redtube /
rdtcdn / adtng hosts to it and every other host to NOTFOUND. The runner is ExtractorsMixin with recording
stubs for its side-effect methods; the real ``_try_aylo_extractor`` runs on the real page.
"""
from __future__ import annotations

import ast
import http.server
import json
import threading
from pathlib import Path

import pytest

from bulk_downloader import detect, extractors_aylo, runner_extractors

BD_GATE_SCOPE = "module"

SCENE = "http://www.redtube.com/191397851"
CDN = "http://ev-ph.rdtcdn.com/videos/202411/11/460383961"
FILE_720 = f"{CDN}/720P_4000K_460383961.mp4?validfrom=1&validto=2&hash=h720"
FILE_480 = f"{CDN}/480P_2000K_460383961.mp4?validfrom=1&validto=2&hash=h480"
FILE_240 = f"{CDN}/240P_1000K_460383961.mp4?validfrom=1&validto=2&hash=h240"
HLS_720 = f"{CDN}/720P_4000K_460383961.mp4/master.m3u8?hdnea=h"
PREVIEW = "http://evtubescms.phncdn.com/pre_videos/002/479/741/2479741/mp4_720.mp4"
AD = "http://a.adtng.com/get/10002809?time=1&ata=brazzers"
TEMPLATE_LEARNED = {"row_selectors": ["video.mgp_videoElement source[type='video/mp4']",
                                      "video.mgp_videoElement"],
                    "url_attribute": "src"}

MEDIA_DEFS = [
    {"format": "hls", "videoUrl": "http://www.redtube.com/media/hls?s=eyJrIjoiaGxzIn0", "remote": True,
     "quality": [], "defaultQuality": False},
    {"format": "mp4", "videoUrl": "http://www.redtube.com/media/mp4?s=eyJrIjoibXA0In0", "remote": True,
     "quality": [], "defaultQuality": False},
]
MP4_LIST = [
    {"defaultQuality": True, "format": "mp4", "videoUrl": FILE_720, "quality": "720", "remote": True},
    {"defaultQuality": False, "format": "mp4", "videoUrl": FILE_480, "quality": "480", "remote": True},
    {"defaultQuality": False, "format": "mp4", "videoUrl": FILE_240, "quality": "240", "remote": True},
]
HLS_LIST = [{"defaultQuality": True, "format": "hls", "videoUrl": HLS_720, "quality": "720"}]


def _config_script(shell: str) -> str:
    # json.dumps escapes "/" as-is; the live page writes "https:\/\/" -- mirror that.
    defs = json.dumps(MEDIA_DEFS).replace("/", "\\/")
    if shell == "playervars":
        # redtube: the config is a page_params object, not a flashvars_<id> object.
        return ("var page_params = {}; page_params.video_player_setup = {\"playervars\": "
                "{\"video_id\": 191397851, \"mediaDefinitions\": " + defs + ", \"autoplay\": false}};")
    return 'var flashvars_191397851 = {"video_title": "Scene", "mediaDefinitions": ' + defs + '};'


def _scene_html(shell: str) -> bytes:
    return f"""<!doctype html><html><head><title>Scene - Free Sex Video - RedTube</title>
<script>{_config_script(shell)}</script></head><body>
<header><nav><a href="/redtube/hd">HD</a> <a href="/mobile">View Mobile Version</a></nav></header>
<div id="age_check"><a id="btn_agree" class="removeAdLink" href="#">I am 18 or older - Enter</a></div>
<div class="video-wrapper"><div class="mgp_videoWrapper">
<video class="mgp_videoElement" controlslist="nodownload" playsinline preload="none" src="{PREVIEW}"
 width="1280" height="720"><source class="mgp_sourceElement" preload="none"></video></div></div>
<div class="ad-box"><a href="{AD}" target="_blank" rel="nofollow">BRAZZERS 4K</a></div>
<div id="cookie_modal"><button id="modal_consent_accept_essential">Save and Close</button></div>
</body></html>""".encode()


@pytest.fixture(scope="module")
def server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path
            if path.startswith("/191397851"):
                shell = "flashvars" if "shell=flashvars" in path else "playervars"
                code, ctype, body = 200, "text/html; charset=UTF-8", _scene_html(shell)
            elif path.startswith("/media/mp4"):
                code, ctype, body = 200, "application/json", json.dumps(MP4_LIST).encode()
            elif path.startswith("/media/hls"):
                code, ctype, body = 200, "application/json", json.dumps(HLS_LIST).encode()
            else:
                code, ctype, body = 404, "text/plain", b"nope"
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
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(5)


@pytest.fixture(scope="module")
def browser(server):
    from playwright.sync_api import sync_playwright
    rules = ",".join([f"MAP www.redtube.com 127.0.0.1:{server}",
                      f"MAP ev-ph.rdtcdn.com 127.0.0.1:{server}",
                      f"MAP a.adtng.com 127.0.0.1:{server}",
                      "MAP * ~NOTFOUND"])
    pw = sync_playwright().start()
    b = None
    try:
        b = pw.chromium.launch(headless=True, args=["--no-sandbox", f"--host-resolver-rules={rules}"])
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


class _Runner(runner_extractors.ExtractorsMixin):
    site_id = "tpl95redtube"

    def __init__(self, download_dir):
        # min_resolution unset: the app default DEFAULT_MIN_RESOLUTION applies.
        self.config = {"name": "redtube", "download_dir": str(download_dir),
                       "quality_preference": "1080,720,480"}
        self.jobs = {}
        self.dispatched, self.events, self.jobs_seen = [], [], []

    def _update_job(self, url, status, message="", **k):
        self.jobs_seen.append((status, message))

    def log_event(self, kind, message="", **k):
        self.events.append((kind, message))

    def _probe_for_higher_tier(self, url, *, referer=""):
        return url

    def _do_direct_http_download(self, *, page_url, file_url, output_path, referer=""):
        self.dispatched.append(file_url)
        Path(output_path).write_bytes(b"\x00" * 64)
        return True

    def _hls_download_guarded(self, _hls, manifest_url, output_path, **kw):
        raise AssertionError(f"HLS dispatched while a same-tier MP4 exists: {manifest_url}")

    def _embed_metadata_if_mp4(self, *a, **k):
        pass

    def _size_on_disk_after_tagging(self, output_path, downloaded_size):
        return downloaded_size


def _goto(page, shell):
    url = SCENE if shell == "playervars" else f"{SCENE}?shell=flashvars"
    page.goto(url, wait_until="domcontentloaded")
    return url


def test_control_the_dom_scorer_does_not_find_the_scene_file(server, page):
    """Defect input: with the applied template row the DOM scorer never names a scene file -- the <video> holds
    only the preview and the <source> has no src; whatever it returns is the preview, an ad or a nav link."""
    _goto(page, "playervars")
    best = detect.find_best_download(page, "", learned=TEMPLATE_LEARNED)
    got = json.dumps(best, default=str) if best else ""
    assert "460383961" not in got, f"fixture drifted: the DOM scorer found a scene file: {got[:300]}"


def test_redtube_is_routed_to_the_page_extractor():
    assert extractors_aylo.is_aylo_url("https://www.redtube.com/191397851") is True, (
        "tpl95-redtube-1 RED: redtube.com is not an Aylo host, so the page's own mediaDefinitions are never "
        "read and the DOM scorer's ad/nav winner decides the job")
    assert extractors_aylo.is_aylo_url("https://www.redtubefan.example/1") is False
    assert extractors_aylo.is_free_tube_url("https://www.redtube.com/191397851") is True
    assert extractors_aylo.is_free_tube_url("https://site-ma.brazzers.com/scene/1") is False


@pytest.mark.parametrize("shell", ["playervars", "flashvars"])
def test_scene_page_dispatches_the_720p_scene_file_not_an_ad_or_nav(server, page, tmp_path, monkeypatch, shell):
    logged = []
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: logged.append(a))
    url = _goto(page, shell)
    r = _Runner(tmp_path)
    r.config["min_resolution"] = 480   # explicitly low: 720p clears the floor

    took_over = r._try_aylo_extractor(url, page)

    assert took_over is True and r.dispatched == [FILE_720], (
        f"tpl95-redtube-1 RED ({shell}): the page's own mediaDefinitions did not yield the 720p scene file; "
        f"took_over={took_over} dispatched={r.dispatched} events={r.events}")
    assert not any("/media/" in u or "adtng" in u or "/redtube/hd" in u or "pre_videos" in u
                   for u in r.dispatched)
    assert logged and logged[0][3] == "done", logged


def test_indirect_entries_are_never_picked_as_files():
    """Without the page (no session to resolve them) an indirect-only config yields no variant rather than a
    JSON endpoint downloaded as an .mp4."""
    html = "<script>" + _config_script("flashvars") + "</script>"
    res = extractors_aylo.extract_from_html(html, quality_pref=[1080, 720, 480])
    assert res.ok is False, f"a JSON endpoint was picked as the file: {res.variant}"


def test_direct_flashvars_entries_still_win_unchanged():
    """Regression control for the paysite brands: a direct flashvars config is picked exactly as before."""
    html = ('<script>var flashvars_1 = {"video_title": "T", "mediaDefinitions": ['
            '{"format": "mp4", "videoUrl": "https://cdn.example/a_1080.mp4", "quality": "1080"},'
            '{"format": "mp4", "videoUrl": "https://cdn.example/a_720.mp4", "quality": "720"}]};</script>')
    res = extractors_aylo.extract_from_html(html, quality_pref=[720])
    assert res.ok and res.variant.url == "https://cdn.example/a_720.mp4"


def test_process_one_runs_the_page_extractor_before_the_dom_scorer():
    src = (Path(runner_extractors.__file__).parent / "runner.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_process_one")
    aylo = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Attribute) and n.attr == "_try_aylo_extractor"]
    scorer = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Name) and n.func.id == "find_best_download"]
    assert aylo and scorer and min(aylo) < min(scorer), (aylo, scorer)


# ---- R1 (lens B13-B F1): the free-tube route holds the min-resolution floor ----
def test_default_floor_unapproved_720_is_held_for_review_not_saved(server, page, tmp_path, monkeypatch):
    """Adopted from the B13-B probe: min_resolution unset (DEFAULT_MIN_RESOLUTION) and the job not approved --
    the 720p scene file is below the floor, so nothing is saved and the job waits for Approve."""
    DEFAULT_MIN_RESOLUTION = runner_extractors.DEFAULT_MIN_RESOLUTION
    assert DEFAULT_MIN_RESOLUTION > 720, "fixture premise: the default floor is above the 720p scene file"
    logged = []
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: logged.append(a))
    url = _goto(page, "playervars")
    r = _Runner(tmp_path)

    took = r._try_aylo_extractor(url, page)

    assert r.dispatched == [], (
        f"tpl95-redtube-1 R1 RED: redtube 720p saved under the {DEFAULT_MIN_RESOLUTION}p floor: {r.dispatched}")
    assert took is True, "a held job is handled, not handed to the DOM scorer"
    assert list(tmp_path.iterdir()) == []
    status, message = r.jobs_seen[-1]
    assert status == "needs_review", r.jobs_seen
    assert message.startswith(f"Best is 720p (below {DEFAULT_MIN_RESOLUTION}p) — Approve to force"), message
    assert logged and logged[-1][3] == "needs_review", logged


def test_default_floor_forced_job_still_dispatches_720(server, page, tmp_path, monkeypatch):
    """Control: the same page at the default floor on an approved (force_download) job downloads the 720p file."""
    logged = []
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: logged.append(a))
    url = _goto(page, "playervars")
    r = _Runner(tmp_path)
    r.jobs = {url: {"force_download": True}}

    took = r._try_aylo_extractor(url, page)

    assert took is True and r.dispatched == [FILE_720], (took, r.dispatched, r.jobs_seen)
    assert logged and logged[-1][3] == "done", logged
