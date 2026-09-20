"""RED-first tests for Row 899: adaptive-streaming manifest parser (HLS/DASH).

Player pages fetch .m3u8 (HLS) and .mpd (DASH) manifests via async
Fetch/XHR that a static scraper never sees. AdaptiveManifestWatcher groups
CDP Network.requestWillBeSent events by requestId so a manifest reached
through one or more redirects is reported with its full hop-by-hop chain,
and BrowserMixin._on_adaptive_manifest_detected hands the detection off to
self.manifest_urls for the transport pipeline to consume. Pure/synthetic
CDP events -- no real browser required.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"


def _req_event(request_id, url):
    return "Network.requestWillBeSent", {
        "requestId": request_id,
        "request": {"method": "GET", "url": url},
    }


class _FakePage:
    pass


class _FakeCDPClient:
    def __init__(self):
        self.sent = []
        self.handlers = {}

    def send(self, method, *a, **k):
        self.sent.append(method)

    def on(self, event, handler):
        self.handlers[event] = handler


class _FakeContext:
    """Minimal stand-in for a Playwright BrowserContext: only the surface
    _install_adaptive_manifest_capture touches (.pages, .new_cdp_session,
    .on('page', ...))."""

    def __init__(self, pages=None):
        self.pages = list(pages or [])
        self.clients = []
        self.page_handlers = []

    def new_cdp_session(self, page):
        client = _FakeCDPClient()
        self.clients.append(client)
        return client

    def on(self, event, handler):
        self.page_handlers.append((event, handler))


def test_interception_of_adaptive_playlist_urls_during_playback_setup():
    from bulk_downloader.runner_browser import AdaptiveManifestWatcher
    watcher = AdaptiveManifestWatcher()

    method, params = _req_event("1", "https://cdn.example.com/index.html")
    assert watcher.feed(method, params) is None

    method, params = _req_event("2", "https://cdn.example.com/master.m3u8")
    hls = watcher.feed(method, params)
    assert hls is not None
    assert hls["kind"] == "hls"
    assert hls["url"] == "https://cdn.example.com/master.m3u8"

    method, params = _req_event("3", "https://cdn.example.com/stream.mpd?t=1")
    dash = watcher.feed(method, params)
    assert dash is not None
    assert dash["kind"] == "dash"

    assert len(watcher.manifests) == 2


def test_proper_tracking_of_http_redirect_chains():
    from bulk_downloader.runner_browser import AdaptiveManifestWatcher
    watcher = AdaptiveManifestWatcher()

    # Same requestId across every hop, per CDP semantics -- a fresh
    # requestWillBeSent for each leg, joined by requestId.
    rid = "42"
    assert watcher.feed(*_req_event(rid, "https://a.example.com/go")) is None
    assert watcher.feed(*_req_event(rid, "https://b.example.com/go2")) is None
    result = watcher.feed(*_req_event(rid, "https://cdn.example.com/playlist.m3u8"))

    assert result is not None
    assert result["kind"] == "hls"
    assert result["redirect_chain"] == [
        "https://a.example.com/go", "https://b.example.com/go2",
    ]
    assert result["request_id"] == rid


def test_a_non_manifest_redirect_chain_is_never_reported():
    from bulk_downloader.runner_browser import AdaptiveManifestWatcher
    watcher = AdaptiveManifestWatcher()
    rid = "7"
    assert watcher.feed(*_req_event(rid, "https://a.example.com/x")) is None
    assert watcher.feed(*_req_event(rid, "https://b.example.com/y.html")) is None
    assert watcher.manifests == []


def test_handover_to_transport_pipeline():
    from bulk_downloader.runner_browser import BrowserMixin

    class _FakeRunner(BrowserMixin):
        pass

    runner = _FakeRunner()
    assert not hasattr(runner, "manifest_urls")

    entry = {"url": "https://cdn.example.com/master.m3u8", "kind": "hls",
              "redirect_chain": [], "request_id": "1"}
    runner._on_adaptive_manifest_detected(None)  # non-manifest event: no-op
    assert not hasattr(runner, "manifest_urls")

    runner._on_adaptive_manifest_detected(entry)
    assert runner.manifest_urls == [entry]

    entry2 = {"url": "https://cdn.example.com/stream.mpd", "kind": "dash",
               "redirect_chain": ["https://cdn.example.com/redir"],
               "request_id": "2"}
    runner._on_adaptive_manifest_detected(entry2)
    assert runner.manifest_urls == [entry, entry2]


def test_install_wires_cdp_listener_on_existing_pages_and_detects_a_manifest():
    from bulk_downloader.runner_browser import BrowserMixin

    class _FakeRunner(BrowserMixin):
        pass

    runner = _FakeRunner()
    page = _FakePage()
    ctx = _FakeContext(pages=[page])

    watcher = runner._install_adaptive_manifest_capture(ctx)

    assert len(ctx.clients) == 1, "no CDP session opened for the existing page"
    client = ctx.clients[0]
    assert "Network.enable" in client.sent
    handler = client.handlers["Network.requestWillBeSent"]

    handler({"requestId": "1", "request": {
        "method": "GET", "url": "https://cdn.example.com/master.m3u8"}})
    assert len(runner.manifest_urls) == 1
    assert runner.manifest_urls[0]["kind"] == "hls"
    assert watcher.manifests == runner.manifest_urls

    # A non-manifest request on the same wired listener is a no-op.
    handler({"requestId": "2", "request": {
        "method": "GET", "url": "https://cdn.example.com/index.html"}})
    assert len(runner.manifest_urls) == 1


def test_install_wires_pages_opened_after_the_call_too():
    from bulk_downloader.runner_browser import BrowserMixin

    class _FakeRunner(BrowserMixin):
        pass

    runner = _FakeRunner()
    ctx = _FakeContext(pages=[])

    runner._install_adaptive_manifest_capture(ctx)

    assert ctx.page_handlers, "no ctx.on('page', ...) listener registered"
    handlers = dict(ctx.page_handlers)
    assert set(handlers) == {"response", "page"}          # context-level response path + per-page CDP wiring
    page_handler = handlers["page"]
    assert not ctx.clients, "no page open yet -- nothing should be wired"

    page_handler(_FakePage())
    assert len(ctx.clients) == 1, "a page opened later must still get a CDP session"


# ---- fixer (O928): correctness REFUTE E1-E3 ----------------------------------

import ast
import http.server
import inspect
import threading

import pytest


def test_e2_the_manifest_extension_must_be_in_the_path_not_a_query_value():
    from bulk_downloader.runner_browser import AdaptiveManifestWatcher, _adaptive_manifest_kind
    assert _adaptive_manifest_kind("https://cdn.example/player?next=master.m3u8") is None
    assert _adaptive_manifest_kind("https://cdn.example/player#stream.mpd") is None
    assert _adaptive_manifest_kind("https://cdn.example/a/master.m3u8?token=1") == "hls"
    assert _adaptive_manifest_kind("https://cdn.example/a/stream.MPD") == "dash"
    assert _adaptive_manifest_kind("https://cdn.example/a/master.m3u8x") is None
    watcher = AdaptiveManifestWatcher()
    assert watcher.feed(*_req_event("1", "https://cdn.example/player?next=master.m3u8")) is None
    assert watcher.manifests == []


def test_e3_a_manifest_that_redirects_is_reported_with_its_resolved_url_and_complete_chain():
    """E3: master.m3u8 -> 302 -> /signed-playlist?token=test: ONE detection
    whose url is the resolved URL and whose chain is every earlier hop; a
    response whose mimeType is a manifest type is detected even without the
    extension anywhere."""
    from bulk_downloader.runner_browser import AdaptiveManifestWatcher, BrowserMixin
    watcher = AdaptiveManifestWatcher()
    first = watcher.feed(*_req_event("9", "https://cdn.example/master.m3u8"))
    assert first["url"] == "https://cdn.example/master.m3u8" and first["redirect_chain"] == []
    method, params = _req_event("9", "https://cdn.example/signed-playlist?token=test")
    params["redirectResponse"] = {"status": 302, "url": "https://cdn.example/master.m3u8"}
    final = watcher.feed(method, params)
    assert final is not None and final is first                     # one entry per request, refreshed
    assert final["url"] == "https://cdn.example/signed-playlist?token=test"
    assert final["redirect_chain"] == ["https://cdn.example/master.m3u8"] and final["kind"] == "hls"
    assert watcher.manifests == [final]
    # content-type detection for an extension-less URL
    got = watcher.feed("Network.responseReceived", {"requestId": "10", "response": {
        "url": "https://cdn.example/api/playlist", "mimeType": "application/vnd.apple.mpegurl"}})
    assert got["kind"] == "hls" and got["url"] == "https://cdn.example/api/playlist"
    assert watcher.feed("Network.responseReceived", {"requestId": "11", "response": {
        "url": "https://cdn.example/x.js", "mimeType": "application/javascript"}}) is None
    # the runner's queue holds the entry once and drain() snapshots it
    runner = type("R", (BrowserMixin,), {})()
    runner._on_adaptive_manifest_detected(first)
    runner._on_adaptive_manifest_detected(final)
    assert runner.manifest_urls == [final]
    drained = runner.drain_manifest_urls()
    assert drained == [final] and drained[0] is not final and runner.manifest_urls == []


class _RedirectingCdn(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/master.m3u8":
            self.send_response(302); self.send_header("Location", "/signed-playlist?token=test")
            self.send_header("Content-Length", "0"); self.end_headers(); return
        if self.path.startswith("/signed-playlist"):
            body = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-ENDLIST\n"
            self.send_response(200); self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        elif self.path == "/player":
            body = (b"<html><body><script>fetch('/master.m3u8').then(r => r.text())"
                    b".then(() => { window.__done = true; });</script></body></html>")
            self.send_response(200); self.send_header("Content-Type", "text/html")
        else:
            body = b""; self.send_response(404); self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_e1_e3_real_chromium_playback_page_installs_the_capture_and_the_pipeline_consumes_it():
    """E1/E3 on real Chromium: the capture installed on a real context sees
    the player's async fetch of master.m3u8, follows its 302 to the signed
    playlist, and the transport read side (drain_manifest_urls) receives the
    resolved URL with the complete redirect chain."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from bulk_downloader.runner_browser import BrowserMixin
    srv = http.server.HTTPServer(("127.0.0.1", 0), _RedirectingCdn)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    runner = type("R", (BrowserMixin,), {"config": {}, "site_id": "t"})()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context()
            assert runner._maybe_install_adaptive_manifest_capture(ctx) is not None
            page = ctx.new_page()
            page.goto(f"{base}/player")
            page.wait_for_function("window.__done === true", timeout=10000)
            # CDP events are dispatched asynchronously: wait for the redirect leg to land
            deadline = __import__("time").monotonic() + 5
            while (not any(e.get("redirect_chain") for e in getattr(runner, "manifest_urls", []))
                   and __import__("time").monotonic() < deadline):
                page.wait_for_timeout(50)
            browser.close()
    finally:
        srv.shutdown(); srv.server_close()
    got = runner.drain_manifest_urls()
    assert len(got) == 1, got
    assert got[0]["kind"] == "hls"
    assert got[0]["url"] == f"{base}/signed-playlist?token=test"
    assert got[0]["redirect_chain"] == [f"{base}/master.m3u8"]
    assert runner.drain_manifest_urls() == []
    # config switch: the capture is not installed when the site turns it off
    off = type("R", (BrowserMixin,), {"config": {"adaptive_manifest_capture": False}})()
    assert off._maybe_install_adaptive_manifest_capture(_FakeContext(pages=[_FakePage()])) is None


