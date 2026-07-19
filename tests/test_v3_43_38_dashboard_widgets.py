"""v3.43.38 regression tests — dashboard summary widgets.

Coverage:
  - Module surface
  - _RateWindow: add / get_rate / eviction / thread safety
  - _Widgets: note_progress, note_finish, snapshot
  - Snapshot computation: rate, success_pct, ETA logic
  - Edge cases: empty buffers, zero rate, missing runners
  - format_rate / format_eta helpers
  - Defensive: bad inputs don't crash, exceptions swallowed
  - Runner integration: _update_job publishes to widgets
  - App wiring: snapshot merged into _dashboard_snapshot
  - Frontend: rate + success cells, fmtRate helper
"""
from __future__ import annotations

import threading
import time
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DW_PY = _REPO_ROOT / "bulk_downloader" / "dashboard_widgets.py"
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"
_RUNNER_PY = _REPO_ROOT / "bulk_downloader" / "runner.py"


def _APP_SRC():
    """app.py + its extracted app_*.py blueprint modules (Phase 4 thin-core-shell):
    route handlers now live in app_<group>.py, so source-level route assertions read
    the aggregate rather than app.py alone."""
    pkg = _REPO_ROOT / "bulk_downloader"
    parts = [_APP_PY.read_text(encoding="utf-8")]
    parts += [p.read_text(encoding="utf-8") for p in sorted(pkg.glob("app_*.py"))]
    return "\n".join(parts)



# ── Module surface ────────────────────────────────────────────────────

def test_dashboard_widgets_module_importable():
    import bulk_downloader.dashboard_widgets as dw  # noqa: F401


def test_get_widgets_returns_singleton():
    from bulk_downloader.dashboard_widgets import get_widgets
    a = get_widgets()
    b = get_widgets()
    assert a is b


def test_required_exports():
    """The module must expose the shortcut functions that publishers
    call from hot paths."""
    from bulk_downloader import dashboard_widgets as dw
    for name in ("note_progress", "note_finish", "snapshot",
                  "format_rate", "format_eta", "get_widgets"):
        assert hasattr(dw, name), f"missing export: {name}"


# ── _RateWindow ──────────────────────────────────────────────────────

def test_rate_window_empty_returns_zero():
    from bulk_downloader.dashboard_widgets import _RateWindow
    w = _RateWindow(window_s=60.0)
    assert w.get_rate() == 0.0


def test_rate_window_accumulates():
    """Sum of bytes / window seconds, where elapsed is capped."""
    from bulk_downloader.dashboard_widgets import _RateWindow
    w = _RateWindow(window_s=60.0)
    w.add(1000)
    w.add(2000)
    w.add(3000)
    rate = w.get_rate()
    # Total 6000 bytes; elapsed is tiny (microseconds), so we hit
    # the floor of 1.0s → rate = 6000 bytes/sec
    assert 5000 <= rate <= 6500, f"unexpected rate {rate}"


def test_rate_window_evicts_old_samples():
    """Samples older than window_s must drop out of the rate calc."""
    from bulk_downloader.dashboard_widgets import _RateWindow
    w = _RateWindow(window_s=0.2)
    w.add(1_000_000)
    time.sleep(0.4)  # old sample now outside window
    # New sample drives the window
    w.add(500)
    rate = w.get_rate()
    # Should only count the 500-byte sample, not the 1MB one
    assert rate < 10000, f"old sample didn't evict, rate={rate}"


def test_rate_window_ignores_zero_negative():
    """Status-only updates (zero or negative bytes) must not be
    counted — they'd dilute the rate window."""
    from bulk_downloader.dashboard_widgets import _RateWindow
    w = _RateWindow(window_s=60.0)
    w.add(0)
    w.add(-100)
    w.add(-1)
    assert w.get_rate() == 0.0


