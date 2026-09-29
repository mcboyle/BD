"""dl95-tube8-2 (harness-work/UIUX-20260928/download-95/A5-A/RESULT-tube8.md#D1, shot _review/nr7/NR7__T8.png):
on a tube8 scene page (Aylo family) the site-header category link ``<a href="/cat/hd/">HD</a>`` was scored as the
720p rendition, so the min-resolution gate refused the job -- "Best is 720p (below 1080p) (no identity proof) ...
saw: 720p(?):HD /cat/hd/ | Save and Close" -- and the page's own HLS player was never consulted.

Row 722 already refuses a link whose path names a listing (/tag/, /category/, /categories/, /search, /model(s)/)
before scoring; tube8 spells its category segment /cat/. With the nav link refused the DOM has no candidate, which
is the state in which Row 722 consults what the page itself fetched (its HLS master).

Local headless chromium, fixture pages served by ``page.route``; no live site, no login, no credentials.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-t8.test"
SCENE_URL = ORIGIN + "/porn-video/71234561/"
MASTER = ORIGIN + "/hls/videos/202609/28/71234561/1080P_4000K_71234561.mp4/master.m3u8"

NAV = (
    '<header><a href="/">Home</a> <a href="/cat/hd/">HD</a> <a href="/cat/amateur/">Amateur</a>'
    ' <a href="/categories/">Categories</a></header>'
)
SAVE = '<div class="modal"><button type="button">Save and Close</button></div>'


def _html(extra=""):
    return f"""<!doctype html><html><body>{NAV}
<h1>Fixture scene 71234561</h1>
<div id="player"><video id="v" preload="none"></video></div>
{extra}{SAVE}
<script>fetch('{MASTER}').catch(()=>{{}}); window.__fetched = true;</script>
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

    body = _html(extra)

    def handler(route, request):
        if request.url.startswith(SCENE_URL):
            route.fulfill(status=200, content_type="text/html", body=body)
        elif ".m3u8" in request.url:
            route.fulfill(
                status=200,
                content_type="application/vnd.apple.mpegurl",
                body="#EXTM3U\n",
            )
        else:
            route.fulfill(status=404, content_type="text/plain", body="nope")

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.route(ORIGIN + "/**", handler)
            page.goto(SCENE_URL, wait_until="load")
            page.wait_for_function("() => window.__fetched === true", timeout=10000)
            page.wait_for_timeout(300)
            yield page
        finally:
            browser.close()


def _texts(best):
    return [c["text"] for c in best.get("_all_candidates", []) or []] + [best["text"]]


def test_cat_nav_link_is_not_scored_as_a_rendition(capsys):
    """THE ROW: the /cat/hd/ header link is refused before scoring, so no 720p guess blocks the page's player."""
    from bulk_downloader.detect import find_best_download

    with _page() as page:
        best = find_best_download(page)
    texts = _texts(best) if best else []
    assert not any("/cat/" in t for t in texts), (
        f"the category nav link was scored as a rendition: best={best and (best['score'], best['text'])}"
    )
    # Refused by name, not merely absent. On the lane the admission filter (dl95-pussyspace-1) rejects the link
    # before Row 722's listing rule logs it, so each layer is pinned on its own and the run must name a refusal.
    from bulk_downloader import candidate_filter
    from bulk_downloader.detect import _listing_link_path

    assert _listing_link_path("HD /cat/hd/") == "/cat/hd/", "Row 722 listing rule no longer names /cat/"
    v = candidate_filter.classify(url=ORIGIN + "/cat/hd/", text="HD", page_host="fixture-t8.test")
    assert not v.accepted and "navigation URL" in v.rejections, v
    err = capsys.readouterr().err
    assert ("download: skipped listing link 'HD' (/cat/hd/)" in err
            or "navigation_url=" in err), err


def test_positive_control_a_media_file_under_cat_keeps_its_score():
    """A /cat/ path that is itself a media file stays a candidate (Row 722's file exception holds)."""
    from bulk_downloader.detect import find_best_download

    extra = '<a class="dl" href="/cat/hd/fixture_71234561_1080p.mp4">Download 1080p</a>'
    with _page(extra) as page:
        best = find_best_download(page)
    assert best and best["score"] == 1080, best and (best["score"], best["text"])
    assert "fixture_71234561_1080p.mp4" in best["text"], best["text"]