def test_e1_the_persistent_launch_path_installs_the_capture():
    """E1 (production caller): both persistent-context return paths of
    _launch_browser install the capture right after stealth."""
    from bulk_downloader import runner_browser
    src = inspect.getsource(runner_browser.BrowserMixin._launch_browser)
    tree = ast.parse(__import__("textwrap").dedent(src))
    persistent_returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return)
                          and isinstance(n.value, ast.Tuple) and isinstance(n.value.elts[0], ast.Constant)
                          and n.value.elts[0].value is None]
    assert len(persistent_returns) == 2
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_maybe_install_adaptive_manifest_capture"]
    assert len(calls) == 2
    assert all(c.lineno < r.lineno for c, r in zip(sorted(calls, key=lambda n: n.lineno), sorted(persistent_returns, key=lambda n: n.lineno)))


# ---- fx1: a listener the context refuses is reported, never swallowed ------

def test_a_refused_context_listener_is_diagnosed_on_stderr(capsys):
    from bulk_downloader.runner_browser import BrowserMixin

    class _FakeRunner(BrowserMixin):
        pass

    class _RefusingContext(_FakeContext):
        def on(self, event, handler):
            raise RuntimeError(f"context closed before {event} listener")

    runner = _FakeRunner()
    ctx = _RefusingContext(pages=[_FakePage()])

    watcher = runner._install_adaptive_manifest_capture(ctx)

    err = capsys.readouterr().err
    assert watcher is not None                         # fail-soft: the watcher still comes back
    assert len(ctx.clients) == 1                        # the per-page CDP path was still wired
    assert err.count("[runner_browser] manifest ") == 2, err
    assert "manifest response listener not attached: RuntimeError" not in err
    assert "manifest response listener not attached: context closed before response listener" in err
    assert "manifest page listener not attached: context closed before page listener" in err

    # negative control: a context that accepts both listeners writes nothing
    quiet = _FakeContext(pages=[_FakePage()])
    _FakeRunner()._install_adaptive_manifest_capture(quiet)
    assert capsys.readouterr().err == ""
