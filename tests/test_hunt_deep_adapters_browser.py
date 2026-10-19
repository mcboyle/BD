"""deep-adapters-browser (O1789 sweep) items 1-4 on runner_browser.

1 _collect_virtualized_media_urls: max_seconds bounds every blocking call it
  starts (no evaluate/wait begins after the deadline, a wait never outlasts it).
2 WebSocketFrameDispatcher._on_frame: a deeply nested JSON frame never raises
  into the CDP listener (RecursionError used to escape).
3 WebSocket capture: a page's dispatcher is closed and dropped when the page
  closes (context close closes every page).
4 manifest capture: _on_adaptive_manifest_detected does not rescan the queue
  per detection; AdaptiveManifestWatcher._chains does not keep every requestId.
pc_* tests are positive controls on the same code.
"""
import sys
import types

from bulk_downloader import runner_browser
from bulk_downloader.runner_browser import AdaptiveManifestWatcher, BrowserMixin, WebSocketFrameDispatcher

BD_GATE_SCOPE = "module"


# ---- 1: virtual-scroll time budget -------------------------------------------------------------------------------
class _Clock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t


class _SlowCatalogPage:
    """Fake page on a fake clock: each evaluate costs `eval_s`, each wait its ms."""

    def __init__(self, clock, deadline, eval_s, at_end):
        self.clock, self.deadline, self.eval_s, self.at_end = clock, deadline, eval_s, at_end
        self.late_calls, self.waits, self.n = [], [], 0

    def _start(self, what):
        if self.clock.t > self.deadline + 1e-9:
            self.late_calls.append(what)

    def evaluate(self, script, arg=None):
        self._start("evaluate")
        self.clock.t += self.eval_s
        if script is runner_browser._VIRTUAL_SCROLL_BY_JS:
            return None
        self.n += 1
        return {"viewport": 720, "scroll_y": self.n * 540, "at_end": self.at_end,
                "urls": [f"https://media.example/{self.n}.mp4"]}

    def wait_for_timeout(self, ms):
        self._start("wait")
        self.waits.append(ms)
        self.clock.t += ms / 1000.0


def _run_budget(monkeypatch, eval_s, at_end, **kw):
    clock = _Clock()
    monkeypatch.setattr(runner_browser, "time", types.SimpleNamespace(monotonic=clock.monotonic))
    max_seconds = 2.0
    page = _SlowCatalogPage(clock, clock.t + max_seconds, eval_s, at_end)
    urls = BrowserMixin()._collect_virtualized_media_urls(page, ".catalog-item", max_seconds=max_seconds, **kw)
    return urls, clock.t - 1000.0, page


def test_virtual_scroll_max_seconds_bounds_waits_and_calls(monkeypatch):
    for eval_s, at_end, kw in ((0.1, True, {"end_settle_ms": 5000}), (1.5, False, {})):
        urls, elapsed, page = _run_budget(monkeypatch, eval_s, at_end, **kw)
        assert not page.late_calls and elapsed <= 2.0 + eval_s + 1e-9, (  # float clock
            f"SWEEP-VS-1 returned after {elapsed:.2f}s with max_seconds=2.0 (evaluate={eval_s}s): "
            f"calls started after the deadline={page.late_calls} waits={page.waits}")
        assert urls, "collected URLs must be returned"


def test_pc_virtual_scroll_inside_budget_keeps_settle_times(monkeypatch):
    urls, elapsed, page = _run_budget(monkeypatch, 0.01, False, max_rounds=3, settle_ms=250)
    assert urls == [f"https://media.example/{i}.mp4" for i in (1, 2, 3)]
    assert page.waits == [250, 250, 250] and not page.late_calls


# ---- 2: deeply nested WebSocket frame ----------------------------------------------------------------------------
def _frame(payload):
    return {"requestId": "1", "timestamp": 0.0, "response": {"opcode": 1, "mask": False, "payloadData": payload}}


