"""dl95-fullporner-1: a scene whose player is a cross-origin embed iframe must yield the embed's <video><source> files.

Live on test2 (harness-work/UIUX-20260928/download-95/B6-B/p1/fullporner/, probes in
harness-work/FIX/dl95-fullporner-1-bd-worker-B10-B/probe*.txt): fullporner.com/watch/<id> has no video/source of its own;
the player is <iframe src="//xiaoshenke.net/video/<id>/<n>">, whose <video> carries typed, extension-less sources
(/vid/<n>/480|720|1080, type=video/mp4) while its currentSrc is a tsyndicate pre-roll AD. The spa-api fallback only
read the top frame, so the job failed "[page_shape] No download button found".

  1. embed_frame_candidates reads <video> > <source> in child frames, admits a typed video/* source without an
     extension, ranks 1080 first, and names the file after the scene page (not "1080").
  2. The embed <video>'s own currentSrc (the ad) and an <audio> source are never candidates.
  3. third_party_frame_hosts names the cross-origin embed host for the failure message.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bulk_downloader import spa_media_extract as spa  # noqa: E402

BD_GATE_SCOPE = "module"

SCENE = "https://scene.example/watch/6aba5725ee50424b5f304d89"
EMBED = "https://embed-player.example/video/9526433/14"

_SCENE_HTML = f"""<!doctype html><html><body><h1>scene</h1>
<iframe src="{EMBED}" width="640" height="360"></iframe></body></html>"""

_EMBED_HTML = """<!doctype html><html><body>
<video src="https://ads.example/cmp_preroll"  preload="none">
  <source src="/vid/3346259/480" type="video/mp4">
  <source src="/vid/3346259/720" type="video/mp4">
  <source src="/vid/3346259/1080" type="video/mp4">
