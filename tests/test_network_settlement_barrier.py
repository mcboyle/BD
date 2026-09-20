"""Row 926: EVENT-DRIVEN-NETWORK-IDLE-AND-MUTATION-SETTLEMENT-BARRIER

Tests for event-driven network idle and mutation settlement barrier in
bulk_downloader/settlement.py. Verifies:
(1) Sub-600ms settlement on quiet pages
(2) Proper handling of long-polling / streaming connections
(3) Zero navigation race conditions via DOM mutation settling
"""
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from bulk_downloader.settlement import (
    SettlementBarrier,
    SettlementResult,
    wait_for_settlement,
    is_long_poll_request,
)

BD_GATE_SCOPE = "module"


class _FakeRequest:
    def __init__(self, url, resource_type="xhr", headers=None):
        self.url = url
        self.resource_type = resource_type
        self.headers = headers or {}


class _SimulatedPage:
    """Mock Playwright page simulating CDP/Playwright network and mutation events."""
    def __init__(self, requests_schedule=None, mutations_schedule=None):
        """
        requests_schedule: list of (start_offset_s, finish_offset_s, url, resource_type)
        mutations_schedule: list of mutation_offsets_s
        """
        self._handlers = {}
        self._start_time = time.monotonic()
        self._requests_schedule = [
            (start_off, fin_off, _FakeRequest(url, res_type))
            for (start_off, fin_off, url, res_type) in (requests_schedule or [])
        ]
        self._mutations_schedule = list(mutations_schedule or [])
        self._started_reqs = set()
        self._finished_reqs = set()
        self._last_mutation_time = self._start_time
        self._mutation_count = 0
        self._installed = False

    def on(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        self._handlers.get(event, []).remove(handler)

    def listener_count(self):
        return sum(len(v) for v in self._handlers.values())

    def _emit(self, event, *args):
        for h in self._handlers.get(event, []):
            h(*args)

    def _tick(self):
        now = time.monotonic()
        for idx, (start_off, fin_off, req) in enumerate(self._requests_schedule):
            if now >= self._start_time + start_off and idx not in self._started_reqs:
                self._started_reqs.add(idx)
                self._emit("request", req)
            if fin_off is not None and now >= self._start_time + fin_off and idx not in self._finished_reqs:
                self._finished_reqs.add(idx)
                self._emit("requestfinished", req)

        for m_off in self._mutations_schedule:
            m_time = self._start_time + m_off
            if now >= m_time and m_time > self._last_mutation_time:
                self._last_mutation_time = m_time
                self._mutation_count += 1

    def evaluate(self, js, arg=None):
        self._tick()
        now = time.monotonic()
        if "new MutationObserver" in js:          # the install script
            self._installed = True
            return True

        if "window.__bd_last_mutation" in js:      # the read script
            if not self._installed:
                return {"installed": False, "elapsed_ms": None, "count": 0}
            elapsed_ms = (now - self._last_mutation_time) * 1000.0
            return {
                "installed": True,
                "elapsed_ms": elapsed_ms,
                "count": self._mutation_count,
            }

        return None

    def wait_for_timeout(self, ms):
        time.sleep(ms / 1000.0)
        self._tick()


def test_sub_600ms_settlement_on_quiet_page():
    """Acceptance (1): sub-600ms settlement on quiet pages where requests complete fast
    and DOM mutations settle within 250ms."""
    # Fast page: 1 request finishes at 0.05s, 1 mutation at 0.02s
    page = _SimulatedPage(
        requests_schedule=[(0.01, 0.05, "https://example.com/api/data", "xhr")],
        mutations_schedule=[0.02],
    )
    result = wait_for_settlement(page, mutation_settle_ms=250, timeout=2.0)
    assert result.settled is True
    assert result.duration_ms < 600.0, f"Expected sub-600ms settlement, took {result.duration_ms}ms"
    assert result.duration_ms >= 250.0, "Must satisfy at least 250ms mutation quiet window"


def test_proper_handling_of_long_polling_connections():
    """Acceptance (2): proper handling of long-polling / streaming connections.
    Long-polling endpoints or connections held open do not block settlement."""
    # Standard request finishes at 0.05s; long poll stays open (fin_off=None)
    page = _SimulatedPage(
        requests_schedule=[
            (0.01, 0.05, "https://example.com/api/items", "xhr"),
            (0.01, None, "https://example.com/stream/poll?cursor=123", "xhr"),
            (0.01, None, "https://example.com/events/sub", "eventsource"),
        ],
        mutations_schedule=[0.02],
    )
    result = wait_for_settlement(
        page,
        mutation_settle_ms=250,
        timeout=2.0,
        long_poll_threshold_s=0.2,
    )
    assert result.settled is True
    assert result.long_polls_ignored >= 1
    assert result.duration_ms < 1000.0


def test_zero_navigation_race_conditions_awaits_mutations():
    """Acceptance (3): zero navigation race conditions. When DOM mutations are
    actively occurring, the barrier does NOT resolve prematurely; it awaits a full
    250ms quiet window after the last mutation."""
    # Mutations at 0.05, 0.15, 0.25s. Last mutation is at 0.25s.
    # Settlement must wait until 0.25 + 0.25 = 0.50s (500ms).
    page = _SimulatedPage(
        requests_schedule=[(0.01, 0.05, "https://example.com/api/data", "xhr")],
        mutations_schedule=[0.05, 0.15, 0.25],
    )
    result = wait_for_settlement(page, mutation_settle_ms=250, timeout=3.0)
    assert result.settled is True
    assert result.duration_ms >= 500.0, (
        f"Settled too early ({result.duration_ms}ms); expected >= 500ms to avoid race condition"
    )


def test_in_flight_network_requests_block_early_settlement():
    """Verify that in-flight HTTP requests prevent premature settlement even if DOM is idle."""
    # Request takes 0.35s to complete; DOM quiet from start.
    page = _SimulatedPage(
        requests_schedule=[(0.01, 0.35, "https://example.com/api/large-json", "xhr")],
        mutations_schedule=[],
    )
    result = wait_for_settlement(page, mutation_settle_ms=100, timeout=2.0)
    assert result.settled is True
    assert result.duration_ms >= 350.0


def test_timeout_when_mutations_never_cease():
    """Verify timeout behavior when mutations continuously arrive."""
    # Mutations every 50ms forever
    mutations = [i * 0.05 for i in range(20)]
    page = _SimulatedPage(
        requests_schedule=[],
        mutations_schedule=mutations,
    )
    result = wait_for_settlement(
        page,
        mutation_settle_ms=250,
        timeout=0.3,
        raise_on_timeout=False,
    )
    assert result.settled is False
    assert result.reason == "timeout"


def test_is_long_poll_request_heuristic():
    """Verify URL pattern and content/resource type classification for long polling."""
    assert is_long_poll_request("https://site.com/sse/events", "eventsource") is True
    assert is_long_poll_request("https://site.com/socket.io/?EIO=4", "websocket") is True
    assert is_long_poll_request("https://site.com/api/longpoll", "xhr") is True
    assert is_long_poll_request("https://site.com/heartbeat", "fetch") is True
    assert is_long_poll_request("https://site.com/static/bundle.js", "script") is False
    assert is_long_poll_request("https://site.com/api/catalog?page=1", "xhr") is False
    # adaptive manifests are short fetches the page depends on: NOT exempt
    assert is_long_poll_request("https://cdn.site.com/v/master.m3u8", "xhr") is False
    assert is_long_poll_request("https://cdn.site.com/v/manifest.mpd?t=1", "fetch") is False
    assert is_long_poll_request("https://cdn.site.com/v/seg-0001.ts", "xhr") is True


def test_an_in_flight_manifest_fetch_blocks_settlement():
    """E5: a .m3u8 playlist request in flight is an ordinary request -- the
    barrier waits for it (settles only after 0.35s), it is not a long-poll."""
    page = _SimulatedPage(
        requests_schedule=[(0.01, 0.35, "https://cdn.site.com/v/master.m3u8", "xhr")],
        mutations_schedule=[],
    )
    result = wait_for_settlement(page, mutation_settle_ms=100, timeout=2.0)
    assert result.settled is True and result.long_polls_ignored == 0
    assert result.duration_ms >= 350.0


def test_an_unreadable_mutation_state_is_unknown_not_settled():
    """E2 (UNKNOWN != permission): when page.evaluate raises (navigation in
    flight / execution context destroyed) the barrier must NOT declare the
    page settled; it keeps polling and times out."""

    class _BrokenEvaluatePage(_SimulatedPage):
        def evaluate(self, js, arg=None):
            raise RuntimeError("Execution context was destroyed")

    result = wait_for_settlement(_BrokenEvaluatePage(), mutation_settle_ms=100, timeout=0.3)
    assert result.settled is False and result.reason == "timeout"

    class _NeverInstalledPage(_SimulatedPage):
        """A new document since the install: the observer is gone and the
        read reports installed=False every time."""
        def evaluate(self, js, arg=None):
            self._tick()
            if "new MutationObserver" in js:
                return True                       # install 'succeeds' but never sticks
            return {"installed": False, "elapsed_ms": None, "count": 0}

    result = wait_for_settlement(_NeverInstalledPage(), mutation_settle_ms=100, timeout=0.3)
    assert result.settled is False and result.reason == "timeout"


def test_listeners_are_detached_after_every_wait():
    """E3 (teardown parity): the three handlers a barrier attaches are removed
    when wait() returns, times out, or raises; a page waited on N times
    carries zero barrier handlers afterwards."""
    page = _SimulatedPage(requests_schedule=[], mutations_schedule=[])
    for _ in range(3):
        assert wait_for_settlement(page, mutation_settle_ms=50, timeout=1.0).settled is True
    assert page.listener_count() == 0

    mutating = _SimulatedPage(mutations_schedule=[i * 0.02 for i in range(50)])
    barrier = SettlementBarrier(mutating)
    assert mutating.listener_count() == 3
    with pytest.raises(TimeoutError):
        barrier.wait(timeout=0.15, raise_on_timeout=True)
    assert mutating.listener_count() == 0

    with SettlementBarrier(page) as scoped:
        assert page.listener_count() == 3
    assert page.listener_count() == 0 and scoped._attached is False


def test_real_chromium_quiet_page_settles_fast_and_leaves_no_handlers():
    """Acceptance (1) on a real browser: a served page whose one fetch
    completes and whose last DOM mutation lands ~100ms after load settles in
    under 600ms and never before its 250ms quiet window; the page's request
    listeners are gone afterwards (Playwright's own listener registry)."""
    import http.server
    import threading
    from playwright.sync_api import sync_playwright

    html = b"""<html><body><div id=x>loading</div><script>
      fetch('/api').then(r => r.text()).then(t => {
        document.getElementById('x').textContent = t;
        setTimeout(() => { document.body.appendChild(document.createElement('p')); }, 100);
      });
    </script></body></html>"""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b"done" if self.path == "/api" else html
            self.send_response(200)
            self.send_header("Content-Type", "text/plain" if self.path == "/api" else "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{srv.server_port}/", wait_until="commit")
            result = wait_for_settlement(page, mutation_settle_ms=250, timeout=5.0)
            assert result.settled is True, result
            assert 250.0 <= result.duration_ms < 600.0, result
            assert page.evaluate("document.getElementById('x').textContent") == "done"
            assert page.evaluate("document.querySelectorAll('p').length") == 1
            # the <p> appended 100ms after the fetch lands after the observer
            # is installed; the textContent write may precede it (goto returns
            # at commit) and is not counted on
            assert result.mutations_seen >= 1 and result.requests_seen >= 1
            for event in ("request", "requestfinished", "requestfailed"):
                assert page._impl_obj.listeners(event) == [], event
            browser.close()
    finally:
        srv.shutdown()


def test_warmup_visits_wait_for_settlement_between_goto_and_the_reading_scroll(monkeypatch):
    """The production seam (O989): BrowserMixin._warm_session visits a warmup
    URL with goto(domcontentloaded), then waits for EVENT-DRIVEN settlement,
    then scrolls and takes the human-pacing pause. Order is asserted on the
    page's own call record; the pause is kept (sleep still called); the
    settlement wait is what a mutant that deletes the call would lose."""
    from bulk_downloader import runner_browser
    from bulk_downloader.runner_browser import BrowserMixin

    calls = []

    class _Mouse:
        def wheel(self, x, y):
            calls.append(("wheel", y))

    class _WarmupPage(_SimulatedPage):
        def __init__(self):
            super().__init__(requests_schedule=[(0.0, 0.05, "https://site.example/api/home", "xhr")],
                             mutations_schedule=[0.02])
            self.mouse = _Mouse()

        def goto(self, url, **kw):
            calls.append(("goto", url, kw.get("wait_until")))
            self._start_time = time.monotonic()      # the schedule runs from navigation

    runner = BrowserMixin.__new__(BrowserMixin)
    runner.config = {"warmup_urls": "https://site.example/", "warmup_every": 0}
    runner._last_warmup_at = 0.0
    events = []
    runner.log_event = lambda kind, msg, **kw: events.append((kind, msg))
    real_sleep = time.sleep

    def sleep(seconds):
        # the barrier's 15ms poll sleeps run for real; the 2.5-6s reading pause is recorded, not served
        if seconds >= 1.0:
            calls.append(("sleep", seconds))
        else:
            real_sleep(seconds)

    monkeypatch.setattr(runner_browser.time, "sleep", sleep)

    page = _WarmupPage()
    runner._warm_session(page)

    kinds = [c[0] for c in calls]
    assert kinds == ["goto", "wheel", "sleep"], calls
    # settlement happened after goto and before the scroll: the page's request
    # listeners were attached and detached (a wait ran), and the settled event
    # was logged before the wheel
    settled = [m for k, m in events if k == "warmup" and m.startswith("Settled: settled")]
    assert len(settled) == 1, events
    assert page.listener_count() == 0
    assert 2.5 <= [c[1] for c in calls if c[0] == "sleep"][0] <= 6.0   # the reading pause is kept
    assert runner._last_warmup_at > 0
