"""Work this fleet launches must be killable whole, and bounded in time.

TWO DEFECTS, ONE MODEL: something is started and nobody owns its lifetime.

BACKLOG 88 -- `bd-jobs reap` killed the REGISTERED pid and nothing beneath it.
Measured 2026-08-12: a parity driver was registered, its pytest child was not,
and reaping the driver left the pytest running. That orphan then failed the
`deploy.sh` step-0 preflight on two hosts, so every deploy refused until it was
found by hand. The tool whose entire subject is untracked work produced
untracked work -- CLAUDE.md section 0's shape, in the tool written against it.

BACKLOG 90 -- nothing bounded a run whose xdist WORKER died. `--timeout` is
enforced by pytest-timeout INSIDE the worker process, so when the worker itself
dies there is nothing left to fire it. Measured the same night on three separate
runs: `[gwN] node down: Not properly terminated` at ~99%, then load 0.00 with
pytest still resident and no further output for 44 minutes. A cap has to live
OUTSIDE the process it bounds, which is exactly what CLAUDE.md section 5 already
says -- "run it under a whole-run cap as well, and wait on a written exit
marker" -- and what no launcher implemented.

WHY killpg IS NOT THE OBVIOUS ONE-LINER. `os.killpg(os.getpgid(pid), 9)` on a
pid that is NOT its own group leader signals the launcher's group -- this
session's shell, the agent harness above it, everything. The repair is therefore
two-sided: launch with `start_new_session=True` so the child IS a leader, and at
reap time VERIFY leadership before signalling the group. When it cannot be
verified the tool kills the single pid and SAYS it could not vouch for children,
because a reap that silently covers less than it claims is how backlog 88
happened in the first place.

A CAPPED RUN MUST NEVER READ AS A PASS. Section 10: the verdict line is the
least-tested output and the only one anybody reads. A cap that returned 0 would
convert an unbounded hang into a false green, which is worse than the hang --
the hang at least announces itself by never finishing.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BD_JOBS = REPO / "toolchain" / "bin" / "bd-jobs"
BD_RUN = REPO / "toolchain" / "bin" / "bd-run"


def _python_for(repo: Path) -> Path:
    local = repo / "venv" / "bin" / "python"
    return local if local.is_file() else Path(sys.executable)


PY = _python_for(REPO)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_the_tools_are_present():
    """Denominator. Both paths below shell out; a missing tool would make every
    assertion pass over a subprocess that never ran."""
    assert BD_JOBS.is_file(), f"missing {BD_JOBS}"
    assert BD_RUN.is_file(), f"missing {BD_RUN}"
    assert PY.is_file(), f"missing {PY}"


def test_tool_runner_works_when_the_checkout_has_no_local_venv(tmp_path):
    """A CI checkout is allowed to use the interpreter running pytest.

    The tools are source files in this checkout; requiring an untracked
    ``venv/bin/python`` makes their real behavior untestable on a clean clone.
    """
    interpreter = _python_for(tmp_path)
    proc = subprocess.run(
        [str(interpreter), str(BD_RUN), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        "the tool runner required an untracked repo-local venv in a clean "
        f"checkout: rc={proc.returncode}\n{proc.stdout[-400:]}{proc.stderr[-400:]}"
    )


# ----------------------------------------------------------------- backlog 90

def test_bd_run_caps_a_hanging_command(tmp_path):
    """RED before the fix: no cap exists, so this blocks until the test times
    out rather than returning."""
    started = time.time()
    proc = subprocess.run(
        [str(PY), str(BD_RUN), "--dir", str(tmp_path), "--label", "capme",
         "--max-seconds", "3", "--", "sleep", "120"],
        capture_output=True, text=True, timeout=60,
    )
    elapsed = time.time() - started
    assert elapsed < 45, (
        f"bd-run did not bound a 120s command with --max-seconds 3; it ran "
        f"{elapsed:.1f}s. An unbounded launcher is what let a dead xdist worker "
        "hang a full-suite run for 44 minutes."
    )
    assert proc.returncode != 0, (
        "a CAPPED run exited 0. That converts an unbounded hang into a false "
        f"green, which is worse than the hang.\nstdout={proc.stdout[-800:]}"
    )
    # Match the EXACT emitted token, not a substring of it. `"CAP" in
    # stdout.upper()` also matches the LOG PATH, because tmp_path is derived
    # from this test's own name -- the denominator contained something that was
    # not the subject, and the first draft of this assertion passed on it.
    assert "CAPPED at" in proc.stdout, (
        "the verdict does not say the run was capped, so a capped run is "
        f"indistinguishable from a completed one.\nstdout={proc.stdout[-800:]}"
    )


def test_bd_run_does_not_cap_a_command_that_finishes(tmp_path):
    """THE OVER-SENSITIVITY CONTROL.

    A cap that fired early -- or always -- would pass the test above and break
    every real band. The exit code must still be the CHILD's, which bd-run's
    own comment calls out as load-bearing ("THE CHILD'S CODE, NEVER A DERIVED
    ONE").
    """
    proc = subprocess.run(
        [str(PY), str(BD_RUN), "--dir", str(tmp_path), "--label", "quick",
         "--max-seconds", "60", "--", "sh", "-c", "exit 7"],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 7, (
        "a command that finished well inside the cap did not return its own "
        f"exit code: rc={proc.returncode}\nstdout={proc.stdout[-800:]}"
    )
    assert "CAPPED at" not in proc.stdout, (
        "a run that completed normally was reported as capped:\n"
        f"{proc.stdout[-800:]}"
    )


# ----------------------------------------------------------------- backlog 88

def test_reap_kills_the_whole_process_group(tmp_path):
    """RED before the fix: the grandchild outlives the reap.

    The shape is the measured one -- a registered shell whose CHILD is the
    long-lived process. `setsid` is not used here by the test: the point is
    that bd-jobs must establish the group itself at launch, so a test that
    created the group would be proving its own fixture rather than the tool.
    """
    marker = tmp_path / "gc.pid"
    # The registered process is the shell; the grandchild is what must also die.
    cmd = f"sh -c 'sleep 300 & echo $! > {marker}; wait'"
    out = subprocess.run(
        [str(PY), str(BD_JOBS), "run", "--purpose", "1054 reap group test",
         "--", "bash", "-c", cmd],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, f"bd-jobs run failed: {out.stderr[-600:]}"
    job_id = out.stdout.strip().splitlines()[-1].strip()
    assert job_id, f"no job id printed: {out.stdout!r}"

    deadline = time.time() + 15
    while time.time() < deadline and not marker.exists():
        time.sleep(0.2)
    assert marker.exists(), (
        "the grandchild never recorded its pid, so this test would assert over "
        "a process that was never created -- the fixture, not the tool"
    )
    gc_pid = int(marker.read_text().strip())
    assert _alive(gc_pid), "precondition: the grandchild should be running"

    try:
        subprocess.run([str(PY), str(BD_JOBS), "reap", "--id", job_id],
                       capture_output=True, text=True, timeout=60)
        for _ in range(50):
            if not _alive(gc_pid):
                break
            time.sleep(0.2)
        assert not _alive(gc_pid), (
            f"grandchild pid {gc_pid} survived the reap. Reaping only the "
            "registered pid is what left an orphaned pytest blocking every "
            "deploy on two hosts."
        )
    finally:
        if _alive(gc_pid):
            try:
                os.kill(gc_pid, signal.SIGKILL)
            except OSError:
                pass
