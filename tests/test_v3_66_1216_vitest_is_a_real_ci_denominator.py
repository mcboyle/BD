"""The frontend suite is in CI, and its denominator cannot silently collapse.

WHAT WAS TRUE BEFORE THIS CUT. The repository tracked 122 frontend spec files
containing 499 tests, all green in 14 seconds, and `.github/workflows/ci.yml`
contained no `setup-node`, no `npm ci` and no `vitest` -- zero hits. They were
not UNRUN (vitest.config.ts calls itself "a sandbox-cut gate only" and
bd-cut:368 runs it before a deploy); they were UN-CI'd, which means no pull
request had ever been judged by them. That is the "a gate CI does not run does
not exist" class at a scale of 499.

WHY WIRING IT IN IS NOT ENOUGH, AND WHY THIS FILE IS MOSTLY ABOUT THE CHECKER.
`vitest run` EXITS 0 WHEN IT COLLECTS NOTHING. An `include` pattern that stops
matching, a moved directory, a renamed extension -- each produces a PASSING CI
job over an empty denominator. Adding the job alone would have bought the
appearance of 499 tests and the reality of whatever survived, which is worse
than not adding it, because it also buys confidence.

So the job runs `--reporter=json` and a tracked checker reconciles the files
vitest reported against `git ls-files`. This file tests THE CHECKER, against
fixtures that encode each way the denominator can lie. A checker nobody tests is
just a longer way to write `|| true`.

THE FILE COUNT IS RECONCILED, NOT PINNED, so it cannot go stale as specs are
added; the TEST count gets a floor, because tests are written constantly and an
exact pin would fail every cut that adds one. Those are different questions and
they get different instruments on purpose.

A NOTE ON THE GLOB, because it already cost one wrong measurement. Git's
`src/**/*.test.ts` requires at least one intermediate directory, so specs
sitting directly in `src/` are excluded by it. The first count read 120 against
122 real files and looked authoritative. Both the bare and the nested shapes are
required. CLAUDE.md A1: a glob is a denominator choice.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

BD_GATE_SCOPE = "module"

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools" / "check_vitest_denominator.py"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _load_checker():
    spec = importlib.util.spec_from_file_location("bd_vitest_check", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tracked_specs() -> list[str]:
    mod = _load_checker()
    return sorted(mod.tracked_specs(ROOT))


def _report(files, total, failed=0) -> dict:
    return {
        "testResults": [{"name": str(ROOT / f)} for f in files],
        "numTotalTests": total,
        "numFailedTests": failed,
        "success": failed == 0,
    }


def _run(tmp_path, report) -> tuple[int, str]:
    path = tmp_path / "vitest.json"
    if report is not None:
        path.write_text(json.dumps(report), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(CHECKER), str(path), "--root", str(ROOT)],
        capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout + proc.stderr


def test_the_repository_really_has_a_frontend_suite_to_gate():
    """PRECONDITION. Every assertion below is vacuous if the suite is empty, so
    the denominator is established first and required to be substantial."""
    tracked = _tracked_specs()
    assert len(tracked) >= 100, (
        "the tracked frontend spec population collapsed to %d files; either the "
        "globs stopped matching or the suite was deleted, and both are findings "
        "rather than a reason to relax this gate" % len(tracked))
    assert all(f.startswith("frontend/src/") for f in tracked), tracked[:5]


def test_the_glob_sees_specs_that_sit_directly_in_src():
    """THE MEASUREMENT BUG THAT ALMOST BECAME THE GATE'S FOUNDATION.

    `frontend/src/**/*.test.ts` requires an intermediate directory. With only
    that pattern the count read 120 against 122 real files, and nothing about it
    looked wrong. If a future edit drops the bare `src/*` shapes, this fails.
    """
    tracked = _tracked_specs()
    top_level = [f for f in tracked
                 if f.count("/") == 2 and f.startswith("frontend/src/")]
    assert top_level, (
        "no spec files directly under frontend/src/ are being counted. Either "
        "they were all moved into subdirectories, or the glob lost its bare "
        "`frontend/src/*.test.ts` shapes and the denominator is now short.")


def test_a_full_report_passes(tmp_path):
    tracked = _tracked_specs()
    rc, out = _run(tmp_path, _report(tracked, 499))
    assert rc == 0, out
    assert "VITEST-DENOMINATOR-OK" in out, out


def test_an_empty_collection_fails_even_though_vitest_would_exit_zero(tmp_path):
    """THE CENTRAL CASE. This is the state `vitest run` reports success for."""
    rc, out = _run(tmp_path, _report([], 0))
    assert rc == 1, out
    assert "VITEST-DENOMINATOR-FAIL" in out, out
    assert "were NOT run" in out, out


def test_a_single_dropped_file_is_named(tmp_path):
    """A partial collapse is the realistic failure, and it must name the file
    rather than only reporting a count -- a count tells you something moved, a
    name tells you what."""
    tracked = _tracked_specs()
    dropped = tracked[-1]
    rc, out = _run(tmp_path, _report(tracked[:-1], 495))
    assert rc == 1, out
    assert pathlib.Path(dropped).name in out, out


def test_a_test_count_collapse_fails_even_with_every_file_present(tmp_path):
    """Files can all load while their tests vanish -- a broken setupFile, or a
    describe block that stops registering. The file check alone would pass."""
    tracked = _tracked_specs()
    rc, out = _run(tmp_path, _report(tracked, 3))
    assert rc == 1, out
    assert "below the floor" in out, out


def test_a_failing_test_fails_the_gate(tmp_path):
    tracked = _tracked_specs()
    rc, out = _run(tmp_path, _report(tracked, 499, failed=2))
    assert rc == 1, out
    assert "numFailedTests=2" in out, out


def test_a_missing_or_unreadable_report_is_unknown_not_a_pass(tmp_path):
    """FAIL-CLOSED, WITH ITS OWN EXIT CODE. If the vitest step dies before
    writing the report, the checker must not read the absence as an empty
    collection or -- worse -- as nothing to complain about."""
    rc, out = _run(tmp_path, None)
    assert rc == 2, out
    assert "UNKNOWN" in out, out

    broken = tmp_path / "vitest.json"
    broken.write_text("{not json", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(CHECKER), str(broken), "--root", str(ROOT)],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2, proc.stdout
    assert "UNKNOWN" in proc.stdout, proc.stdout


def test_ci_actually_runs_vitest_and_then_checks_the_denominator():
    """The job must exist, install node, run vitest, AND run the checker.

    This is a structural read of the workflow rather than a substring hunt: the
    steps are parsed and their order asserted, because a checker that runs
    BEFORE vitest would pass on a stale report and a vitest step with no checker
    after it is the empty-denominator hole this cut exists to close.
    """
    import yaml
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    assert "frontend-vitest" in workflow["jobs"], sorted(workflow["jobs"])
    steps = workflow["jobs"]["frontend-vitest"]["steps"]

    def index_of(pred):
        for i, step in enumerate(steps):
            blob = " ".join(str(v) for v in step.values())
            if pred(blob):
                return i
        return -1

    node = index_of(lambda b: "setup-node" in b)
    install = index_of(lambda b: "npm ci" in b)
    run = index_of(lambda b: "vitest run" in b)
    check = index_of(lambda b: "check_vitest_denominator.py" in b)

    assert node >= 0, "no setup-node step; npx vitest would not exist"
    assert install >= 0, "no npm ci step"
    assert run >= 0, "the job never runs vitest"
    assert check >= 0, (
        "vitest runs with NO denominator check after it, so an empty collection "
        "would exit 0 and this job would go green over nothing")
    assert node < install < run < check, (
        "the frontend-vitest steps are out of order (node=%d install=%d run=%d "
        "check=%d); the checker must run AFTER vitest or it judges a stale or "
        "absent report" % (node, install, run, check))

    run_step = " ".join(str(v) for v in steps[run].values())
    assert "--reporter=json" in run_step and "--outputFile" in run_step, (
        "vitest must emit the JSON report the checker reconciles; without it "
        "the checker can only ever report UNKNOWN: %r" % run_step)
