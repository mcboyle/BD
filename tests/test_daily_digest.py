"""F2.4 daily ops digest — unit tests.

Covers the spec pins: payload assembly on fixtures + zero-delta
suppression, plus the default-OFF enable gate, min-spacing guard, and a
fail-soft collector. No live DB / no real notifier needed — run_digest
exposes ``metrics`` + ``_notifier`` injection seams.

Harness notes: zero-arg test functions; tempfile.mkdtemp (no pytest
tmp_path); module-global swaps restored in try/finally.
"""

import tempfile
from pathlib import Path

from bulk_downloader import daily_digest as dd


class _Recorder:
    """Captures (title, body) the digest tries to dispatch."""
    def __init__(self):
        self.calls = []

    def __call__(self, title, body):
        self.calls.append((title, body))


def _tmp_state():
    return Path(tempfile.mkdtemp(prefix="bd_digest_")) / "state.json"


# ── zero-delta suppression (pin) ─────────────────────────────────────────────
def test_zero_delta_suppresses_no_dispatch():
    sp = _tmp_state()
    metrics = {"events_24h": 12, "errors_24h": 1, "warnings_24h": 3,
               "drafts_pending": 2}
    # seed the snapshot so curr == prev -> zero delta
    dd.save_state({"last_run": 0.0, "last_sent": 0.0, "snapshot": dict(metrics)}, sp)
    rec = _Recorder()
    out = dd.run_digest(metrics=metrics, state_path=sp, _notifier=rec)
    assert out["zero_delta"] is True
    assert out["sent"] is False
    assert out["reason"] == "zero_delta"
    assert rec.calls == []  # nothing dispatched on a quiet day


def test_first_run_all_zero_is_quiet():
    sp = _tmp_state()
    rec = _Recorder()
    out = dd.run_digest(metrics={"events_24h": 0, "errors_24h": 0,
                                 "warnings_24h": 0, "drafts_pending": 0},
                        state_path=sp, _notifier=rec)
    assert out["zero_delta"] is True
    assert out["sent"] is False
    assert rec.calls == []


# ── payload assembly + dispatch on delta (pin) ───────────────────────────────
def test_delta_dispatches_with_payload():
    sp = _tmp_state()
    dd.save_state({"last_run": 0.0, "last_sent": 0.0,
                   "snapshot": {"events_24h": 10, "errors_24h": 0,
                                "warnings_24h": 0, "drafts_pending": 2}}, sp)
    metrics = {"events_24h": 15, "errors_24h": 2, "warnings_24h": 0,
               "drafts_pending": 1}
    rec = _Recorder()
    out = dd.run_digest(metrics=metrics, state_path=sp, _notifier=rec)
    assert out["zero_delta"] is False
    assert out["sent"] is True
    assert out["reason"] == "sent"
    assert len(rec.calls) == 1
    title, body = rec.calls[0]
    assert "daily digest" in title.lower()
    # body shows current values + signed deltas, counts only
    assert "Errors (24h): 2 (+2)" in body
    assert "Timeline events (24h): 15 (+5)" in body
    assert "Drafts awaiting review: 1 (-1)" in body
    assert "Warnings (24h): 0 (—)" in body
    # snapshot advanced to current so tomorrow measures from today
    assert dd.load_state(sp)["snapshot"] == metrics


def test_delta_no_dispatch_still_advances_snapshot():
    sp = _tmp_state()
    metrics = {"events_24h": 5, "errors_24h": 0, "warnings_24h": 0,
               "drafts_pending": 0}
    out = dd.run_digest(metrics=metrics, state_path=sp, dispatch=False)
    assert out["zero_delta"] is False
    assert out["sent"] is False
    assert out["reason"] == "delta_no_dispatch"
    assert dd.load_state(sp)["snapshot"] == metrics


# ── pure helpers ─────────────────────────────────────────────────────────────
def test_compute_delta_and_zero_detection():
    d = dd.compute_delta({"a": 5, "b": 2}, {"a": 5, "b": 0})
    assert d == {"a": 0, "b": 2}
    assert dd.is_zero_delta({"a": 0, "b": 0}) is True
    assert dd.is_zero_delta({"a": 0, "b": -1}) is False
    # missing prev key treated as 0
    assert dd.compute_delta({"x": 3}, {}) == {"x": 3}


def test_build_body_counts_only():
    body = dd.build_body({"events_24h": 7, "errors_24h": 0,
                          "warnings_24h": 1, "drafts_pending": 4},
                         {"events_24h": 7, "errors_24h": 0,
                          "warnings_24h": -2, "drafts_pending": 0})
    assert body.startswith("Changes since the last digest:")
    assert "Timeline events (24h): 7 (+7)" in body
    assert "Warnings (24h): 1 (-2)" in body
    assert "Drafts awaiting review: 4 (—)" in body


# ── enable gate (default OFF) ────────────────────────────────────────────────
def test_disabled_by_default_no_op():
    sp = _tmp_state()
    out = dd.scheduled_digest(state_path=sp)
    assert out == {"ran": False, "reason": "disabled"} or out["reason"] == "disabled"
    assert out["ran"] is False


def test_enabled_runs_then_min_spacing_guards():
    sp = _tmp_state()
    orig = dd._enabled
    dd._enabled = lambda: True
    try:
        first = dd.scheduled_digest(now=1000.0, state_path=sp)
        assert first["ran"] is True
        # a second tick inside the spacing window is a cheap no-op
        second = dd.scheduled_digest(now=1000.0 + 60.0, state_path=sp)
        assert second["ran"] is False
        assert second["reason"] == "min_spacing"
    finally:
        dd._enabled = orig


# ── collector is fail-soft and integer-typed ─────────────────────────────────
def test_collect_metrics_returns_ints():
    m = dd.collect_metrics()
    assert isinstance(m, dict)
    for k in ("events_24h", "errors_24h", "warnings_24h", "drafts_pending"):
        assert k in m
        assert isinstance(m[k], int)
