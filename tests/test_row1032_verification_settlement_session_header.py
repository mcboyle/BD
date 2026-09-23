"""Row 1032: Verification Settlement Watcher & Session Header Capture Hook.

Validates:
(1) VerificationSettlementWatcher combining network idle, DOM mutation settling, and verification checkpoints.
(2) SessionHeaderCaptureHook capturing request/response session headers during page navigation.
(3) Integration with bulk_downloader.runner_browser or settlement pipeline.

RED on baseline: fails with explicit semantic AssertionError (capability missing), not an unhandled ImportError.
Includes positive control test passing on baseline to prove the probe can say YES.
"""
from __future__ import annotations

import sys
import time
from typing import Any, Callable, Dict, List, Optional
import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import verification_settlement
except ImportError:
    verification_settlement = None


class _MockRequest:
    def __init__(self, url: str, resource_type: str = "xhr", headers: Optional[Dict[str, str]] = None):
        self.url = url
        self.resource_type = resource_type
        self.headers = headers or {}


class _MockResponse:
    def __init__(self, url: str, status: int = 200, headers: Optional[Dict[str, str]] = None, request: Any = None):
        self.url = url
        self.status = status
        self.headers = headers or {}
        self.request = request or _MockRequest(url, headers={})


class _SimulatedPage:
    """Mock Playwright page simulating CDP/Playwright network and mutation events."""

    def __init__(self, requests_schedule=None, mutations_schedule=None):
        self._handlers: Dict[str, List[Callable]] = {}
        self._start_time = time.monotonic()
        self._requests_schedule = [
            (start_off, fin_off, _MockRequest(url, res_type, hdrs))
            for (start_off, fin_off, url, res_type, hdrs) in (requests_schedule or [])
        ]
        self._mutations_schedule = list(mutations_schedule or [])
        self._started_reqs = set()
        self._finished_reqs = set()
        self._last_mutation_time = self._start_time
        self._mutation_count = 0
        self._installed = False

    def on(self, event: str, handler: Callable) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler: Callable) -> None:
        if event in self._handlers and handler in self._handlers[event]:
            self._handlers[event].remove(handler)

    def listener_count(self) -> int:
        return sum(len(v) for v in self._handlers.values())

    def _emit(self, event: str, *args: Any) -> None:
        for h in list(self._handlers.get(event, [])):
            h(*args)

    def _tick(self) -> None:
        now = time.monotonic()
        for idx, (start_off, fin_off, req) in enumerate(self._requests_schedule):
            if now >= self._start_time + start_off and idx not in self._started_reqs:
                self._started_reqs.add(idx)
                self._emit("request", req)
            if fin_off is not None and now >= self._start_time + fin_off and idx not in self._finished_reqs:
                self._finished_reqs.add(idx)
                self._emit("requestfinished", req)
                # Emit response as well
                resp = _MockResponse(req.url, status=200, headers={"content-type": "application/json", "x-session-token": "tok-12345"}, request=req)
                self._emit("response", resp)

        for m_off in self._mutations_schedule:
            m_time = self._start_time + m_off
            if now >= m_time and m_time > self._last_mutation_time:
                self._last_mutation_time = m_time
                self._mutation_count += 1

    def evaluate(self, js: str, arg: Any = None) -> Any:
        self._tick()
        now = time.monotonic()
        if "new MutationObserver" in js:
            self._installed = True
            return True

        if "window.__bd_last_mutation" in js:
            if not self._installed:
                return {"installed": False, "elapsed_ms": None, "count": 0}
            elapsed_ms = (now - self._last_mutation_time) * 1000.0
            return {
                "installed": True,
                "elapsed_ms": elapsed_ms,
                "count": self._mutation_count,
            }

        return None

    def wait_for_timeout(self, ms: int) -> None:
        time.sleep(ms / 1000.0)
        self._tick()


def test_positive_control_settlement_baseline():
    """Positive control (Rule 7): proves test runner and probe can say YES on baseline settlement capabilities."""
    from bulk_downloader import settlement

    assert hasattr(settlement, "SettlementBarrier")
    assert callable(settlement.SettlementBarrier)
    assert hasattr(settlement, "wait_for_settlement")
    assert callable(settlement.wait_for_settlement)

    page = _SimulatedPage(
        requests_schedule=[(0.01, 0.05, "https://example.com/api/data", "xhr", {})],
        mutations_schedule=[0.02],
    )
    result = settlement.wait_for_settlement(page, mutation_settle_ms=200, timeout=2.0)
    assert result.settled is True


