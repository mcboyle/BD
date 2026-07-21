"""U49 — workers_total null on empty fleet.

Pre-v3.63.6 the widgets collector computed `workers_total = total_cap
or 16`, where total_cap is the sum of every runner's max_concurrent.
On a fresh install with no sites configured, total_cap is 0, so the
`or 16` fallback fabricated a 16-slot fleet — the dashboard then
showed "0/16 workers" on a deployment with no runners, lying about
the actual state.

v3.63.6 unit #6 emits None instead, and the widget renderer null-
guards (matching the cpu_ram / gpu pattern from units #4 and #5).
The widget shows '—' when no fleet exists; honest.

These tests pin the source contract via grep (no magic-16 in the
collector) plus an in-process test that verifies the actual behaviour
against a fake runners dict.
"""
from __future__ import annotations

import importlib
import re
import sys
import threading
from pathlib import Path
from unittest.mock import patch


_REPO_ROOT = Path(__file__).resolve().parent.parent
_API_PY = _REPO_ROOT / "bulk_downloader" / "app_widgets_api.py"


# ── Source-grep contract ────────────────────────────────────────────


def test_no_magic_16_fallback_in_workers_total():
    """The pattern `total_cap or 16` must not appear in executable
    code. Comments may legitimately mention it to document the bug.
    """
    body = _API_PY.read_text(encoding="utf-8")
    executable_lines = [
        line for line in body.splitlines()
        if not line.lstrip().startswith("#")
    ]
    executable = "\n".join(executable_lines)
    assert "total_cap or 16" not in executable, (
        "`total_cap or 16` is the regression. Use "
        "`total_cap if total_cap > 0 else None` so the widget can "
        "honestly render '—' on empty fleets."
    )


# ── In-process behaviour contract ──────────────────────────────────


def test_workers_total_is_none_when_no_runners():
    """With an empty runners dict, the collector must emit
    workers_total=None (not 16, not 0).
    """
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)
        import importlib as _il
        app_mod = _il.import_module("bulk_downloader.app_state")

        # Force runners to empty for this test.
        original_runners = getattr(app_mod, "runners", None)
        app_mod.runners = {}
        try:
            out = app_widgets_api._collect_data(None)
        finally:
            if original_runners is None:
                if hasattr(app_mod, "runners"):
                    del app_mod.runners
            else:
                app_mod.runners = original_runners

        assert "workers_total" in out, out
        assert out["workers_total"] is None, (
            f"expected None on empty fleet, got {out['workers_total']!r}"
        )
        assert out.get("workers_active", -1) == 0, (
            f"workers_active should be 0 on empty fleet, got "
            f"{out.get('workers_active')!r}"
        )
    finally:
        sys.path.pop(0)