def test_rate_window_thread_safe():
    """Multiple threads adding samples concurrently must not crash
    or corrupt the deque."""
    from bulk_downloader.dashboard_widgets import _RateWindow
    w = _RateWindow(window_s=60.0)
    def add_many():
        for i in range(100):
            w.add(i + 1)
    threads = [threading.Thread(target=add_many) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    # No crash → pass. Rate is positive.
    assert w.get_rate() > 0


# ── _Widgets ─────────────────────────────────────────────────────────

def test_note_progress_increments_window():
    """A progress event flows through to the rate window."""
    from bulk_downloader.dashboard_widgets import _Widgets
    w = _Widgets()
    w.note_progress("siteA", 1024)
    snap = w.snapshot()
    assert snap["bytes_per_sec"] > 0


def test_note_progress_ignores_invalid_inputs():
    """note_progress must not crash on garbage inputs (publishers
    are in hot paths)."""
    from bulk_downloader.dashboard_widgets import _Widgets
    w = _Widgets()
    w.note_progress("siteA", None)
    w.note_progress("siteA", "string")
    w.note_progress("siteA", -100)
    w.note_progress("siteA", 0)
    # Nothing landed in the window
    assert w.snapshot()["bytes_per_sec"] == 0


def test_note_finish_tracks_success_rate():
    """Recent finishes feed the success_pct calculation."""
    from bulk_downloader.dashboard_widgets import _Widgets
    w = _Widgets()
    for i in range(15):
        w.note_finish("siteA", f"url-{i}", success=(i < 12))  # 80%
    snap = w.snapshot()
    assert snap["success_pct"] == 80.0
    assert snap["success_sample_size"] == 15


def test_success_pct_none_below_data_threshold():
    """With zero samples, success_pct must be None (not 0 or 100),
    so the UI knows to hide the tile rather than show misleading data."""
    from bulk_downloader.dashboard_widgets import _Widgets
    w = _Widgets()
    snap = w.snapshot()
    assert snap["success_pct"] is None
    assert snap["success_sample_size"] == 0


def test_finish_buffer_bounded():
    """The recent-finishes deque must cap at RECENT_FINISHES_MAX
    so a long-running server doesn't accumulate unbounded state."""
    from bulk_downloader.dashboard_widgets import _Widgets, RECENT_FINISHES_MAX
    w = _Widgets()
    for i in range(RECENT_FINISHES_MAX * 3):
        w.note_finish("siteA", f"url-{i}", success=True)
    snap = w.snapshot()
    assert snap["success_sample_size"] == RECENT_FINISHES_MAX


def test_note_finish_ignores_non_string_inputs():
    """Defensive: a bad site_id or url shouldn't crash the publisher."""
    from bulk_downloader.dashboard_widgets import _Widgets
    w = _Widgets()
    w.note_finish(None, "url", True)
    w.note_finish("site", None, True)
    w.note_finish(123, "url", True)
    snap = w.snapshot()
    assert snap["success_sample_size"] == 0


# ── Snapshot ─────────────────────────────────────────────────────────

def test_snapshot_shape():
    """Standard snapshot shape with all expected keys."""
    from bulk_downloader.dashboard_widgets import _Widgets
    w = _Widgets()
    snap = w.snapshot()
    for key in ("bytes_per_sec", "rate_window_s", "success_pct",
                  "success_sample_size", "active_workers",
                  "remaining_bytes", "eta_seconds"):
        assert key in snap, f"missing snapshot key: {key}"


def test_snapshot_active_workers_counts_alive_threads():
    """active_workers requires runner.state='running' AND at least
    one alive worker thread."""
    from bulk_downloader.dashboard_widgets import _Widgets

    class _FakeRunner:
        def __init__(self, state, thread_count):
            self._state = state
            self._threads = [_FakeThread(True) for _ in range(thread_count)]
            self._lock = threading.Lock()
            self.jobs = {}
        def state(self): return self._state

    class _FakeThread:
        def __init__(self, alive): self._alive = alive
        def is_alive(self): return self._alive

    w = _Widgets()
    runners = {
        "site_running": _FakeRunner("running", 3),
        "site_idle": _FakeRunner("idle", 2),
        "site_running_no_threads": _FakeRunner("running", 0),
    }
    snap = w.snapshot(runners_dict=runners)
    # Only the running site with alive threads contributes
    assert snap["active_workers"] == 3


def test_snapshot_eta_computes_when_rate_known():
    """ETA = remaining_bytes / bytes_per_sec when both are positive."""
    from bulk_downloader.dashboard_widgets import (
        _Widgets, MIN_RATE_FOR_ETA_BPS,
    )

    class _FakeRunner:
        def __init__(self):
            self._state = "running"
            self._threads = [_FakeThread()]
            self._lock = threading.Lock()
            self.jobs = {
                "url1": {"status": "running", "bytes_total": 10_000_000,
                          "file_size": 0},
            }
        def state(self): return self._state

    class _FakeThread:
        def is_alive(self): return True

    w = _Widgets()
    # Establish a rate well above MIN_RATE_FOR_ETA_BPS
    for _ in range(10):
        w.note_progress("site1", 100_000)  # adds 100KB samples
    runners = {"site1": _FakeRunner()}
    snap = w.snapshot(runners_dict=runners)
    # eta_seconds = 10_000_000 / bytes_per_sec. Should be positive.
    assert snap["eta_seconds"] is not None
    assert snap["eta_seconds"] > 0


def test_snapshot_eta_none_below_min_rate():
    """When rate is below MIN_RATE_FOR_ETA_BPS, ETA must be None
    (the computation is too noisy to be useful)."""
    from bulk_downloader.dashboard_widgets import _Widgets
    w = _Widgets()
    snap = w.snapshot()
    assert snap["eta_seconds"] is None


def test_snapshot_eta_none_above_max():
    """Insanely long ETAs (>30 days) report as None — the rate is
    almost certainly transient and the absolute number misleading."""
    from bulk_downloader.dashboard_widgets import (
        _Widgets, MAX_ETA_S,
    )

    class _FakeRunner:
        def __init__(self):
            self._state = "running"
            self._threads = [_FakeThread()]
            self._lock = threading.Lock()
            # A 1TB pending download
            self.jobs = {
                "url1": {"status": "running",
                          "bytes_total": 1024 ** 4,
                          "file_size": 0},
            }
        def state(self): return self._state

    class _FakeThread:
        def is_alive(self): return True

    w = _Widgets()
    # Slow rate: 1 KB/s — would take years
    for _ in range(10):
        w.note_progress("site1", 1024)
        time.sleep(0.01)
    snap = w.snapshot(runners_dict={"site1": _FakeRunner()})
    # 1TB / 1KBps = way more than MAX_ETA_S
    assert snap["eta_seconds"] is None


# ── Formatters ───────────────────────────────────────────────────────

def test_format_rate_units():
    from bulk_downloader.dashboard_widgets import format_rate
    assert "B/s" in format_rate(500)
    assert "KB/s" in format_rate(5000)
    assert "MB/s" in format_rate(5_000_000)
    assert "GB/s" in format_rate(5_000_000_000)


def test_format_rate_zero():
    from bulk_downloader.dashboard_widgets import format_rate
    assert format_rate(0) == "0 B/s"


def test_format_rate_handles_garbage():
    """Defensive: None or string inputs become '0 B/s' not crash."""
    from bulk_downloader.dashboard_widgets import format_rate
    assert format_rate(None) == "0 B/s"


def test_format_eta_units():
    from bulk_downloader.dashboard_widgets import format_eta
    assert format_eta(45) == "45s"
    assert format_eta(150) == "2m"
    assert format_eta(3700) == "1h 1m"
    assert format_eta(3600) == "1h"
    assert format_eta(90000) == "1d 1h"


def test_format_eta_none():
    from bulk_downloader.dashboard_widgets import format_eta
    assert format_eta(None) == "—"
    assert format_eta(0) == "—"
    assert format_eta(-5) == "—"


# ── Defensive: shortcuts ──────────────────────────────────────────────

def test_shortcut_publishers_never_raise():
    """Module-level publishers are called from hot paths; they MUST
    swallow any exception so the runner never crashes from a widget
    bug."""
    from bulk_downloader import dashboard_widgets as dw
    # Garbage inputs
    dw.note_progress(None, None)
    dw.note_progress("", "bad")
    dw.note_finish(None, None, None)
    dw.note_finish(123, [], "yes")
    # Reaching here = no exception


def test_shortcut_snapshot_returns_safe_default_on_failure():
    """When the snapshot computation fails, return a dict with the
    expected keys (defaults) rather than None or raising."""
    from bulk_downloader import dashboard_widgets as dw
    # Pass a malformed runners dict to trip an error
    result = dw.snapshot(runners_dict={"x": "not a runner"})
    assert isinstance(result, dict)
    assert "bytes_per_sec" in result
    assert "active_workers" in result


# ── Runner integration ───────────────────────────────────────────────

def test_update_job_publishes_progress():
    """_update_job must call note_progress when file_size advances."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _update_job")
    end = src.find("\n    def _watch_done", pos)
    body = src[pos:end]
    assert "dashboard_widgets" in body
    assert "note_progress" in body


def test_update_job_publishes_finish():
    """_update_job must call note_finish on terminal status."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _update_job")
    end = src.find("\n    def _watch_done", pos)
    body = src[pos:end]
    assert "note_finish" in body
    # And it considers retries when computing success
    assert "retries" in body and "success = " in body.replace("\n", " ")


