"""A test that exceeds its bound must fail BY NAME, not by killing its worker.

WHY THIS IS BEHAVIOURAL AND NOT A TEXT PIN. The property is about what pytest
DOES when a bound fires, and the two ways it can respond are separated by one
flag whose effect is invisible to any grep. `--timeout-method=thread` ends in
`os._exit(1)` from a timer thread: no exception, no report, no unwinding, and
the worker is gone. `--timeout-method=signal` raises inside the test's own
thread and the failure is reported like any other.

THE MEASUREMENT THAT SETTLED IT, 2026-08-24. CLAUDE.md justified `thread` with
"thread mode exposes stacks". Under xdist it exposes nothing: pytest-timeout
writes its `+++ Timeout +++` banner and every thread's stack to
`item.config.get_terminal_writer()`, which is the worker's `sys.stdout`, and
execnet points every worker's fd 1 at /dev/null. Measured with a positive
control: 0 banners under `-n 2`, and 2 banners with 34 stack lines from the
identical subject run SERIALLY. So the flag's stated benefit was zero in the one
configuration the sanctioned command uses, while its cost was a dead worker --
and a dead worker during the drain livelocks the whole session (row 145, and
`upstream/xdist-drain-livelock/README.md`, where a master once span 11.6 hours).

WHAT `--max-worker-restart=0` ADDS, and why signal alone is not enough. Signal
removes TIMEOUTS as a cause of worker death. It cannot remove a segfault, an
OOM kill, or an `os._exit` inside test code. Row 145 measured that the livelock
does not reproduce with the restart cap at zero, because `worker_errordown` then
calls `triggershutdown()` directly instead of cloning a replacement and waiting
for a `tests_finished` that never becomes true. The run is lost either way; the
difference is a loud abort in seconds against a silent hang for hours.

THE ARMS BELOW ARE THE EXPERIMENT, RE-RUN. Arm A is the sanctioned shape and
must name the test with no worker death. Arm B is the same subject under
`thread`, and must show the opposite -- a crashed worker and zero banners. B is
the negative control: without it, A passing would prove only that nothing timed
out, which is exactly the vacuous green this repository keeps finding.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]

_SUBJECT = (
    "import time\n"
    "\n"
    "\n"
    "def test_a_fast_neighbour():\n"
    "    assert True\n"
    "\n"
    "\n"
    "def test_the_slow_one():\n"
    "    time.sleep(60)\n"
)

# Small enough that both arms are seconds, large enough that a loaded host
# cannot cross it by accident. The subject sleeps 60s against it.
_INNER_TIMEOUT_S = 5
# Strictly above the inner bound plus interpreter start-up, so THIS test's own
# subprocess never becomes the very defect it is testing for.
_OUTER_BUDGET_S = 120


def _run_arm(method: str, restart_cap: str | None) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "tests").mkdir()
        (root / "tests" / "test_subject.py").write_text(_SUBJECT, encoding="utf-8")
        argv = [sys.executable, "-m", "pytest", "tests/", "-n", "2",
                "--dist", "loadfile", "--timeout=%d" % _INNER_TIMEOUT_S,
                "--timeout-method=%s" % method, "-p", "no:randomly"]
        if restart_cap is not None:
            argv.append("--max-worker-restart=%s" % restart_cap)
        return subprocess.run(argv, capture_output=True, text=True,
                              cwd=str(root), timeout=_OUTER_BUDGET_S)


def _crashed_workers(out: str) -> int:
    return len(re.findall(r"crashed while running", out))


def test_the_sanctioned_method_names_the_test_and_keeps_the_worker():
    """ARM A -- the shape CLAUDE.md's sanctioned command now uses."""
    r = _run_arm("signal", "0")
    blob = r.stdout + r.stderr

    assert "Failed: Timeout" in blob, (
        "a test that ran 60s against a %ds bound did not produce a NAMED "
        "pytest-timeout failure, so the bound either did not fire or did not "
        "report:\n%s" % (_INNER_TIMEOUT_S, blob[-2000:]))
    assert "test_the_slow_one" in blob, (
        "the failure did not name the offending test, which is the whole "
        "property:\n%s" % blob[-2000:])
    assert _crashed_workers(blob) == 0, (
        "a worker was crashed and replaced under the sanctioned method; the "
        "timeout is supposed to be an ordinary failure now:\n%s" % blob[-2000:])
    assert "test_a_fast_neighbour" not in _failed_names(blob), (
        "the innocent neighbour was reported failed, so the bound is taking "
        "down more than its own test")


def _failed_names(blob: str) -> str:
    tail = blob.split("short test summary info")[-1] if "short test summary info" in blob else ""
    return tail


def test_the_old_method_still_kills_its_worker_and_still_says_nothing():
    """ARM B -- THE NEGATIVE CONTROL, and the recorded reason for the change.

    If this ever starts passing arm A's assertions, then pytest-timeout or xdist
    changed and this gate's premise needs re-deriving -- do not delete it.
    """
    r = _run_arm("thread", "1")
    blob = r.stdout + r.stderr

    assert _crashed_workers(blob) >= 1, (
        "the thread method no longer crashes its worker. That would be good "
        "news, but it means this gate's premise is stale: re-measure before "
        "trusting either arm:\n%s" % blob[-2000:])
    assert "+++ Timeout" not in blob, (
        "the thread method's stack dump REACHED the log under xdist. That "
        "contradicts the measurement this change was made on (0 banners under "
        "-n 2 against 2 serially) and means execnet stopped pointing worker "
        "stdout at /dev/null. Re-derive before relying on either arm.")
    assert "Failed: Timeout" not in blob, (
        "the thread method produced a NAMED timeout failure, which it cannot "
        "do -- os._exit(1) leaves no report. Premise stale.")


def test_both_arms_ran_the_same_subject_so_the_comparison_is_honest():
    """PRECONDITION. Two arms are evidence only if they differ in one thing."""
    assert "time.sleep(60)" in _SUBJECT
    assert _INNER_TIMEOUT_S < 60, "the subject must outlive the bound"
    assert _OUTER_BUDGET_S > _INNER_TIMEOUT_S * 4, (
        "this gate's own subprocess budget must clear the inner bound by a wide "
        "margin, or it reproduces the very defect it tests for")
