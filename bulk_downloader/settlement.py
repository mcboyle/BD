"""settlement -- event-driven network idle and DOM mutation settlement barrier.

Row 926 (v3.66.1564): replaces static time.sleep delays with event-driven
readiness barriers based on CDP/Playwright network lifecycle events and
DOM MutationObserver settling.

Resolves as soon as:
1. In-flight HTTP requests hit zero (with proper long-polling / streaming exemption).
2. DOM mutations settle for at least 250ms.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional


_LONG_POLL_RE = re.compile(
    r"/(poll|longpoll|subscribe|stream|comet|events|heartbeat|ping|socket\.io|"
    r"live_chat|telemetry|analytics|beacon|sse|eventstream|signalr)(/|\?|#|$)",
    re.IGNORECASE,
)
# Media SEGMENTS may stream continuously; adaptive MANIFESTS (.m3u8 / .mpd)
# are short fetches whose completion the page's readiness depends on, so
# they are ordinary in-flight requests, never exempt.
_STREAMING_EXT_RE = re.compile(
    r"\.(ts|aac|m4s)(\?|#|$)",
    re.IGNORECASE,
)
_LONG_POLL_TYPES = {"eventsource", "websocket", "media"}

_INSTALL_OBSERVER_JS = """
(() => {
    if (window.__bd_settlement_installed) return true;
    window.__bd_settlement_installed = true;
    window.__bd_last_mutation = Date.now();
    window.__bd_mutation_count = 0;
    try {
        const observer = new MutationObserver((mutations) => {
            window.__bd_last_mutation = Date.now();
            window.__bd_mutation_count += (mutations ? mutations.length : 1);
        });
        const target = document.documentElement || document.body || document;
        if (target) {
            observer.observe(target, {
                childList: true,
                subtree: true,
                attributes: true,
                characterData: true
            });
            window.__bd_mutation_observer = observer;
        }
    } catch (e) {}
    return true;
})();
"""

_READ_MUTATION_JS = """
(() => {
    if (!window.__bd_settlement_installed) {
        return { installed: false, elapsed_ms: null, count: 0 };
    }
    const last = window.__bd_last_mutation;
    const count = window.__bd_mutation_count || 0;
    return { installed: true, elapsed_ms: last ? (Date.now() - last) : null, count: count };
})();
"""

_EVENTS = ("request", "requestfinished", "requestfailed")


def is_long_poll_request(url: str, resource_type: str = "") -> bool:
    """Classify whether a request is long-polling, SSE, streaming, or websocket.

    These connections stay in-flight deliberately and must not block page settlement.
    """
    if not url:
        return False
    res_type = (resource_type or "").lower()
    if res_type in _LONG_POLL_TYPES:
        return True
    if _LONG_POLL_RE.search(url) or _STREAMING_EXT_RE.search(url):
        return True
    return False


@dataclass
class SettlementResult:
    settled: bool
    duration_ms: float
    requests_seen: int
    long_polls_ignored: int
    mutations_seen: int
    reason: str


class SettlementBarrier:
    """Tracks network in-flight requests and DOM mutations to determine exact readiness."""

    def __init__(
        self,
        page: Any,
        *,
        mutation_settle_ms: float = 250.0,
        long_poll_threshold_s: float = 1.5,
        poll_interval_ms: float = 15.0,
    ):
        self.page = page
        self.mutation_settle_ms = float(mutation_settle_ms)
        self.long_poll_threshold_s = float(long_poll_threshold_s)
        self.poll_interval_ms = float(poll_interval_ms)

        self._in_flight: dict[Any, tuple[float, str, str]] = {}
        self._long_polls: set[Any] = set()
        self._total_requests = 0
        self._attached = False
        self._handlers: dict[str, Callable[..., None]] = {}
        self._attach_listeners()

    def __enter__(self) -> "SettlementBarrier":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.detach()

    def _attach_listeners(self) -> None:
        if not hasattr(self.page, "on"):
            return

        def _on_request(request: Any) -> None:
            now = time.monotonic()
            url = getattr(request, "url", "")
            res_type = getattr(request, "resource_type", "")
            self._total_requests += 1
            if is_long_poll_request(url, res_type):
                self._long_polls.add(request)
            else:
                self._in_flight[request] = (now, url, res_type)

        def _on_finished(request: Any) -> None:
            self._in_flight.pop(request, None)
            self._long_polls.discard(request)

        def _on_failed(request: Any) -> None:
            self._in_flight.pop(request, None)
            self._long_polls.discard(request)

        handlers = {"request": _on_request, "requestfinished": _on_finished,
                    "requestfailed": _on_failed}
        try:
            for event, handler in handlers.items():
                self.page.on(event, handler)
                self._handlers[event] = handler   # recorded as attached, for detach()
            self._attached = True
        except Exception:
            self.detach()

    def detach(self) -> None:
        """Remove every handler this barrier attached (idempotent). A barrier
        is one wait's worth of bookkeeping; handlers left on the page would
        keep counting -- and keep the closures alive -- for every later
        navigation of that page."""
        remove = getattr(self.page, "remove_listener", None)
        for event, handler in list(self._handlers.items()):
            if callable(remove):
                try:
                    remove(event, handler)
                except Exception:
                    pass
            self._handlers.pop(event, None)
        self._attached = False

    def wait(
        self,
        *,
        timeout: float = 10.0,
        raise_on_timeout: bool = False,
    ) -> SettlementResult:
        """Wait until in-flight network requests hit zero and DOM mutations
        settle for ``mutation_settle_ms``. The page's listeners are detached
        before returning (or raising), whatever happened."""
        try:
            return self._wait(timeout=timeout, raise_on_timeout=raise_on_timeout)
        finally:
            self.detach()

    def _install_observer(self) -> bool:
        try:
            return bool(self.page.evaluate(_INSTALL_OBSERVER_JS))
        except Exception:
            return False

    def _read_mutation_state(self) -> Optional[dict]:
        """The page's mutation record, or None when it cannot be read -- an
        evaluate that raises (navigation in flight, execution context
        destroyed) or an observer that is not installed (a new document
        since the install) says NOTHING about quiet; UNKNOWN is not
        settled, so the observer is re-installed and the poll continues."""
        try:
            info = self.page.evaluate(_READ_MUTATION_JS)
        except Exception:
            return None
        if not isinstance(info, dict) or not info.get("installed"):
            self._install_observer()
            return None
        if info.get("elapsed_ms") is None:
            return None
        return info

    def _wait(self, *, timeout: float, raise_on_timeout: bool) -> SettlementResult:
        start_time = time.monotonic()
        deadline = start_time + max(0.1, float(timeout))

        # Install MutationObserver in page DOM
        self._install_observer()

        mutations_count = 0
        while True:
            now = time.monotonic()
            if now >= deadline:
                dur = (now - start_time) * 1000.0
                if raise_on_timeout:
                    raise TimeoutError(
                        f"Page failed to settle within {timeout}s (in-flight={len(self._in_flight)})"
                    )
                return SettlementResult(
                    settled=False,
                    duration_ms=dur,
                    requests_seen=self._total_requests,
                    long_polls_ignored=len(self._long_polls),
                    mutations_seen=mutations_count,
                    reason="timeout",
                )

            # Re-classify any requests in-flight exceeding long_poll_threshold_s
            timed_out_long_polls = [
                req
                for req, (st, url, rtype) in self._in_flight.items()
                if (now - st) >= self.long_poll_threshold_s
            ]
            for req in timed_out_long_polls:
                self._in_flight.pop(req, None)
                self._long_polls.add(req)

            # Check network idle condition: non-long-poll in-flight requests == 0
            mutation_info = self._read_mutation_state() if not self._in_flight else None
            if mutation_info is not None:
                elapsed_ms = float(mutation_info.get("elapsed_ms"))
                mutations_count = int(mutation_info.get("count", 0))

                # If DOM has been quiet for at least mutation_settle_ms (250ms)
                if elapsed_ms >= self.mutation_settle_ms:
                    dur = (now - start_time) * 1000.0
                    return SettlementResult(
                        settled=True,
                        duration_ms=dur,
                        requests_seen=self._total_requests,
                        long_polls_ignored=len(self._long_polls),
                        mutations_seen=mutations_count,
                        reason="settled",
                    )

            # Bounded poll sleep
            sleep_s = self.poll_interval_ms / 1000.0
            if hasattr(self.page, "wait_for_timeout"):
                try:
                    self.page.wait_for_timeout(int(self.poll_interval_ms))
                except Exception:
                    time.sleep(sleep_s)
            else:
                time.sleep(sleep_s)


def wait_for_settlement(
    page: Any,
    *,
    mutation_settle_ms: float = 250.0,
    timeout: float = 10.0,
    long_poll_threshold_s: float = 1.5,
    poll_interval_ms: float = 15.0,
    raise_on_timeout: bool = False,
) -> SettlementResult:
    """Convenience helper to wait for page network and DOM mutation settlement."""
    barrier = SettlementBarrier(
        page,
        mutation_settle_ms=mutation_settle_ms,
        long_poll_threshold_s=long_poll_threshold_s,
        poll_interval_ms=poll_interval_ms,
    )
    return barrier.wait(timeout=timeout, raise_on_timeout=raise_on_timeout)
