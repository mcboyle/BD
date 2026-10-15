"""dl95-xvideos-2 r2: on an xvideos scene page the player's own sources answer before the DOM scorer.

LIVE on .95 (1e334559, RESULT-dl95-xvideos-2-2231Z.md): the row URL /video.ompcckuc5f1/56047537/0/... went
needs_review "no dl event; scored ok but no download fired; saw: 1440p(?):1440p /video.uhvfabd90c8/man_h" -- the
scorer clicked a RELATED video's tile. fed0c255f (detect._is_other_scene_link) refuses such a tile only when the page
URL carries a numeric id, but xvideos redirects to the id-less /video.<hash>/<slug>, so the rule fails open there.

Shape reduced from dl95-xvideos-2/r2/ev/capture/row-page.html (curl from .95, 2026-10-14): the html5player
setVideoUrlLow/UrlHigh calls and one related tile; CDN paths renamed with their tier, no signed tokens.
Contract: the pre-scorer source-list step (runner.py, source_list_only) takes the page's one WGCZ html5player as the
scene's published source list and admits only its own sources; a page without it falls through to the scorer unchanged.
r3 (lens F1 on r2, VERIFY-NONWGCZ.md): a bare setVideoUrlHigh(...) anywhere in the HTML is not that player. Off a known
WGCZ host (xvideos/xnxx) only calls on the variable the page builds with new HTML5Player(...), in code -- not in a JS
comment or string -- prove it; an ad widget's call or a commented one must leave the page to the scorer.
"""
import threading
from contextlib import contextmanager

import pytest
from playwright.sync_api import sync_playwright

from tests.test_row722_spa_api_media_extraction import _make_runner

BD_GATE_SCOPE = "module"
ROW = "https://www.xvideos.com/video.ompcckuc5f1/56047537/0/spanish_man_hard_fucks_chinese_baddie"
LIVE = "https://www.xvideos.com/video.ompcckuc5f1/spanish_man_hard_fucks_chinese_baddie_with_huge_tits"
RELATED = "/video.uhvfabd90c8/man_hard_fucks_and_cum_on_big_tits_fitness_redhead_after_gym"
CDN = "https://mp4-cdn77.xvideos-cdn.com/fixture-d0939259/6/"
TILE_MEDIA = "https://thumb-cdn77.xvideos-cdn.com/fixture-related/videopreview_1440p.mp4"


def scene_html(player=(("setVideoUrlLow", "mp4_360p.mp4"), ("setVideoUrlHigh", "mp4_1080p.mp4"))):
    calls = "".join(f"html5player.{fn}('{CDN}{leaf}');" for fn, leaf in player)
    return ("<!doctype html><html><head><title>Spanish Man Hard Fucks Chinese Baddie with HUGE Tits - XVIDEOS.COM"
            "</title></head><body><div id='video-player-bg'><div id='html5video'></div></div>"
            "<script>var html5player = {setVideoUrlLow() {}, setVideoUrlHigh() {}, setVideoHLS() {}};"
            f"{calls}</script>"
            "<div id='related-videos'><div class='thumb-block'>"
            f"<a href='{RELATED}'><video muted src='{TILE_MEDIA}'></video>"
            "<span class='video-hd-mark'>1440p</span> <span>CC</span></a></div></div></body></html>")


@contextmanager
def page(html, at=LIVE):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            pg = browser.new_page()
            pg.route("**/*", lambda r: r.fulfill(content_type="text/html", body=html)
                     if r.request.url == at else r.abort())
            pg.goto(at, wait_until="load")
            yield pg
        finally:
            browser.close()


def run(tmp_path, monkeypatch, html, minimum=0, at=LIVE, row=ROW):
    r = _make_runner(tmp_path, monkeypatch)
    r.config["min_resolution"] = minimum
    r.states = r.jobs
    r.jobs = {row: {"force_download": False}}
    r._lock = threading.RLock()
    r._update_job = lambda _url, status, message, **extra: r.states.append((status, message, extra))
    r._screenshot = lambda *_: "fixture.png"
    with page(html, at) as pg:
        assert pg.url == at, "XV2R2_FIXTURE_NOT_ON_THE_ID_LESS_URL"
        handled = r._try_spa_api_media_extractor(row, pg, source_list_only=True)
    return r, handled


def test_player_answers_before_the_scorer_and_no_related_tile_is_taken(tmp_path, monkeypatch):
    r, handled = run(tmp_path, monkeypatch, scene_html())
    got = [t["file_url"] for t in r.transfers]
    assert handled, f"XV2R2_PLAYER_NOT_CONSULTED_BEFORE_SCORER: the DOM scorer runs next and clicks {RELATED[:22]} ({r.states})"
    assert got == [CDN + "mp4_1080p.mp4"], f"XV2R2_NOT_THE_PLAYERS_BEST {got}"
    assert TILE_MEDIA not in got and not any("uhvfabd90c8" in u for u in got), f"XV2R2_RELATED_TILE_TAKEN {got}"


