"""Row 529: review coverage is reconciled against the Git diff denominator."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_TOOL = _REPO / "toolchain" / "bin" / "bd-review-reconcile"
_OMITTED = "tests/test_row097_real_apt_sandbox.py"
_REVIEWED = "bulk_downloader/example.py"


def _git(work: Path, *args: str) -> str:
    run = subprocess.run(
        ["git", "-C", str(work), *args], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    return run.stdout.strip()


def _changed_repo(tmp_path: Path) -> tuple[Path, str]:
    work = tmp_path / "work"
    (work / "tests").mkdir(parents=True)
    (work / "bulk_downloader").mkdir()
    (work / _OMITTED).write_text("original apt test\n", encoding="utf-8")
    (work / _REVIEWED).write_text("original source\n", encoding="utf-8")
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "row529@example.invalid")
    _git(work, "config", "user.name", "Row 529")
    _git(work, "add", "--", _OMITTED, _REVIEWED)
    _git(work, "commit", "-qm", "base")
    base = _git(work, "rev-parse", "HEAD")
    (work / _OMITTED).write_text("changed apt test\n", encoding="utf-8")
    (work / _REVIEWED).write_text("changed source\n", encoding="utf-8")
    changed = _git(work, "diff", "--name-only", base, "--").splitlines()
    assert changed == [_REVIEWED, _OMITTED]
    assert len(changed) == 2, "changed-file denominator is zero or drifted"
    return work, base


def _run(work: Path, base: str, tmp_path: Path, reviews: list[dict]):
    evidence = tmp_path / "coverage.json"
    evidence.write_text(json.dumps({"reviews": reviews}), encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(_TOOL), "--work", str(work), "--base", base,
         "--coverage", str(evidence), "--json"],
        cwd=_REPO, capture_output=True, text=True)
    start = run.stdout.find("{")
    assert start >= 0, "review reconciler produced no JSON: " + run.stderr
    return run, json.loads(run.stdout[start:])


def _review(reader: str, verdict: str, path: str, method: str) -> dict:
    return {
        "reader": reader,
        "verdict": verdict,
        "files": [{"path": path, "method": method}],
    }


def test_review_omission_names_the_changed_file_instead_of_reporting_clean(
        tmp_path):
    work, base = _changed_repo(tmp_path)
    run, result = _run(
        work, base, tmp_path,
        [_review("implementation-reader", "PASS", _REVIEWED, "read")])

    assert run.returncode == 1, run.stdout + run.stderr
    assert result["verdict"] == "FAIL"
    assert result["changed_count"] == 2
    assert result["examined_count"] == 1
    assert result["gaps"] == [{"path": _OMITTED, "reason": "no_reader"}]


def test_text_search_is_recorded_but_does_not_count_as_read(tmp_path):
    work, base = _changed_repo(tmp_path)
    reviews = [
        _review("implementation-reader", "PASS", _REVIEWED, "read"),
        _review("grep-reader", "PASS", _OMITTED, "grep"),
    ]
    run, result = _run(work, base, tmp_path, reviews)

    assert run.returncode == 1, run.stdout + run.stderr
    assert result["changed_count"] == 2
    assert result["examined_count"] == 1
    assert result["gaps"] == [{"path": _OMITTED, "reason": "search_only"}]
    omitted = next(row for row in result["files"] if row["path"] == _OMITTED)
    assert omitted["evidence"] == [{
        "reader": "grep-reader", "verdict": "PASS", "method": "grep"}]


def test_complete_review_has_no_gaps_and_a_nonzero_examined_count(tmp_path):
    work, base = _changed_repo(tmp_path)
    reviews = [
        _review("implementation-reader", "PASS", _REVIEWED, "read"),
        _review("test-reader", "PASS", _OMITTED, "read"),
    ]
    run, result = _run(work, base, tmp_path, reviews)

    assert run.returncode == 0, run.stdout + run.stderr
    assert result["verdict"] == "PASS"
    assert result["changed_count"] == 2
    assert result["examined_count"] == 2
    assert result["gaps"] == []


def test_unavailable_diff_is_unknown_not_a_zero_denominator_pass(tmp_path):
    work, _base = _changed_repo(tmp_path)
    run, result = _run(
        work, "missing-base", tmp_path,
        [_review("test-reader", "PASS", _OMITTED, "read")])

    assert run.returncode == 2, run.stdout + run.stderr
    assert result["verdict"] == "UNKNOWN"
    assert result["changed_count"] is None
    assert result["examined_count"] == 0
    assert result["gaps"] == []


def test_zero_changed_file_denominator_is_unknown_not_pass(tmp_path):
    work, base = _changed_repo(tmp_path)
    (work / _OMITTED).write_text("original apt test\n", encoding="utf-8")
    (work / _REVIEWED).write_text("original source\n", encoding="utf-8")
    changed = _git(work, "diff", "--name-only", base, "--").splitlines()
    assert changed == [], "fixture must present an exact zero-file denominator"

    run, result = _run(work, base, tmp_path, [])

    assert run.returncode == 2, run.stdout + run.stderr
    assert result["verdict"] == "UNKNOWN"
    assert result["reason"] == "changed-file denominator is zero"
    assert result["changed_count"] is None
    assert result["examined_count"] == 0
    assert result["gaps"] == []


def test_transform_control_only_runs_help():
    """Import/argument parsing alone does not judge review reconciliation."""
    run = subprocess.run(
        [sys.executable, str(_TOOL), "--help"],
        cwd=_REPO, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "--coverage" in run.stdout
