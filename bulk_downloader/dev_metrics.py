"""In-process metrics collector for the dev tools.

Holds two small bounded ring buffers — recent HTTP requests (timing +
status) and recent unhandled request exceptions. The Flask request
hooks in app.py feed them; the dev_suite latency / slow-endpoint /
error-rate / exception-log tools read them.

Both buffers are deque(maxlen=...), so they are bounded by
construction and cannot grow without limit (lesson J1 — the failure
class is never-evicted retention).

Import-clean: this module only defines two empty deques, a lock, and
functions. It spawns no thread, touches no DB, and does no I/O at
import time, so it is safe for app.py to import.
"""
from __future__ import annotations

import threading
import time
import traceback
from collections import deque

_MAX_REQUESTS = 2000
_MAX_EXCEPTIONS = 200

_lock = threading.Lock()
_requests: deque = deque(maxlen=_MAX_REQUESTS)
_exceptions: deque = deque(maxlen=_MAX_EXCEPTIONS)


def record_request(method, path, rule, status, duration_ms) -> None:
    """Record one completed request. Called from app.py's after_request
    hook — kept cheap (a dict build + a locked append) and must never
    raise into the response path."""
    try:
        rec = {
            "ts": time.time(),
            "method": str(method),
            "path": str(path),
            "rule": str(rule),
            "status": int(status),
            "duration_ms": round(float(duration_ms), 2),
        }
    except Exception:
        return
    with _lock:
        _requests.append(rec)


# Soft cost ceiling: a pathologically slow request gets an immediate operator
# warning in the main log. The per-endpoint aggregate above is for the dashboard;
# this is the "something is hung / unexpectedly expensive right now" signal.
SLOW_REQUEST_MS = 10000.0


def slow_request_note(method, path, duration_ms, threshold_ms: float = SLOW_REQUEST_MS):
    """Return a one-line warning string if this request exceeded threshold_ms,
    else None. Cheap + never raises (the after_request hook logs the result)."""
    try:
        if duration_ms is None or float(duration_ms) < float(threshold_ms):
            return None
        return (f"slow request: {method} {path} took "
                f"{float(duration_ms) / 1000.0:.1f}s "
                f"(>{float(threshold_ms) / 1000.0:.0f}s)")
    except Exception:
        return None


# Hard cost ceiling (OBS-1). SLOW_REQUEST_MS above only WARNS; this BOUNDS. Flask
# here is synchronous/threaded, so a running request cannot be force-killed from a
# hook -- the budget is a COOPERATIVE deadline: a long collect-style route calls
# check_budget() at its loop boundaries and aborts with a 503 once exceeded. The
# after_request hook additionally flags ANY over-budget response via over_budget(),
# so even a non-cooperative slow route is visible before it wedges. This is a module
# constant (a sibling of SLOW_REQUEST_MS), not a BD_ env var / config field, so it
# adds no config surface. Generous default: a request has to be genuinely stuck.
REQUEST_BUDGET_MS = 30000.0


class RequestBudgetExceeded(Exception):
    """Raised by check_budget() when a request outruns its wall-clock budget. The
    app.py error handler maps it to a 503 carrying these diagnostic fields."""

    def __init__(self, method: str = "", path: str = "",
                 elapsed_ms: float = 0.0, budget_ms: float = REQUEST_BUDGET_MS):
        self.method = method
        self.path = path
        self.elapsed_ms = float(elapsed_ms)
        self.budget_ms = float(budget_ms)
        super().__init__(
            f"request budget exceeded: {method} {path} ran "
            f"{self.elapsed_ms / 1000.0:.1f}s (>{self.budget_ms / 1000.0:.0f}s)"
        )


def over_budget(elapsed_ms, budget_ms: float = REQUEST_BUDGET_MS) -> bool:
    """Pure predicate: did a request taking elapsed_ms exceed the budget? Used by
    the after_request hook to flag a slow response. Never raises."""
    try:
        return float(elapsed_ms) >= float(budget_ms)
    except Exception:
        return False


def check_budget(t0: float, now: float | None = None,
                 budget_ms: float = REQUEST_BUDGET_MS,
                 method: str = "", path: str = "") -> None:
    """Cooperative deadline check. Given a request start time ``t0`` (seconds,
    e.g. flask.g._dev_t0), raise RequestBudgetExceeded once the elapsed time meets
    or exceeds ``budget_ms``. A long route calls this at its loop boundaries so it
    fails fast with a clean 503 instead of running unbounded. ``now`` is injectable
    for tests; defaults to the current clock."""
    if now is None:
        now = time.time()
    elapsed_ms = (float(now) - float(t0)) * 1000.0
    if elapsed_ms >= float(budget_ms):
        raise RequestBudgetExceeded(method=method, path=path,
                                    elapsed_ms=elapsed_ms, budget_ms=budget_ms)


def route_percentiles() -> dict:
    """Per-rule latency percentiles over the recent-requests buffer (OBS-2 core).
    Returns ``{rule: {count, p50, p95, max}}`` in milliseconds. Pure read over the
    existing buffer -- the durations are already recorded, so this adds no new
    collection. The status panel that surfaces this is a separate follow-on."""
    with _lock:
        rows = list(_requests)
    by_rule: dict = {}
    for r in rows:
        by_rule.setdefault(r.get("rule") or r.get("path") or "?", []).append(
            float(r.get("duration_ms") or 0.0))
    out: dict = {}
    for rule, durs in by_rule.items():
        durs.sort()
        out[rule] = {
            "count": len(durs),
            "p50": _percentile(durs, 50),
            "p95": _percentile(durs, 95),
            "max": durs[-1] if durs else 0.0,
        }
    return out


def _percentile(sorted_vals: list, p: float) -> float:
    """Nearest-rank percentile of a pre-sorted, non-empty list; 0.0 if empty."""
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1,
                   int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return round(sorted_vals[k], 2)


def record_exception(exc, path="") -> None:
    """Record one unhandled request exception. Called from the
    got_request_exception signal handler in app.py."""
    try:
        tb = "".join(traceback.format_exception(
            type(exc), exc, getattr(exc, "__traceback__", None)))
        rec = {
            "ts": time.time(),
            "type": type(exc).__name__,
            "message": str(exc)[:300],
            "path": str(path),
            "traceback": tb[-4000:],
        }
    except Exception:
        return
    with _lock:
        _exceptions.append(rec)


def request_snapshot() -> list:
    """A copy of the recent-requests buffer, oldest first."""
    with _lock:
        return list(_requests)


def exception_snapshot() -> list:
    """A copy of the recent-exceptions buffer, oldest first."""
    with _lock:
        return list(_exceptions)


def reset() -> None:
    """Clear both buffers. Used by tests; harmless in production."""
    with _lock:
        _requests.clear()
        _exceptions.clear()
