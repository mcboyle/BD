"""dl95-youjizz-1: the page-media fallback fetched 240p of a 720p scene and
saved it as ``master.mp4``.

MEASURED on test2 2026-09-29 (site youjizz 65bfc6bc, scene
``/videos/best-blowjob-ever-by-a-milf-34404551.html``; evidence
``harness-work/UIUX-20260928/download-95/A8-A/p1/youjizz/``): "API/media:
downloading 240p (page-media)" landed ``youjizz/master.mp4`` -- h264 426x240.

MEASURED on the live page (hub Chromium, ``harness-work/FIX/
dl95-youjizz-1-bd-worker-B13-B/LIVE-PROBE-page-media.txt``): the player's media
is ONE HLS master, ``...-,426-240-175,640-360-287,852-480-305,1280-720-696,
-h264.mp4.urlset/master.m3u8``, whose EXT-X-STREAM-INF list is 426x240 FIRST,
then 640x360, 852x480, 1280x720.  ``_height_of`` read "240" from the URL digits
and ``hls_downloader`` maps ``0:v:0`` -- the first variant.

The contract: an HLS master is ranked and fetched by its best variant (RFC 8216
rendition set of one presentation), and a page-derived file is named from the
scene title.  Inline page JSON is NOT a source: three lens REFUTEs showed an
inline list cannot be tied to the scene.  ``.example`` hosts; nothing live.
"""

# The gate parses a module-level ASSIGNMENT, not a docstring line.
BD_GATE_SCOPE = "module"

import json
import os
from contextlib import contextmanager

import pytest

ORIGIN = "https://www.youjizz.example"
SCENE_URL = ORIGIN + "/videos/best-blowjob-ever-by-a-milf-34404551.html"
_Q = "?validfrom=0&validto=0&rate=0&hash=0"
_STEM = "8ae5e18a6ddc50eb5d210506d94041da1479124218"
_TIERS = [("240", "426-240-175"), ("288", "512-288-338"), ("360", "640-360-287"),
          ("480", "852-480-305"), ("720", "1280-720-696")]
MP4 = {q: f"//cdne-mobile.youjizz.example/videos/8/a/e/5/e/{_STEM}-{d}-h264.mp4{_Q}"
       for q, d in _TIERS}
HLS = {q: f"//abre-videos.youjizz.example/_hls/videos/8/a/e/5/e/{_STEM}-{d}-h264.mp4/master.m3u8{_Q}"
       for q, d in _TIERS}
_URLSET = (f"https://abre-videos.youjizz.example/_hls/videos/8/a/e/5/e/{_STEM}"
           "-,426-240-175,640-360-287,852-480-305,1280-720-696,-h264.mp4.urlset/")
MASTER = _URLSET + "master.m3u8" + _Q
# The live master's shape: 240p listed first (measured), signing zeroed.
MASTER_BODY = "#EXTM3U\n" + "".join(
    f"#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH={bw},RESOLUTION={res},"
    f'FRAME-RATE=24.000,CODECS="avc1.640029,mp4a.40.2"\nindex-f{n}-v1-a1.m3u8{_Q}\n'
    for n, bw, res in [(1, 168800, "426x240"), (2, 280888, "640x360"),
                       (3, 299155, "852x480"), (4, 712704, "1280x720")])
BEST_VARIANT = _URLSET + "index-f4-v1-a1.m3u8" + _Q
TITLE = "Best Blowjob Ever By A Milf"


def _scene_html(with_list=False, src=None):
    """The scene page; ``with_list`` adds the site's inline rendition JSON,
    which must NOT be a source."""
    script = ""
    if with_list:
        enc = [{"quality": q, "filename": MP4[q], "name": f"{q}p"} for q, _d in _TIERS]
        script = f"<script>var dataEncodings = {json.dumps(enc)};</script>"
    return ("<!doctype html><html><head><title>" + TITLE + "</title></head><body>"
            '<div id="content">' + script
            + '<video id="player" src="' + (src or "https:" + HLS["240"]) + '"></video>'
            "</div></body></html>")


CHROME = os.environ.get("BD_PW_CHROME", "")


def _launch(p):
    from playwright.sync_api import Error as PWError
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    kw = {"executable_path": CHROME} if CHROME and os.path.exists(CHROME) else {}
    try:
        return p.chromium.launch(headless=True, timeout=20000, args=args, **kw)
    except PWError as e:
        pytest.fail(f"chromium not launchable here -- a launch failure is a "
                    f"FAILURE, not a skip (T5): {e}")


@contextmanager
def _page(html, master_status=200):
    from playwright.sync_api import sync_playwright

    def _serve(route, request):
        cors = {"Access-Control-Allow-Origin": ORIGIN,
                "Access-Control-Allow-Credentials": "true"}
        if request.url == SCENE_URL:
            route.fulfill(status=200, content_type="text/html", body=html)
        elif request.url == MASTER:
            route.fulfill(status=master_status, headers=cors,
                          content_type="application/vnd.apple.mpegurl",
                          body=MASTER_BODY if master_status == 200 else "")
        else:
            route.fulfill(status=404, headers=cors, body="")

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            page.route("**/*", _serve)
            page.goto(SCENE_URL, wait_until="load")
            yield page
        finally:
            browser.close()


class _HlsResult:
    ok = True
    error = ""
    bytes_written = 16


