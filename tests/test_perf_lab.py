"""Tests for bulk_downloader.perf_lab — the internal memory audit and
load injector, plus their /api/dev/* endpoints.

The injector keeps module-level state, so every test that runs a
profile calls purge() in a finally block to reset for the next test.
"""
import time

import pytest

from bulk_downloader import perf_lab as pl


def _wait_idle(timeout=15.0):
    """Poll injection_status until the run finishes; return the final
    status dict. Fails the test if it never settles."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = pl.injection_status()
        if not st.get("running"):
            return st
        time.sleep(0.02)
    raise AssertionError("load injection did not finish within timeout")


# ── memory audit ──────────────────────────────────────────────────

def test_snapshot_has_expected_shape():
    snap = pl.snapshot()
    for key in ("ts", "rss_bytes", "threads", "processes",
                "interpreter", "tracemalloc_tracing", "app_structures"):
        assert key in snap, f"snapshot missing {key}"
    assert snap["threads"] >= 1
    assert isinstance(snap["interpreter"]["gc_objects"], int)
    assert snap["interpreter"]["gc_objects"] > 0
    # rss is an int on Linux, or None where /proc + resource both fail
    assert snap["rss_bytes"] is None or isinstance(snap["rss_bytes"], int)


def test_snapshot_includes_per_runner_structures_when_passed():
    class _FakeRunner:
        jobs = {"a": 1, "b": 2}
        urls = ["u1", "u2", "u3"]
        _event_log = [1, 2]
    snap = pl.snapshot(runners={"site1": _FakeRunner()})
    aps = snap["app_structures"]
    assert aps["runner_count"] == 1
    assert aps["total_queued_jobs"] == 2
    assert aps["runners"][0]["urls"] == 3


def test_audit_reports_deltas_and_findings():
    result = pl.audit(settle_seconds=0)
    assert "baseline" in result and "final" in result
    assert "deltas" in result and "findings" in result
    assert isinstance(result["findings"], list) and result["findings"]
    # an idle 0-second settle should not flag a leak
    assert result["deltas"]["gc_objects"] is not None


def test_audit_clamps_absurd_settle_value():
    # 9999 seconds would hang the suite — audit must clamp to <= 60.
    # v3.66.9: mock time.sleep so the test doesn't actually wait 60s
    # (which collides with pytest --timeout=60 in the partitioned sweep).
    from unittest import mock
    with mock.patch("bulk_downloader.perf_lab.time.sleep") as m_sleep:
        result = pl.audit(settle_seconds=9999)
    assert result["settle_seconds"] <= 60
    # And confirm the clamp actually controlled the sleep, not just the report
    m_sleep.assert_called_once()
    assert m_sleep.call_args.args[0] <= 60.0


def test_tracemalloc_start_and_stop():
    try:
        started = pl.tracemalloc_start()
        assert started["ok"] is True
        snap = pl.snapshot()
        assert snap["tracemalloc_tracing"] is True
        # while tracing, snapshot carries a top-allocations list
        assert isinstance(snap["tracemalloc"], list)
    finally:
        stopped = pl.tracemalloc_stop()
        assert stopped["ok"] is True
    assert pl.snapshot()["tracemalloc_tracing"] is False


# ── load injector: validation ─────────────────────────────────────

def test_start_injection_rejects_unknown_profile():
    res = pl.start_injection("not-a-profile", 10)
    assert res["ok"] is False
    assert "profile" in res["error"].lower()


def test_start_injection_rejects_nonpositive_magnitude():
    assert pl.start_injection("objects", 0)["ok"] is False
    assert pl.start_injection("objects", -5)["ok"] is False


def test_only_one_injection_runs_at_a_time():
    try:
        first = pl.start_injection("objects", 50)   # ~50k objects, ~0.25s
        assert first["ok"] is True
        # running flag is set synchronously before the worker thread —
        # a second start must be rejected while the first is active
        second = pl.start_injection("objects", 5)
        assert second["ok"] is False
        assert "already" in second["error"].lower()
    finally:
        pl.purge()
        _wait_idle()


# ── load injector: profiles run and reverse ───────────────────────

def test_objects_profile_runs_to_completion_then_purges():
    try:
        res = pl.start_injection("objects", 3)      # 3000 objects
        assert res["ok"] is True
        final = _wait_idle()
        assert final["detail"] == "complete"
        assert final["progress"] == 3000
        assert not final["error"]
    finally:
        freed = pl.purge()
    assert freed["freed"]["objects_freed"] >= 3000
    # state is released after purge
    assert pl.injection_status().get("running") is False


def test_memory_profile_allocates_and_purges():
    try:
        res = pl.start_injection("memory", 8)       # 8 MB
        assert res["ok"] is True
        final = _wait_idle()
        assert final["detail"] == "complete"
        assert final["progress"] >= 1
    finally:
        freed = pl.purge()
    assert freed["freed"]["memory_chunks_freed"] >= 1


def test_stop_injection_cancels_a_run():
    try:
        res = pl.start_injection("memory", 4000)    # large — won't finish
        assert res["ok"] is True
        time.sleep(0.05)
        stop = pl.stop_injection()
        assert stop["ok"] is True
        final = _wait_idle()
        assert final["detail"] == "cancelled"
    finally:
        pl.purge()


def test_queue_profile_inserts_and_purges_synthetic_rows(fresh_app):
    from bulk_downloader.db import db_conn

    def _count():
        with db_conn() as cx:
            row = cx.execute(
                "SELECT COUNT(*) FROM queue WHERE site_id=?",
                (pl._LOADTEST_SITE_ID,)).fetchone()
            return row[0] if row else 0

    assert _count() == 0
    try:
        res = pl.start_injection("queue", 150)
        assert res["ok"] is True
        final = _wait_idle()
        assert final["detail"] == "complete"
        assert _count() == 150
    finally:
        freed = pl.purge()
    assert freed["freed"]["queue_rows_deleted"] == 150
    assert _count() == 0


# ── /api/dev/* endpoints ──────────────────────────────────────────

def test_mem_audit_endpoint_returns_snapshot(fresh_app):
    r = fresh_app.get("/api/dev/mem_audit")
    assert r.status_code == 200
    body = r.get_json()
    assert "threads" in body and "interpreter" in body


def test_mem_audit_endpoint_settle_runs_diff(fresh_app):
    r = fresh_app.get("/api/dev/mem_audit?settle=0")
    assert r.status_code == 200
    body = r.get_json()
    assert "findings" in body and "deltas" in body


def test_load_endpoint_status_and_lifecycle(fresh_app):
    # GET — status
    r = fresh_app.get("/api/dev/load")
    assert r.status_code == 200
    # POST start
    r = fresh_app.post("/api/dev/load",
                       json={"action": "start", "profile": "objects",
                             "magnitude": 2})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    try:
        _wait_idle()
    finally:
        r = fresh_app.post("/api/dev/load", json={"action": "purge"})
        assert r.status_code == 200
        assert r.get_json()["ok"] is True


def test_load_endpoint_rejects_bad_profile(fresh_app):
    r = fresh_app.post("/api/dev/load",
                       json={"action": "start", "profile": "bogus",
                             "magnitude": 1})
    assert r.status_code == 200
    assert r.get_json()["ok"] is False


def test_load_endpoint_rejects_bad_action(fresh_app):
    r = fresh_app.post("/api/dev/load", json={"action": "frobnicate"})
    assert r.status_code == 400
