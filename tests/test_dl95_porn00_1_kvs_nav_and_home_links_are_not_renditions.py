"""dl95-porn00-1 (harness-work/UIUX-20260928/download-95/B6-B/p1/porn00/RESULT.md, shot stage03-needs_review.png):
on a porn00 (KVS) scene page the scorer's best was the site-nav category link ``<a href=".../category-name/4k/">4K</a>``
scored 2160p; clicking it fired no download -> needs_review "saw: 4K(?):4K https://www.porn00.org/cate | 720p(?):
Porn00: Watch free 720p HD Por | ...". Measured on the real page HTML (served locally, harness-work/FIX/
dl95-porn00-1-bd-worker-B5-B/ASK-scope.md): with the category link refused, the next best was the site LOGO link
``<a href="https://www.porn00.org">Porn00: Watch free 720p HD Porn Videos</a>`` scored 720p -- also not a file.

Row 722's listing refusal gains KVS's /category-name/<x>/ and /categories-list/ segments (and tube8's /cat/, see
test_dl95_tube8_2_*), and a link to a site's root is not a download candidate. With both refused the DOM holds no
candidate, the state in which Row 722 consults what the page itself fetched.

Local headless chromium, fixture pages served by ``page.route``; no live site, no login, no credentials.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://www.fixture-kvs.test"
SCENE_URL = ORIGIN + "/video/fixture-duo-scene/"

PAGE = """<!doctype html><html><head><title>Fixture Duo Scene - FixtureKVS</title></head><body>
<a href="https://www.fixture-kvs.test">FixtureKVS: Watch free 720p HD Porn Videos</a><br/>
<nav><a href="https://www.fixture-kvs.test/categories-list/" id="item6">Categories</a></nav>
<h1>Fixture Duo Scene</h1>
<div class="player"><div id="kt_player"></div></div>
<script>var flashvars = {video_url: '/get_file/3/aa/42000/42561/42561.mp4/', video_url_text: '360p',
 video_alt_url: '/get_file/3/bb/42000/42561/42561_720p.mp4/', video_alt_url_text: '720p'};</script>
<div class="info">Categories:
 <a href="https://www.fixture-kvs.test/category-name/4k/">4K</a>
 <a href="https://www.fixture-kvs.test/category-name/babe/">babe</a>
 <a href="https://www.fixture-kvs.test/category-name/threesome/">threesome</a></div>
%s
</body></html>"""


def _launch(p):
    from playwright.sync_api import Error as PlaywrightError

    try:
        return p.chromium.launch(
            headless=True,
            timeout=20000,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
    except PlaywrightError as e:
        pytest.fail(
            f"chromium not launchable here -- a launch failure is a FAILURE, not a skip: {e}"
        )


@contextmanager
def _page(extra=""):
    from playwright.sync_api import sync_playwright

    body = PAGE % extra

    def handler(route, request):
        if request.url == SCENE_URL:
            route.fulfill(status=200, content_type="text/html", body=body)
        else:
            route.fulfill(status=404, content_type="text/plain", body="nope")

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route(ORIGIN + "/**", handler)
            page.goto(SCENE_URL, wait_until="load")
            page.wait_for_timeout(300)
            yield page
        finally:
            browser.close()


def _best(page):
    from bulk_downloader.detect import find_best_download

    best = find_best_download(page)
    texts = []
    if best:
        texts = [c["text"] for c in best.get("_all_candidates") or []] + [best["text"]]
    return best, texts


def test_kvs_category_nav_and_home_link_are_not_candidates(capsys):
    """THE ROW: neither /category-name/4k/ (2160p) nor the logo link to the site root (720p) is a candidate."""
    with _page() as page:
        best, texts = _best(page)
    assert not any("/category-name/" in t for t in texts), (
        f"the KVS category nav link was scored as a rendition: {texts}"
    )
    assert best is None, (
        f"a non-file link is still the download candidate: {best and (best['score'], best['text'])}"
    )
    err = capsys.readouterr().err
    assert "download: skipped listing link '4K' (/category-name/4k/)" in err, err


def test_positive_control_real_file_links_keep_their_score():
    """A get_file .mp4 anchor and a root link WITH a download query stay candidates beside the refused links."""
    extra = (
        '<a class="dl" href="/get_file/3/bb/42000/42561/42561_720p.mp4/">Download 720p</a>'
        ' <a class="dl" href="/?download=42561_1080p">Download 1080p</a>'
    )
    with _page(extra) as page:
        best, texts = _best(page)
    assert best and best["score"] == 1080, best and (best["score"], best["text"])
    assert any("42561_720p.mp4" in t for t in texts), texts
    assert not any("/category-name/" in t for t in texts), texts
