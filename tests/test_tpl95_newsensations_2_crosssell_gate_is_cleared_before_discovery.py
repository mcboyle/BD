"""tpl95-newsensations-2 (harness-work/DOT95-LANE/live-tpl95-newsensations-1/LIVE-RESULT-A8-A.md, MED).

LIVE on .82 (build 9a389265, 05:35Z): scene discovery on https://www.newsensations.com/members/ recorded
effective_listing_url /members/offers.php. It queued bannerload.php?track=2311/2313/2447 ("New Sensations Premium Access")
as scenes (run1-queued.txt). The members URL answers with a cross-sell interstitial first. The capture
(tests/corpus/recognizer/news.cap.json) shows /members/ -> offers.php, its only way on is
<a href="javascript:nothanks()"><button>TAKE ME TO MY MEMBERSHIP</button></a>, and after it /members/ serves the home with
its gallery.php?id=<id>&type=vids scene cards. The built-in new_sensations dismiss_selectors names that button.
Discovery clicked it, but it read the page it was redirected to (the offers page, whose navigation away the script only
schedules) and recorded the interstitial as the listing.

G1 re-requested the listing after a REPORTED click. LIVE FAIL of G1 (live-tpl95-newsensations-2/LIVE-RESULT-A8-A.md): on the
host no gate was reported, run 1 stalled 185 s on offers.php, and run 2 crashed with "Page.evaluate: TypeError: Cannot read
properties of null (reading 'scrollHeight')", a document with no body mid-navigation. On the same host the DOWNLOAD path
cleared this exact page ("site: cleared via a:has-text(\"TAKE ME TO MY MEMBERSHIP\")", "interstitial: cleared via
[class*='close' i]:visible", "re-requested the original url after an interstitial").

Contract after G2: listing pages clear their gates through the runner's gate path (interstitial.dismiss_gates with the
listing as the destination), and discovery reads the listing from where the gates leave it. A body-less document never
crashes the scroll. A listing that answers at its own URL costs no extra request.

Drives the real crawl_with_page on a real chromium page (BD's cloak) against a 127.0.0.1 site in the capture's shape.
enqueue_fn is a recorder.
"""
from __future__ import annotations

import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

BD_GATE_SCOPE = "module"

DISMISS = 'a:has-text("TAKE ME TO MY MEMBERSHIP")'     # the built-in new_sensations dismiss_selectors
SCENE_IDS = (11309, 11308, 11307, 11306)
OFFERS = (
    "<!doctype html><html><head><title>Special Offers</title><script>"
    # the capture blanks the script; the navigation away is scheduled, not immediate
    "function nothanks(){document.cookie='ns_nothanks=1; path=/';"
    "setTimeout(function(){location.href='/members/';}, 1500);}</script></head><body>"
    '<div align="center" class="centerwrap clear"><a href="javascript:nothanks()">'
    '<button class="btn btn-custom">TAKE ME TO MY MEMBERSHIP</button></a>'
    + "".join(f'<div class="offer"><a href="/members/bannerload.php?track={t}"><img src="/b/{t}.jpg" '
              f'alt="New Sensations Premium Access"></a></div>' for t in (2311, 2313, 2447))
    + '<a href="/members/logout.php">Logout</a></div>'
    # the pop-up slider the live journal closed ("interstitial: cleared via [class*='close' i]:visible")
    '<div class="pop-up-slider" style="position:fixed;right:0;bottom:0;width:200px;height:120px;z-index:9">'
    '<a class="close" href="#" onclick="this.parentNode.remove(); return false">Close</a></div></body></html>')
BODYLESS = ('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>members</title></head>'
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/></html>')
HOME = (
    "<!doctype html><html><head><title>New Sensations Members</title></head><body>"
    '<button id="cookie-accept" onclick="this.remove()">Accept cookies</button>'
    '<a href="/members/logout.php">Logout</a>'
    '<a href="/members/bannerload.php?zone=ns_home_02"><img src="/b/home.jpg"></a><main>'
    + "".join(f'<div class="ex-card"><a href="/members/gallery.php?id={i}&type=vids&catid=5">'
              f'<img src="/t/{i}.jpg" alt="Scene {i}"></a><h3>Scene {i}</h3></div>' for i in SCENE_IDS)
    + "</main></body></html>")


def _handler(hits, crosssell):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            hits[path] += 1
            passed = "ns_nothanks=1" in (self.headers.get("Cookie") or "")
            if path == "/members/" and crosssell and not passed:
                self.send_response(302)
                self.send_header("Location", "/members/offers.php")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = {"/members/": HOME, "/members/offers.php": OFFERS, "/bodyless/": BODYLESS}.get(path)
            if body is None:
                self.send_error(404)
                return
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/xhtml+xml" if path == "/bodyless/"
                             else "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_a):
            return

    return H


@pytest.fixture
def site():
    servers = []

    def _start(crosssell=True):
        hits = Counter()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler(hits, crosssell))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_port}", hits

    yield _start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


