"""dl95-porndoe-1: a scene whose player has not started is not a dead page.

Measured on test2 (2026-09-29, harness-work/UIUX-20260928/download-95/A9-A/
p1/porndoe/RESULT.md#D1) and re-measured live on the hub
(harness-work/UIUX-20260928/download-95/B14-B/NOTE-dl95-porndoe-1-scope.md):
porndoe /watch/<id> renders NO <video> until its poster play button is
clicked.  find_best_download therefore admits only chrome -- "Mobile menu"
(score 0, no href) and a playlist "Save" -- the click fires nothing and the
run ends needs_review "no dl event; looks like a modal-trigger button".
After the play click the page holds four <video> elements; the durations
below are the measured ones: the scene's HLS (709.76 s), a hover preview
(8.92 s), a pre-roll ad (29.79 s) and an unloaded ad (NaN).

The fix: on that exact failure (zero-score control, no download event, not
a probe) the runner starts the player with ONE click on a player-start
control and hands the FEATURE media -- the longest finite media, at least
60 s -- to the existing page-media transfer.  Hermetic: fake page objects,
no browser, no network.
"""

import math

import pytest

from bulk_downloader import spa_media_extract as spa
from bulk_downloader.runner_integrity import IntegrityMixin
from bulk_downloader.runner_transport import TransportMixin
from tests.test_row759_modal_trigger_click_reenters import _modal_page

BD_GATE_SCOPE = "repo-wide"

SCENE = ("https://prhls.cdnc.pd.example/SIG==,1790736954/movie/2/3/8/6/9/4/3/"
         "hls/6a9d0005a234b-565-480p-1000.m3u8")
PREVIEW = "https://p.cdnc.pd.example/movie/2/3/8/6/9/4/3/6a9d0005a234b-565-preview.mp4"
AD_PREROLL = "https://i.ads.example/video/4946/946/6a93142_high_low.mp4"
AD_UNLOADED = "https://cdn.ads.example/ab146774_360p.mp4"
POST_PLAY = [
    {"src": SCENE, "duration": 709.76},
    {"src": PREVIEW, "duration": 8.92},
    {"src": AD_PREROLL, "duration": 29.791667},
    {"src": AD_UNLOADED, "duration": math.nan},
]
POSTER_SEL = "button[class*='poster-play' i]"


# ── feature selection ──

def test_the_scene_is_the_only_feature_media_after_play():
    assert spa.feature_media_urls(POST_PLAY) == [SCENE]


@pytest.mark.parametrize("item", [
    {"src": PREVIEW, "duration": 8.92},
    {"src": AD_PREROLL, "duration": 29.791667},
    {"src": AD_UNLOADED, "duration": math.nan},
    {"src": SCENE, "duration": math.inf},          # live stream: no feature
    {"src": "blob:https://pd.example/1", "duration": 709.76},
    {"src": SCENE, "duration": True},
])
def test_a_preview_an_ad_or_an_unmeasured_source_is_never_feature(item):
    assert spa.feature_media_urls([item]) == []


# ── player start ──

class _Loc:
    def __init__(self, page, sel):
        self._page, self._sel = page, sel

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self._sel in self._page.controls else 0

    def is_visible(self):
        return self._page.controls.get(self._sel, False)

    def click(self, timeout=None):
        self._page.clicks.append(self._sel)
        if self._page.loads:
            self._page.videos = list(POST_PLAY)


class _PlayerPage:
    url = "https://pd.example/watch/pd9v8b2a8c9s"

    def __init__(self, controls, loads=True, videos=()):
        self.controls = dict(controls)
        self.loads = loads
        self.videos = list(videos)
        self.clicks = []

    def locator(self, sel):
        return _Loc(self, sel)

    def evaluate(self, js, *a):
        assert js == spa.FEATURE_MEDIA_JS
        return list(self.videos)


def _start(page):
    return spa.start_player_for_feature_media(
        page, wait_s=2.0, poll_s=0.5, sleep=lambda _s: None)


def test_the_poster_play_is_clicked_once_and_the_scene_is_returned():
    page = _PlayerPage({POSTER_SEL: True})
    assert _start(page) == [SCENE]
    assert page.clicks == [POSTER_SEL]


def test_a_started_player_is_not_clicked_again():
    page = _PlayerPage({POSTER_SEL: True}, videos=POST_PLAY)
    assert _start(page) == [SCENE]
    assert page.clicks == []


def test_no_player_start_control_clicks_nothing():
    # A related card reads "Play ..." but is an anchor; no selector names it.
    page = _PlayerPage({"a.video-item-link": True})
    assert _start(page) == []
    assert page.clicks == []


def test_a_hidden_control_is_not_clicked():
    page = _PlayerPage({POSTER_SEL: False})
    assert _start(page) == []
    assert page.clicks == []


def test_a_start_that_loads_nothing_is_one_click_and_a_bounded_miss():
    page = _PlayerPage({POSTER_SEL: True, "button.vjs-big-play-button": True},
                       loads=False)
    assert _start(page) == []
    assert page.clicks == [POSTER_SEL]


# ── the runner seams ──

class _Extractor:
    """_try_player_media_extractor on its real class with the SPA transfer
    reduced to a recorder."""

    def take(self, url, page):
        from bulk_downloader.runner_extractors import ExtractorsMixin
        return ExtractorsMixin._try_player_media_extractor(self, url, page)

    def __init__(self):
        self.handed = []

    def _try_spa_api_media_extractor(self, url, page, page_media=None):
        self.handed.append((url, page_media))
        return True


