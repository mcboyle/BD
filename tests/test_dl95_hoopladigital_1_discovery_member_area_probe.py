"""dl95-hoopladigital-1 (harness-work/UIUX-20260928/download-95/A5-A/RESULT-login.md, _discovery/sd1/SD1__HD.png, MED).

hoopladigital (954f2e41): Verify login passes -- the cookies reach the member area (success_url /my/) -- yet
scene discovery on /browse/movie/popular ends NOT_LOGGED_IN after one page with a /movie/<slug>/<n> scene cohort
(test2 scene_crawl_runs 213b23e9, cc802124, a040d232). The crawler accepts only listing-page evidence: a logout
anchor, a members path, or a listing that is itself under success_url. hoopla's logged-in header carries "My Hoopla"
and a settings button, no logout anchor, and its catalog lives outside /members/, so a live session reads as logged
out and _mark_login_wall then marks the site's jar walled.

Fix under test: when a listing shows no members evidence and no login wall, discovery asks the site's own member
area (success_url, same host) with the same session -- the check Verify makes. A session that stays on success_url
without a login wall is logged in.

Served on an ephemeral loopback port and driven through the real crawl_with_page with BD's cloak page; no live site
is contacted. Controls: no session (success_url redirects to /login), a stale session (success_url serves a login
form in place), and a site with no success_url all stay NOT_LOGGED_IN with nothing queued.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

BD_GATE_SCOPE = "module"

N_SCENES = 6
_CARDS = "".join(
    f'<div class="card"><a href="/movie/title-{i}/{20022400 + i}">'
    f'<img src="/covers/{i}.jpg" alt="Title {i}"></a>'
    f'<p><a href="/movie/title-{i}/{20022400 + i}">Title {i}</a></p></div>'
    for i in range(N_SCENES)
)
_HEADER_IN = ('<header><a href="/browse">Browse</a><a href="/my/hoopla">My Hoopla</a>'
              '<button aria-label="Settings">Settings</button></header>')
_HEADER_OUT = '<header><a href="/browse">Browse</a><a href="/login">Log In</a></header>'
_LOGIN = ('<!doctype html><html><body><form action="/login" method="post"><input name="email">'
          '<input type="password" name="password"><button type="submit">Log In</button></form></body></html>')


def _session(handler) -> str:
    for part in (handler.headers.get("Cookie") or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == "sid":
            return value
    return ""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        sid = _session(self)
        if path == "/browse/movie/popular":
            header = _HEADER_IN if sid == "live" else _HEADER_OUT
            body = f"<!doctype html><html><body>{header}<main>{_CARDS}</main></body></html>"
        elif path.startswith("/my/"):
            if not sid:
                self.send_response(302)
                self.send_header("Location", "/login")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            # A stale session: the member area renders its login form in place.
            body = (f"<!doctype html><html><body>{_HEADER_IN}<h1>My Hoopla</h1><p>Borrowed</p></body></html>"
                    if sid == "live" else _LOGIN)
        elif path == "/login":
            body = _LOGIN
        else:
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


def _crawl(page, origin, tmp_path, *, sid, success_url="/my/"):
    from bulk_downloader import scene_crawler

    page.context.clear_cookies()
    if sid:
        page.context.add_cookies([{"name": "sid", "value": sid, "url": origin}])
    config = {"name": "hoopladigital", "login_url": origin + "/login"}
    if success_url:
        config["success_url"] = origin + success_url
    queued = []
    result = scene_crawler.crawl_with_page(
        page, site_id="954f2e41", listing_url=origin + "/browse/movie/popular", site_config=config,
        newest_n=0, max_pages=1, max_scrolls=1, delay_s=0, title_fetch_limit=0,
        db_path=str(tmp_path / "hoopla.sqlite"),
        enqueue_fn=lambda sid_, url: queued.append(url) or {"added": 1, "dupes": 0, "skipped": 0},
    )
    return result, queued


def test_live_session_without_logout_link_is_discovered(page, origin, tmp_path):
    result, queued = _crawl(page, origin, tmp_path, sid="live")
    assert result["state"] != "NOT_LOGGED_IN", (
        "dl95-hoopladigital-1: live session (success_url /my/ reachable, no login wall) refused as "
        f"NOT_LOGGED_IN because the listing has no logout anchor: {result}")
    assert result["state"] == "COMPLETED", result
    assert result["discovered"] == N_SCENES, result
    assert sorted(queued) == sorted(f"{origin}/movie/title-{i}/{20022400 + i}" for i in range(N_SCENES))


def test_no_session_stays_not_logged_in(page, origin, tmp_path):
    result, queued = _crawl(page, origin, tmp_path, sid="")
    assert result["state"] == "NOT_LOGGED_IN", result
    assert queued == []


def test_stale_session_with_login_form_at_member_area_stays_not_logged_in(page, origin, tmp_path):
    result, queued = _crawl(page, origin, tmp_path, sid="stale")
    assert result["state"] == "NOT_LOGGED_IN", result
    assert queued == []


def test_site_without_success_url_stays_fail_closed(page, origin, tmp_path):
    result, queued = _crawl(page, origin, tmp_path, sid="live", success_url="")
    assert result["state"] == "NOT_LOGGED_IN", result
    assert queued == []
