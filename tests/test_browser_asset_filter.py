BD_GATE_SCOPE = "module"

from bulk_downloader.runner_browser import BrowserMixin


class Page:
    def __init__(self, url="https://site.test/watch/1"):
        self.routes = []
        self.url = url

    def route(self, pattern, handler):
        self.routes.append((pattern, handler))


class Route:
    def __init__(self):
        self.action = None

    def abort(self):
        self.action = "abort"

    def continue_(self):
        self.action = "continue"


class Request:
    def __init__(self, resource_type, url):
        self.resource_type = resource_type
        self.url = url


class Runner(BrowserMixin):
    config = {"browser_asset_filter": True}
    _launched_headless = True  # a worker browser


def apply(page, resource_type, url):
    Runner()._install_browser_asset_filter(page)
    route = Route()
    page.routes[0][1](route, Request(resource_type, url))
    return route.action


def test_static_assets_and_telemetry_are_aborted():
    assert apply(Page(), "image", "https://site.test/banner.png") == "abort"
    assert apply(Page(), "font", "https://site.test/font.woff2") == "abort"
    assert apply(Page(), "stylesheet", "https://site.test/theme.css") == "continue"   # round 4: never aborted
    assert apply(Page(), "stylesheet", "https://cdn.other.test/theme.css") == "continue"
    assert apply(Page(), "script", "https://tracker.example/collect?v=1") == "abort"


def test_document_api_and_media_manifests_continue():
    assert apply(Page(), "document", "https://site.test/watch/1") == "continue"
    assert apply(Page(), "xhr", "https://site.test/api/metadata") == "continue"
    assert apply(Page(), "media", "https://cdn.test/video.m3u8") == "continue"
    assert apply(Page(), "fetch", "https://cdn.test/video.mpd") == "continue"


# ── FIXER (row923 REFUTE E1/E2) ──────────────────────────────────────────

import pytest


@pytest.mark.parametrize("resource_type,url", [
    ("document", "https://site.test/collections/12"),          # E1 probe: "/collect" substring
    ("document", "https://site.test/analytics/dashboard"),      # a page ABOUT analytics is a page
    ("xhr", "https://site.test/api/collect/items"),             # API call, never telemetry-judged
    ("fetch", "https://site.test/beacon.json"),                 # API call
    ("media", "https://cdn.test/analytics/video.m3u8"),         # manifest
    ("websocket", "wss://site.test/beacon"),
    ("document", "https://mixpanel.com/help"),                  # a document on a tracker host is still a document
])
def test_documents_api_and_media_are_never_telemetry_judged(resource_type, url):
    """E1: the telemetry rule applies to script/ping/beacon-class requests
    only, and matches a HOST or a PATH SEGMENT -- never a substring of the
    whole URL. Acceptance 3: zero interference with document/API/manifests."""
    assert apply(Page(), resource_type, url) == "continue"


@pytest.mark.parametrize("resource_type,url", [
    ("script", "https://www.google-analytics.com/analytics.js"),
    ("script", "https://cdn.segment.io/v1/analytics.min.js"),
    ("ping", "https://site.test/beacon"),
    ("ping", "https://site.test/collect?v=1"),
    ("other", "https://site.test/telemetry/event"),
    ("script", "https://cdn.tracker.test/analytics/"),      # third-party script
    ("script", "https://stats.other.test/collect/t.js"),
])
def test_tracker_hosts_and_telemetry_endpoints_are_aborted(resource_type, url):
    assert apply(Page(), resource_type, url) == "abort"


@pytest.mark.parametrize("url", [
    "https://site.test/analytics/app.js",          # first-party player/UI bundle
    "https://site.test/collect/player.js",
    "https://static.site.test/analytics/hydrate.js",  # subdomain of the page's site
])
def test_first_party_scripts_under_telemetry_like_paths_are_not_aborted(url):
    """Round 2 E3: the path rule judges a SCRIPT only when it is third-party
    to the page; a site's own /analytics/app.js is application code."""
    assert apply(Page(), "script", url) == "continue"
    # the same path as a ping/beacon is still telemetry
    assert apply(Page(), "ping", url) == "abort"
    # unknown page host: scripts are not judged by path (do not interfere)
    assert apply(Page(url=""), "script", url) == "continue"


def test_first_party_script_named_like_a_collection_is_not_aborted():
    assert apply(Page(), "script", "https://site.test/collections.js") == "continue"
    assert apply(Page(), "script", "https://notmixpanel.com/app.js") == "continue"


def test_filter_is_not_installed_on_a_headed_manual_page():
    """E2: runner_manual (headless=False, the operator's window) installs
    nothing; a headless worker launch does; a runner whose browser was
    launched elsewhere follows its own config["headless"] (default True)."""
    class Headed(BrowserMixin):
        config = {"browser_asset_filter": True}
        _launched_headless = False

    class UnknownHeaded(BrowserMixin):          # launched elsewhere, config says headed
        config = {"browser_asset_filter": True, "headless": False}

    class UnknownHeadless(BrowserMixin):        # launched elsewhere, config default (headless)
        config = {"browser_asset_filter": True}

    for runner in (Headed(), UnknownHeaded()):
        page = Page()
        runner._apply_stealth_library_to_page(page)
        assert page.routes == []
    for runner in (Runner(), UnknownHeadless()):   # round 2 E2: config fallback
        page = Page()
        runner._apply_stealth_library_to_page(page)
        assert page.routes and page.routes[0][0] == "**/*"
    page = Page()
    Headed()._install_browser_asset_filter(page, headless=True)  # explicit override for a worker page
    assert page.routes


def test_filter_is_installed_before_page_navigation_hook():
    page = Page()
    Runner()._apply_stealth_library_to_page(page)
    assert page.routes and page.routes[0][0] == "**/*"


def test_real_chromium_css_hidden_duplicate_control_stays_hidden_with_the_filter():
    """Round 4: a page whose external stylesheet hides the mobile duplicate of
    an "Accept All" control. With the filter installed the stylesheet still
    loads (visibility is what every gate keys off), while an image on the
    same page is aborted (positive control that the filter is installed)."""
    import http.server, socketserver, threading
    from playwright.sync_api import sync_playwright
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path == "/theme.css":
                body, ctype = b".mobile-nav{display:none}", "text/css"
            elif self.path == "/":
                body, ctype = (b'<html><head><link rel="stylesheet" href="/theme.css"></head><body>'
                               b'<div class="mobile-nav"><button id="m">Accept All</button></div>'
                               b'<div class="desktop-nav"><button id="d">Accept All</button></div>'
                               b'<img src="/banner.png"></body></html>'), "text/html"
            else:
                body, ctype = b"\x89PNG", "image/png"
            self.send_response(200); self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    class Worker(BrowserMixin):
        config = {"browser_asset_filter": True}
        _launched_headless = True

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        Worker()._install_browser_asset_filter(page)
        page.goto(url, wait_until="load")
        assert page.locator("#m").is_visible() is False       # CSS applied: hidden duplicate stays hidden
        assert page.locator("#d").is_visible() is True
        assert "/theme.css" in hits and "/banner.png" not in hits  # stylesheet fetched, image aborted
        browser.close()
    finally:
        pw.stop()
        srv.shutdown(); srv.server_close()
