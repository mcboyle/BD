"""dl95-pussyspace-2 (O1513 A5-A finding D2): Scrape listing returned category pages and missed the scenes.

Measured on test2: pussyspace.com -> 4 links, all facets of the site's own video index (/video/random/,
/video/1-10min/, ...), while the page's /vid-<id>-<slug> scene links were not returned. GREEN: one classifier
(bulk_downloader.listing_links) for /api/scrape_listing and the subscription scanner: a scene-id path segment is a
video; a lone sort/duration facet after /video/ is not.

HOME is the anchor markup of https://www.pussyspace.com/ as captured 2026-09-29 (nav links single-quoted and
absolute; each scene linked twice, relative) -- the base tree returns exactly those 4 facets from the full capture.
Hermetic: httpx.Client is replaced by a scripted client; no network.
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

import bulk_downloader.app as app_mod
from bulk_downloader import runner as runner_mod
from bulk_downloader.provider_resolve_impl import _common as common

BD_GATE_SCOPE = "module"

SITE = "https://www.pussyspace.com/"
FACETS = ["video/random/", "video/1-10min/", "video/10-60min/", "video/movies/"]
SCENES = ["vid-6167743-dick-just-feels-so-much-better-than-a-dildoaxgo/",
          "vid-6167794-giselle-takes-prince-yahshua-s-bbc-in-her-ass/"]
HOME = "".join(f"<li><a href='{SITE}{f}'><div>nav</div></a></li>" for f in FACETS) + "".join(
    f'<a class="video_img not_lazy" href="/{s}"><img></a><a href="/{s}">title</a>' for s in SCENES)
WANT = [SITE + s for s in SCENES]


class _Resp:
    status_code = 200
    headers: dict = {}
    text = HOME

    def raise_for_status(self):
        return None


class _Client:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None):
        return _Resp()


def test_api_returns_the_scenes_not_the_video_index_facets(monkeypatch):
    monkeypatch.setattr(httpx, "Client", _Client)
    monkeypatch.setattr(app_mod, "_is_url_public", lambda url: True)
    body = app_mod.app.test_client().post("/api/scrape_listing", json={"url": SITE}).get_json()
    assert body["ok"] is True, body
    assert body["found"] == WANT, body


def test_subscription_scanner_returns_the_same_links(monkeypatch):
    monkeypatch.setattr(httpx, "Client", _Client)
    monkeypatch.setattr(common, "_is_safe_public_host", lambda host: (True, ""))
    runner = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    runner._download_proxy_url = lambda: None
    assert runner._scrape_listing_urls(SITE) == WANT


def test_filter_listings_false_still_returns_the_facets(monkeypatch):
    monkeypatch.setattr(httpx, "Client", _Client)
    monkeypatch.setattr(app_mod, "_is_url_public", lambda url: True)
    body = app_mod.app.test_client().post(
        "/api/scrape_listing", json={"url": SITE, "filter_listings": False}).get_json()
    assert body["found"] == [SITE + f for f in FACETS] + WANT, body


@pytest.mark.parametrize("url,want", [
    (SITE + "vid-6167743-some-title", True),               # scene id in the segment, no trailing slash
    (SITE + "video/random/", False),
    (SITE + "video/1-10min/", False),
    (SITE + "video/", False),                               # the video index itself
    (SITE + "video/12345/some-scene/", True),               # unchanged: id below /video/
    (SITE + "video/my-long-scene-title/", True),            # unchanged: slug below /video/
    (SITE + "v/ab12cd/", True),                             # unchanged: short-id pages are not facets
    (SITE + "category/amateur/video/", False),              # unchanged: listing pattern
    (SITE + "category/video/123", True),                    # unchanged: numeric last segment
    (SITE + "media/clip.mp4", True),                        # unchanged: media extension
    (SITE + "best/videos/2015-01/", False),                 # unchanged: not video-shaped at all
])
def test_is_video_link(url, want):
    from bulk_downloader.listing_links import is_video_link
    assert is_video_link(url) is want


def test_both_callers_share_the_one_classifier():
    root = Path(runner_mod.__file__).parent
    for name in ("app_scrape_listing.py", "runner.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert "extract_video_links(" in src, name
        assert not re.search(r"^\s*(VIDEO_PATTERNS|LISTING_PATTERNS)\s*=", src, re.M), name
