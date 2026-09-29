"""dl95-pegasproductions-1: a zero-scene discovery must say WHY it found nothing.

O1508 rerun on test2 (harness-work/UIUX-20260928/download-95/A1-A/FINDINGS-A1-A.md, A9; shot
pegasproductions/pegasproductions__scan_state.png): discovery of https://www.pegasproductions.com/nouveautes-2 (the
site's success_url) finished COMPLETED, 0 discovered, zero_scenes_found=true, while plain curl from test2 got 403 there
(bot wall).  The result could not tell a challenge page or an HTTP error from a genuinely empty listing.

Loopback fixture pages only (the row 374 pattern); every page carries a logout link so the walk is authenticated and
reaches the zero-scene branch rather than NOT_LOGGED_IN (/blank uses the data-authenticated marker).
"""
from __future__ import annotations

import importlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

BD_GATE_SCOPE = "module"

_LOGOUT = "<a href='/account/logout'>Logout</a>"
_NAV = "<a href='/en/models/all'>Models</a><a href='/en/help/faq'>FAQ</a>"
_PAGES = {
    # Cloudflare serves its interstitial with 503; the challenge is the answer, not the status.
    "/challenge": (503, "<title>Just a moment...</title>"
                        "<div id='challenge-stage'><div class='cf-turnstile'></div></div>" + _LOGOUT),
    "/unavailable": (503, "<title>Service Unavailable</title>" + _NAV + _LOGOUT),
    # No anchors at all: members evidence comes from the data-authenticated marker instead of a logout link.
    "/blank": (200, "<title>Nouveautes</title><p>Nothing here</p><div data-authenticated='true'></div>"),
    "/nav-only": (200, "<title>Nouveautes</title>" + _NAV + _LOGOUT),
    "/listing": (200, "<title>Videos</title>" + _LOGOUT + "".join(
        f"<article><a href='/en/video/scene-{n}/{n}00'><img src='/t{n}.jpg' alt='S{n}'></a><h3>Scene {n}</h3></article>"
        for n in (1, 2, 3))),
    "/paged": (200, "<title>Videos</title>" + _LOGOUT + "<a rel='next' href='/nav-only'>Next</a>" + "".join(
        f"<article><a href='/en/video/scene-{n}/{n}00'><img src='/t{n}.jpg' alt='S{n}'></a><h3>Scene {n}</h3></article>"
        for n in (4, 5))),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib handler API
        status, body = _PAGES.get(urlsplit(self.path).path, (404, "<title>404</title>"))
        raw = f"<!doctype html><html><head></head><body>{body}</body></html>".encode()
        self.send_response(status)
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


def _crawl(page, origin, tmp_path, path, max_pages=1):
    crawler = importlib.import_module("bulk_downloader.scene_crawler")
    return crawler.crawl_with_page(
        page, site_id="pegas", listing_url=origin + path, site_config={}, newest_n=0, max_pages=max_pages,
        max_scrolls=1, delay_s=0, title_fetch_limit=0, db_path=str(tmp_path / "pegas.sqlite"),
        enqueue_fn=lambda sid, url: {"added": 1},
    )


@pytest.mark.parametrize("path,reason", [
    ("/challenge", "challenge_page"),
    ("/unavailable", "http_error"),
    ("/blank", "no_links"),
    ("/nav-only", "no_thumbnails"),
])
def test_zero_scene_result_names_its_reason(page, origin, tmp_path, path, reason):
    result = _crawl(page, origin, tmp_path, path)
    assert result["state"] == "COMPLETED" and result["zero_scenes_found"] is True, result
    assert result.get("zero_reason") == reason, (
        f"dl95-pegasproductions-1: {path} zero scenes reported as {result.get('zero_reason')!r}, want {reason!r}")
    ev = result["zero_page"]
    assert ev["url"].endswith(path), ev


def test_zero_page_evidence_carries_status_and_challenge_type(page, origin, tmp_path):
    blocked = _crawl(page, origin, tmp_path, "/unavailable")["zero_page"]
    assert blocked["status"] == 503 and blocked["challenge"] == "", blocked
    walled = _crawl(page, origin, tmp_path, "/challenge")["zero_page"]
    assert walled["challenge"] == "cloudflare", walled


def test_a_listing_with_scenes_has_no_zero_reason(page, origin, tmp_path):
    result = _crawl(page, origin, tmp_path, "/listing")
    assert result["discovered"] == 3 and result["zero_scenes_found"] is False, result
    assert result["zero_reason"] == "" and result["zero_page"] == {}, result


def test_an_empty_later_page_does_not_leak_a_zero_reason(page, origin, tmp_path):
    # Page 1 has scenes, its pager leads to a page without any: the crawl found scenes, so no zero diagnosis.
    result = _crawl(page, origin, tmp_path, "/paged", max_pages=2)
    assert result["pages_walked"] == 2 and result["discovered"] == 2, result
    assert result["zero_reason"] == "" and result["zero_page"] == {}, f"dl95-pegasproductions-1: {result['zero_page']}"