@pytest.fixture(scope="module")
def browser_ctx():
    from bulk_downloader import cloak

    with cloak.cloaked_page(headless=True, config={"browser_backend": "playwright"},
                            viewport={"width": 1280, "height": 720}) as p:
        yield p


@pytest.fixture
def page(browser_ctx):
    # On an unfixed tree the previous test's cross-sell still holds its scheduled navigation, which can
    # interrupt this one; land on a blank page once it has had its turn.
    for _ in range(4):
        try:
            browser_ctx.goto("about:blank")
            break
        except Exception:  # noqa: BLE001 -- an interrupted navigation is retried
            browser_ctx.wait_for_timeout(500)
    browser_ctx.context.clear_cookies()
    return browser_ctx


def _discover(page, origin, tmp_path, dismiss=DISMISS, listing="/members/"):
    from bulk_downloader import scene_crawler

    sent = []
    result = scene_crawler.crawl_with_page(
        page, site_id="newsensations", listing_url=origin + listing,
        site_config={"dismiss_selectors": dismiss}, newest_n=3, max_pages=1, max_scrolls=1, delay_s=0.5,
        title_fetch_limit=0, db_path=str(tmp_path / "crawl.sqlite"),
        enqueue_fn=lambda _sid, url: sent.append(url) or {"added": 1, "dupes": 0, "skipped": 0})
    return result, sent


def test_the_crosssell_is_cleared_and_the_members_home_is_the_listing(page, site, tmp_path):
    origin, hits = site()
    result, sent = _discover(page, origin, tmp_path)
    banners = [u for u in sent if "bannerload.php" in u]
    assert not banners, f"TPL95_NEWSENSATIONS2_BANNERS_QUEUED: {sent} (effective {result['effective_url']})"
    assert sent and all("/members/gallery.php?id=" in u and "type=vids" in u for u in sent), sent
    assert result["effective_url"] == origin + "/members/", (
        f"TPL95_NEWSENSATIONS2_INTERSTITIAL_IS_THE_LISTING: {result['effective_url']}")
    assert hits["/members/offers.php"] >= 1, hits    # positive control: the cross-sell was served


def test_the_re_requested_listing_has_its_own_page_gates_cleared(page, site, tmp_path):
    """Lens cx-worker-2 (REFUTE G2, F1): the cross-sell control in the login scope, a cookie bar declared per page, and a
    listing with a query. The listing reached after the cross-sell is a new document; its cookie bar is cleared too."""
    from bulk_downloader import scene_crawler

    origin, _hits = site()
    listing, sent = origin + "/members/?sort=new", []
    result = scene_crawler.crawl_with_page(
        page, site_id="newsensations", listing_url=listing,
        site_config={"dismiss_selectors_login": DISMISS, "dismiss_selectors": "button#cookie-accept"},
        newest_n=3, max_pages=1, max_scrolls=1, delay_s=0.5, title_fetch_limit=0,
        db_path=str(tmp_path / "crawl.sqlite"),
        enqueue_fn=lambda _sid, url: sent.append(url) or {"added": 1, "dupes": 0, "skipped": 0})
    assert result["effective_url"] == listing and len(sent) == 3 and all("gallery.php" in u for u in sent), (result, sent)
    assert page.locator("#cookie-accept").count() == 0, "TPL95_NEWSENSATIONS2_RE_REQUESTED_GATE_LEFT: cookie bar still up"


def test_a_listing_that_answers_at_its_own_url_is_requested_once(page, site, tmp_path):
    """A gate clicked ON the listing itself (a cookie bar) is not an interstitial: no re-request."""
    origin, hits = site(crosssell=False)
    result, sent = _discover(page, origin, tmp_path, dismiss=DISMISS + "\nbutton#cookie-accept")
    assert page.locator("#cookie-accept").count() == 0, "positive control: the cookie gate was clicked"
    assert hits["/members/"] == 1 and hits["/members/offers.php"] == 0, hits
    assert result["effective_url"] == origin + "/members/" and len(sent) == 3, (result, sent)


def test_without_the_declared_control_the_crosssell_is_not_passed(page, site, tmp_path):
    """The cross-sell's way on is the site's declared control; without it the listing stays the interstitial."""
    origin, hits = site()
    result, sent = _discover(page, origin, tmp_path, dismiss="")
    assert hits["/members/offers.php"] >= 1 and not [u for u in sent if "gallery.php" in u], (hits, sent)
    assert result["effective_url"] == origin + "/members/offers.php", result["effective_url"]


def test_a_listing_document_without_a_body_does_not_crash_the_run(page, site, tmp_path):
    origin, _hits = site(crosssell=False)
    try:
        result, sent = _discover(page, origin, tmp_path, dismiss="", listing="/bodyless/")
    except Exception as exc:  # noqa: BLE001 -- the crash IS the finding
        pytest.fail(f"TPL95_NEWSENSATIONS2_BODYLESS_CRASH: {type(exc).__name__}: {exc}"[:400])
    assert sent == [] and result["pages_walked"] == 1, result
