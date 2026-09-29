"""dl95-porndig-2 (harness-work/UIUX-20260928/download-95/A5-A/RESULT-porndig.md#D2, shot _review/nr3/NR3__PD.png):
Scrape listing on porndig.com returned only the "/video/" index link. Its scene links are in the static HTML as
/videos/<id>/<slug>.html (e.g. /videos/242142/nikole-masturbates-on-the-stairs-in-front-of-the-library.html), and
the video-path heuristic knew "/video/" but not the plural "/videos/<numeric id>" scene shape.

Both copies of the heuristic are driven: the /api/scrape_listing view and SiteRunner._scrape_listing_urls (the
subscription scanner, documented as "same scrape logic"). Fetches are faked; no network, no live site.
"""

from __future__ import annotations

import bulk_downloader.app as a
import httpx
from bulk_downloader import runner
from bulk_downloader.provider_resolve_impl import _common as common

BD_GATE_SCOPE = "module"

LISTING = "https://www.porndig.com/"
SCENE_1 = "https://www.porndig.com/videos/242142/nikole-masturbates-on-the-stairs-in-front-of-the-library.html"
SCENE_2 = "https://www.porndig.com/videos/242518/in-pov-and-vr-we-will-see-this-blonde-enjoy-the-best-sex.html"
NOT_SCENES = (
    "https://www.porndig.com/videos/",
    "https://www.porndig.com/videos/popular/",
    "https://www.porndig.com/videos/top-rated",
    "https://www.porndig.com/videos/2/",
    "https://www.porndig.com/videos/3",
)

HTML = (
    '<nav><a href="/">Home</a> <a href="/video/">Videos</a> <a href="/videos/">All</a>'
    ' <a href="/videos/popular/">Popular</a> <a href="/videos/top-rated">Top</a></nav>'
    '<div class="pager"><a href="/videos/2/">2</a> <a href="/videos/3">3</a></div>'
    '<div class="thumbs">'
    '<a class="thumb" href="/videos/242142/nikole-masturbates-on-the-stairs-in-front-of-the-library.html">'
    '<img src="/t/242142.jpg"></a>'
    '<a class="thumb" href="https://www.porndig.com/videos/242518/'
    'in-pov-and-vr-we-will-see-this-blonde-enjoy-the-best-sex.html"><img src="/t/242518.jpg"></a>'
    "</div>"
)


class _Resp:
    status_code = 200
    text = HTML

    def raise_for_status(self):
        return None


class _Client:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None):
        return _Resp()


class _RunnerStub:
    def _download_proxy_url(self):
        return None


def _endpoint_found(monkeypatch):
    monkeypatch.setattr(a, "_is_url_public", lambda url: True)
    monkeypatch.setattr(httpx, "Client", _Client)
    r = a.app.test_client().post("/api/scrape_listing", json={"url": LISTING})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    return r.get_json()["found"]


def _runner_found(monkeypatch):
    monkeypatch.setattr(common, "_is_safe_public_host", lambda host: (True, ""))
    monkeypatch.setattr(httpx, "Client", _Client)
    return runner.SiteRunner._scrape_listing_urls(_RunnerStub(), LISTING)


def test_scrape_listing_endpoint_returns_videos_id_scene_links(monkeypatch):
    found = _endpoint_found(monkeypatch)
    assert SCENE_1 in found and SCENE_2 in found, (
        f"porndig scene links dropped: {found}"
    )


def test_subscription_scan_returns_videos_id_scene_links(monkeypatch):
    found = _runner_found(monkeypatch)
    assert SCENE_1 in found and SCENE_2 in found, (
        f"porndig scene links dropped: {found}"
    )


def test_plural_videos_listing_pages_are_not_scenes(monkeypatch):
    """Negative control: /videos/ index, named and numbered pages are listings, in both copies."""
    for found in (_endpoint_found(monkeypatch), _runner_found(monkeypatch)):
        leaked = [u for u in NOT_SCENES if u in found]
        assert not leaked, f"plural /videos/ listing pages returned as scenes: {leaked}"
