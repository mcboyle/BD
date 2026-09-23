"""Verification Settlement Watcher & Session Header Capture Hook.

Provides event-driven verification settlement tracking (combining network idle,
DOM mutation quiet periods, and verification checkpoints) along with session
header capture for authenticated browser sessions.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from .capture_redact import PLACEHOLDER, SENSITIVE_HEADER
from .settlement import SettlementBarrier, SettlementResult

# Headers captured when the caller names none: descriptive, never credentials.
DEFAULT_HEADER_ALLOWLIST = ("content-type", "server", "cache-control", "x-request-id")


@dataclass
class VerificationSettlementResult:
    settled: bool
    duration_ms: float
    requests_seen: int
    mutations_seen: int
    long_polls_ignored: int = 0
    headers_captured: Dict[str, str] = field(default_factory=dict)
    verification_passed: bool = True
    checkpoints_satisfied: List[str] = field(default_factory=list)
    reason: str = "settled"


class SessionHeaderCaptureHook:
    """Hooks into page/context request and response events to extract and index session headers.

    Only headers named in ``header_filter`` (default DEFAULT_HEADER_ALLOWLIST) are
    kept, and a credential-bearing header (capture_redact.SENSITIVE_HEADER) keeps
    its name but never its value: a settlement result is something callers log."""

    def __init__(self, header_filter: Optional[List[str]] = None) -> None:
        names = DEFAULT_HEADER_ALLOWLIST if header_filter is None else header_filter
        self.header_filter = [h.lower() for h in names]
        self._captured: Dict[str, str] = {}
        self._page: Any = None
        self._handlers: Dict[str, Callable[..., None]] = {}
        self.is_attached = False

    def attach(self, page_or_context: Any) -> None:
        self.detach()
        self._page = page_or_context
        if not hasattr(self._page, "on"):
            self._page = None
            return

        def _on_request(request: Any) -> None:
            self._ingest_headers(getattr(request, "headers", None))

        def _on_response(response: Any) -> None:
            self._ingest_headers(getattr(response, "headers", None))

        self._handlers = {"request": _on_request, "response": _on_response}
        for event, handler in self._handlers.items():
            try:
                self._page.on(event, handler)
            except Exception:
                pass
        self.is_attached = True

    def detach(self) -> None:
        if self._page and hasattr(self._page, "remove_listener"):
            for event, handler in list(self._handlers.items()):
                try:
                    self._page.remove_listener(event, handler)
                except Exception:
                    pass
        self._handlers.clear()
        self._page = None
        self.is_attached = False

    def _ingest_headers(self, headers: Optional[Dict[str, str]]) -> None:
        if not headers or not isinstance(headers, dict):
            return
        for k, v in headers.items():
            k_lower = k.lower()
            if k_lower in self.header_filter:
                self._captured[k_lower] = PLACEHOLDER if SENSITIVE_HEADER.search(k_lower) else str(v)

    def get_captured_headers(self) -> Dict[str, str]:
        return dict(self._captured)

    def drain_headers(self) -> Dict[str, str]:
        drained = dict(self._captured)
        self._captured.clear()
        return drained

    def clear(self) -> None:
        self._captured.clear()


class VerificationSettlementWatcher:
    """Coordinates page settlement and verifies application readiness checkpoints."""

    def __init__(
        self,
        page: Any,
        *,
        mutation_settle_ms: float = 250.0,
        long_poll_threshold_s: float = 1.5,
        poll_interval_ms: float = 15.0,
        header_filter: Optional[List[str]] = None,
    ) -> None:
        self.page = page
        self.mutation_settle_ms = mutation_settle_ms
        self.long_poll_threshold_s = long_poll_threshold_s
        self.poll_interval_ms = poll_interval_ms
        self._checkpoints: Dict[str, Callable[[Any], bool]] = {}
        self.header_hook = SessionHeaderCaptureHook(header_filter=header_filter)
        self.header_hook.attach(page)

    def add_checkpoint(self, name: str, predicate: Callable[[Any], bool]) -> None:
        self._checkpoints[name] = predicate

    def watch(self, *, timeout: float = 10.0, raise_on_timeout: bool = False) -> VerificationSettlementResult:
        barrier = SettlementBarrier(
            self.page,
            mutation_settle_ms=self.mutation_settle_ms,
            long_poll_threshold_s=self.long_poll_threshold_s,
            poll_interval_ms=self.poll_interval_ms,
        )

        start_time = time.monotonic()

        # First run barrier wait
        settle_res: SettlementResult = barrier.wait(timeout=timeout, raise_on_timeout=raise_on_timeout)

        satisfied_checkpoints: List[str] = []
        all_passed = True

        for name, pred in self._checkpoints.items():
            try:
                passed = bool(pred(self.page))
            except Exception:
                passed = False
            if passed:
                satisfied_checkpoints.append(name)
            else:
                all_passed = False

        duration = (time.monotonic() - start_time) * 1000.0
        return VerificationSettlementResult(
            settled=settle_res.settled,
            duration_ms=max(duration, settle_res.duration_ms),
            requests_seen=settle_res.requests_seen,
            mutations_seen=settle_res.mutations_seen,
            long_polls_ignored=settle_res.long_polls_ignored,
            headers_captured=self.header_hook.get_captured_headers(),
            verification_passed=settle_res.settled and all_passed,
            checkpoints_satisfied=satisfied_checkpoints,
            reason=settle_res.reason if all_passed else "checkpoints_failed",
        )

    def close(self) -> None:
        self.header_hook.detach()