</video>
<audio><source src="/sfx/click.mp4" type="audio/mp4"></audio>
<video preload="none"><source src="/vid/untyped-no-ext" ></video>
</body></html>"""


def _route(route):
    url = route.request.url
    if url.startswith(SCENE):
        return route.fulfill(status=200, content_type="text/html", body=_SCENE_HTML)
    if url.startswith(EMBED):
        return route.fulfill(status=200, content_type="text/html", body=_EMBED_HTML)
    return route.fulfill(status=204, body="")   # no .example host is ever reached


def _with_scene(fn):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page()
            pg.route("**/*", _route)
            pg.goto(SCENE, wait_until="load")
            pg.wait_for_timeout(300)
            assert any(f.url == EMBED for f in pg.frames), f"fixture embed frame not loaded: {[f.url for f in pg.frames]}"
            return fn(pg)
        finally:
            br.close()


def test_embed_frame_typed_sources_are_candidates_best_first():
    cands = _with_scene(lambda pg: spa.embed_frame_candidates(pg, SCENE))
    urls = [c["url"] for c in spa.rank_candidates(cands)]
    assert urls == ["https://embed-player.example/vid/3346259/1080",
                    "https://embed-player.example/vid/3346259/720",
                    "https://embed-player.example/vid/3346259/480"], f"DL95_FP_EMBED_SOURCES: {urls!r}"
    assert {c["filename"] for c in cands} == {"6aba5725ee50424b5f304d89.mp4"}, f"DL95_FP_NAME: {cands!r}"


def test_top_frame_alone_still_has_no_page_media():
    """Control: the top page carries nothing -- the fallback before this cut saw exactly this."""
    media = _with_scene(lambda pg: pg.evaluate(spa.PAGE_MEDIA_JS) or [])
    assert spa.page_media_candidates(SCENE, media) == []


def test_third_party_embed_host_is_named():
    hosts = _with_scene(lambda pg: spa.third_party_frame_hosts(pg))
    assert hosts == ["embed-player.example"], f"DL95_FP_EMBED_HOST: {hosts!r}"


@pytest.mark.parametrize("pairs,expected", [
    ([["/vid/1/1080", "video/mp4"]], ["https://embed-player.example/vid/1/1080"]),
    ([["/vid/1/clip.webm", ""]], ["https://embed-player.example/vid/1/clip.webm"]),
    ([["/vid/1/1080", ""]], []),                      # untyped and no media extension
    ([["/sfx/a.mp4", "audio/mp4"]], []),              # typed audio
    ([["javascript:void(0)", "video/mp4"]], []),
])
def test_frame_source_candidates_filter(pairs, expected):
    got = [c["url"] for c in spa.frame_source_candidates(EMBED, pairs, SCENE)]
    assert got == expected


class _Frame:
    def __init__(self, url, pairs, parent=None):
        self.url, self._pairs, self.parent_frame = url, pairs, parent

    def frame_element(self):
        class _El:
            def is_visible(self):
                return True

            def evaluate(self, js):
                return True

            def bounding_box(self):
                return {"x": 0, "y": 0, "width": 640, "height": 360}
        return _El()

    def evaluate(self, js):
        return self._pairs


class _Page:
    """Top frame with no media and one cross-origin embed frame listing no files."""
    def __init__(self, embed_pairs):
        self.url = SCENE
        self.main_frame = _Frame(SCENE, [])
        self.frames = [self.main_frame, _Frame(EMBED, embed_pairs, self.main_frame),
                       _Frame("about:blank", [], self.main_frame)]

    def evaluate(self, js):
        return []


def _extractor_runner():
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner.__new__(SiteRunner)
    r._spa_api_capture = None
    r.config = {}  # the lane's KVS flashvars arm (dl95-kvs-flashvars-1) reads min_resolution
    r._spa_embed_hosts = ["stale-previous-job.example"]
    return r


def test_extractor_records_the_embed_host_when_the_embed_lists_nothing():
    r = _extractor_runner()
    assert r._try_spa_api_media_extractor(SCENE, _Page([])) is False
    assert r._spa_embed_hosts == ["embed-player.example"], f"DL95_FP_EMBED_HOST_NOT_RECORDED: {r._spa_embed_hosts!r}"


# ── Lens R1 (bd-cx-worker-1 VERDICT-correctness): an ad iframe must never replace the scene ──────────────────────
# Production extractor end to end (tests/test_row722_spa_api_media_extraction._make_runner records transfers).
_R1_SCENE = "https://scene.example/watch/scene-one"
_R1_FRAME = "https://embed-player.example/player/scene-one"
_R1_ADFRAME = "https://ads.example/banner"
_R1_MEDIA = "https://scene.example/media/scene-one-1080p.mp4"
_R1_AD = "https://ads.example/promo-2160p.mp4"


_R1_BODY_OVERRIDES: dict = {}


def _r1_run(tmp_path, monkeypatch, html):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from tests.test_row722_spa_api_media_extraction import _make_runner
    runner = _make_runner(tmp_path, monkeypatch)
    bodies = {
        _R1_SCENE: html,
        _R1_FRAME: '<video preload="none"><source type="video/mp4" src="/vid/scene-one/1080"></video>',
        _R1_ADFRAME: f'<video preload="none"><source type="video/mp4" src="{_R1_AD}"></video>',
    }
    bodies.update(_R1_BODY_OVERRIDES)
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        try:
            p = br.new_page()
            p.route("**/*", lambda r: r.fulfill(content_type="text/html", body=bodies[r.request.url])
                    if r.request.url in bodies else r.abort())
            p.goto(_R1_SCENE, wait_until="load")
            runner._try_spa_api_media_extractor(_R1_SCENE, p)
        finally:
            br.close()
    return runner, [t["file_url"] for t in runner.transfers]


def test_r1_visible_scene_embed_is_taken(tmp_path, monkeypatch):
    r, urls = _r1_run(tmp_path, monkeypatch, f'<iframe src="{_R1_FRAME}" width="640" height="360"></iframe>')
    assert urls == ["https://embed-player.example/vid/scene-one/1080"], f"DL95_FP_EMBED_NOT_TAKEN: {urls!r}"


def test_r1_hidden_ad_iframe_does_not_replace_existing_scene_media(tmp_path, monkeypatch):
    html = (f'<video src="{_R1_MEDIA}" preload="none"></video>'
            f'<iframe title="Advertisement" style="display:none" src="{_R1_ADFRAME}"></iframe>')
    r, urls = _r1_run(tmp_path, monkeypatch, html)
    assert urls == [_R1_MEDIA], f"DL95_FP_AD_IFRAME_REPLACES_SCENE: {urls!r}"


# PM ruling NOTE-PM-ASKS-0245Z 2(a) (hqporner-2): visible embed sources rank WITH page media by height.
def test_ruling_visible_embed_1080_beats_a_top_page_clip_of_unknown_height(tmp_path, monkeypatch):
    """hqporner-2 run 1: one top-page ad clip ('?p') was chosen over the 1080p embed player."""
    html = ('<video src="https://scene.example/media/rotating-ad-clip.mp4" preload="none"></video>'
            f'<iframe src="{_R1_FRAME}" width="640" height="360"></iframe>')
    r, urls = _r1_run(tmp_path, monkeypatch, html)
    assert urls == ["https://embed-player.example/vid/scene-one/1080"], f"DL95_FP_TOP_CLIP_BEATS_EMBED: {urls!r}"


def test_ruling_top_page_1080_scene_beats_a_lower_visible_embed(tmp_path, monkeypatch):
    bodies_720 = '<video preload="none"><source type="video/mp4" src="/vid/scene-one/720"></video>'
    html = (f'<video src="{_R1_MEDIA}" preload="none"></video>'
            f'<iframe src="{_R1_FRAME}" width="640" height="360"></iframe>')
    monkeypatch.setitem(_R1_BODY_OVERRIDES, _R1_FRAME, bodies_720)
    r, urls = _r1_run(tmp_path, monkeypatch, html)
    assert urls == [_R1_MEDIA], f"DL95_FP_LOWER_EMBED_BEATS_SCENE: {urls!r}"


@pytest.mark.parametrize("style", [
    "display:none",
    "visibility:hidden;width:640px;height:360px",          # lens R1 gen2: keeps its box
    "opacity:0;width:640px;height:360px",
    "position:absolute;left:-5000px;width:640px;height:360px",
    "width:1px;height:1px",
], ids=["display-none", "visibility-hidden", "opacity-0", "offscreen", "pixel"])
def test_r1_hidden_ad_iframe_alone_is_neither_a_candidate_nor_a_named_embed(tmp_path, monkeypatch, style):
    r, urls = _r1_run(tmp_path, monkeypatch,
                      f'<iframe title="Advertisement" style="{style}" src="{_R1_ADFRAME}"></iframe>')
    assert urls == [], f"DL95_FP_HIDDEN_AD_TAKEN: {urls!r}"
    assert r._spa_embed_hosts == [], f"DL95_FP_HIDDEN_AD_NAMED: {r._spa_embed_hosts!r}"


# Lens R1 gen3: visibility must hold through every embedding document, not just the innermost <iframe>.
_R1_WRAP = "https://wrapper.example/w"


def _r1_nested_run(tmp_path, monkeypatch, outer_style):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from tests.test_row722_spa_api_media_extraction import _make_runner
    runner = _make_runner(tmp_path, monkeypatch)
    bodies = {
        _R1_SCENE: f'<iframe style="{outer_style}" src="{_R1_WRAP}"></iframe>',
        _R1_WRAP: f'<iframe src="{_R1_ADFRAME}" width="640" height="360"></iframe>',
        _R1_ADFRAME: f'<video preload="none"><source type="video/mp4" src="{_R1_AD}"></video>',
    }
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        try:
            p = br.new_page()
            p.route("**/*", lambda r: r.fulfill(content_type="text/html", body=bodies[r.request.url])
                    if r.request.url in bodies else r.abort())
            p.goto(_R1_SCENE, wait_until="load")
            assert any(f.url == _R1_ADFRAME for f in p.frames), "nested fixture frame not loaded"
            runner._try_spa_api_media_extractor(_R1_SCENE, p)
        finally:
            br.close()
    return runner, [t["file_url"] for t in runner.transfers]


@pytest.mark.parametrize("outer_style", [
    "opacity:0;width:800px;height:600px",
    "visibility:hidden;width:800px;height:600px",
    "position:absolute;left:-5000px;width:800px;height:600px",
], ids=["opacity-0", "visibility-hidden", "offscreen"])
def test_r1_player_nested_in_a_hidden_outer_iframe_is_not_taken(tmp_path, monkeypatch, outer_style):
    r, urls = _r1_nested_run(tmp_path, monkeypatch, outer_style)
    assert urls == [], f"DL95_FP_HIDDEN_ANCESTOR_AD_TAKEN: {urls!r}"


def test_r1_player_nested_in_a_visible_outer_iframe_is_taken(tmp_path, monkeypatch):
    r, urls = _r1_nested_run(tmp_path, monkeypatch, "width:800px;height:600px")
    assert urls == [_R1_AD], f"DL95_FP_VISIBLE_NESTED_PLAYER_DROPPED: {urls!r}"
