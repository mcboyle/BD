"""Runtime ranking must discard links the download URL gate will reject."""
from contextlib import contextmanager

import pytest
from playwright.sync_api import sync_playwright

from bulk_downloader.detect import find_best_download

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe
LEARNED = {"row_selectors": [".choice"]}
AD = '<a class="choice" href="https://landing.advertiser.example/offer">BRAND HD</a>'


@contextmanager
def _page(html):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.fulfill(
                status=200, content_type="text/html", body="<main>" + html + "</main>"))
            page.goto("https://media.example/watch/123")
            yield page
        finally:
            browser.close()


@pytest.mark.parametrize("learned", [None, LEARNED], ids=["wide", "learned"])
def test_ad_only_page_has_no_download_candidate(learned):
    with _page(AD) as page:
        assert page.locator("a.choice").is_visible()
        best = find_best_download(page, learned=learned)
        assert best is None, f"OK1_AD_ADMITTED_BEFORE_RESOLUTION: {best}"


@pytest.mark.parametrize("learned", [None, LEARNED], ids=["wide", "learned"])
def test_valid_lower_quality_media_wins_over_ad(learned):
    media = '<a class="choice" href="https://cdn.example/clip.mp4">480p</a>'
    with _page(AD + media) as page:
        best = find_best_download(page, learned=learned)
        assert best is not None, "OK1_REAL_MEDIA_LOST"
        assert best["locator"].get_attribute("href") == "https://cdn.example/clip.mp4", (
            f"OK1_AD_OUTRANKED_REAL_MEDIA: {best}")


@pytest.mark.parametrize("html", [
    '<a class="choice" href="https://cdn.example/clip.mp4">1080p</a>',
    '<a class="choice" href="https://cdn.example/master.m3u8">1080p</a>',
    '<a class="choice" href="/download/123">1080p</a>',
    '<button class="choice">Download 1080p</button>',
])
def test_real_media_and_click_controls_remain_candidates(html):
    with _page(html) as page:
        assert find_best_download(page, learned=LEARNED) is not None, "OK1_VALID_CONTROL_REJECTED"


def test_learned_url_attribute_matches_transport_resolution():
    html = '<a class="choice" href="https://landing.advertiser.example/offer" data-media="https://cdn.example/movie.mp4">HD</a>'
    with _page(html) as page:
        best = find_best_download(page, learned={
            "row_selectors": [".choice"], "url_attribute": "data-media"})
        assert best is not None and best.get("_via_learned"), "OK1_TAUGHT_MEDIA_REJECTED"
