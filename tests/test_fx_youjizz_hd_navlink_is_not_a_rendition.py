"""fx-youjizz-hd-navlink (O1568d, spare12 10.0.70.183; results/spare12/youjizz.md): every youjizz scene page carries
the header HD toggle ``<a class="hd" href="/highdefinition/1.html"><span>HD</span></a>`` (and an icon twin) -- page 1
of the HD listing. The scorer took it as the 720p rendition, so jobs were held:

  needs_review: Best is 720p (below 1080p) (no identity proof ...) -- Approve to force. Saw: 720p(?):HD /highdefinition/1.html

As dl95-tube8-2 (/cat/hd/) and dl95-pussyspace-1 (/hd/): a root quality listing is a nav link, not a rendition; youjizz
spells it /highdefinition/<page>.html. Local headless chromium, fixture pages served by page.route; no live site.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-yj.test"
SCENE_URL = ORIGIN + "/videos/fixture-scene-41670211.html"
NAV = ('<div class="nav"><a class="hd" data-auto="0" href="/highdefinition/1.html"><span>HD</span></a>'
       '<span class="open-hd "><a class="hd icon" href="/highdefinition/1.html" data-auto="0"></a></span></div>')


def _html(extra=""):
    return f"""<!doctype html><html><body>{NAV}
<h1>Fixture scene 41670211</h1><div id="player"><video id="v" preload="none"></video></div>{extra}
</body></html>"""


@contextmanager
def _page(extra=""):
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    body = _html(extra)

    def handler(route, request):
        if request.url.startswith(SCENE_URL):
            route.fulfill(status=200, content_type="text/html", body=body)
        else:
            route.fulfill(status=404, content_type="text/plain", body="nope")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, timeout=20000,
                                        args=["--no-sandbox", "--disable-dev-shm-usage"])
        except PlaywrightError as e:
            pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip: {e}")
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.route(ORIGIN + "/**", handler)
            page.goto(SCENE_URL, wait_until="load")
            page.wait_for_timeout(200)
            yield page
        finally:
            browser.close()


def _texts(best):
    return [c["text"] for c in best.get("_all_candidates", []) or []] + [best["text"]]


def test_hd_listing_nav_link_is_not_scored_as_a_rendition():
    """THE ROW: the /highdefinition/1.html header toggle is refused before scoring."""
    from bulk_downloader.detect import find_best_download

    with _page() as page:
        best = find_best_download(page)
    texts = _texts(best) if best else []
    assert not any("highdefinition" in t for t in texts), (
        f"FX_YOUJIZZ_HD_NAVLINK: the HD listing nav link was scored as a rendition: "
        f"best={best and (best['score'], best['text'])}")


def test_the_listing_rules_name_it():
    from bulk_downloader import candidate_filter
    from bulk_downloader.detect import _listing_link_path

    assert _listing_link_path("HD /highdefinition/1.html") == "/highdefinition/1.html"
    v = candidate_filter.classify(url=ORIGIN + "/highdefinition/1.html", text="HD", page_host="fixture-yj.test")
    assert not v.accepted and "navigation URL" in v.rejections, v


def test_positive_control_a_media_file_under_it_keeps_its_score():
    from bulk_downloader.detect import find_best_download

    extra = '<a class="dl" href="/highdefinition/fixture_41670211_1080p.mp4">Download 1080p</a>'
    with _page(extra) as page:
        best = find_best_download(page)
    assert best and best["score"] == 1080, best and (best["score"], best["text"])


@pytest.mark.parametrize("path", ["/videos/free-use-family-41670211.html", "/highdefinition-tips-41670211.html"])
def test_control_scene_pages_are_not_listings(path):
    from bulk_downloader.detect import _listing_link_path

    assert _listing_link_path("Watch " + path) == ""
