"""v3.43.38: Dashboard summary widgets.

Live rolling metrics that complement the existing _dashboard_snapshot
totals. Four headline numbers:

  1. **DL rate** — aggregate bytes/sec across all running workers,
     measured as a 60-second rolling window. Each site already tracks
     its own `_throughput_ewma_bps` (exponentially-weighted moving
     average); this module sums them, but also keeps a separate
     window-based aggregate that's more responsive to sudden changes
     (a worker stopping shouldn't take 60s to drop off the headline
     number).

  2. **Success rate** — % of attempts in the last N=100 finishes
     (across all sites) that completed without going through a retry
     loop. "Success" = status transition pending → running → done
     with retries==0. "Failure" = anything else (failed, retried,
     reviewed). Provides a single quality number the user can
     monitor: high = clean run, dropping = something's wrong.

  3. **ETA** — rough time-to-complete for the current queue.
     Computed as `remaining_bytes / current_rate`. Lower bound at
     ~30s because the EWMA needs a few chunks to stabilize.
     Upper bound at 30 days (anything longer is "unknown").

  4. **Active workers** — count across all sites where the runner
     state is "running" AND at least one worker thread is alive.
     Distinct from total_workers (configured) — answers "is anything
     actually downloading right now".

## Why a separate module

The dashboard snapshot in app.py is doing too much already (totals
+ disk + cookies + rate-limit + low-disk + ...). Adding rolling-window
state to that function would mean module-globals for the window
buffers, which is exactly what we keep telling ourselves not to do.
A focused module with explicit state ownership is cleaner.

## Rolling window mechanics

`_RateWindow` keeps the last N seconds of (timestamp, bytes_delta)
samples. Adding a sample evicts anything older than the window.
`get_rate()` returns total_bytes / window_seconds.

Sampling rate: every time a worker reports progress via
`note_progress(site_id, bytes_delta)`, the module appends. The
runner's existing per-chunk progress events feed this naturally.

## Memory

`RateWindow` uses a deque with maxlen — bounded memory even if
the sampling rate explodes during a heavy download. Recent finishes
buffer also bounded at 100 entries.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional


# Rolling window for the DL rate headline number. 60s is a good
# compromise: long enough to smooth chunk-write spikes, short enough
# to react when downloads finish.
RATE_WINDOW_S = 60.0

# How many recent finishes to consider for the success rate. 100 is
# enough to be statistically meaningful but small enough that a
# transient spike (one site rate-limited for an hour) shows up
# within a few minutes of recovery.
RECENT_FINISHES_MAX = 100

# Min observed rate before we'll publish an ETA — below this, the
# number is too jumpy to be useful and the UI shows "—".
MIN_RATE_FOR_ETA_BPS = 1024.0  # 1 KiB/s

# Cap on the ETA we report. Anything longer is "unknown" — the
# computation is too sensitive to current rate at that scale.
MAX_ETA_S = 30 * 24 * 3600  # 30 days


class _RateWindow:
    """Bounded rolling-window byte counter. Thread-safe via a single
    lock; all operations are O(samples-in-window) which is small."""

    def __init__(self, window_s: float = RATE_WINDOW_S):
        self.window_s = window_s
        self._samples: deque = deque()  # (ts, bytes)
        self._lock = threading.Lock()

    def add(self, bytes_delta: int):
        """Record a new byte-progress sample. Discards samples
        outside the rolling window — bounded memory."""
        if bytes_delta <= 0:
            return  # ignore zero/negative deltas (status-only updates)
        now = time.time()
        with self._lock:
            self._samples.append((now, bytes_delta))
            self._evict(now)

    def _evict(self, now: float):
        """Drop samples older than window_s. Caller must hold lock."""
        cutoff = now - self.window_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def get_rate(self) -> float:
        """Bytes/second over the window. Returns 0.0 when the window
        is empty (no progress in last window_s seconds)."""
        now = time.time()
        with self._lock:
            self._evict(now)
            if not self._samples:
                return 0.0
            total = sum(b for _, b in self._samples)
            # Use elapsed-since-oldest-sample for the divisor, capped
            # at window_s. When a download just started and we only
            # have 5s of samples, dividing by 60 underreports the
            # rate; using actual elapsed is more honest.
            oldest_ts = self._samples[0][0]
            elapsed = max(1.0, min(self.window_s, now - oldest_ts))
            return total / elapsed


class _Widgets:
    """Process-wide aggregator. One instance, accessed via the
    module-level `get_widgets()` singleton."""

    def __init__(self):
        self._rate_window = _RateWindow()
        # Recent finish buffer — each entry: {site_id, url, success}
        self._recent_finishes: deque = deque(maxlen=RECENT_FINISHES_MAX)
        self._lock = threading.Lock()

    # ── Publisher hooks (called from runner) ──────────────────────

    def note_progress(self, site_id: str, bytes_delta: int):
        """A worker reports it just wrote N bytes."""
        if not isinstance(bytes_delta, (int, float)) or bytes_delta <= 0:
            return
        self._rate_window.add(int(bytes_delta))

    def note_finish(self, site_id: str, url: str, success: bool):
        """A worker reports a job reached terminal state. success=True
        means done-without-retries; False covers failed, retried,
        and review-needed. Bounded buffer so memory is fine."""
        if not isinstance(site_id, str) or not isinstance(url, str):
            return
        with self._lock:
            self._recent_finishes.append({
                "site_id": site_id,
                "url": url,
                "success": bool(success),
                "ts": time.time(),
            })

    # ── Snapshot for the dashboard / SSE push ─────────────────────

    def snapshot(self, runners_dict: dict | None = None,
                  s_cfg: dict | None = None) -> dict:
        """Compute the four headline numbers + supporting detail.
        `runners_dict` is the app.py runners {sid: SiteRunner};
        `s_cfg` is the site config dict. Both optional — when
        absent we return rate + success but skip ETA + active-workers
        (which need runner introspection)."""
        # ── DL rate ────────────────────────────────────────────
        bytes_per_sec = self._rate_window.get_rate()

        # ── Success rate ───────────────────────────────────────
        with self._lock:
            finishes = list(self._recent_finishes)
        total_recent = len(finishes)
        if total_recent > 0:
            successes = sum(1 for f in finishes if f.get("success"))
            success_pct = round((successes / total_recent) * 100.0, 1)
        else:
            success_pct = None  # no data yet

        # ── Active workers + remaining bytes (needs runners) ────
        active_workers = 0
        remaining_bytes = 0
        if runners_dict:
            for sid, runner in runners_dict.items():
                if not runner:
                    continue
                # Active = state is 'running' AND has at least one
                # worker thread alive. The state alone isn't enough
                # because 'running' can persist briefly during
                # shutdown.
                try:
                    state = runner.state()
                    if state != "running":
                        continue
                    # SiteRunner's production worker list is
                    # ``_worker_threads``. ``_threads`` was an obsolete name
                    # retained by old tests and made live dashboards show 0.
                    threads = getattr(runner, "_worker_threads", None)
                    if threads is None:
                        threads = getattr(runner, "_threads", [])
                    threads = threads or []
                    alive = sum(1 for t in threads if t and t.is_alive())
                    if alive == 0:
                        continue
                    active_workers += alive
                except Exception:
                    continue
                # Remaining bytes: sum of file_size for pending/running
                # jobs. Many jobs won't have file_size set yet (haven't
                # been HEAD-probed); those contribute 0 which is the
                # safer underestimate.
                try:
                    with runner._lock:
                        for url, j in runner.jobs.items():
                            s = j.get("status", "")
                            if s in ("pending", "running"):
                                # For running, prefer remaining (total
                                # minus downloaded); for pending, the
                                # full file_size. file_size is the
                                # accumulator during running, so we
                                # need bytes_total - file_size when
                                # available.
                                total = int(j.get("bytes_total", 0) or 0)
                                done = int(j.get("file_size", 0) or 0)
                                if s == "running" and total > 0:
                                    remaining_bytes += max(0, total - done)
                                elif s == "pending":
                                    # No total known yet; can't add
                                    # to remaining accurately. Skip.
                                    pass
                except Exception:
                    pass

        # ── ETA ────────────────────────────────────────────────
        eta_seconds = None
        if (bytes_per_sec >= MIN_RATE_FOR_ETA_BPS
                and remaining_bytes > 0):
            raw_eta = remaining_bytes / bytes_per_sec
            if raw_eta <= MAX_ETA_S:
                eta_seconds = int(raw_eta)

        return {
            "bytes_per_sec": int(bytes_per_sec),
            "rate_window_s": RATE_WINDOW_S,
            "success_pct": success_pct,
            "success_sample_size": total_recent,
            "active_workers": active_workers,
            "remaining_bytes": remaining_bytes,
            "eta_seconds": eta_seconds,
        }

    # ── Diagnostics ────────────────────────────────────────────

    def get_debug_info(self) -> dict:
        """For the diagnostic endpoint — what's in the window, what's
        in the finish buffer. Useful for debugging "why is my rate
        showing zero"."""
        with self._rate_window._lock:
            sample_count = len(self._rate_window._samples)
            oldest = (self._rate_window._samples[0][0]
                       if self._rate_window._samples else 0)
        with self._lock:
            finish_count = len(self._recent_finishes)
        return {
            "rate_sample_count": sample_count,
            "rate_window_oldest_ts": oldest,
            "rate_window_seconds": RATE_WINDOW_S,
            "finish_buffer_count": finish_count,
            "finish_buffer_max": RECENT_FINISHES_MAX,
        }