def test_only_the_feature_media_reaches_the_transfer():
    ex = _Extractor()
    page = _PlayerPage({POSTER_SEL: True})
    import bulk_downloader.spa_media_extract as m
    real = m.start_player_for_feature_media
    try:
        m.start_player_for_feature_media = lambda p: real(
            p, wait_s=1.0, poll_s=0.5, sleep=lambda _s: None)
        assert ex.take(page.url, page) is True
    finally:
        m.start_player_for_feature_media = real
    assert ex.handed == [(page.url, [SCENE])]


class _CallerRunner(TransportMixin, IntegrityMixin):
    def __init__(self, player_result):
        self.config = {"quality_preference": "best", "name": "porndoe-test"}
        self.site_id = 1
        self.jobs = []
        self.player_calls = []
        self._player_result = player_result

    def _update_job(self, url, status, msg="", **kw):
        self.jobs.append((status, msg))

    def _screenshot(self, page, url):
        return "/dev/null/shot.png"

    def _try_player_media_extractor(self, url, page):
        self.player_calls.append(url)
        return self._player_result


def _drive(monkeypatch, player_result, *, probe=False, score=0):
    import bulk_downloader.runner_transport as rt
    monkeypatch.setattr(rt, "db_log", lambda *a, **kw: None)
    page, best, _div, _label, _cell = _modal_page(opens=False)
    best["score"] = score
    runner = _CallerRunner(player_result)
    runner._do_download(page, None, page.url, best, None, "?", probe=probe)
    return runner


def test_the_dead_zero_score_click_takes_the_player_media(monkeypatch):
    runner = _drive(monkeypatch, True)
    assert len(runner.player_calls) == 1
    assert [s for s, _ in runner.jobs if s == "needs_review"] == []


def test_no_player_media_still_files_the_modal_trigger_review(monkeypatch):
    runner = _drive(monkeypatch, False)
    assert len(runner.player_calls) == 1
    assert [s for s, _ in runner.jobs] == ["needs_review"]
    assert "modal-trigger" in runner.jobs[0][1]


def test_a_probe_never_takes_over_the_transfer(monkeypatch):
    runner = _drive(monkeypatch, True, probe=True)
    assert runner.player_calls == []


# ── lens R1 (bd-cx-worker-1, CX1_PREVIEW_OVERRIDES_FEATURE): the feature media
# is the WHOLE population the transfer ranks; a captured API preview record
# must not be merged back in and outrank it. ──

_API_PREVIEW = "https://pd.example/media/preview-2160p.mp4"
_API_RECORDS = [{"url": "https://pd.example/api/videos/1",
                 "json": {"sources": [{"quality": "2160p",
                                       "url": _API_PREVIEW}]}}]


class _Capture:
    def records(self):
        return list(_API_RECORDS)


def _extractors_mixin():
    from bulk_downloader.runner_extractors import ExtractorsMixin
    return ExtractorsMixin


class _SpaRunner(_extractors_mixin()):
    """The real extractor mixin (its helpers included); only the capture
    and the lane's KVS sweep are stubbed."""
    _spa_api_capture = _Capture()
    # T154 (lane merge): the lane's beeg-2-live-1 reads config["min_resolution"] /
    # ["allow_av1"] after ranking; the stub carries the defaults it did before.
    config = {}

    def _kvs_flashvars_media(self, page, spa_mod, forced=False):
        return []            # the lane's KVS sweep: nothing on this page

    def run(self, page, **kw):
        from bulk_downloader.runner_extractors import ExtractorsMixin
        return ExtractorsMixin._try_spa_api_media_extractor(
            self, page.url, page, **kw)


class _MediaPage:
    url = "https://pd.example/watch/pd9v8b2a8c9s"

    def evaluate(self, js, *a):
        assert js == spa.PAGE_MEDIA_JS
        return [SCENE]


def _ranked_population(monkeypatch, **kw):
    seen = []

    def _rank(cands):
        seen.extend(c["url"] for c in cands)
        return []            # nothing resolves: the extractor returns False

    monkeypatch.setattr(spa, "rank_candidates", _rank)
    assert _SpaRunner().run(_MediaPage(), **kw) is False
    return seen


def test_feature_media_is_the_whole_ranked_population(monkeypatch):
    assert _ranked_population(monkeypatch, page_media=[SCENE]) == [SCENE]


def test_the_default_spa_path_still_merges_api_options(monkeypatch):
    # Positive control: the preview record IS a candidate on the row-722 path,
    # so the exclusion above is the feature-only rule, not a dead fixture.
    assert _ranked_population(monkeypatch) == [_API_PREVIEW, SCENE]


# lens (cx-worker-1, lane delta): the feature-only population must run the
# WHOLE lane extractor past ranking -- the lane's later `if scene_candidates:`
# gate read a name the feature branch never bound (UnboundLocalError).

class _Reached(Exception):
    pass


def test_feature_media_runs_past_the_lane_scene_gate_to_the_transfer():
    feature = "https://pd.example/media/scene-720p.mp4"

    class _Runner(_SpaRunner):
        def log_event(self, kind, msg, url=None):
            raise _Reached(msg)

    try:
        _Runner().run(_MediaPage(), page_media=[feature])
    except _Reached as reached:
        assert "chose 720p from page-media" in str(reached)
    else:
        raise AssertionError("the extractor never reached the transfer log")