def test_workers_total_is_real_value_when_runners_configured():
    """With a runners dict that has real max_concurrent, the collector
    emits a real positive integer.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)
        import importlib as _il
        app_mod = _il.import_module("bulk_downloader.app_state")

        class FakeRunner:
            max_concurrent = 4
            def active_worker_count(self):
                return 2

        original_runners = getattr(app_mod, "runners", None)
        app_mod.runners = {"site_a": FakeRunner(), "site_b": FakeRunner()}
        try:
            out = app_widgets_api._collect_data(None)
        finally:
            if original_runners is None:
                if hasattr(app_mod, "runners"):
                    del app_mod.runners
            else:
                app_mod.runners = original_runners

        assert out.get("workers_total") == 8, (
            f"expected 8 (2 runners × max_concurrent=4), got "
            f"{out.get('workers_total')!r}"
        )
        assert out.get("workers_active") == 4, (
            f"expected 4 (2 runners × 2 active), got "
            f"{out.get('workers_active')!r}"
        )
    finally:
        sys.path.pop(0)


class _ProductionShapedRunner:
    """The real SiteRunner has config/_worker_threads/get_status, but no
    active_worker_count() method or max_concurrent attribute."""

    def __init__(self, *, pending=0, running=0, rate=0.0, capacity=3):
        self.config = {"max_concurrent": capacity}
        self._lock = threading.Lock()
        self.jobs = {
            **{f"p-{i}": {"status": "pending"} for i in range(pending)},
            **{f"r-{i}": {"status": "running"} for i in range(running)},
        }
        self._rate = rate

    def get_status(self, light=False):
        return {
            "state": "running" if any(
                j["status"] == "running" for j in self.jobs.values()) else "idle",
            "counts": {
                "pending": sum(j["status"] == "pending" for j in self.jobs.values()),
                "running": sum(j["status"] == "running" for j in self.jobs.values()),
            },
        }

    def state(self):
        return self.get_status(light=True)["state"]

    def _current_throughput_bps(self):
        return self._rate


def test_worker_counts_follow_real_runner_shape_and_running_jobs():
    """A production-shaped runner must not collapse to 0/unknown simply
    because it lacks the obsolete active_worker_count/max_concurrent attrs."""
    from bulk_downloader.app_widgets_api import compute_worker_counts

    runners = {
        "busy": _ProductionShapedRunner(pending=5, running=1, capacity=4),
        "idle": _ProductionShapedRunner(capacity=2),
    }

    assert compute_worker_counts(runners) == {
        "workers_active": 1,
        "workers_total": 6,
        "sites_running": 1,
        "sites_total": 2,
    }


def test_widget_collector_overlays_live_queue_rate_and_capacity(monkeypatch):
    """The catalog's headline fields must agree with the live runner status
    used by health/dashboard endpoints, even when db_stats is stale."""
    from bulk_downloader import app_state, app_widgets_api
    from bulk_downloader import db as db_mod

    runners = {
        "busy": _ProductionShapedRunner(
            pending=5, running=1, rate=2048.0, capacity=4),
        "idle": _ProductionShapedRunner(capacity=2),
    }
    monkeypatch.setattr(app_state, "runners", runners)
    monkeypatch.setattr(app_state, "s_cfg", {
        "busy": {"name": "Busy"}, "idle": {"name": "Idle"},
    })
    monkeypatch.setattr(db_mod, "db_stats", lambda site_id=None: {
        "counts": {"pending": 0, "running": 0},
    })

    out = app_widgets_api._collect_data(None)

    assert out["queue_depth"] == 5
    assert out["workers_active"] == 1
    assert out["workers_total"] == 6
    assert out["sites_running"] == 1
    assert out["sites_total"] == 2
    assert out["throughput_fmt"] == "2.0 KB/s"
    assert out["bandwidth_fmt"] == "2.0 KB/s"
    assert out["avg_speed_fmt"] == "2.0 KB/s"


def test_widget_collector_site_scope_excludes_other_runner(monkeypatch):
    """SiteDetail's catalog must derive every live headline from only the
    requested runner, not leak another site's queue, capacity, or rate."""
    from bulk_downloader import app_state, app_widgets_api
    from bulk_downloader import db as db_mod

    runners = {
        "busy": _ProductionShapedRunner(
            pending=5, running=1, rate=2048.0, capacity=4),
        "other": _ProductionShapedRunner(
            pending=99, running=2, rate=8192.0, capacity=8),
    }
    monkeypatch.setattr(app_state, "runners", runners)
    monkeypatch.setattr(app_state, "s_cfg", {
        "busy": {"name": "Busy"}, "other": {"name": "Other"},
    })
    monkeypatch.setattr(db_mod, "db_stats", lambda site_id=None: {
        "counts": {"pending": 0, "running": 0},
    })

    out = app_widgets_api._collect_data("busy")

    assert out["queue_depth"] == 5
    assert out["workers_active"] == 1
    assert out["workers_total"] == 4
    assert out["sites_running"] == 1
    assert out["sites_total"] == 1
    assert out["throughput_fmt"] == "2.0 KB/s"
    assert out["bandwidth_fmt"] == "2.0 KB/s"
    assert out["avg_speed_fmt"] == "2.0 KB/s"