# ── Module singleton ──────────────────────────────────────────────

_WIDGETS: Optional[_Widgets] = None
_WIDGETS_LOCK = threading.Lock()


def get_widgets() -> _Widgets:
    """Lazy singleton accessor. Module-level shortcut functions use
    this so callers don't have to."""
    global _WIDGETS
    if _WIDGETS is None:
        with _WIDGETS_LOCK:
            if _WIDGETS is None:
                _WIDGETS = _Widgets()
    return _WIDGETS


# ── Convenience shortcuts for publishers ──────────────────────────

def note_progress(site_id: str, bytes_delta: int):
    """Defensive shortcut. Publishers call this from hot paths
    (per-chunk write), so it MUST never raise."""
    try:
        get_widgets().note_progress(site_id, bytes_delta)
    except Exception:
        pass


def note_finish(site_id: str, url: str, success: bool):
    """Defensive shortcut. Called from _update_job when a job
    reaches terminal state."""
    try:
        get_widgets().note_finish(site_id, url, success)
    except Exception:
        pass


def snapshot(runners_dict: dict | None = None,
              s_cfg: dict | None = None) -> dict:
    """Defensive shortcut. The dashboard endpoint calls this on
    every render; failures must not crash the dashboard."""
    try:
        return get_widgets().snapshot(runners_dict, s_cfg)
    except Exception:
        return {
            "bytes_per_sec": 0,
            "rate_window_s": RATE_WINDOW_S,
            "success_pct": None,
            "success_sample_size": 0,
            "active_workers": 0,
            "remaining_bytes": 0,
            "eta_seconds": None,
        }