def test_verification_settlement_capability_implemented():
    """RED assertion 1: capability and product callers must be implemented with semantic AssertionError on base."""
    from bulk_downloader import runner_browser

    assert verification_settlement is not None, (
        "Row 1032 capability missing: Verification Settlement Watcher & Session Header Capture Hook "
        "not implemented in bulk_downloader.verification_settlement"
    )
    assert hasattr(verification_settlement, "VerificationSettlementWatcher"), (
        "Row 1032 component missing: bulk_downloader.verification_settlement.VerificationSettlementWatcher"
    )
    assert hasattr(verification_settlement, "SessionHeaderCaptureHook"), (
        "Row 1032 component missing: bulk_downloader.verification_settlement.SessionHeaderCaptureHook"
    )
    assert hasattr(runner_browser.BrowserMixin, "watch_verification_settlement"), (
        "Row 1032 caller missing: bulk_downloader.runner_browser.BrowserMixin.watch_verification_settlement"
    )


def test_session_header_capture_hook():
    """Verify SessionHeaderCaptureHook captures request and response headers."""
    assert verification_settlement is not None, "verification_settlement capability missing"
    from bulk_downloader.verification_settlement import SessionHeaderCaptureHook

    hook = SessionHeaderCaptureHook(header_filter=["authorization", "x-session-token", "cookie"])
    page = _SimulatedPage()
    hook.attach(page)

    req = _MockRequest(
        "https://example.com/api/v1/auth",
        resource_type="xhr",
        headers={"Authorization": "Bearer sess-token-xyz", "Cookie": "session_id=abc12345; user=mboyle"},
    )
    resp = _MockResponse(
        "https://example.com/api/v1/auth",
        status=200,
        headers={"x-session-token": "refreshed-tok-999", "Set-Cookie": "auth=valid"},
        request=req,
    )

    page._emit("request", req)
    page._emit("response", resp)

    captured = hook.get_captured_headers()
    # Named credential headers keep their NAME (the session carried one) but never the value.
    assert captured == {"authorization": "<scrubbed>", "cookie": "<scrubbed>", "x-session-token": "<scrubbed>"}
    for secret in ("sess-token-xyz", "abc12345", "refreshed-tok-999"):
        assert secret not in repr(captured)

    drained = hook.drain_headers()
    assert len(drained) >= 2
    assert len(hook.get_captured_headers()) == 0

    hook.detach()
    assert hook.is_attached is False


def test_verification_settlement_watcher_with_checkpoints():
    """Verify VerificationSettlementWatcher waits for network/DOM quiet AND custom checkpoints."""
    assert verification_settlement is not None, "verification_settlement capability missing"
    from bulk_downloader.verification_settlement import (
        VerificationSettlementWatcher,
        SessionHeaderCaptureHook,
    )

    page = _SimulatedPage(
        requests_schedule=[(0.01, 0.05, "https://example.com/auth/verify", "xhr", {"x-auth-step": "init"})],
        mutations_schedule=[0.02],
    )

    checkpoint_called = False

    def auth_checkpoint(p: Any) -> bool:
        nonlocal checkpoint_called
        checkpoint_called = True
        return True

    watcher = VerificationSettlementWatcher(page, mutation_settle_ms=200)
    watcher.add_checkpoint("auth_checkpoint", auth_checkpoint)

    result = watcher.watch(timeout=2.0)
    assert result.settled is True
    assert result.verification_passed is True
    assert checkpoint_called is True
    assert "auth_checkpoint" in result.checkpoints_satisfied
    assert result.duration_ms >= 200.0


def test_runner_browser_caller_integration():
    """Verify BrowserMixin.watch_verification_settlement integrates watcher and header capture."""
    assert verification_settlement is not None, "verification_settlement capability missing"
    from bulk_downloader.runner_browser import BrowserMixin

    runner = BrowserMixin.__new__(BrowserMixin)
    runner.config = {"settlement_ms": 200}
    page = _SimulatedPage(
        requests_schedule=[(0.01, 0.04, "https://example.com/login", "xhr", {"Authorization": "Bearer creds"})],
        mutations_schedule=[0.02],
    )

    result = runner.watch_verification_settlement(page, timeout=2.0)
    assert result is not None
    assert result.settled is True
    assert result.verification_passed is True


# ── Rebuild r2 (refutes N6-A / P2-B, E1-E4) ─────────────────────────────────

