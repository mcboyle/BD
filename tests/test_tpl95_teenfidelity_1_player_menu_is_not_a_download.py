"""tpl95-teenfidelity-1: the scorer picked the video player's quality menu over the MP4 download links.

Measured on test2 (B4-B tpl-teenfidelity/events.jsonl run_done, twice, with and without the applied template
user_b4b_teenfidelity_o1517_*): winner "1080p(?):1080p Open quality selector me" -> no download event -> needs_review,
while the page lists "MP4 4k (713.04 MB)" / "MP4 1080p (340.88 MB)" / "MP4 720p" scored "auto". The MP4 labels DO
parse (res_score 2160/1080/720); they read "auto" because they sit in a download modal that is hidden until opened
(row 759 zeroes hidden scores), while video.js labels its visible quality-menu button with the current tier.
GREEN: detect refuses a resolution-labelled control inside a media player's control bar/menu (player_control), so the
MP4 links win on the wide sweep and on the applied template's a:has-text('MP4') rows.

The page is built at test time from the real kellymadisonmedia capture in the repo
(tests/corpus/recognizer/kelly.cap.json: the video.js quality selector, the two download-modal toggles, the modal),
plus the two stock stylesheet rules the capture does not carry: Bulma's hidden .modal and video.js's off-flow
.vjs-control-text / hidden .vjs-menu. Hermetic: real Chromium, no network.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path

import pytest

from bulk_downloader.detect import find_best_download, res_score

BD_GATE_SCOPE = "module"

CAPTURE = Path(__file__).parent / "corpus" / "recognizer" / "kelly.cap.json"
CSS = ("<style>.modal{display:none}.modal.is-active{display:flex}.vjs-menu{display:none}"
       ".video-js .vjs-control-text{border:0;clip:rect(0 0 0 0);height:1px;overflow:hidden;padding:0;"
       "position:absolute;width:1px}.vjs-button>.vjs-icon-placeholder{display:block}</style>")
TEMPLATE = {"row_selectors": ["a:has-text('MP4')"]}
# The menu button shows the CURRENT tier: "4k" in the kelly capture, "1080p" in the finding's winner label
# ("1080p(?):1080p Open quality selector me"). Both shapes are exercised; "1080p" is the one a title-shape
# rule (dl95-africancasting-4: "4k" is not an explicit height) cannot refuse for us.
TIERS = {"4k": 2160, "1080p": 1080}


@contextmanager
def _browser():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


def _episode_html(browser, tier):
    page = browser.new_page()
    page.route("**/*", lambda route: route.abort())
    page.set_content(json.loads(CAPTURE.read_text(encoding="utf-8"))["dom_log"][0]["html"],
                     wait_until="domcontentloaded")
    quality = page.locator(".vjs-quality-selector").first.evaluate(
        "(e, tier) => { e.querySelector('.vjs-icon-placeholder').textContent = tier; return e.outerHTML; }", tier)
    toggles = page.locator("button.js-toggle-modal[data-target='#download-modal']").evaluate_all(
        "es => es.map(e => e.outerHTML).join('')")
    modal = page.locator("#download-modal").first.evaluate("e => e.outerHTML")
    page.close()
    body = f'<div class="video-js"><div class="vjs-control-bar">{quality}</div></div>{toggles}{modal}'
    return f"<!doctype html><html><head>{CSS}</head><body>{re.sub(r'memberid=[0-9]+', 'memberid=0', body)}</body></html>"


@contextmanager
def _episode(tier="4k"):
    with _browser() as browser:
        html = _episode_html(browser, tier)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.set_content(html, wait_until="load")
        yield page


def _text(best):
    return " ".join(str((best or {}).get("text") or "").split())


@pytest.mark.parametrize("tier", TIERS)
def test_precondition_the_fixture_has_the_measured_shape(tier):
    with _episode(tier) as page:
        menu = page.locator("button[title='Open quality selector menu']")
        assert menu.is_visible() and res_score(menu.inner_text()) == TIERS[tier]  # the visible tier-labelled control
        mp4 = page.locator("a:has-text('MP4')")
        assert mp4.count() >= 3 and not mp4.first.is_visible()              # the links wait in the closed modal
        assert res_score(mp4.first.inner_text()) == 2160                    # and their tier does parse


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("learned", [None, TEMPLATE], ids=["wide", "applied-template"])
def test_the_mp4_download_link_wins_not_the_player_quality_menu(learned, tier):
    with _episode(tier) as page:
        best = find_best_download(page, learned=learned)
    assert best is not None, learned
    assert "quality selector" not in _text(best).lower(), _text(best)
    assert _text(best).startswith("MP4 4k") and "/download/video/" in _text(best), _text(best)


def test_a_download_control_inside_the_player_bar_is_kept():
    """Negative control: only tier-labelled playback controls are refused, not a player's own Download button."""
    html = (f"<!doctype html><html><head>{CSS}</head><body><div class=\"video-js\"><div class=\"vjs-control-bar\">"
            '<button class="vjs-control vjs-button vjs-download-button">Download 1080p</button>'
            "</div></div></body></html>")
    with _browser() as browser:
        page = browser.new_page()
        page.set_content(html, wait_until="load")
        best = find_best_download(page)
    assert best is not None and _text(best) == "Download 1080p", _text(best)


def test_a_media_file_link_inside_a_player_menu_stays_a_candidate():
    """REFUTE F1 (bd-worker-B3-B): a player-menu item that LINKS a media file is a download, not a playback control."""
    html = (f"<!doctype html><html><head><style>.vjs-menu{{display:block}}</style></head><body>"
            '<div class="video-js"><div class="vjs-control-bar"><div class="vjs-menu"><ul class="vjs-menu-content">'
            '<li><a href="/media/scene_2160.mp4">2160p</a></li><li><a href="/media/scene_1080.mp4">1080p</a></li>'
            "</ul></div></div></div></body></html>")
    with _browser() as browser:
        page = browser.new_page()
        page.set_content(html, wait_until="load")
        best = find_best_download(page)
    assert best is not None and best.get("score") == 2160 and "scene_2160.mp4" in _text(best), _text(best)


def test_the_player_control_refusal_is_counted_for_the_operator():
    """REFUTE F2: the refusal reaches row 499's candidate_admission_filtered summary."""
    events = []

    class _Runner:
        def log_event(self, kind, msg, extra=None):
            events.append((kind, msg, dict(extra or {})))

    with _episode("1080p") as page:  # the finding's label; a "4k" menu may be refused earlier, uncounted
        find_best_download(page, runner=_Runner())
    summaries = [e for e in events if e[0] == "candidate_admission_filtered"]
    assert summaries and summaries[0][2].get("player_control") == 1, events