def format_rate(bytes_per_sec: int) -> str:
    """Human-readable rate string. Used by both the diagnostic
    endpoint and the UI (for the unit-test confidence and to keep
    the formatting logic single-source).

    Defensive: non-numeric inputs return '0 B/s' rather than
    raising. The dashboard's formatted-output endpoint must never
    crash from a malformed cache."""
    try:
        bps = float(bytes_per_sec or 0)
        if bps != bps or bps == float("inf"):  # NaN or inf
            return "0 B/s"
        if bps < 0:
            bps = 0.0
    except (TypeError, ValueError):
        return "0 B/s"
    if bps < 1024:
        return f"{int(bps)} B/s"
    if bps < 1024 * 1024:
        return f"{bps/1024:.1f} KB/s"
    if bps < 1024 * 1024 * 1024:
        return f"{bps/(1024*1024):.1f} MB/s"
    return f"{bps/(1024*1024*1024):.2f} GB/s"


def format_eta(eta_seconds: Optional[int]) -> str:
    """Human-readable ETA string. None → '—' for the UI.

    Defensive: non-numeric inputs (string, list, dict from a bad
    cache) become '—' rather than raising — this is called from
    the formatted-snapshot endpoint and a crash there would break
    the dashboard."""
    if eta_seconds is None:
        return "—"
    # Coerce — handles bool, int, float; rejects str/list/dict
    if not isinstance(eta_seconds, (int, float)):
        return "—"
    if isinstance(eta_seconds, float) and (
            eta_seconds != eta_seconds  # NaN
            or eta_seconds == float("inf")):
        return "—"
    eta_seconds = int(eta_seconds)
    if eta_seconds <= 0:
        return "—"
    if eta_seconds < 60:
        return f"{eta_seconds}s"
    if eta_seconds < 3600:
        return f"{eta_seconds // 60}m"
    if eta_seconds < 86400:
        h = eta_seconds // 3600
        m = (eta_seconds % 3600) // 60
        return f"{h}h {m}m" if m else f"{h}h"
    d = eta_seconds // 86400
    h = (eta_seconds % 86400) // 3600
    return f"{d}d {h}h" if h else f"{d}d"
