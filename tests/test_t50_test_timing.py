"""T50/D-71 — /api/dev/test_timing dev tool.

Coverage:
  - dev_suite.test_timing() function:
    * missing artifact → ok=False with helpful error
    * old-schema artifact → ok=False, error mentions schema_version
    * valid artifact → ok=True, slowest sorted desc, by_file totals
    * empty tests list → ok=True, verdict explains the empty case
    * top parameter capped/defaulted defensively
  - /api/dev/test_timing route:
    * happy path returns JSON with the tool fields
    * top query param respected
    * dev-mode gate blocks when BD_DEV_MODE_DISABLE=1
"""
from __future__ import annotations

import json
from pathlib import Path

from bulk_downloader import dev_suite as ds


# ── Helpers ─────────────────────────────────────────────────────────

def _write_v2_artifact(tmp_path, tests):
    """Write a minimal schema_version=2 test_results.json."""
    p = tmp_path / "test_results.json"
    failed = sum(1 for t in tests if t["status"] == "fail")
    skipped = sum(1 for t in tests if t["status"] == "skip")
    passed = sum(1 for t in tests if t["status"] == "pass")
    p.write_text(json.dumps({
        "schema_version": 2,
        "version": "3.63.1",
        "timestamp": "2026-05-20T12:00:00",
        "total": len(tests),
        "passed": passed, "failed": failed, "skipped": skipped,
        "ok": failed == 0,
        "tests": tests,
        "failures": [], "skips": [],
    }), encoding="utf-8")
    return p


def _write_v1_artifact(tmp_path):
    """Write a schema_version=1 (no `tests` list) artifact."""
    p = tmp_path / "test_results.json"
    p.write_text(json.dumps({
        "version": "3.63.0",
        "timestamp": "2026-05-19T12:00:00",
        "total": 2, "passed": 2, "failed": 0, "skipped": 0,
        "ok": True,
        "failures": [], "skips": [],
    }), encoding="utf-8")
    return p


# ── Function: error paths ───────────────────────────────────────────

def test_test_timing_missing_artifact_returns_error(tmp_path):
    """No artifact at the path → ok=False, error names the issue."""
    target = tmp_path / "nope.json"
    r = ds.test_timing(results_path=str(target))
    assert r["ok"] is False
    assert "no test-results artifact" in r["error"]


def test_test_timing_rejects_old_schema(tmp_path):
    """Schema 1 has no per-test durations — must refuse, not lie."""
    p = _write_v1_artifact(tmp_path)
    r = ds.test_timing(results_path=str(p))
    assert r["ok"] is False
    assert "schema_version" in r["error"]
    assert ">= 2" in r["error"]


# ── Function: happy path ────────────────────────────────────────────

def test_test_timing_orders_slowest_descending(tmp_path):
    p = _write_v2_artifact(tmp_path, [
        {"file": "a.py", "test": "test_fast",
         "status": "pass", "duration_seconds": 0.01},
        {"file": "b.py", "test": "test_slow",
         "status": "pass", "duration_seconds": 5.5},
        {"file": "a.py", "test": "test_medium",
         "status": "pass", "duration_seconds": 1.2},
    ])
    r = ds.test_timing(results_path=str(p))
    assert r["ok"] is True
    assert r["total_tests"] == 3
    assert r["slowest"][0]["test"] == "test_slow"
    assert r["slowest"][0]["duration_seconds"] == 5.5
    assert r["slowest"][-1]["test"] == "test_fast"


def test_test_timing_by_file_aggregates(tmp_path):
    p = _write_v2_artifact(tmp_path, [
        {"file": "a.py", "test": "test_1",
         "status": "pass", "duration_seconds": 1.0},
        {"file": "a.py", "test": "test_2",
         "status": "pass", "duration_seconds": 3.0},
        {"file": "b.py", "test": "test_3",
         "status": "pass", "duration_seconds": 0.5},
    ])
    r = ds.test_timing(results_path=str(p))
    by_file = {row["file"]: row for row in r["by_file"]}
    assert by_file["a.py"]["count"] == 2
    assert by_file["a.py"]["total_seconds"] == 4.0
    assert by_file["a.py"]["avg_seconds"] == 2.0
    assert by_file["a.py"]["max_seconds"] == 3.0
    assert by_file["b.py"]["count"] == 1
    # by_file sorted by total_seconds desc — a.py first
    assert r["by_file"][0]["file"] == "a.py"