class _Stub:
    site_id = "fixture-yj1"

    def __init__(self, tmp_path):
        # min_resolution 0: the tier pick is under test, not main's 1080p hold.
        self.config = {"name": "youjizz", "download_dir": str(tmp_path), "min_resolution": 0}
        self.jobs = []
        self.events = []
        self.transfers = []
        self._spa_api_capture = None

    def _update_job(self, url, status, message, **extra):
        self.jobs.append((status, message, extra))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        self.transfers.append({"file_url": file_url, "output_path": output_path})
        with open(output_path, "wb") as fh:
            fh.write(b"\x00" * 16)
        return True

    def _hls_download_guarded(self, _hls, file_url, output_path, **_kw):
        self.transfers.append({"file_url": file_url, "output_path": output_path})
        return _HlsResult()

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


def _run(tmp_path, monkeypatch, html, master_status=200):
    from bulk_downloader import hls_downloader
    from bulk_downloader import runner_extractors as rx
    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs",
                        lambda *a, **k: {"title": TITLE})
    monkeypatch.setattr(hls_downloader, "is_available", lambda: True)
    runner = type("StubRunner", (_Stub, rx.ExtractorsMixin), {})(tmp_path)
    with _page(html, master_status) as page:
        took = runner._try_spa_api_media_extractor(SCENE_URL, page)
    assert took is True, runner.events
    assert len(runner.transfers) == 1, runner.transfers
    return runner.transfers[0]


def test_the_hls_master_s_best_variant_is_fetched_not_its_first(tmp_path, monkeypatch):
    t = _run(tmp_path, monkeypatch, _scene_html(src=MASTER))
    assert t["file_url"] == BEST_VARIANT, (
        "DL95-YJ1: the player's HLS master lists 240/360/480/720p but the "
        f"fallback handed {t['file_url']!r} to the transfer -- the master itself "
        "(whose first variant is 240p) instead of its 1280x720 variant")


def test_the_file_is_named_from_the_scene_title_not_the_cdn_leaf(tmp_path, monkeypatch):
    t = _run(tmp_path, monkeypatch, _scene_html(src=MASTER))
    name = os.path.basename(t["output_path"])
    assert TITLE in name and "master" not in name and "index" not in name, (
        f"DL95-YJ1: page-media file named {name!r}; a CDN leaf is not a name, "
        f"the scene title {TITLE!r} is")


def test_control_an_unreadable_master_is_fetched_as_before(tmp_path, monkeypatch):
    """Fail-safe: no variant list -> today's behaviour, the bound URL."""
    t = _run(tmp_path, monkeypatch, _scene_html(src=MASTER), master_status=403)
    assert t["file_url"] == MASTER, t


def test_control_inline_page_json_is_never_a_source(tmp_path, monkeypatch):
    """The site's own dataEncodings list, bound media not in it: the bound
    file is fetched (lens REFUTEs r1-r3: inline lists are not scene-bound)."""
    t = _run(tmp_path, monkeypatch, _scene_html(with_list=True))
    assert t["file_url"] == "https:" + HLS["240"], t


@pytest.mark.parametrize("own,other", [
    ("https://cdn.example/downloads/12345678/240p.mp4",
     "https://cdn.example/downloads/87654321/2160p.mp4"),
    ("https://cdn.example/20260929/scene-alpha/240p.mp4",
     "https://cdn.example/20260929/scene-beta/2160p.mp4"),
])
def test_control_another_work_in_an_inline_playlist_is_never_fetched(
        tmp_path, monkeypatch, own, other):
    """The reviewer's r2/r3 repros (shared directory, shared date bucket)."""
    playlist = [{"height": 240, "url": own}, {"height": 2160, "url": other}]
    html = ('<video src="' + own + '"></video><script>var playlist = '
            + json.dumps(playlist) + ";</script>")
    t = _run(tmp_path, monkeypatch, html)
    assert t["file_url"] == own, t


ALT_AUDIO_BODY = ("#EXTM3U\n"
                  '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="en",DEFAULT=YES,URI="audio.m3u8"\n'
                  '#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,AUDIO="aud"\nv360.m3u8\n'
                  '#EXT-X-STREAM-INF:BANDWIDTH=2400000,RESOLUTION=1280x720,AUDIO="aud"\nv720.m3u8\n')


def test_control_a_master_with_a_separate_audio_group_is_not_split(tmp_path, monkeypatch):
    """Lens REFUTE r4 (bd-cx-worker-2): a video-only variant of an alt-audio
    master would lose the sound.  Such a master is fetched as itself."""
    global MASTER_BODY
    saved, MASTER_BODY = MASTER_BODY, ALT_AUDIO_BODY
    try:
        t = _run(tmp_path, monkeypatch, _scene_html(src=MASTER))
    finally:
        MASTER_BODY = saved
    assert t["file_url"] == MASTER, (
        "DL95-YJ1: a master whose variants reference an EXT-X-MEDIA audio group "
        f"was replaced by one variant {t['file_url']!r}; the audio would be lost")


def test_master_parser_reads_resolution_not_list_order():
    from bulk_downloader import spa_media_extract as spa
    variants = spa.hls_master_variants(MASTER_BODY, MASTER)
    assert [v["height"] for v in variants] == [240, 360, 480, 720]
    assert spa.best_hls_variant(variants)["url"] == BEST_VARIANT
    assert spa.hls_master_variants("#EXTM3U\n#EXTINF:4.0,\nseg-1.ts\n", MASTER) == []
    assert spa.hls_master_variants(ALT_AUDIO_BODY, MASTER) == []