def test_widget_publish_is_defensive():
    """The widget publishes must be in a try/except — they're an
    optimization, not load-bearing."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("from . import dashboard_widgets")
    # Look at surrounding context for try/except
    # v3.43.53: widened window because the worker-success hook
    # was added inside this try block, pushing the trailing
    # `except Exception` further down.
    body = src[max(0, pos - 200):pos + 3000]
    assert "try:" in body
    assert "except Exception" in body


# ── App wiring ───────────────────────────────────────────────────────

def test_dashboard_snapshot_uses_widgets():
    """_dashboard_snapshot must merge in the widget metrics rather
    than hard-coding throughput_bps=0."""
    src = _APP_SRC()
    pos = src.find("def _dashboard_snapshot")
    assert pos > 0
    body = src[pos:pos + 4000]
    assert "dashboard_widgets" in body
    assert "throughput_bps" in body
    # The hard-coded throughput_bps=0 from the old code must be gone
    assert '"throughput_bps": 0' not in body or "widgets.get" in body


def test_widgets_endpoint_registered():
    src = _APP_SRC()
    assert "/api/dashboard/widgets" in src
    assert "def api_dashboard_widgets" in src


# ── UI ───────────────────────────────────────────────────────────────





# ── Adversarial: defensive formatters (locks in audit fixes) ─────────

def test_format_eta_handles_non_numeric_inputs():
    """Audit catch: format_eta('') crashed `'<=' not supported between
    instances of 'str' and 'int'`. Must return '—' for any non-numeric."""
    from bulk_downloader.dashboard_widgets import format_eta
    for bad in ("", "string", [], {}, object()):
        assert format_eta(bad) == "—", f"failed on {bad!r}"


def test_format_eta_handles_nan_and_inf():
    """NaN and infinity must produce '—' rather than confusing
    output like 'inf seconds'."""
    from bulk_downloader.dashboard_widgets import format_eta
    assert format_eta(float("nan")) == "—"
    assert format_eta(float("inf")) == "—"


def test_format_rate_handles_non_numeric_inputs():
    """Same defensive treatment for format_rate."""
    from bulk_downloader.dashboard_widgets import format_rate
    for bad in (None, "", "string", [], {}, object()):
        result = format_rate(bad)
        assert isinstance(result, str), f"failed on {bad!r}"
        # Non-numeric falls through to '0 B/s'
        assert "B/s" in result or "KB/s" in result


def test_format_rate_handles_negative_inputs():
    """Negative rates are nonsense (you can't download backwards).
    Clamp to 0."""
    from bulk_downloader.dashboard_widgets import format_rate
    assert format_rate(-100) == "0 B/s"
    assert format_rate(-1_000_000) == "0 B/s"


def test_format_rate_handles_nan_and_inf():
    from bulk_downloader.dashboard_widgets import format_rate
    assert format_rate(float("nan")) == "0 B/s"
    assert format_rate(float("inf")) == "0 B/s"
