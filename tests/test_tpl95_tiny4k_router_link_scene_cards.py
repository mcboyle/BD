"""tpl95-tiny4k-1 (test2 2026-09-29, B4-B tpl-tiny4k): a logged-in scan of
https://tiny4k.com/members/movies walked 1 page and reported zero_scenes_found.

The listing is a Vue SPA.  Each scene title is rendered as a literal, unresolved
<nuxtlink to="/members/video/<slug>"> -- there is no scene <a href> on the page,
and the thumbnail is a sibling of that link, not inside it.  The crawler read
only a[href], so the scene cohort was empty.

The fixture is that page's rendered DOM captured on test2 (scripts/stylesheets
stripped, markup verbatim).  Everything is served on loopback; any other
request is aborted.
"""
from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from bulk_downloader import scene_crawler

BD_GATE_SCOPE = "module"
FIXTURE = Path(__file__).parent / "fixtures" / "tpl95_tiny4k" / "members_movies.html"

# One thumbnail for the whole grid: no router link has a card of its own.
_SHARED_IMAGE = (
    "<!doctype html><html><body><a href='/account/logout'>Logout</a>"
    "<div><img src='/t.jpg'>"
    + "".join(
        f"<div><nuxtlink to='/members/video/s{i}'><strong>Scene {i}</strong></nuxtlink></div>"
        for i in range(4)
    )
    + "</div></body></html>"
)
# Each image shared by one router link and one resolved <a href> scene link
# (lens bd-cx-worker-1 R1): an anchor sibling is as much a rival card as a router one.
_MIXED_LINKS = (
    "<!doctype html><html><body><a href='/account/logout'>Logout</a>"
    + "".join(
        f"<div><img src='/t{i}.jpg'>"
        f"<div><nuxtlink to='/members/video/r{i}'><strong>Routed {i}</strong></nuxtlink></div>"
        f"<div><a href='/members/video/a{i}'>Anchor {i}</a></div></div>"
        for i in range(3)
    )
    + "</body></html>"
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path == "/members/movies":
            raw = FIXTURE.read_bytes()
        elif path == "/members/shared":
            raw = _SHARED_IMAGE.encode()
        elif path == "/members/mixed":
            raw = _MIXED_LINKS.encode()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _fmt, *_args):
        return


@pytest.fixture(scope="module")
def origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def page(origin):
    from bulk_downloader import cloak

    with cloak.cloaked_page(
        headless=True,
        config={"browser_backend": "playwright"},
        viewport={"width": 1280, "height": 720},
    ) as browser_page:
        browser_page.route(
            "**/*",
            lambda route: route.continue_()
            if route.request.url.startswith(origin) else route.abort(),
        )
        yield browser_page


def _crawl(page, origin, tmp_path, path):
    return scene_crawler.crawl_with_page(
        page,
        site_id="tiny4k",
        listing_url=origin + path,
        site_config={},
        newest_n=0,
        max_pages=1,
        max_scrolls=2,
        delay_s=0,
        title_fetch_limit=0,
        db_path=str(tmp_path / "crawl.sqlite"),
        enqueue_fn=lambda _sid, _url: {"added": 1},
    )


def test_fixture_precondition_scene_links_are_router_links_not_anchors():
    html = FIXTURE.read_text()
    routed = re.findall(r'<nuxtlink[^>]*\bto="(/members/video/[^"]+)"', html)
    assert len(routed) == 12
    assert not re.search(r'<a\b[^>]*href="[^"]*/members/video/', html)


def test_the_row_listing_discovers_its_twelve_router_link_scenes(page, origin, tmp_path):
    expected = {
        origin + slug
        for slug in re.findall(r'<nuxtlink[^>]*\bto="(/members/video/[^"]+)"', FIXTURE.read_text())
    }
    result = _crawl(page, origin, tmp_path, "/members/movies")
    assert result["state"] == "COMPLETED", result
    assert result["zero_scenes_found"] is False, (
        f"tiny4k router-link scene cards not discovered: {result}")
    assert {s["url"] for s in result["scenes"]} == expected, result["scenes"]
    assert result["scene_shapes"] == ["/members/video/<slug>"], result["scene_shapes"]
    assert all(s["title"].strip() for s in result["scenes"]), result["scenes"]
    assert result["queued"] == 12


@pytest.mark.parametrize("path", ["/members/shared", "/members/mixed"], ids=["shared", "mixed"])
def test_a_router_link_without_a_card_image_of_its_own_is_not_a_scene(page, origin, tmp_path, path):
    """Negative control: the image must belong to that link's card, not the grid."""
    result = _crawl(page, origin, tmp_path, path)
    assert result["state"] == "COMPLETED", result
    assert result["discovered"] == 0, result["scenes"]
    assert result["zero_scenes_found"] is True, result
