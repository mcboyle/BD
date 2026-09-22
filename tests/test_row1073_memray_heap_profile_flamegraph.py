"""Row 1073: Continuous Heap Profiling Integration with Memray and Automated Flamegraph Generation (MemrayProfile).

Verifies:
(1) Positive control: perf_lab baseline methods exist and are callable.
(2) Behavioral RED: real production API caller `/api/dev/mem_audit` returns continuous heap profiling telemetry.
(3) Behavioral RED: `/api/dev/mem_audit/track` manages memray lifecycle (start, stop, flamegraph) through production API.
(4) Behavioral RED (H657 control): caller fails when `memray_profile` is moved aside / unimportable.
(5) Three-state disposition (O1224): unmeasured state reports UNAVAILABLE and None, never zero.
(6) MemrayProfile direct class API and context manager support.
(7) Safe error handling and idempotency.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

import pytest

BD_GATE_SCOPE = "module"


def test_positive_control_perf_lab_baseline():
    """Positive control: bulk_downloader.perf_lab exists on baseline with standard tools."""
    import bulk_downloader.perf_lab as pl
    assert hasattr(pl, "snapshot")
    assert hasattr(pl, "audit")
    assert hasattr(pl, "tracemalloc_start")
    assert hasattr(pl, "tracemalloc_stop")
    snap = pl.snapshot()
    assert isinstance(snap, dict)
    assert "rss_mb" in snap


def test_behavioral_red_mem_audit_endpoint_heap_profile(fresh_app):
    """Behavioral RED: production endpoint /api/dev/mem_audit returns heap_profile from memray profiler.

    On base (bc1544b7), /api/dev/mem_audit snapshot does not include 'heap_profile'.
    With the cut, /api/dev/mem_audit returns three-state heap profiling status.
    """
    r = fresh_app.get("/api/dev/mem_audit")
    assert r.status_code == 200
    body = r.get_json()
    assert "heap_profile" in body, (
        "BEHAVIORAL RED: /api/dev/mem_audit missing 'heap_profile' telemetry from memray profiler"
    )
    hp = body["heap_profile"]
    assert hp.get("state") in ("unavailable", "active", "measured")


def test_behavioral_red_mem_audit_track_memray_lifecycle(fresh_app):
    """Behavioral RED: production endpoint /api/dev/mem_audit/track manages memray start/stop/flamegraph.

    On base (bc1544b7), /api/dev/mem_audit/track rejects engine='memray' or flamegraph with 400.
    With the cut, the API route starts profiling, stops profiling, and generates flamegraphs.
    """
    # Start memray profiling via API
    r_start = fresh_app.post("/api/dev/mem_audit/track", json={"action": "start", "engine": "memray"})
    assert r_start.status_code == 200, (
        f"BEHAVIORAL RED: /api/dev/mem_audit/track rejected memray engine: {r_start.status_code}"
    )
    start_body = r_start.get_json()
    assert start_body.get("ok") is True
    assert start_body.get("state") == "active"

    # Stop memray profiling via API
    r_stop = fresh_app.post("/api/dev/mem_audit/track", json={"action": "stop", "engine": "memray"})
    assert r_stop.status_code == 200
    stop_body = r_stop.get_json()
    assert stop_body.get("ok") is True
    assert stop_body.get("state") == "measured"

    # Generate flamegraph via API
    r_fg = fresh_app.post("/api/dev/mem_audit/track", json={"action": "flamegraph"})
    assert r_fg.status_code == 200
    fg_body = r_fg.get_json()
    assert fg_body.get("ok") is True
    fg_path = Path(fg_body["flamegraph_path"])
    assert fg_path.exists()
    content = fg_path.read_text(encoding="utf-8")
    assert "flamegraph" in content.lower() or "heap profile" in content.lower()


def test_behavioral_red_h657_fails_when_memray_profile_moved_aside(fresh_app, monkeypatch):
    """H657 control: caller fails with ModuleNotFoundError when memray_profile is moved aside."""
    import bulk_downloader.perf_lab as pl

    # Move memray_profile aside by blocking its import
    monkeypatch.setitem(sys.modules, "bulk_downloader.memray_profile", None)

    with pytest.raises((ModuleNotFoundError, ImportError)):
        pl.memray_start()


def test_behavioral_red_three_state_unmeasured_reports_none_o1224(fresh_app):
    """Verify three-state disposition (O1224): unmeasured profiler reports UNAVAILABLE and None, never zero.

    Under O1224, reporting 0 when unmeasured is a fail-open defect; unmeasured state must report None.
    Verified through production endpoint /api/dev/mem_audit.
    """
    import bulk_downloader.perf_lab as pl
    from bulk_downloader.memray_profile import get_memray_profiler

    get_memray_profiler().reset()

    r = fresh_app.get("/api/dev/mem_audit")
    assert r.status_code == 200
    hp = r.get_json().get("heap_profile", {})

    assert hp.get("state") == "unavailable"
    assert hp.get("peak_memory_bytes") is None, (
        f"Expected None for unmeasured peak_memory_bytes under O1224, got {hp.get('peak_memory_bytes')}"
    )
    assert hp.get("current_memory_bytes") is None
    assert hp.get("allocation_count") is None


def test_memray_profile_direct_class_api():
    """Verify MemrayProfile class direct API, options, and context manager."""
    from bulk_downloader.memray_profile import MemrayProfile

    with tempfile.TemporaryDirectory() as tmpdir:
        fg_path = Path(tmpdir) / "custom_flame.html"
        with MemrayProfile(capture_leaks=True) as profiler:
            assert profiler.is_active is True
            # Allocate
            arr = [list(range(500)) for _ in range(10)]
            stats = profiler.get_stats()
            assert stats["is_active"] is True
            assert stats["state"] == "active"

        assert profiler.is_active is False
        assert profiler.get_stats()["state"] == "measured"
        gen_path = profiler.generate_flamegraph(output_file=fg_path)
        assert Path(gen_path).exists()
        assert Path(gen_path).stat().st_size > 100


def test_memray_profile_error_handling():
    """Verify safe error handling and idempotency of start/stop calls."""
    from bulk_downloader.memray_profile import MemrayProfile

    prof = MemrayProfile()
    # Stop when not started should not throw and return unavailable
    res = prof.stop()
    assert res.get("ok") is True
    assert res.get("was_active") is False
    assert res.get("state") == "unavailable"

    # Double start
    prof.start()
    res2 = prof.start()
    assert res2.get("ok") is True
    assert res2.get("already_active") is True
    assert res2.get("state") == "active"
    prof.stop()
