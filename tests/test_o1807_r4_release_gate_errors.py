"""O1807 R4 (O1778 finding #18): an errored test file must fail the release gate.

A runner process that exits non-zero after printing a recognized summary with
``Failed: 0`` and ``Total`` above zero landed in no bucket: not a real failure,
not a harness failure, not unmeasured.  The release gate reported it green.
The runner's summary has no Errors field, so the exit code is the only signal
that something outside the counted tests went wrong (a teardown crash, a
cleanup error, an interpreter abort after the summary line).

Every case drives the real ``_run_one`` subprocess path.  A throwaway
``run_tests.py`` in a fixture root prints a fixed summary and exits with a
fixed code.  Only the structural sibling gates are stubbed, because they are
not the subject.

Controls:
  * a counted failure (Failed: 1, rc=1) is a real failure on base and fix;
  * a clean run (Failed: 0, rc=0) passes on base and fix;
  * an errored run carrying a GTK/DISPLAY signature stays HARNESS/ENV.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_TARGET = "tests/test_selected.py"
_SUMMARY = "  Total: 3 | Passed: {passed} | Failed: {failed} | Skipped: 0"


def _load_verify_release():
    spec = importlib.util.spec_from_file_location(
        "o1807_r4_verify_release", _REPO / "tools" / "verify_release.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VR = _load_verify_release()


def _fixture_root(root: Path, *, stdout: str, rc: int,
                  sleep_s: int = 0) -> None:
    package = root / "bulk_downloader"
    package.mkdir()
    (package / "__init__.py").write_text(
        '__version__ = "3.66.1313"\n', encoding="ascii")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_selected.py").write_text(
        "# selected runner target\n", encoding="ascii")
    # A negative rc is a signal death: the runner kills itself with -rc.
    exit_line = (f"sys.stdout.flush()\nos.kill(os.getpid(), {-rc})\n"
                 if rc < 0 else f"sys.exit({rc})\n")
    (root / "run_tests.py").write_text(
        "import os, sys, time\n"
        f"sys.stdout.write({stdout!r})\n"
        f"time.sleep({sleep_s})\n"
        + exit_line,
        encoding="ascii")


def _gate(root: Path, monkeypatch, capsys, *, stdout: str, rc: int,
          parsed: bool = True, sleep_s: int = 0):
    _fixture_root(root, stdout=stdout, rc=0 if sleep_s else rc,
                  sleep_s=sleep_s)
    siblings = (SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    monkeypatch.setattr(VR, "_import_siblings", lambda _root: siblings)
    monkeypatch.setattr(VR, "check_version", lambda *_a: (True, ["v"]))
    monkeypatch.setattr(VR, "check_docs", lambda *_a: (True, ["d"]))
    monkeypatch.setattr(VR, "check_templates", lambda *_a: (True, ["t"]))
    exit_code = VR.main(["--root", str(root), "--tests", "full", "--json"])
    payload = json.loads(capsys.readouterr().out)
    tests = payload["tests"]
    assert tests["files"] == 1
    row = tests["results"][0]
    assert row["file"] == _TARGET
    assert row["rc"] == rc
    assert row["summary_parsed"] is parsed
    return exit_code, payload["gates"]["tests:full"], tests


def _files(rows):
    return [r["file"] for r in rows]


def test_errored_file_with_zero_counted_failures_fails_the_gate(
        tmp_path, monkeypatch, capsys):
    stdout = _SUMMARY.format(passed=3, failed=0) + "\nTraceback: teardown\n"
    exit_code, gate_ok, tests = _gate(
        tmp_path, monkeypatch, capsys, stdout=stdout, rc=1)
    assert tests["results"][0]["total"] == 3
    assert tests["results"][0]["failed"] == 0
    assert _files(tests["real_failures"]) == [_TARGET], (
        "ERRORED-FILE-PASSED-RELEASE-GATE: rc=1 total=3 failed=0 "
        f"landed in no bucket: real={_files(tests['real_failures'])} "
        f"harness={_files(tests['harness_failures'])}")
    assert gate_ok is False
    assert exit_code == 1


def test_unevaluable_zero_total_run_fails_the_gate(
        tmp_path, monkeypatch, capsys):
    stdout = ("  Total: 0 | Passed: 0 | Failed: 0 | Skipped: 0\n"
              "  BD-RUNNER UNEVALUABLE: 1 file(s) requested, ZERO tests "
              "collected.\n")
    exit_code, gate_ok, tests = _gate(
        tmp_path, monkeypatch, capsys, stdout=stdout, rc=2)
    assert tests["results"][0]["total"] == 0
    assert _files(tests["real_failures"]) == [_TARGET], (
        "UNEVALUABLE-RUN-PASSED-RELEASE-GATE: rc=2 total=0 "
        f"landed in no bucket: real={_files(tests['real_failures'])} "
        f"harness={_files(tests['harness_failures'])}")
    assert gate_ok is False
    assert exit_code == 1


def test_nonzero_exit_without_summary_fails_the_gate(
        tmp_path, monkeypatch, capsys):
    stdout = "Traceback: ImportError before the summary line\n"
    exit_code, gate_ok, tests = _gate(
        tmp_path, monkeypatch, capsys, stdout=stdout, rc=1, parsed=False)
    assert tests["results"][0]["total"] is None
    assert _files(tests["real_failures"]) == [_TARGET], (
        "UNSUMMARIZED-CRASH-PASSED-RELEASE-GATE: rc=1 no summary "
        f"landed in no bucket: real={_files(tests['real_failures'])} "
        f"unmeasured={_files(tests['unmeasured'])}")
    assert gate_ok is False
    assert exit_code == 1


def test_timed_out_file_fails_the_gate(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(VR, "_STANDARD_TEST_FILE_TIMEOUT_S", 1)
    exit_code, gate_ok, tests = _gate(
        tmp_path, monkeypatch, capsys, stdout="", rc=124, parsed=False,
        sleep_s=30)
    assert tests["results"][0]["timeout"] is True
    assert tests["timeouts"] == [_TARGET]
    assert _files(tests["real_failures"]) == [_TARGET], (
        "TIMED-OUT-FILE-PASSED-RELEASE-GATE: rc=124 "
        f"landed in no bucket: real={_files(tests['real_failures'])} "
        f"unmeasured={_files(tests['unmeasured'])}")
    assert gate_ok is False
    assert exit_code == 1


def test_rc3_after_clean_summary_fails_the_gate(
        tmp_path, monkeypatch, capsys):
    exit_code, gate_ok, tests = _gate(
        tmp_path, monkeypatch, capsys,
        stdout=_SUMMARY.format(passed=3, failed=0) + "\n", rc=3)
    assert tests["results"][0]["failed"] == 0
    assert _files(tests["real_failures"]) == [_TARGET], (
        "RC3-FILE-PASSED-RELEASE-GATE: rc=3 total=3 failed=0 "
        f"landed in no bucket: real={_files(tests['real_failures'])} "
        f"harness={_files(tests['harness_failures'])}")
    assert gate_ok is False
    assert exit_code == 1


def test_signal_killed_file_after_clean_summary_fails_the_gate(
        tmp_path, monkeypatch, capsys):
    exit_code, gate_ok, tests = _gate(
        tmp_path, monkeypatch, capsys,
        stdout=_SUMMARY.format(passed=3, failed=0) + "\n", rc=-9)
    assert tests["results"][0]["total"] == 3
    assert tests["results"][0]["failed"] == 0
    assert _files(tests["real_failures"]) == [_TARGET], (
        "SIGKILLED-FILE-PASSED-RELEASE-GATE: rc=-9 total=3 failed=0 "
        f"landed in no bucket: real={_files(tests['real_failures'])} "
        f"harness={_files(tests['harness_failures'])}")
    assert gate_ok is False
    assert exit_code == 1


def test_control_counted_failure_is_a_real_failure(
        tmp_path, monkeypatch, capsys):
    exit_code, gate_ok, tests = _gate(
        tmp_path, monkeypatch, capsys,
        stdout=_SUMMARY.format(passed=2, failed=1) + "\n", rc=1)
    assert _files(tests["real_failures"]) == [_TARGET]
    assert gate_ok is False
    assert exit_code == 1


def test_control_clean_run_passes_the_gate(tmp_path, monkeypatch, capsys):
    exit_code, gate_ok, tests = _gate(
        tmp_path, monkeypatch, capsys,
        stdout=_SUMMARY.format(passed=3, failed=0) + "\n", rc=0)
    assert tests["real_failures"] == []
    assert tests["harness_failures"] == []
    assert tests["unmeasured"] == []
    assert gate_ok is True
    assert exit_code == 0


def test_errored_file_with_display_signature_is_harness_env(
        tmp_path, monkeypatch, capsys):
    stdout = _SUMMARY.format(passed=3, failed=0) + "\nBad display name\n"
    _exit_code, _gate_ok, tests = _gate(
        tmp_path, monkeypatch, capsys, stdout=stdout, rc=1)
    assert _files(tests["harness_failures"]) == [_TARGET]
    assert tests["real_failures"] == []
