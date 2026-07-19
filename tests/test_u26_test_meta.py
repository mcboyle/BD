"""U26 — test-meta cluster: guard-test status (D-77), test-coverage
map (D-69), test-run diff (D-74). All read-only.

D-71 (test-timing report) is intentionally NOT in this unit — neither
test_results.json nor dev_tools._runs records per-test timing, so
there is nothing to read. See the U26 scope note in dev_suite.py.
"""
import json
import os

from bulk_downloader import dev_suite as ds
from bulk_downloader import dev_tools


# ── guard_test_status (D-77) ───────────────────────────────────────

def _write_artifact(tmp_path, **overrides):
    payload = {
        "version": "3.62.5", "timestamp": "2026-05-19T04:00:00",
        "ok": True, "failures": [], "skips": [],
    }
    payload.update(overrides)
    p = tmp_path / "test_results.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def test_guard_status_missing_artifact_is_a_clean_error():
    r = ds.guard_test_status(results_path="/tmp/u26_nope_xyz.json")
    assert r["ok"] is False
    assert "run_tests.py --json" in r["error"]


def test_guard_status_all_clear(tmp_path):
    art = _write_artifact(tmp_path)
    r = ds.guard_test_status(results_path=art)
    assert r["ok"] is True
    assert r["guard_failures"] == []
    assert r["verdict"] == "all guard tests holding"


def test_guard_status_isolates_guard_failures(tmp_path):
    art = _write_artifact(tmp_path, ok=False, failures=[
        {"file": "tests/test_v3_62_2_guards.py",
         "test": "test_tags", "error": "boom"},
        {"file": "tests/test_login.py",
         "test": "test_other", "error": "unrelated"},
    ])
    r = ds.guard_test_status(results_path=art)
    # only the guard-file failure is surfaced as a guard failure
    assert len(r["guard_failures"]) == 1
    assert r["guard_fail_files"] == ["tests/test_v3_62_2_guards.py"]
    assert "guard test(s) FAILING" in r["verdict"]
    assert r["suite_ok"] is False


def test_guard_status_recognises_guard_markers(tmp_path):
    # a file with 'bounded' / 'robustness' in the name is a guard too
    art = _write_artifact(tmp_path, ok=False, failures=[
        {"file": "tests/test_mass_import_jobs_bounded.py",
         "test": "t", "error": "e"},
    ])
    r = ds.guard_test_status(results_path=art)
    assert len(r["guard_failures"]) == 1


# ── test_coverage_map (D-69) ───────────────────────────────────────

def test_coverage_map_lists_modules_and_tests():
    r = ds.test_coverage_map()
    assert r["ok"] is True
    assert r["module_count"] > 50
    assert r["test_file_count"] > 50
    # covered + uncovered partition the module set
    assert (len(r["covered_modules"]) + len(r["uncovered_modules"])
            == r["module_count"])
    assert 0.0 <= r["coverage_ratio"] <= 1.0


def test_coverage_map_finds_a_named_module():
    r = ds.test_coverage_map()
    # dev_suite itself has test_u*.py files referencing it indirectly,
    # but db has explicit test files — at least one known module with
    # a test file must register as covered
    assert "db" in r["covered_modules"] or "runner" in r["covered_modules"]


# ── test_run_diff (D-74) ───────────────────────────────────────────

def test_run_diff_empty_history():
    r = ds.test_run_diff()
    assert r["ok"] is True
    # in a fresh test process the runner has no history
    assert r["run_count"] == 0
    assert r["latest_diff"] is None


def test_run_diff_summarizes_history(monkeypatch):
    fake = [
        {"run_id": "newest01", "target": "all", "state": "done",
         "returncode": 0, "started": 100.0, "finished": 142.0},
        {"run_id": "older002", "target": "all", "state": "failed",
         "returncode": 1, "started": 10.0, "finished": 70.0},
    ]
    monkeypatch.setattr(dev_tools, "recent_runs",
                        lambda **k: fake)
    r = ds.test_run_diff()
    assert r["run_count"] == 2
    assert r["runs"][0]["duration_seconds"] == 42.0
    d = r["latest_diff"]
    assert d["state_changed"] is True
    assert d["newest_state"] == "done"
    assert d["duration_delta_seconds"] == -18.0  # 42 - 60


def test_run_diff_clamps_limit(monkeypatch):
    captured = {}

    def _spy(**k):
        captured["limit"] = k.get("limit")
        return []

    monkeypatch.setattr(dev_tools, "recent_runs", _spy)
    ds.test_run_diff(limit=1)       # below floor -> 2
    assert captured["limit"] == 2
    ds.test_run_diff(limit=99999)   # above ceiling -> 50
    assert captured["limit"] == 50


# ── routes ─────────────────────────────────────────────────────────

def test_guard_status_route(fresh_app):
    # no artifact in the test cwd -> a clean error, not a crash
    body = fresh_app.get("/api/dev/guard_status").get_json()
    assert "ok" in body


def test_coverage_map_route(fresh_app):
    body = fresh_app.get("/api/dev/coverage_map").get_json()
    assert body["ok"] is True
    assert "covered_modules" in body


def test_run_diff_route(fresh_app):
    body = fresh_app.get("/api/dev/test_run_diff").get_json()
    assert body["ok"] is True
    assert "runs" in body
