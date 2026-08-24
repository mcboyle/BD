"""A capture stage that wedges is STOPPED and NAMED, not left running forever.

BACKLOG 102, THE OPERATIONAL HALF. capture.sh's `run_with_heartbeat` polled
`while kill -0 "$pid"` with NO time bound, so a pytest master that wedges hangs
the capture indefinitely. The row's own warning is why that matters: A
NO-VERDICT RUN IS NOT A GREEN ONE -- a log with no pytest summary line contains
no occurrence of the word `failed` and reads as clean to anything scanning for
it.

THE WEDGE IS MEASURED, NOT HYPOTHESISED. Caught 2026-08-13 at v3.66.1107 on
test6, run test6-full-005-190725: a full suite at `-n 48` printed
`[gw28] node down: Not properly terminated` at 99% and then wrote nothing for
726 seconds at a 1-minute load average of 0.27. py-spy on the master showed the
MainThread in xdist's `dsession.loop_once` at `queue.get`, 48 receiver threads
idle in `execnet read`, and ONE unreaped zombie child. xdist's loop exits only
when `self._active_nodes` empties, so it span every 2s forever. Left alone it
would still be running.

WHY A PER-TEST TIMEOUT CANNOT COVER THIS, which is the whole argument for a
second bound. `--timeout=240 --timeout-method=signal` runs INSIDE the worker.
When the worker is the thing that died there is nothing left to fire it -- and
capture.sh's lanes do not pass it at all. The bound has to live in the parent,
outside the process it bounds, because a limit that shares a fate with its
subject is not a limit.

WHY THE FUNCTION MOVED TO scripts/lib/. It was inline in capture.sh, so the
only thing a test could do was grep capture.sh for the string
`run_with_heartbeat` -- which three tests do. A source check cannot tell a
bound that FIRES from a bound that is merely written down. Same move as
scripts/lib/tree_state.sh (@1092) and scripts/lib/capture_run_dir.sh (@1099).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

# Its subject is one library file and capture.sh's wiring to it, not an
# invariant over the tree.
BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_LIB = _REPO / "scripts" / "lib" / "heartbeat.sh"


def _run(body: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Source the library and run BODY, returning the completed process.

    Sourcing the real file rather than a copy is the point: a test against an
    extracted duplicate certifies the duplicate.
    """
    script = ". %s\n%s\n" % (str(_LIB), body)
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=timeout)


