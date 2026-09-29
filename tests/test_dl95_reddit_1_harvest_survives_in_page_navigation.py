"""dl95-reddit-1 (harness-work/UIUX-20260928/download-95/A1-A/FINDINGS-A1-A.md#A11, MED).

Scene discovery on https://www.reddit.com/ ended FAILED after 5 s with 0 pages walked:
"Error: Locator.evaluate_all: Execution context was destroyed, most likely because of a navigation"
(reddit/summary.json, reddit/events.jsonl scan_done 2026-09-28T23:41:09Z). The listing replaced its own
document after DOMContentLoaded while the crawler was harvesting links, and that one read failed the run.

Real Chromium, served on an ephemeral loopback port; no live site is contacted. The navigating listing
replaces itself (location.replace) while the crawler's link harvest is being evaluated in it, which is the
exact shape of the reddit failure. The harvest must wait for the new document and read it. Controls: the
same listing without navigation harvests the same scenes; a non-navigation read error still propagates at
once; a page that never stops navigating still fails loudly after a bounded number of reads.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

BD_GATE_SCOPE = "module"

N_SCENES = 5
_CARDS = "".join(
    f'<div class="thumb"><a href="/video.{7100 + i}/front_page_post_{i}">'
    f'<img src="/thumbs/{i}.jpg" alt="Post {i}"></a>'
    f'<p><a href="/video.{7100 + i}/front_page_post_{i}">Post {i}</a></p></div>'
    for i in range(N_SCENES)
)
_LISTING = (
    "<!doctype html><html><head><title>Front page</title></head><body>"
    '<nav><a href="/r/popular">Popular</a><a href="/r/all">All</a></nav>'
    f"<main>{_CARDS}</main></body></html>"
)
# The first harvest read of the anchor list starts the client-side redirect and
# is still pending when the new document commits -- the navigation lands in
# the middle of the harvest, deterministically.
_REDIRECTING = (
    "<!doctype html><html><head><title>Loading</title><script>"
    "(() => { const map = Array.prototype.map; let fired = false;"
    " Array.prototype.map = function (...args) {"
    "  if (!fired && this.length && this[0] instanceof HTMLAnchorElement) {"
    "   fired = true; location.replace('/landed'); return new Promise(() => {}); }"
    "  return map.apply(this, args); }; })();"
    "</script></head><body>"
    '<nav><a href="/r/popular">Popular</a><a href="/login">Log in</a></nav></body></html>'
)
_PAGES = {"/": _REDIRECTING, "/landed": _LISTING, "/still": _LISTING}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = _PAGES.get(urlsplit(self.path).path)
        if body is None:
            self.send_error(404)
            return
        raw = body.encode()
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
def page():
    from bulk_downloader import cloak

    with cloak.cloaked_page(headless=True, config={"browser_backend": "playwright"},
                            viewport={"width": 1280, "height": 720}) as browser_page:
        yield browser_page


def _crawl(page, origin, tmp_path, path, site_id):
    from bulk_downloader import scene_crawler

    queued = []
    result = scene_crawler.crawl_with_page(
        page, site_id=site_id, listing_url=origin + path, site_config={"name": "front"},
        newest_n=0, max_pages=1, max_scrolls=1, delay_s=0, title_fetch_limit=0,
        db_path=str(tmp_path / f"{site_id}.sqlite"),
        enqueue_fn=lambda sid, url: queued.append(url) or {"added": 1, "dupes": 0, "skipped": 0},
    )
    return result, queued


def _expected(origin):
    return sorted(f"{origin}/video.{7100 + i}/front_page_post_{i}" for i in range(N_SCENES))


def test_listing_that_navigates_during_harvest_is_harvested(page, origin, tmp_path):
    try:
        result, queued = _crawl(page, origin, tmp_path, "/", "dl95-reddit-nav")
    except Exception as exc:  # the scan's worker turns this into state FAILED
        pytest.fail(
            "dl95-reddit-1: scene discovery run failed because the listing navigated during "
            f"the link harvest: {type(exc).__name__}: {str(exc)[:200]}")
    assert result["state"] == "COMPLETED", result
    assert result["discovered"] == N_SCENES, result
    assert sorted(queued) == _expected(origin), queued
    assert page.url == origin + "/landed"


def test_listing_without_navigation_harvests_the_same_scenes(page, origin, tmp_path):
    result, queued = _crawl(page, origin, tmp_path, "/still", "dl95-reddit-still")
    assert result["state"] == "COMPLETED", result
    assert result["discovered"] == N_SCENES, result
    assert sorted(queued) == _expected(origin), queued


class _FailingPage:
    """A page whose anchor read raises ``error`` every time."""

    def __init__(self, error):
        self.error = error
        self.reads = 0
        self.load_waits = 0

    def locator(self, _selector):
        return self

    def evaluate_all(self, _script):
        self.reads += 1
        raise self.error

    def wait_for_load_state(self, *_args, **_kwargs):
        self.load_waits += 1


def test_a_non_navigation_read_error_still_propagates_at_once():
    from bulk_downloader import scene_crawler

    fake = _FailingPage(RuntimeError("Target page, context or browser has been closed"))
    with pytest.raises(RuntimeError, match="has been closed"):
        scene_crawler._collect_anchors(fake)
    assert (fake.reads, fake.load_waits) == (1, 0)


def test_a_page_that_never_stops_navigating_fails_loudly_and_bounded():
    from bulk_downloader import scene_crawler

    fake = _FailingPage(RuntimeError(
        "Locator.evaluate_all: Execution context was destroyed, most likely because of a navigation"))
    with pytest.raises(RuntimeError, match="Execution context was destroyed"):
        scene_crawler._collect_anchors(fake)
    assert 1 <= fake.reads <= 5, fake.reads
    assert fake.load_waits == fake.reads - 1