def test_ws_deeply_nested_frame_does_not_raise():
    d = WebSocketFrameDispatcher()
    raised = []
    n_walk = 5 * sys.getrecursionlimit()
    for payload in ("[" * n_walk + "]" * n_walk,
                    '{"a":' * n_walk + '"https://cdn.example/deep.mp4"' + "}" * n_walk,
                    "[" * 100000 + "]" * 100000):
        try:
            d._on_frame(_frame(payload))
        except RecursionError as e:
            raised.append(f"len={len(payload)}: {e}")
    assert not raised, f"SWEEP-WS-2 _on_frame raised RecursionError into the CDP listener: {raised}"
    d._on_frame(_frame('{"data": {"url": "https://cdn.example/after.m3u8"}}'))
    assert "https://cdn.example/after.m3u8" in d.media_urls()


def test_pc_ws_nested_frame_keeps_urls_in_document_order():
    from bulk_downloader.runner_browser import extract_media_urls
    msg = {"type": "media.ready", "data": {"url": "https://cdn.example/v/1.m3u8", "poster": "https://cdn.example/p.jpg",
                                          "alts": [{"src": "https://cdn.example/v/1.mp4"}, "ws://x"]}}
    assert extract_media_urls(msg) == ["https://cdn.example/v/1.m3u8", "https://cdn.example/v/1.mp4",
                                       "https://cdn.example/p.jpg"]
    d = WebSocketFrameDispatcher()
    d._on_frame(_frame("[" * 50 + '{"url": "https://cdn.example/x.mp4"}' + "]" * 50))
    assert d.media_urls() == ["https://cdn.example/x.mp4"]


# ---- 3: WebSocket capture lifecycle ------------------------------------------------------------------------------
class _Emitter:
    def __init__(self):
        self.handlers = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event, arg):
        for h in list(self.handlers.get(event, [])):
            h(arg)


class _Client(_Emitter):
    def __init__(self):
        super().__init__()
        self.detached = 0

    def send(self, method, *a, **k):
        pass

    def detach(self):
        self.detached += 1


class _Ctx(_Emitter):
    def __init__(self):
        super().__init__()
        self.pages = []

    def new_cdp_session(self, page):
        return page.client


class _Page(_Emitter):
    def __init__(self, ctx):
        super().__init__()
        self.context, self.client = ctx, _Client()


def test_ws_capture_released_when_page_closes():
    runner, ctx = BrowserMixin(), _Ctx()
    runner._maybe_install_websocket_json_capture(ctx)
    pages = [_Page(ctx) for _ in range(3)]
    for p in pages:
        ctx.emit("page", p)
        for i in range(20):
            p.client.emit(runner_browser._CDP_WS_FRAME_RECEIVED, _frame('{"seq": %d}' % i))
    for p in pages:
        p.emit("close", p)
    held = runner._websocket_dispatchers
    assert not held and all(p.client.detached == 1 for p in pages), (
        f"SWEEP-WS-3 3 closed pages still hold {len(held)} dispatcher(s) "
        f"queued={[d.queue.qsize() for d in held]} detached={[p.client.detached for p in pages]}")


def test_pc_ws_capture_open_page_still_routes():
    runner, ctx = BrowserMixin(), _Ctx()
    runner._maybe_install_websocket_json_capture(ctx)
    a, b = _Page(ctx), _Page(ctx)
    ctx.emit("page", a)
    ctx.emit("page", b)
    a.emit("close", a)
    b.client.emit(runner_browser._CDP_WS_FRAME_RECEIVED, _frame('{"url": "https://cdn.example/b.mp4"}'))
    assert runner.drain_websocket_media_urls() == ["https://cdn.example/b.mp4"] and b.client.detached == 0


# ---- 4: manifest capture state -----------------------------------------------------------------------------------
class _CountingList(list):
    yielded = 0

    def __iter__(self):
        for item in list.__iter__(self):
            type(self).yielded += 1
            yield item


