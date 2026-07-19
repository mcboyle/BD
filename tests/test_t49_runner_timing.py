"""T49 — run_tests.py per-test timing artifact.

Verifies that running the runner against a tiny test file produces
a JSON artifact with schema_version >= 2 and per-test
duration_seconds entries, and that the SUMMARY.txt tail includes a
SLOWEST section.

This is the prerequisite artifact for the T50 /api/dev/test_timing
dev tool. Without these guarantees, T50 has nothing to read.

Implementation detail: runs run_tests.py in a subprocess pointed at
a generated tiny test file in a tmpdir. We don't recurse on the
real runner inside its own pytest collection — that's a footgun
and would also be slow.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_RUN_TESTS = _REPO_ROOT / "run_tests.py"


# ── Helper ──────────────────────────────────────────────────────────

def _run_runner_on_tiny_file(tmp_path):
    """Invoke run_tests.py against a freshly written tiny test file
    inside tmp_path. Returns (returncode, json_dict, summary_text).
    """
    test_file = tmp_path / "tests" / "test_t49_tiny.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    # Two tests: one instant-passing, one with a small measurable
    # sleep so a non-zero duration is observable. Keeps the run
    # short (under 0.2s wall) so it doesn't bloat the gate run.
    test_file.write_text(
        "import time\n"
        "def test_t49_instant():\n"
        "    assert True\n"
        "def test_t49_slept():\n"
        "    time.sleep(0.05)\n"
        "    assert True\n",
        encoding="utf-8",
    )

    json_path = tmp_path / "t49.json"
    summary_path = tmp_path / "t49.txt"

    env = dict(os.environ)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    # Run from the real repo root so the runner discovers its own
    # tests dir machinery, but point it at our temp file explicitly.
    cp = subprocess.run(
        [sys.executable, str(_RUN_TESTS),
         str(test_file),
         f"--json={json_path}",
         f"--summary={summary_path}"],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True, text=True, timeout=120,
    )
    data = json.loads(json_path.read_text(encoding="utf-8"))
    summary = summary_path.read_text(encoding="utf-8")
    return cp.returncode, data, summary


# ── Tests ───────────────────────────────────────────────────────────

def test_json_has_schema_version_at_least_2(tmp_path):
    """T49 bumped the JSON schema to 2 when adding per-test
    durations and the full `tests` list."""
    rc, data, _summary = _run_runner_on_tiny_file(tmp_path)
    assert rc == 0, f"runner failed: rc={rc}"
    sv = data.get("schema_version")
    assert isinstance(sv, int) and sv >= 2, (
        f"schema_version must be >= 2 (got {sv!r}); without this "
        "the T50 dev tool cannot detect old artifacts")


def test_json_has_tests_list_with_duration_seconds(tmp_path):
    """The full per-test list is the new payload T50 reads."""
    rc, data, _summary = _run_runner_on_tiny_file(tmp_path)
    assert rc == 0
    tests = data.get("tests")
    assert isinstance(tests, list) and len(tests) == 2, (
        f"expected 2 test records, got {tests!r}")
    for rec in tests:
        assert "test" in rec
        assert "duration_seconds" in rec
        assert isinstance(rec["duration_seconds"], (int, float))
        assert rec["duration_seconds"] >= 0.0
        assert rec.get("status") in ("pass", "skip", "fail")


def test_slept_test_has_nonzero_duration(tmp_path):
    """A test that sleeps 50ms must record a duration above ~0.04s.
    Catches a regression that timed everything as zero (e.g. timer
    placed outside the actual call site)."""
    rc, data, _summary = _run_runner_on_tiny_file(tmp_path)
    assert rc == 0
    by_name = {r["test"]: r for r in data["tests"]}
    slept = by_name.get("test_t49_slept")
    assert slept is not None, f"could not find slept test in {by_name!r}"
    assert slept["duration_seconds"] >= 0.04, (
        f"slept test should have measurable duration; got "
        f"{slept['duration_seconds']}")


def test_summary_text_includes_slowest_section(tmp_path):
    """The Slowest-N tail block must be present when --summary is
    used. Top-of-file format is unchanged (still starts with
    'BulkDownloader test summary')."""
    rc, _data, summary = _run_runner_on_tiny_file(tmp_path)
    assert rc == 0
    assert summary.startswith("BulkDownloader test summary"), (
        "top-of-summary format must stay stable for existing "
        "consumers")
    assert "SLOWEST" in summary, (
        f"SLOWEST tail missing from SUMMARY.txt:\n{summary}")
