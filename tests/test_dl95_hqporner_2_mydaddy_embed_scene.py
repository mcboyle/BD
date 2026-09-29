"""dl95-hqporner-2: hqporner's scene player is a cross-origin //mydaddy.cc/video/<id>/ iframe.

Live on test2 (harness-work/UIUX-20260928/download-95/A8-A/p1/hqporner/RESULT.md, bytes-landed.txt):
  run 1 (v1710, /hdporn/127983) chose one top-page clip ("chose 0p from page-media") and saved a 5.9 s 854x480
  file as DONE -- a preview/ad, not the scene;
  run 2 (v1711, /hdporn/127984) failed "[page_shape] No download button found": the mydaddy.cc iframe was never
  entered.
The page shape below is the real-page probe of /hdporn/127984 (harness-work/FIX/dl95-hqporner-2-bd-worker-A2-A/
probe-127984-frames.json): top page with no <video>; a 1323x110 banner iframe; the player iframe
src="//mydaddy.cc/video/<id>/" (560x350 attributes, laid out 856x480) whose <video> lists typed <source>s
360.mp4 / 720.mp4 / 1080.mp4 on s1.bigcdn.cc titled "360p" / "720p HD" / "1080p Full HD"; a hidden mydaddy.cc/hf.html
frame playing a 7 s bkcdn.net ad clip (untyped <source>). Run 1's top-page URL was not recorded (the ad rotates),
so the captured seconds-long bkcdn clip stands in for it.

The halves landed on main as dl95-fullporner-1 (embed frames join the ranking, 6e2cc464b) and dl95-cumlouder-3
GEN 2 (a seconds-long pick of unknown height is held, 9aacef35e); this pins hqporner's exact shape end to end
through the production _try_spa_api_media_extractor.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

BD_GATE_SCOPE = "module"

SCENE = "https://hqporner.com/hdporn/127984-fixing_problems_like_a_mommy.html"
PLAYER = "https://mydaddy.cc/video/0bdfd2a67deb53a4ca/"
HF_AD = "https://mydaddy.cc/hf.html"
BANNER = "https://a.adtng.com/get/10013050?time=1637006605959"
CDN = "https://s1.bigcdn.cc/pubs/6abb2398130f62.39031172/"
AD_CLIP = "https://z6v2p9a8.bkcdn.net/library/44177/49aa9130f6d7601ad5c51731d15c447eaa947afb.mp4"

_PLAYER_WITH_SOURCES = f"""<!doctype html><html><body style="margin:0">
<video id="player" preload="none" style="width:100%;height:100%">
  <source src="{CDN}360.mp4" type="video/mp4" title="360p">
  <source src="{CDN}720.mp4" type="video/mp4" title="720p HD">
  <source src="{CDN}1080.mp4" type="video/mp4" title="1080p Full HD">
</video>
<iframe src="/hf.html" width="0" height="0" frameborder="0"></iframe>
</body></html>"""

# The app's page shot (05_app_page_shot_127984.png): the player box still says "LOADING MAY TAKE SOME TIME".
_PLAYER_LOADING = """<!doctype html><html><body style="margin:0">
<div>LOADING MAY TAKE SOME TIME ...</div>
<iframe src="/hf.html" width="0" height="0" frameborder="0"></iframe>
</body></html>"""

_HF_AD = f"""<!doctype html><html><body>
<video muted preload="none"><source src="{AD_CLIP}"></video></body></html>"""


def _scene_html(top_clip: bool) -> str:
    clip = f'<video src="{AD_CLIP}" preload="none" muted width="320" height="180"></video>' if top_clip else ""
    return f"""<!doctype html><html><body>
<iframe src="{BANNER}" width="1323" height="110" frameborder="0"></iframe>
{clip}
<iframe src="//mydaddy.cc/video/0bdfd2a67deb53a4ca/" width="560" height="350"
        style="width:856px;height:480px" frameborder="0" allowfullscreen></iframe>