def test_manifest_detection_does_not_rescan_queue():
    runner = BrowserMixin()
    _CountingList.yielded = 0
    runner.manifest_urls = _CountingList()
    for i in range(1000):
        runner._on_adaptive_manifest_detected({"url": f"https://cdn.example/{i}.m3u8", "kind": "hls",
                                               "redirect_chain": []})
    assert len(runner.manifest_urls) == 1000
    assert _CountingList.yielded < 5000, (
        f"SWEEP-MF-4 1000 detections scanned {_CountingList.yielded} queued entries (linear scan per detection)")


def test_manifest_watcher_chains_do_not_keep_every_request():
    w = AdaptiveManifestWatcher()
    for i in range(10000):
        w.feed("Network.requestWillBeSent", {"requestId": f"r{i}", "request": {"url": f"https://site.example/{i}.js"}})
        w.feed("Network.responseReceived", {"requestId": f"r{i}",
                                            "response": {"url": f"https://site.example/{i}.js", "mimeType": "text/javascript"}})
    answered = len(w._chains)
    for i in range(20000):
        w.feed("Network.requestWillBeSent", {"requestId": f"n{i}", "request": {"url": f"https://site.example/n{i}.png"}})
    assert answered == 0 and len(w._chains) <= 5000, (
        f"SWEEP-MF-4b _chains kept {answered} answered non-manifest requests, {len(w._chains)} after 20000 more")


def test_pc_manifest_redirect_chain_and_dedupe_survive():
    w = AdaptiveManifestWatcher()
    for i in range(3000):
        w.feed("Network.requestWillBeSent", {"requestId": f"x{i}", "request": {"url": f"https://site.example/{i}.js"}})
    w.feed("Network.requestWillBeSent", {"requestId": "m", "request": {"url": "https://a.example/start"}})
    w.feed("Network.requestWillBeSent", {"requestId": "m", "request": {"url": "https://b.example/hop"}})
    e = w.feed("Network.responseReceived", {"requestId": "m", "response": {
        "url": "https://c.example/live.bin", "mimeType": "application/vnd.apple.mpegurl"}})
    assert e["kind"] == "hls" and e["redirect_chain"] == ["https://a.example/start", "https://b.example/hop"]
    runner = BrowserMixin()
    runner._on_adaptive_manifest_detected(e)
    runner._on_adaptive_manifest_detected(e)
    runner._on_adaptive_manifest_detected(dict(e, redirect_chain=["https://a.example/start", "https://b.example/hop",
                                                                  "https://b.example/hop2"]))
    assert len(runner.manifest_urls) == 1 and len(runner.manifest_urls[0]["redirect_chain"]) == 3
    assert len(runner.drain_manifest_urls()) == 1 and runner.manifest_urls == []
    runner._on_adaptive_manifest_detected(dict(e))
    assert [q["url"] for q in runner.manifest_urls] == ["https://c.example/live.bin"]


def test_manifest_redirect_leg_survives_chain_cap():
    w = AdaptiveManifestWatcher()
    w.feed("Network.requestWillBeSent", {"requestId": "m", "request": {"url": "https://a.example/live.m3u8"}})
    for i in range(runner_browser._MAX_PENDING_CHAINS):
        w.feed("Network.requestWillBeSent", {"requestId": f"o{i}", "request": {"url": f"https://s.example/{i}.js"}})
    e = w.feed("Network.requestWillBeSent", {"requestId": "m", "request": {"url": "https://b.example/resolved"}})
    assert e is not None and e["url"] == "https://b.example/resolved" and e["redirect_chain"] == [
        "https://a.example/live.m3u8"], f"SWEEP-MF-4c chain cap evicted an in-flight manifest's chain: {e} {w.manifests}"
    assert len(w._chains) <= runner_browser._MAX_PENDING_CHAINS