def _warmup_runner(monkeypatch, page_cls_headers):
    """BrowserMixin._warm_session, the product caller (runner.py -> _warm_session),
    over a simulated page whose one request carries ``page_cls_headers``."""
    from bulk_downloader import runner_browser
    from bulk_downloader.runner_browser import BrowserMixin

    class _Mouse:
        def wheel(self, x, y):
            pass

    class _WarmupPage(_SimulatedPage):
        def __init__(self):
            super().__init__(requests_schedule=[(0.0, 0.05, "https://site.example/api/home", "xhr",
                                                 page_cls_headers)],
                             mutations_schedule=[0.02])
            self.mouse = _Mouse()

        def goto(self, url, **kw):
            self._start_time = time.monotonic()

    runner = BrowserMixin.__new__(BrowserMixin)
    runner.config = {"warmup_urls": "https://site.example/", "warmup_every": 0}
    runner._last_warmup_at = 0.0
    events = []
    runner.log_event = lambda kind, msg, **kw: events.append((kind, msg))
    real_sleep = time.sleep
    monkeypatch.setattr(runner_browser.time, "sleep",
                        lambda s: None if s >= 1.0 else real_sleep(s))
    page = _WarmupPage()
    runner._warm_session(page)
    return page, events


def test_e1_warm_session_settles_through_the_verification_watcher(monkeypatch):
    """E1 RED on 0ce4cb31: the product warmup path never ran the watcher (orphan)."""
    page, events = _warmup_runner(monkeypatch, {"Authorization": "Bearer WARM_SECRET_1",
                                                "Cookie": "sid=WARM_SECRET_2"})
    settled = [m for k, m in events if k == "warmup" and m.startswith("Settled: settled")]
    assert len(settled) == 1, events
    headers = [m for k, m in events if k == "warmup" and m.startswith("Session headers:")]
    assert headers == ["Session headers: content-type=application/json"], (
        f"E1: _warm_session must settle through the verification watcher and log its headers; events {events}")
    assert page.listener_count() == 0, "watcher listeners must be detached after the warmup visit"


def test_e2_warm_session_log_carries_no_credential(monkeypatch):
    page, events = _warmup_runner(monkeypatch, {"Authorization": "Bearer WARM_SECRET_1",
                                                "Cookie": "sid=WARM_SECRET_2"})
    blob = repr(events)
    leaked = [s for s in ("WARM_SECRET_1", "WARM_SECRET_2", "tok-12345") if s in blob]
    assert leaked == [], f"E2: credential values reached the warmup log: {leaked}"


def test_e2_default_watcher_captures_no_credential_header():
    from bulk_downloader.verification_settlement import VerificationSettlementWatcher

    page = _SimulatedPage(
        requests_schedule=[(0.01, 0.04, "https://e.x/a", "xhr",
                            {"Authorization": "Bearer SECRET_A", "Cookie": "sid=SECRET_C"})],
        mutations_schedule=[0.02])
    watcher = VerificationSettlementWatcher(page, mutation_settle_ms=100)
    result = watcher.watch(timeout=2.0)
    watcher.close()
    assert result.headers_captured == {"content-type": "application/json"}, (
        f"E2: default capture must be the allow-list only, got {result.headers_captured}")
    assert "SECRET_A" not in repr(result) and "SECRET_C" not in repr(result)


def test_e3_failing_checkpoint_fails_verification():
    from bulk_downloader.verification_settlement import VerificationSettlementWatcher

    page = _SimulatedPage(requests_schedule=[(0.01, 0.04, "https://e.x/a", "xhr", {})],
                          mutations_schedule=[0.02])
    watcher = VerificationSettlementWatcher(page, mutation_settle_ms=100)
    watcher.add_checkpoint("ok", lambda p: True)
    watcher.add_checkpoint("bad", lambda p: False)
    result = watcher.watch(timeout=2.0)
    assert result.settled is True, "control: the page itself settles"
    assert (result.verification_passed, result.reason, result.checkpoints_satisfied) == (
        False, "checkpoints_failed", ["ok"])


def test_e3_hanging_request_times_out_unsettled():
    from bulk_downloader.verification_settlement import VerificationSettlementWatcher

    page = _SimulatedPage(requests_schedule=[(0.0, None, "https://e.x/hang", "xhr", {})])
    result = VerificationSettlementWatcher(page, mutation_settle_ms=50).watch(timeout=0.5)
    assert (result.settled, result.verification_passed, result.reason) == (False, False, "timeout")


def test_e3_header_outside_filter_is_not_captured():
    from bulk_downloader.verification_settlement import SessionHeaderCaptureHook

    hook = SessionHeaderCaptureHook(header_filter=["content-type"])
    page = _SimulatedPage()
    hook.attach(page)
    page._emit("response", _MockResponse("https://e.x/a", headers={
        "content-type": "text/html", "x-session-token": "T", "server": "nginx"}))
    assert hook.get_captured_headers() == {"content-type": "text/html"}


def test_e4_attach_to_object_without_events_reports_not_attached():
    from bulk_downloader.verification_settlement import SessionHeaderCaptureHook

    hook = SessionHeaderCaptureHook()
    hook.attach(object())
    assert hook.is_attached is False, "E4: attach() on an object without .on must not report attached"
    hook.attach(_SimulatedPage())
    assert hook.is_attached is True, "control: a page with .on attaches"
