"""tpl95-porndig-1: the site dl_selector picked the very rows the template teaches, and both taught selectors took a miss.

Measured on test2 (B6-B FINDING-O1517-porndig-justporn-B6-B.md#porndig, site 43c18b89, template user_b6b_porndig_o1517):
"download: triggered via [button.btn_download_post_action]", then "custom selector matched 5 option(s); picked
'1080p : 428 MB ...'" (site dl_selector a.post_download_link), and export learned._per_selector: both row selectors
hits 0 misses 1, no "learned hit via" line. The learned pass skips a taught row it cannot click yet (hidden,
v3.66.247), so dl_selector ranks the same a.post_download_link elements and wins; the runner then booked a miss
for every row selector and ran demotion -- 6 such runs DROP a template selector that names the winner.
GREEN: record_learned_download_outcome credits the learned row selector that names the picked element (hit +
"learned hit via [...]"); a pick no learned row names is still a miss for all of them.

The page is built at test time from the real control markup (tests/corpus/porndig/scene_download_menu.html, from the
.95 campaign capture): the menu is closed (display:none, as measured on porndig.com: "none/post_download_wrapper").
Hermetic: real Chromium, no network.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from pathlib import Path

import pytest

from bulk_downloader.detect import find_best_download
from bulk_downloader import runner_util

BD_GATE_SCOPE = "module"

MENU = Path(__file__).parent / "corpus" / "porndig" / "scene_download_menu.html"
PAGE_URL = "https://www.porndig.com/videos/257992/lola-cheeks-has-sexy-feet-made-to-wank-big-thick-cocks.html"
DL_SELECTOR = "a.post_download_link"  # the site's dl_selector on test2
ROWS = [".post_download_wrapper a.post_download_link[href*='/download/index/']",
        "a.post_download_link[href*='videos.porndig.com/download/index/']"]  # the template's row selectors
LEARNED = {"trigger_selectors": ["button.btn_download_post_action"], "row_selectors": ROWS, "url_attribute": "href"}
# tpl95-justporn-1: a hidden taught row the template FETCHES by url_attribute is now a learned hit itself (no click
# needed). The credit path below is about a template the learned pass must skip while the menu is shut: one that
# clicks its row (no url_attribute).
LEARNED_CLICK = dict(LEARNED, url_attribute="")
CSS = "<style>.post_download_wrapper{display:none}.post_download_wrapper.open{display:block}.hidden{display:none}</style>"


@contextmanager
def _scene(menu_open=False):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    html = f"<!doctype html><html><head>{CSS}</head><body>{MENU.read_text(encoding='utf-8')}</body></html>"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.route("**/*", lambda r: r.fulfill(body=html, content_type="text/html")
                       if r.request.url == PAGE_URL else r.abort())
            page.goto(PAGE_URL, wait_until="domcontentloaded")
            if menu_open:
                page.evaluate("document.querySelector('.post_download_wrapper').classList.add('open')")
            yield page
        finally:
            browser.close()


def _config():
    return {"learned": {"download": copy.deepcopy(LEARNED_CLICK)}}


def _counts(config):
    return config["learned"]["download"].get("_per_selector", {})


def test_precondition_the_site_selector_picks_a_taught_row_while_the_learned_pass_skips_it():
    with _scene() as page:
        best = find_best_download(page, DL_SELECTOR, learned=copy.deepcopy(LEARNED_CLICK))
        assert best is not None and not best.get("_via_learned"), best
        assert best.get("_custom_selector") == DL_SELECTOR  # "custom selector matched 5 option(s)"
        assert "/download/index/" in best["text"], best["text"]
        assert best["locator"].evaluate("(e, s) => e.matches(s)", ROWS[0])  # a taught row IS the winner


def test_the_taught_row_that_names_the_pick_is_credited_not_missed(capsys):
    config = _config()
    with _scene() as page:
        best = find_best_download(page, DL_SELECTOR, learned=copy.deepcopy(LEARNED_CLICK))
        runner_util.record_learned_download_outcome(config, LEARNED_CLICK, best)
    counts = _counts(config)
    assert counts.get(ROWS[0]) == {"hits": 1, "misses": 0}, counts
    assert not any(rec.get("misses") for rec in counts.values()), counts
    assert config["learned"]["download"]["row_selectors"] == ROWS  # nothing demoted
    assert f"learned hit via [{ROWS[0]}]" in capsys.readouterr().err


def test_a_learned_pick_is_still_a_plain_hit():
    config = _config()
    with _scene(menu_open=True) as page:
        best = find_best_download(page, DL_SELECTOR, learned=copy.deepcopy(LEARNED))
        assert best.get("_via_learned"), best
        runner_util.record_learned_download_outcome(config, LEARNED, best)
    assert _counts(config).get(best["_learned_sel"]) == {"hits": 1, "misses": 0}


def test_a_hidden_taught_row_fetched_by_href_is_a_learned_hit():
    """tpl95-justporn-1: with url_attribute href the shut menu's taught row is fetched directly."""
    config = {"learned": {"download": copy.deepcopy(LEARNED)}}
    with _scene() as page:
        best = find_best_download(page, DL_SELECTOR, learned=copy.deepcopy(LEARNED))
        assert best is not None and best.get("_via_learned") and best["_learned_sel"] == ROWS[0], best
        runner_util.record_learned_download_outcome(config, LEARNED, best)
    assert _counts(config).get(ROWS[0]) == {"hits": 1, "misses": 0}


def test_control_a_pick_no_taught_row_names_is_a_miss_for_every_row():
    """#video_full_download_btn (a.post_download_link to the site root) matches dl_selector but no taught row."""
    config = _config()
    with _scene() as page:
        best = {"locator": page.locator("#video_full_download_btn"), "text": "Download full video", "score": 0}
        runner_util.record_learned_download_outcome(config, LEARNED, best)
    counts = _counts(config)
    assert all(counts.get(sel) == {"hits": 0, "misses": 1} for sel in ROWS), counts


def test_a_selector_the_dom_cannot_evaluate_names_nothing():
    """Playwright-only syntax (:has-text) cannot be matched in the DOM: counted as before, never raised."""
    learned = dict(LEARNED_CLICK, row_selectors=["a:has-text('1080p')"])
    config = {"learned": {"download": copy.deepcopy(learned)}}
    with _scene() as page:
        best = find_best_download(page, DL_SELECTOR, learned=copy.deepcopy(learned))
        runner_util.record_learned_download_outcome(config, learned, best)
    assert _counts(config).get("a:has-text('1080p')") == {"hits": 0, "misses": 1}