def test_the_library_exists_and_parses():
    """Precondition. If bash cannot parse it, every assertion below fails for
    that reason rather than for the behaviour under test."""
    assert _LIB.is_file(), f"{_LIB} is missing"
    r = subprocess.run(["bash", "-n", str(_LIB)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_a_wedged_stage_is_stopped_and_returns_124(tmp_path):
    """THE DEFECT. Without the cap this command runs forever and the test hangs
    rather than fails -- which is exactly what the capture did."""
    log = tmp_path / "stage.log"
    started = time.time()
    r = _run(
        'CAPTURE_STAGE_CAP=5\n'
        'run_with_heartbeat "wedged stage" "%s" sleep 600\n'
        'echo "RC=$?"' % log, timeout=90)
    elapsed = time.time() - started

    assert "RC=124" in r.stdout, (
        f"a stage that outran its cap did not return 124:\n{r.stdout}\n{r.stderr}")
    assert elapsed < 60, (
        f"the cap was set to 5s and the stage took {elapsed:.0f}s to stop; the "
        "bound is being checked too rarely to be a bound")


def test_the_cap_says_so_in_the_stage_log_not_only_on_stdout(tmp_path):
    """The archive is what survives. A reader of 02_pytest_parallel.log must
    not have to INFER from a missing summary that the run was stopped -- that
    inference is the failure mode the row is about."""
    log = tmp_path / "stage.log"
    _run('CAPTURE_STAGE_CAP=5\n'
         'run_with_heartbeat "wedged stage" "%s" sleep 600' % log, timeout=90)
    text = log.read_text(encoding="utf-8", errors="replace")
    assert "CAPTURE-STAGE-CAP" in text, (
        f"the stage log does not record that the stage was capped:\n{text[:400]}")
    assert "124" in text, "the log does not name the exit code the caller sees"


def test_the_child_is_actually_dead_afterwards(tmp_path):
    """A bound that returns 124 while leaving the process running is WORSE than
    no bound: the caller believes it stopped, and the machine keeps the load.

    Matched on `comm` plus a unique marker rather than on a command-line
    pattern, per CLAUDE.md section 5 -- a `pgrep -f` here would match this
    test's own bash and report a phantom survivor.
    """
    log = tmp_path / "stage.log"
    pidfile = tmp_path / "child.pid"
    r = _run(
        'CAPTURE_STAGE_CAP=5\n'
        'run_with_heartbeat "wedged" "%s" bash -c \'echo $$ > "%s"; sleep 600\'\n'
        'echo "RC=$?"\n'
        'sleep 2\n'
        'CHILD=$(cat "%s" 2>/dev/null || echo 0)\n'
        'echo "CHILDPID=$CHILD"\n'
        'if kill -0 "$CHILD" 2>/dev/null; then echo "ALIVE=yes"; else echo "ALIVE=no"; fi'
        % (log, pidfile, pidfile), timeout=90)

    # PRECONDITION FIRST. Without it "ALIVE=no" is satisfied by a child that
    # never started, and the assertion passes for the wrong reason -- which is
    # exactly what the first version of this test did: it matched `comm`
    # against an argv[0] set with `exec -a`, so the pattern could never match
    # and the test was vacuous. A mutant deleting the kill escaped it.
    assert pidfile.is_file(), (
        f"the child never recorded a pid, so this test proves nothing:\n{r.stdout}")
    child = pidfile.read_text(encoding="utf-8").strip()
    assert child.isdigit() and int(child) > 1, f"bad child pid {child!r}"
    assert "CHILDPID=%s" % child in r.stdout, r.stdout

    assert "RC=124" in r.stdout, r.stdout
    assert "ALIVE=no" in r.stdout, (
        f"the capped child (pid {child}) outlived the bound that reported "
        f"stopping it:\n{r.stdout}")


# --- over-sensitivity controls -------------------------------------------
# A bound that fires on correct work blocks every capture on this fleet, and
# CLAUDE.md section 0 counts that as a soundness bug rather than a safe default.

def test_a_stage_that_finishes_under_the_cap_is_untouched(tmp_path):
    log = tmp_path / "stage.log"
    r = _run('CAPTURE_STAGE_CAP=60\n'
             'run_with_heartbeat "quick stage" "%s" sleep 2\n'
             'echo "RC=$?"' % log, timeout=90)
    assert "RC=0" in r.stdout, f"a stage well inside its cap did not exit 0:\n{r.stdout}"
    assert "CAPTURE-STAGE-CAP" not in log.read_text(encoding="utf-8", errors="replace")


def test_the_stages_OWN_exit_code_still_reaches_the_caller(tmp_path):
    """The lane exits are what capture_verdict.py grades. If the wrapper
    flattened them the verdict would lose the difference between a failing
    suite and a passing one."""
    log = tmp_path / "stage.log"
    r = _run('CAPTURE_STAGE_CAP=60\n'
             'run_with_heartbeat "failing stage" "%s" bash -c "exit 7"\n'
             'echo "RC=$?"' % log, timeout=90)
    assert "RC=7" in r.stdout, (
        f"the wrapper did not pass the command's own exit code through:\n{r.stdout}")


def test_a_zero_cap_disables_the_bound(tmp_path):
    """An explicit escape hatch, so an operator debugging a genuinely long run
    is not forced to edit the library."""
    log = tmp_path / "stage.log"
    r = _run('CAPTURE_STAGE_CAP=0\n'
             'run_with_heartbeat "unbounded" "%s" sleep 3\n'
             'echo "RC=$?"' % log, timeout=90)
    assert "RC=0" in r.stdout, r.stdout


def test_the_default_cap_is_far_above_the_slowest_measured_lane():
    """The number itself is a judgement and must not drift silently. Measured
    on this fleet 2026-08-13: a FULL suite at -n 48 completes in 219-315s, and
    the capture lane is a subset of that. A default anywhere near those numbers
    would fire on healthy runs."""
    text = _LIB.read_text(encoding="utf-8")
    line = [l for l in text.splitlines() if "CAPTURE_STAGE_CAP:=" in l]
    assert line, "the default cap is not set in the library"
    value = int(line[0].split(":=")[1].split("}")[0])
    assert value >= 1800, (
        f"the default cap is {value}s, close enough to a real lane's runtime "
        "(219-315s measured) to fire on a healthy capture")


# --- the verdict names it --------------------------------------------------

def test_the_verdict_names_a_capped_stage_rather_than_printing_a_bare_number():
    """`exit=124` beside a dozen other numbers reads as a test failure. It is
    not one -- the suite never reported a verdict at all. CLAUDE.md section 10:
    assert the reason, not the code."""
    import sys
    sys.path.insert(0, str(_REPO))
    from tools.capture_verdict import _cap_note

    note = _cap_note(124)
    assert "STAGE CAP" in note, note
    assert "UNFINISHED" in note or "unfinished" in note.lower(), note

    # over-sensitivity: every other exit code is left exactly as it was
    for other in (0, 1, 2, 3, 5, 123, 125, 137):
        assert _cap_note(other) == "", (
            f"exit {other} was annotated as a stage cap, which it is not")


# --- the wiring ------------------------------------------------------------

def test_capture_sh_sources_the_library_and_does_not_redefine_it():
    """A second copy is a denominator that drifts, and the copy nobody updates
    is the one the box runs."""
    src = (_REPO / "capture.sh").read_text(encoding="utf-8")
    assert "scripts/lib/heartbeat.sh" in src, (
        "capture.sh does not source the heartbeat library")
    assert "run_with_heartbeat() {" not in src, (
        "capture.sh still defines run_with_heartbeat inline, so the library and "
        "the box can disagree")
    assert "_stop_process_group() {" not in src, (
        "capture.sh still defines _stop_process_group inline")
    assert 'run_with_heartbeat "parallel-safe pytest lane"' in src, (
        "the parallel lane no longer goes through the bounded wrapper")