def test_player_below_minimum_is_held_not_clicked_through(tmp_path, monkeypatch):
    html = scene_html((("setVideoUrlLow", "mp4_360p.mp4"), ("setVideoUrlHigh", "mp4_720p.mp4")))
    r, handled = run(tmp_path, monkeypatch, html, minimum=1080)
    assert handled and not r.transfers, f"XV2R2_BELOW_MIN_NOT_HELD {handled} {r.transfers}"
    assert any(s == "needs_review" and "720p" in m and "1440" not in m for s, m, _ in r.states), \
        f"XV2R2_HOLD_NAMES_WRONG_TIER {r.states}"


def test_page_without_a_wgcz_player_falls_through_to_the_scorer(tmp_path, monkeypatch):
    r, handled = run(tmp_path, monkeypatch, scene_html(player=()))
    assert not handled and not r.transfers and not r.states, \
        f"XV2R2_NON_WGCZ_PAGE_PREEMPTED {handled} {r.transfers} {r.states}"


def test_witness_why_fed0c255f_missed_the_live_page():
    """Not RED/GREEN: the detect rule is unchanged (outside this cut). It refuses the tile on the queued numeric URL
    and fails open on the id-less URL xvideos redirects to, which is the page.url the scorer reads."""
    from bulk_downloader.detect import _is_other_scene_link

    class El:
        def get_attribute(self, k):
            return RELATED if k == "href" else None
    assert _is_other_scene_link(El(), "1440p CC " + RELATED, ROW), "XV2R2_WITNESS_NUMERIC_URL_NOT_REFUSED"
    assert not _is_other_scene_link(El(), "1440p CC " + RELATED, LIVE), "XV2R2_WITNESS_DETECT_CHANGED"


# r3: a non-WGCZ page whose real download is a 2160p anchor (the lens's fixture shape, VERIFY-NONWGCZ.md).
OFF = "https://library.fixture.test/scene"
OFF_CDN = "https://cdn.fixture.test/"
AD = OFF_CDN + "ad_360p.mp4"
STUB = ("function HTML5Player(id, vid, opts) {}"
        "HTML5Player.prototype.setVideoUrlLow = HTML5Player.prototype.setVideoUrlHigh ="
        " HTML5Player.prototype.setVideoHLS = function () {};"
        "var unrelatedWidget = {setVideoUrlHigh() {}};")
CTOR = "var html5player = new HTML5Player('html5video', '91671604', {});"


def off_host_html(script):
    return ("<!doctype html><html><head><title>Scene</title></head><body>"
            f"<a href='{OFF_CDN}scene_2160p.mp4' download>Download 2160p</a>"
            f"<script>{STUB}{script}</script></body></html>")


@pytest.mark.parametrize("script", [
    f"unrelatedWidget.setVideoUrlHigh('{AD}');",
    f"// html5player.setVideoUrlHigh('{AD}');\n",
    f"{CTOR} // html5player.setVideoUrlHigh('{AD}');\n/* html5player.setVideoUrlLow('{AD}') */"
    f" var s = \"html5player.setVideoHLS('{AD}')\";",
    f"{CTOR} var ad = {{html5player: {{setVideoUrlHigh() {{}}}}}}; ad.html5player.setVideoUrlHigh('{AD}');",
], ids=["unrelated-widget", "commented-call", "constructed-player-but-comment-and-string", "another-objects-property"])
def test_r3_unproven_call_off_a_wgcz_host_leaves_the_page_to_the_scorer(tmp_path, monkeypatch, script):
    r, handled = run(tmp_path, monkeypatch, off_host_html(script), at=OFF, row=OFF)
    assert not handled and not r.transfers and not r.states, \
        f"XV2R3_UNPROVEN_WGCZ_CALL_ACCEPTED: pre-empts the page's 2160p anchor {handled} {r.transfers} {r.states}"


def test_r3_constructed_player_off_a_wgcz_host_answers_with_its_own_sources_only(tmp_path, monkeypatch):
    """Positive control for the proof branch (the xvideos.com tests above pass on the host): the page's own
    new HTML5Player receiver answers; an ad widget's taller call on the same page is not one of its sources."""
    script = (f"{CTOR}html5player.setVideoUrlLow('{OFF_CDN}own_360p.mp4');"
              f"html5player.setVideoUrlHigh('{OFF_CDN}own_1080p.mp4');")
    r, handled = run(tmp_path, monkeypatch, off_host_html(script), at=OFF, row=OFF)
    got = [t["file_url"] for t in r.transfers]
    assert handled and got == [OFF_CDN + "own_1080p.mp4"], f"XV2R3_PROVEN_PLAYER_NOT_TAKEN {handled} {got} {r.states}"


def test_r3_ad_widget_beside_the_proven_player_is_not_one_of_its_sources(tmp_path, monkeypatch):
    script = (f"{CTOR}html5player.setVideoUrlHigh('{OFF_CDN}own_1080p.mp4');"
              f"unrelatedWidget.setVideoUrlHigh('{OFF_CDN}ad_2160p.mp4');")
    r, handled = run(tmp_path, monkeypatch, off_host_html(script), at=OFF, row=OFF)
    got = [t["file_url"] for t in r.transfers]
    assert handled and got == [OFF_CDN + "own_1080p.mp4"], f"XV2R3_AD_WIDGET_MEDIA_TAKEN {handled} {got}"
