"""O1567 fx-tube8 (fresh149, 2026-09-29 21:4xZ live verify): once the age wall
cleared, tube8 scene 235819911 (6:58) went to needs_review "Best is 720p (below
1080p) ... 720p:page-media" -- the DOM scorer only sees page media, while the
page's mediaDefinitions /media/mp4/ endpoint lists 1080p/720p/480p/240p files.
tube8 was not in AYLO_FREE_TUBES, so the Aylo page extractor never ran.

Contract: tube8 routes to the Aylo free-tube extractor and yields the 1080p
file; look-alike hosts do not route.
"""
from __future__ import annotations

import pytest

from bulk_downloader import extractors_aylo as aylo

BD_GATE_SCOPE = "module"

PAGE_URL = "https://www.tube8.com/porn-video/235819911/"
MP4_EP = "https://www.tube8.com/media/mp4/?s=eyJ2a2V5IjoyMzU4MTk5MTF9"
HLS_EP = "https://www.tube8.com/media/hls/?s=eyJ2a2V5IjoyMzU4MTk5MTF9"
FILE_1080 = "https://ev-ph.t8cdn.com/videos/202402/20/448441931/1080P_4000K_448441931.mp4"
FILE_720 = "https://ev-ph.t8cdn.com/videos/202402/20/448441931/720P_4000K_448441931.mp4"

# Live shape (fresh149): absolute indirect entries, JSON-escaped slashes.
LIVE_HTML = (
    '<html><script>var page_params = {"mediaDefinitions":['
    '{"format":"hls","videoUrl":"' + HLS_EP.replace("/", "\\/") + '","remote":true},'
    '{"format":"mp4","videoUrl":"' + MP4_EP.replace("/", "\\/") + '","remote":true}'
    '],"video_unavailable_country":"false"};</script></html>')


class _Page:
    url = PAGE_URL

    def __init__(self):
        self.fetched = []

    def content(self):
        return LIVE_HTML

    def evaluate(self, _js, u):
        self.fetched.append(u)
        if "/media/mp4/" in u:
            return [{"quality": "1080", "format": "mp4", "videoUrl": FILE_1080},
                    {"quality": "720", "format": "mp4", "videoUrl": FILE_720}]
        return None


def test_tube8_routes_to_the_aylo_free_tube_extractor():
    assert aylo.is_aylo_url(PAGE_URL), "tube8 never reaches the Aylo page extractor"
    assert aylo.is_free_tube_url(PAGE_URL), "tube8 must hold the free-tube min-res floor"


def test_tube8_scene_page_yields_the_1080p_file():
    page = _Page()
    result = aylo.extract_from_page(page)
    assert result.ok, "%s %s" % (result.error, result.error_detail)
    assert result.variant.url == FILE_1080
    assert MP4_EP in page.fetched


@pytest.mark.parametrize("url", [
    "https://tube8.com.evil.example/porn-video/1/",
    "https://nottube8.com/porn-video/1/",
    "https://www.tube8.co/porn-video/1/",
])
def test_negative_control_lookalike_hosts_do_not_route(url):
    assert not aylo.is_aylo_url(url), url
    assert not aylo.is_free_tube_url(url), url