def test_test_timing_empty_tests_list_verdict(tmp_path):
    p = _write_v2_artifact(tmp_path, [])
    r = ds.test_timing(results_path=str(p))
    assert r["ok"] is True
    assert r["total_tests"] == 0
    assert "no test records" in r["verdict"]


def test_test_timing_top_parameter_respected(tmp_path):
    tests = [
        {"file": "a.py", "test": f"t{i}",
         "status": "pass", "duration_seconds": float(i)}
        for i in range(50)]
    p = _write_v2_artifact(tmp_path, tests)
    r = ds.test_timing(results_path=str(p), top=5)
    assert len(r["slowest"]) == 5
    # Slowest first.
    assert r["slowest"][0]["duration_seconds"] == 49.0


def test_test_timing_top_defaults_on_garbage(tmp_path):
    tests = [{"file": "a.py", "test": "t",
              "status": "pass", "duration_seconds": 0.1}]
    p = _write_v2_artifact(tmp_path, tests)
    r = ds.test_timing(results_path=str(p), top="not-a-number")
    assert r["ok"] is True
    # Default of 30; only 1 test exists so slowest has 1.
    assert len(r["slowest"]) == 1


# ── Route: function present + dev-gate ──────────────────────────────

def test_route_test_timing_returns_json(fresh_app, tmp_path,
                                          monkeypatch):
    """GET /api/dev/test_timing with an explicit path returns the
    tool's JSON shape on a valid schema-2 artifact."""
    p = _write_v2_artifact(tmp_path, [
        {"file": "a.py", "test": "t1",
         "status": "pass", "duration_seconds": 0.5},
    ])
    # fresh_app's test client respects the same dev-mode guard the
    # real server uses. Default is dev-mode on (see is_dev_mode
    # — BD_DEV_MODE_DISABLE controls it).
    monkeypatch.delenv("BD_DEV_MODE_DISABLE", raising=False)
    resp = fresh_app.get(f"/api/dev/test_timing?path={p}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True
    assert body["tool"] == "test_timing"
    assert body["total_tests"] == 1
    assert body["slowest"][0]["test"] == "t1"


def test_route_test_timing_respects_top_query(fresh_app, tmp_path,
                                                monkeypatch):
    tests = [{"file": "a.py", "test": f"t{i}",
              "status": "pass", "duration_seconds": float(i)}
             for i in range(40)]
    p = _write_v2_artifact(tmp_path, tests)
    monkeypatch.delenv("BD_DEV_MODE_DISABLE", raising=False)
    resp = fresh_app.get(f"/api/dev/test_timing?path={p}&top=3")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["slowest"]) == 3


def test_route_test_timing_dev_mode_gate(fresh_app, tmp_path,
                                          monkeypatch):
    """BD_DEV_MODE_DISABLE=1 → 404 from the dev guard."""
    p = _write_v2_artifact(tmp_path, [
        {"file": "a.py", "test": "t",
         "status": "pass", "duration_seconds": 0.1},
    ])
    monkeypatch.setenv("BD_DEV_MODE_DISABLE", "1")
    resp = fresh_app.get(f"/api/dev/test_timing?path={p}")
    assert resp.status_code == 404, (
        f"dev guard must block when disabled; got {resp.status_code}")


# ── Function-presence guard ─────────────────────────────────────────

def test_function_present_in_dev_suite():
    """dev_suite must expose test_timing as a top-level callable."""
    assert callable(getattr(ds, "test_timing", None)), (
        "dev_suite.test_timing missing — T50 unit not wired")


def test_route_present_in_app():
    """app.py (or an extracted app_*.py blueprint) must register /api/dev/test_timing."""
    bd = Path(__file__).resolve().parent.parent / "bulk_downloader"
    src = (bd / "app.py").read_text(encoding="utf-8")
    for _p in sorted(bd.glob("app_*.py")):
        src += _p.read_text(encoding="utf-8")
    assert '"/api/dev/test_timing"' in src, (
        "route /api/dev/test_timing not registered in app.py or any app_*.py blueprint")
