"""dl95-justporn-1 (O1513, harness-work/UIUX-20260928/download-95/A5-A/RESULT-justporn.md#D1, shot _review/nr4/NR4__JP.png).

Measured on test2 v3.66.1709 (journal 2026-09-28 23:5xZ): justporn 22959 ended needs_review "Clicked but no download
started -- looks like a modal-trigger button ... Saw: auto(?):https://www.justporn.com/get_f | auto(?):...". The winner
was the site's own download link, a KVS /get_file/ URL whose file name is followed by a slash:

    /get_file/5/<hash>/22000/22959/22959_720p.mp4/?v-acctoken=..&download=true&download_filename=<slug>_720p.mp4

_direct_media_route tested path.endswith(".mp4"), so ".mp4/" read as "not a file", the link was clicked, and the
click fired no download event. Contract after the fix: the extension is the last path segment's with the trailing
slash ignored, the signed URL survives verbatim, and the site's download_filename names the file.

The fixture is a verbatim excerpt of the real page (captured 2026-09-29; only the session acctoken is redacted); the
end-to-end test runs the real find_best_download on it and routes the winner it picks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bulk_downloader.runner_transport import TransportMixin

BD_GATE_SCOPE = "module"

PAGE = "https://www.justporn.com/video/22959/aria-rae-faces-a-relentless-casting-session/"
FIXTURE = Path(__file__).parent / "fixtures" / "dl95_justporn_22959_download_menu.html"
GET_720 = (
    "https://www.justporn.com/get_file/5/4ef1a3927a17d75755060b71e4d08cda/22000/22959/22959_720p.mp4/"
    "?v-acctoken=REDACTED&download=true&download_filename=aria-rae-faces-a-relentless-casting-session_720p.mp4"
)
# ok.xxx, the same KVS family (A5-A/ok/playercap/dom-before.html): no query, no site name.
OK_720 = "https://ok.xxx/get_file/13/1912258e8e056bd2a2289c760e6b8493/785000/785100/785100_720p.mp4/"


def route(href, page=PAGE):
    return TransportMixin._direct_media_route(href, page)


def test_kvs_get_file_download_link_is_a_direct_fetch():
    url, name = route(GET_720)
    assert url == GET_720, f"DL95_KVS_GET_FILE_CLICKED: {url!r}"
    assert name == "aria-rae-faces-a-relentless-casting-session_720p.mp4", name


def test_kvs_get_file_without_a_site_name_uses_the_path_file_name():
    assert route(OK_720, "https://ok.xxx/video/785100/") == (OK_720, "785100_720p.mp4")


@pytest.mark.parametrize(
    "href,why",
    [
        (
            "https://www.justporn.com/get_file/0/d19b542e2c818ecda3b3d11ddb7a0fb8/22000/22959/screenshots/1.jpg/",
            "a KVS screenshot, not a video",
        ),
        (
            "https://www.justporn.com/video/28906/sexy-big-booty-and-big-boobs-teen/",
            "a scene PAGE with a trailing slash",
        ),
        ("https://www.justporn.com/get_file/5/abc/22000/22959/", "no file name at all"),
        ("https://cdn.example.com/hls/scene/2.m3u8/", "a manifest stays _stream_route's"),
    ],
)
def test_negative_control_trailing_slash_is_not_a_licence(href, why):
    assert route(href) == (None, None), why


def test_real_page_winner_routes_to_a_direct_fetch():
    """End to end on the captured markup: the detector's own winner is the 720p get_file link, and it routes."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from bulk_downloader.detect import find_best_download

    html = FIXTURE.read_text(encoding="utf-8")
    assert html.count("class='download-link'") == 2, "fixture is not the captured download menu"
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page(viewport={"width": 1400, "height": 900})
            pg.route(
                "**/*",
                lambda r: r.fulfill(status=200, content_type="text/html", body=html)
                if r.request.url == PAGE
                else r.fulfill(status=204, body=""),
            )
            pg.goto(PAGE, wait_until="load")
            best = find_best_download(pg)
            assert best and best.get("locator") is not None, "detector found no candidate"
            href = best["locator"].get_attribute("href")
        finally:
            br.close()
    assert href == GET_720, f"precondition: the measured winner was the 720p get_file link, got {href!r}"
    url, name = route(href)
    assert url == GET_720, f"DL95_KVS_GET_FILE_CLICKED (real winner): {url!r}"
    assert name.endswith("_720p.mp4"), name
