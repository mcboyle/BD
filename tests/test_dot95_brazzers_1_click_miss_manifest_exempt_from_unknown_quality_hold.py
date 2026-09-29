"""dot95-brazzers1-clickmiss-exempt (PM RULING, harness-work/DOT95-LANE/MERGE-0930.md).

On the DOT95 lane the brazzers-1 click-miss fallback (tpl95-site-ma-brazzers-1) admitted the page's HLS master, but
the master also entered scene_stream_candidates, so the lane's dl95-ok-1b scene gate held it as "Scene player best is
unknown quality" and the scored-tier stand-in never ran (brazzers-1 hls_stream / stream_past_a_trailer RED).

Contract: a manifest admitted by ``click_miss_candidates`` (``_try_spa_api_media_extractor(click_miss_floor=...)``) is
exempt from the unknown-quality scene hold. When its master lists RESOLUTION that height is used: at or above the
minimum it is taken, below it the scene hold names the known height. Every other unknown-height scene player (no
click_miss_floor) is still held as unknown quality.

Hermetic: the brazzers-1 page/runner doubles; no browser, network, ffmpeg or site.
"""
from __future__ import annotations

from bulk_downloader import hls_downloader
from bulk_downloader import runner_extractors as extractors
from bulk_downloader import spa_media_extract

from test_tpl95_site_ma_brazzers_1_click_miss_falls_back_to_page_media import (
    MANIFEST, PAGE_URL, _Page, _Runner)

BD_GATE_SCOPE = "module"

V480 = "https://video.brazzers.example/hls/11524409/480p/index.m3u8"
V1080 = "https://video.brazzers.example/hls/11524409/1080p/index.m3u8"


def _master(*variants):
    lines = ["#EXTM3U"]
    for height, uri in variants:
        lines += [f'#EXT-X-STREAM-INF:BANDWIDTH={height * 5000},RESOLUTION={height * 16 // 9}x{height},'
                  'CODECS="avc1.640028,mp4a.40.2"', uri]
    return "\n".join(lines) + "\n"


class _MasterPage(_Page):
    """The scene page, whose own session can read the master playlist (or not: text None)."""

    def __init__(self, media, master_text=None):
        super().__init__(media)
        self.master_text = master_text

    def evaluate(self, script, *args):
        if script == spa_media_extract.MANIFEST_TEXT_JS:
            return self.master_text
        return super().evaluate(script, *args)


def _extract(tmp_path, monkeypatch, master_text=None, **kw):
    monkeypatch.setattr(extractors, "db_log", lambda *_a, **_kw: None)
    monkeypatch.setattr(hls_downloader, "is_available", lambda: True)
    runner = _Runner(tmp_path / "dl")
    took = runner._try_spa_api_media_extractor(PAGE_URL, _MasterPage([MANIFEST], master_text), **kw)
    return took, runner


def _reviews(runner):
    return [m for s, m in runner.status if s == "needs_review"]


def test_a_click_miss_manifest_of_unknown_height_is_not_held_as_unknown_quality(tmp_path, monkeypatch):
    took, runner = _extract(tmp_path, monkeypatch, click_miss_floor=720)
    assert took is True, runner.status
    assert not [m for m in _reviews(runner) if "unknown quality" in m], (
        f"DOT95_BRAZZERS_CLICKMISS_HELD_AS_UNKNOWN: {runner.status}")
    assert runner.hls == [MANIFEST] and runner.status[-1][0] == "done", runner.status


def test_a_click_miss_master_listing_resolution_at_the_floor_takes_that_height(tmp_path, monkeypatch):
    took, runner = _extract(tmp_path, monkeypatch, _master((480, V480), (1080, V1080)), click_miss_floor=720)
    assert took is True and _reviews(runner) == [], runner.status
    assert runner.hls == [V1080], runner.hls
    assert runner.status[-1][0] == "done" and runner.status[-1][1].startswith("API/media 1080p"), runner.status


def test_a_click_miss_master_listing_only_a_height_below_the_floor_holds_naming_it(tmp_path, monkeypatch):
    took, runner = _extract(tmp_path, monkeypatch, _master((480, V480)), click_miss_floor=720)
    assert took is True and runner.hls == [], runner.status
    assert _reviews(runner) == ["Scene player best is 480p (minimum 720p) — Approve to force."], (
        f"DOT95_BRAZZERS_CLICKMISS_KNOWN_LOW_NOT_HELD: {runner.status}")


def test_an_unknown_height_scene_player_off_the_click_miss_path_is_still_held(tmp_path, monkeypatch):
    took, runner = _extract(tmp_path, monkeypatch)
    assert took is True and runner.hls == [], runner.status
    assert _reviews(runner) == ["Scene player best is unknown quality (minimum 720p) — Approve to force."], (
        f"DOT95_OK1B_UNKNOWN_HOLD_LOST: {runner.status}")
