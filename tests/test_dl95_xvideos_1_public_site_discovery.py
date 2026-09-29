"""dl95-xvideos-1 (harness-work/UIUX-20260928/download-95/A1-A/FINDINGS-A1-A.md#A10, HIGH).

Scene discovery on public tube sites (xvideos, porndoe, spankbang, hqporner) ended NOT_LOGGED_IN after one page, with
0 scenes, although a plain GET of the listing returns scene links. The crawler only accepts members-area evidence:
a logout link, a members path or success_url. A public listing has none of these, and it often ships a hidden login
modal with a password field. The site configs had no login_url and no credentials, so login could never produce that
evidence.

Served on an ephemeral loopback port and driven through the real crawl_with_page with BD's cloak page; no live site
is contacted. The listing models a tube page: thumbnail cards on /video.<id>/<slug>, a hidden login modal, and no
logout link. Controls: a site that declares a login (login_url, credentials, accounts or auth_required=True) stays
fail-closed; a public site on a scene-less tour page or on a login URL stays NOT_LOGGED_IN.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

BD_GATE_SCOPE = "module"

N_SCENES = 6
_CARDS = "".join(
    f'<div class="thumb"><a href="/video.{9000 + i}/public_scene_{i}">'
    f'<img src="/thumbs/{i}.jpg" alt="Public scene {i}"></a>'
    f'<p><a href="/video.{9000 + i}/public_scene_{i}">Public scene {i}</a></p></div>'
    for i in range(N_SCENES)
)
_MODAL = ('<div id="login-modal" style="display:none"><form action="/account/signin">'
          '<input name="login"><input type="password" name="password"></form></div>')
_PAGES = {
    "/": f"<!doctype html><html><head><title>Tube</title></head><body>{_MODAL}"
         f'<nav><a href="/tags/x">Tags</a><a href="/tags/y">More</a></nav><main>{_CARDS}</main></body></html>',
    "/tour": "<!doctype html><html><body><h1>Join now</h1><a href='/plans'>Plans</a></body></html>",
    "/login/listing": f"<!doctype html><html><body>{_MODAL}<main>{_CARDS}</main></body></html>",
}


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


def _crawl(page, origin, tmp_path, path, config, site_id="dl95-pub"):
    from bulk_downloader import scene_crawler

    queued = []
    result = scene_crawler.crawl_with_page(
        page, site_id=site_id, listing_url=origin + path, site_config=config,
        newest_n=0, max_pages=1, max_scrolls=1, delay_s=0, title_fetch_limit=0,
        db_path=str(tmp_path / f"{site_id}.sqlite"),
        enqueue_fn=lambda sid, url: queued.append(url) or {"added": 1, "dupes": 0, "skipped": 0},
    )
    return result, queued


def test_public_site_listing_is_discovered(page, origin, tmp_path):
    result, queued = _crawl(page, origin, tmp_path, "/", {"name": "tube"})
    assert result["state"] != "NOT_LOGGED_IN", (
        f"dl95-xvideos-1: public site (no login_url, no credentials) refused as NOT_LOGGED_IN: {result}")
    assert result["state"] == "COMPLETED", result
    assert result["discovered"] == N_SCENES, result
    assert sorted(queued) == sorted(f"{origin}/video.{9000 + i}/public_scene_{i}" for i in range(N_SCENES))


@pytest.mark.parametrize("config", [
    {"login_url": "https://tube.example/login"},
    {"username": "someone"},
    {"cookie_file": "/tmp/cookies.txt"},
    {"accounts": [{"username": "a", "password": "b"}]},
    {"auth_required": True},
], ids=["login_url", "username", "cookie_file", "account", "auth_required"])
def test_a_site_that_declares_a_login_stays_fail_closed(page, origin, tmp_path, config):
    result, queued = _crawl(page, origin, tmp_path, "/", config, site_id="dl95-members")
    assert result["state"] == "NOT_LOGGED_IN", result
    assert queued == []


def test_explicit_auth_required_false_is_public(page, origin, tmp_path):
    result, _ = _crawl(page, origin, tmp_path, "/", {"auth_required": False, "login_url": "https://t.example/login"})
    assert result["state"] == "COMPLETED" and result["discovered"] == N_SCENES, result


def test_public_site_on_a_scene_less_tour_stays_not_logged_in(page, origin, tmp_path):
    result, queued = _crawl(page, origin, tmp_path, "/tour", {})
    assert result["state"] == "NOT_LOGGED_IN", result
    assert queued == []


def test_public_site_on_a_login_url_stays_not_logged_in(page, origin, tmp_path):
    result, queued = _crawl(page, origin, tmp_path, "/login/listing", {})
    assert result["state"] == "NOT_LOGGED_IN", result
    assert queued == []
