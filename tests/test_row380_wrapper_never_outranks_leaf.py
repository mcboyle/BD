"""A layout WRAPPER must never outrank the leaf download control it contains.


Measured on test6 2026-08-29 (v3.66.1339, history rows 107-113): five wowgirls
scenes produced `no dl event; scored ok but no download fired`, top candidate
`8K(2.0 GB):FULL MOVIE DOWNLOAD / 60 FPS ...`.  Forcing the wide sweep against
the live page reproduced it exactly:

  1  score=4325 size=2136746229  DIV  class='content_download video_downloads'
                                      88 descendants, 9 child a.ct_dl_button
  2  score=4325 size=0           A    class='ct_dl_button'  href=...7680x4320...

`gather_text` reads `inner_text`, so the wrapper inherits its 7680x4320 child's
label (equal score) AND parses 1.99GB from the 1080p child's caption, while the
real 8K anchor keeps its size in a SIBLING caption and parses 0.  The
`(score, size)` sort then puts the wrapper first, and clicking a <div> fires no
Playwright download event.

This test asserts the preconditions explicitly (the wrapper really is scored,
really ties, really carries the larger size) so a green result cannot come from
an empty candidate list or from the wrapper never being harvested at all.
"""

# The gate parses a module-level ASSIGNMENT, not a docstring line.
BD_GATE_SCOPE = "module"
from contextlib import contextmanager

import pytest

from bulk_downloader.detect import find_best_download, parse_size_bytes, res_score

# The wowgirls block, reduced to its load-bearing shape: one wrapper whose class
# matches the wide sweep's `[class*='download' i]` selector, four leaf anchors,
# and the per-tier size as a SIBLING caption rather than anchor text.
_FIX = """<!doctype html><html><body>
<div class="content_download video_downloads">
  <div class="ct_dl_title">FULL MOVIE DOWNLOAD</div>
  <div class="tabs"><span class="active">60 FPS</span><span>30 FPS</span></div>
  <div class="row">
    <a class="ct_dl_button" href="https://cdn.example/dl/1920x1080_60FPS.mp4">1920 x 1080</a>
    <div class="caption">FHD &bull; H264 &bull; 60fps &bull; 1.99GB</div>
  </div>
  <div class="row">
    <a class="ct_dl_button" href="https://cdn.example/dl/1280x720_60FPS.mp4">1280 x 720</a>
    <div class="caption">HD &bull; H264 &bull; 60fps &bull; 395MB</div>
  </div>
  <div class="row">
    <a class="ct_dl_button" href="https://cdn.example/dl/3840x2160_60FPS.mp4">3840 x 2160</a>
    <div class="caption">4K &bull; H264 &bull; 60fps &bull; 3.44GB</div>
  </div>
  <div class="row">
    <a class="ct_dl_button" href="https://cdn.example/dl/7680x4320_60FPS.mp4">7680 x 4320</a>
    <div class="caption">8K &bull; HEVC &bull; 60fps &bull; 3.01GB</div>
  </div>
</div>
</body></html>
"""


@contextmanager
def _page(html=_FIX):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page(viewport={"width": 1400, "height": 900})
            pg.set_content(html, wait_until="load")
            yield pg
        finally:
            br.close()


def test_wrapper_and_leaf_really_do_collide():
    """Precondition: the defect's arithmetic is real, not assumed."""
    wrapper_text = ("FULL MOVIE DOWNLOAD 60 FPS 30 FPS 1920 x 1080 "
                    "FHD H264 60fps 1.99GB 1280 x 720 HD H264 60fps 395MB "
                    "3840 x 2160 4K H264 60fps 3.44GB 7680 x 4320 "
                    "8K HEVC 60fps 3.01GB")
    leaf_text = "7680 x 4320 https://cdn.example/dl/7680x4320_60FPS.mp4"
    assert res_score(wrapper_text) == res_score(leaf_text), (
        "wrapper must TIE the leaf on score for this defect to exist")
    assert parse_size_bytes(wrapper_text) > 0
    assert parse_size_bytes(leaf_text) == 0, (
        "the leaf's size lives in a sibling caption, so it must parse 0")


def test_the_fixture_really_presents_the_defect_shape():
    """Precondition: the wrapper is reachable by the wide sweep's own
    selector and really does contain the leaves, and the leaf really is
    harvested.  Asserted on the DOM (not on the candidate list), so it stays
    meaningful after the wrapper is correctly filtered out."""
    with _page() as pg:
        wrappers = pg.locator("[class*='download' i]")
        assert wrappers.count() >= 1, (
            "the wide sweep's [class*='download' i] selector does not reach "
            "this fixture at all — the test could not see its subject")
        wrapper = pg.locator("div.content_download.video_downloads")
        assert wrapper.count() == 1
        assert wrapper.first.evaluate("e => e.tagName") == "DIV"
        assert wrapper.first.locator("a.ct_dl_button").count() == 4, (
            "the wrapper must CONTAIN the leaf controls for the nesting "
            "defect to exist")
        leaf = pg.locator("a.ct_dl_button[href*='7680x4320']")
        assert leaf.count() == 1 and leaf.first.is_visible()

        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None, "no candidate at all — fixture is wrong"
        texts = [c["text"] for c in (best.get("_all_candidates") or [])]
        assert any(t.startswith("7680 x 4320") for t in texts), (
            "the real 8K leaf was never harvested; candidates=%r" % texts)


def test_wide_sweep_winner_is_the_clickable_leaf_not_the_wrapper():
    """RED on v3.66.1339: the winner is the <div> wrapper."""
    with _page() as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None
        loc = best["locator"]
        tag = loc.evaluate("e => e.tagName")
        href = loc.get_attribute("href") or ""
        descendants = loc.evaluate("e => e.querySelectorAll('*').length")
        assert tag == "A", (
            "winner is a %s wrapper with %d descendants (text=%r) — clicking "
            "it fires no download event" % (tag, descendants, best["text"]))
        assert descendants <= 1, "winner still wraps other controls"
        assert "7680x4320" in href, (
            "winner is clickable but not the highest tier: href=%r" % href)


def test_a_real_wrapper_control_is_still_kept():
    """Negative control: a wrapper that IS the control must survive.

    wowgirls' own learned selector is `div.download-button[data-href]` — a DIV
    that carries the affordance itself. A fix that simply drops every element
    with children would delete it, so assert it is still chosen.
    """
    html = """<!doctype html><html><body>
    <div class="downloads">
      <div class="download-button" data-href="https://cdn.example/dl/4320.mp4">
        <span>7680 x 4320</span><span>8K &bull; 3.01GB</span>
      </div>
    </div></body></html>"""
    with _page(html) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None, "the real control was dropped entirely"
        assert best["locator"].get_attribute("data-href") == \
            "https://cdn.example/dl/4320.mp4", best["text"]