</body></html>"""


def _mp4(path, w, h, seconds):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.fail("ffmpeg is required to build the landed-file fixture")
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=10",
         "-t", str(seconds), "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=60)
    return path


class _Stub:
    site_id = "dl95hqp"

    def __init__(self, tmp_path, landed=None):
        self.config = {"name": "hqporner", "download_dir": str(tmp_path / "dl"), "min_resolution": 1080}
        self.jobs = {}
        self.updates, self.events, self.transfers = [], [], []
        self.landed = landed
        self._spa_api_capture = None

    def _update_job(self, url, status, message, **extra):
        self.updates.append((status, message))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return "shot.png"

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        self.transfers.append(file_url)
        if self.landed:
            shutil.copyfile(self.landed, output_path)
        else:
            with open(output_path, "wb") as fh:
                fh.write(b"\x00" * 16)
        return True

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


def _run(tmp_path, monkeypatch, *, top_clip, player_body, landed=None):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from bulk_downloader import runner_extractors as rx

    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})
    runner = type("StubRunner", (_Stub, rx.ExtractorsMixin), {})(tmp_path, landed)
    bodies = {SCENE: _scene_html(top_clip), PLAYER: player_body, HF_AD: _HF_AD, BANNER: "<html><body></body></html>"}
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        try:
            pg = br.new_context(viewport={"width": 1440, "height": 1000}).new_page()
            pg.route("**/*", lambda r: r.fulfill(content_type="text/html", body=bodies[r.request.url])
                     if r.request.url in bodies else r.abort())   # no real host is ever reached
            pg.goto(SCENE, wait_until="load")
            loaded = [f.url for f in pg.frames]
            assert PLAYER in loaded and HF_AD in loaded, f"fixture frames not loaded: {loaded!r}"
            took = runner._try_spa_api_media_extractor(SCENE, pg)
        finally:
            br.close()
    return runner, took


def test_run2_shape_the_mydaddy_embed_1080_is_the_scene(tmp_path, monkeypatch):
    r, took = _run(tmp_path, monkeypatch, top_clip=False, player_body=_PLAYER_WITH_SOURCES)
    assert took is True and r.transfers == [f"{CDN}1080.mp4"], \
        f"DL95_HQP2_EMBED_NOT_ENTERED: took={took} transfers={r.transfers!r} events={r.events!r}"
    status, msg = r.updates[-1]
    assert status == "done" and msg.startswith("API/media 1080p"), r.updates


def test_run1_shape_a_top_page_clip_does_not_beat_the_embed_1080(tmp_path, monkeypatch):
    r, took = _run(tmp_path, monkeypatch, top_clip=True, player_body=_PLAYER_WITH_SOURCES)
    assert took is True and r.transfers == [f"{CDN}1080.mp4"], \
        f"DL95_HQP2_TOP_CLIP_BEATS_EMBED: took={took} transfers={r.transfers!r} events={r.events!r}"


def test_run1_shape_a_5_9_s_clip_is_not_saved_as_the_scene(tmp_path, monkeypatch):
    """The embed has not listed its files yet; the only pick is the top-page clip, which lands 5.9 s 854x480."""
    landed = _mp4(tmp_path / "preview.mp4", 854, 480, 5.9)
    r, took = _run(tmp_path, monkeypatch, top_clip=True, player_body=_PLAYER_LOADING, landed=landed)
    assert took is True and r.transfers == [AD_CLIP], (took, r.transfers)
    status, msg = r.updates[-1]
    assert status == "needs_review" and msg.startswith("Landed a 5.9 s clip"), \
        f"DL95_HQP2_PREVIEW_SAVED_AS_DONE: {status} {msg!r}"
    assert not list((tmp_path / "dl").rglob("*.mp4")), "the preview must not stay in the library"


def test_control_the_hidden_hf_ad_frame_alone_is_never_the_scene(tmp_path, monkeypatch):
    """Control (passes before and after the fixes): the 0x0 hf.html frame's 7 s ad is not a candidate."""
    r, took = _run(tmp_path, monkeypatch, top_clip=False, player_body=_PLAYER_LOADING)
    assert took is False and r.transfers == [], f"DL95_HQP2_HF_AD_TAKEN: {r.transfers!r}"
