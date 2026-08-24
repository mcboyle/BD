"""A heartbeat-wrapped lane still OBSERVES SIGINT. Measured 2026-08-23.

THE DEFECT, MEASURED ON SEVEN HOSTS. Every post-reboot capture on test, test2,
test3, test4, test5, test6 and test7 failed, and every one failed for the same
reason: nine signal-sensitive tests in
`tests/test_v3_66_1132_the_hunt_reaps_what_it_abandons.py` send SIGINT to a
runner and assert cancellation settlement, and under `capture.sh` none of those
runners could observe the signal at all. The parallel lane passed on all seven
hosts; the live lanes had zero failures. It was never seven VMs failing
independently and it was never the reboot -- it was one boundary.

THE BOUNDARY IS `_start_capture_detached`. It launches the lane as an
ASYNCHRONOUS shell job (`setsid "$@" ... &`). POSIX requires a non-interactive
shell to start an asynchronous job with SIGINT and SIGQUIT set to SIG_IGN, so
the child is handed an inherited ignore. `setsid` changes the session; it does
NOT reset signal dispositions. Python then PRESERVES an inherited SIG_IGN
rather than installing its ordinary `default_int_handler`, and every subprocess
pytest spawns inherits the ignore in turn. The chain that matters is exactly
the capture chain: heartbeat -> pytest -> the runner a test signals.

WHY NO EXISTING TEST SAW IT. `tests/test_v3_66_1111_a_wedged_capture_lane_is_
bounded.py` proves the wall-clock bound fires, and
`tests/test_v3_66_1191_two_captures_cannot_share_a_vault.py` proves the
descriptor-closing path closes what it claims to close. Both run the real
library. Neither asks what the wrapped command's SIGNAL DISPOSITIONS are, so
the wrapper was free to change them silently -- and it did, on both launch
paths, for as long as the wrapper has existed. The nine row-212 tests were the
first subjects sensitive to it, which is why the failure arrived with them
rather than with the wrapper.

WHY THIS FILE RUNS THE WRAPPER INSTEAD OF READING IT. A source check cannot
tell a signal reset that FIRES from one that is written down -- the same
argument that moved `run_with_heartbeat` out of capture.sh in the first place.
Every assertion below sources `scripts/lib/heartbeat.sh` itself and reads what
a real wrapped process reports about itself.

THE SEAM IS PROVED NONZERO, not assumed. `test_the_defective_launch_pattern_
still_reports_an_ignored_sigint` reproduces the bare `setsid ... &` pattern
WITHOUT the library and asserts it yields SIG_IGN. That control is what makes
the other assertions falsifiable: it shows the probe can and does distinguish
the two dispositions on this host, so a green result above it is a measurement
rather than a probe that cannot fail.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys
import time
from pathlib import Path

# Its subject is one library file and the capture wiring to it, not an
# invariant over the tree.
BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_LIB = _REPO / "scripts" / "lib" / "heartbeat.sh"
_CAPTURE = _REPO / "capture.sh"
# The interpreter RUNNING this test, not a hard-coded path. The subject is
# "whatever Python the lane wraps keeps its signal dispositions", and the
# lane wraps the interpreter the lane is using. Pinning venv/bin/python
# made this module unrunnable anywhere that is not this fleet -- it failed
# its own precondition on a GitHub runner, where the venv does not exist.
_PYTHON = pathlib.Path(sys.executable)

# Report the disposition by NAME. `signal.getsignal` returns a `signal.Handlers`
# IntEnum whose str() is a bare number on 3.11+, so printing it directly would
# make the log read `INT= 1` and an assertion on it would be an assertion on a
# formatting accident.
_DISPOSITION_PROBE = r"""
import signal, sys

def named(sig):
    handler = signal.getsignal(sig)
    if handler is signal.SIG_IGN:
        return "SIG_IGN"
    if handler is signal.SIG_DFL:
        return "SIG_DFL"
    if handler is signal.default_int_handler:
        return "DEFAULT_INT_HANDLER"
    return "OTHER:%r" % (handler,)

import os as _os
sys.stdout.write("LAUNCH=%s\n" % _os.environ.get("BD_HEARTBEAT_LAUNCH", "UNSET"))
sys.stdout.write("INT=%s\n" % named(signal.SIGINT))
sys.stdout.write("QUIT=%s\n" % named(signal.SIGQUIT))
sys.stdout.flush()
"""

# The capture chain, not a simplification of it: this stands where pytest
# stands, and the child it spawns stands where the signalled runner stands.
# `subprocess.Popen` does not set dispositions of its own -- it hands the child
# whatever this process holds -- so the grandchild's fate measures what the
# wrapper did to the lane.
_NESTED_CANCELLATION_PROBE = r"""
import os, signal, subprocess, sys

pidfile = sys.argv[1]
child = subprocess.Popen(["sleep", "60"])
tmp = pidfile + ".tmp"
with open(tmp, "w") as fh:
    fh.write("%d\n" % child.pid)
    fh.flush()
    os.fsync(fh.fileno())
os.replace(tmp, pidfile)

try:
    rc = child.wait(timeout=30)
except subprocess.TimeoutExpired:
    child.kill()
    child.wait(timeout=10)
    rc = "TIMEOUT"
sys.stdout.write("NESTED_RC=%s\n" % rc)
sys.stdout.flush()
"""


# `_start_capture_detached` chooses its launch path from these three, and
# capture.sh EXPORTS BD_HEARTBEAT_CLOSE_FD for the whole run. Inheriting it
# would silently route the "ordinary path" test down the descriptor-closing
# path -- in the serial lane, which is exactly where the seven-host defect
# lived. Declining to set a variable is not the same as removing it.
_PATH_DECIDING_VARIABLES = (
    "BD_HEARTBEAT_CLOSE_FD",
    "CAPTURE_VAULT_DIR_FD",
    "CAPTURE_VAULT_DIR_LOCK_FD",
    # BASH_ENV and ENV are sourced by non-interactive bash AFTER this test has
    # sanitised the environment, so either can put a path-deciding variable
    # back and silently route the "ordinary path" test down the other branch.
    # Removing three names and leaving the mechanism that can restore them is
    # not isolation.
    "BASH_ENV",
    "ENV",
)


def _bash(body: str, *, env: dict | None = None, timeout: int = 120):
    """Source the real library and run BODY with a KNOWN launch-path input.

    Sourcing the shipped file rather than an extracted copy is the point: a
    test against a duplicate certifies the duplicate.
    """
    environment = dict(os.environ) if env is None else dict(env)
    for name in _PATH_DECIDING_VARIABLES:
        if name not in (env or {}):
            environment.pop(name, None)
    script = '. "%s"\n%s\n' % (str(_LIB), body)
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        env=environment, timeout=timeout)


def _branch(text: str) -> str:
    """Which launch call site `_start_capture_detached` actually took.

    Read from BD_HEARTBEAT_LAUNCH, which each call site sets on the child, so
    this is the library naming itself. The obvious proxy -- the length of
    `_CAPTURE_CLOSE_FDS` in the caller -- answers a DIFFERENT question, and a
    mutation battery proved the difference: changing the branch predicate from
    `-gt 0` to `-ge 0` sent the zero-descriptor case through the
    descriptor-closing call site while that length stayed 0, so the
    "ordinary path" test passed having exercised the other branch.
    """
    for line in text.splitlines():
        if line.startswith("LAUNCH="):
            value = line.split("=", 1)[1].strip()
            return value if value in {"ordinary", "close-fd"} else "UNEXPECTED:" + value
    return "UNREPORTED"


def _dispositions(text: str) -> dict:
    """Parse the probe's own report; refuse to guess when it did not run."""
    found = {}
    for line in text.splitlines():
        if line.startswith(("INT=", "QUIT=")):
            key, _, value = line.partition("=")
            found[key] = value.strip()
    return found


def test_the_library_and_its_capture_consumer_are_present_and_parse():
    """Precondition. Without this, every assertion below could pass or fail
    for a missing file rather than for the behaviour under test."""
    assert _LIB.is_file(), f"{_LIB} is missing"
    assert _CAPTURE.is_file(), f"{_CAPTURE} is missing"
    assert _PYTHON.is_file(), f"{_PYTHON} is missing"
    for path in (_LIB, _CAPTURE):
        parsed = subprocess.run(["bash", "-n", str(path)],
                                capture_output=True, text=True)
        assert parsed.returncode == 0, parsed.stderr


# THE LANE PIN LIVES IN tests/test_u45_capture_sh_shipped.py, NOT HERE. An
# earlier draft of this file counted lines in capture.sh beginning with
# `run_with_heartbeat ` and pinned the count at exactly 3. That is a source
# scan standing in for a runtime property -- the anti-pattern backlog row 210
# exists to retire -- and it was wrong in both directions: a heredoc body line
# or a trailing-comment line would have counted, a lane written
# `if ! run_with_heartbeat ...` would not have, and a legitimate fourth wrapped
# lane would have failed a test whose subject is the library, not the count.
# u45 already asserts >= 3 occurrences plus all three exact lane labels, so the
# duplicate is deleted rather than hardened.


def test_capture_refuses_a_host_without_the_signal_reset_before_touching_evidence(tmp_path):
    """capture.sh must REFUSE a host whose env cannot reset dispositions, and
    must do it before it destroys anything.

    Two failures are being prevented, and the second is the one that bites.
    Without the check every lane still starts and exits 125 into a per-lane
    logfile; tools/capture_verdict.py names 124 and not 125, so the real cause
    reaches the operator as a bare number three stages later. And an earlier
    placement of this check sat AFTER classified-root garbage collection and
    evidence pruning, so a host that could not run a valid capture would still
    have deleted old evidence on its way to saying no.

    This drives the real capture.sh with a PATH-shadowed `env` that rejects the
    option, which is what an old coreutils looks like, and counts the capture
    directories on both sides of the refusal."""
    shadow = tmp_path / "bin"
    shadow.mkdir()
    (shadow / "env").write_text(
        "#!/bin/bash\n"
        "case \" $* \" in *\" --default-signal\"*)\n"
        "  echo \"env: unrecognized option '--default-signal'\" >&2; exit 125;; esac\n"
        "exec /usr/bin/env \"$@\"\n", encoding="utf-8")
    (shadow / "env").chmod(0o755)

    # THE PRUNER MUST BE ABLE TO FIRE, or this test cannot fail.
    # `bd_capture_prune` deletes only when the directory count EXCEEDS
    # CAPTURE_KEEP. An earlier draft left CAPTURE_KEEP at its default of 5 on a
    # host that happened to hold 5 directories, so pruning was a no-op and the
    # before/after comparison held whether or not the preflight ran first. An
    # independent review moved the preflight back after the pruner and the
    # mutant ESCAPED. Force keep=1 and plant enough directories that a prune
    # would definitely delete, then assert it did not.
    # THE BLAST RADIUS IS BOUNDED TO THIS TEST'S OWN DIRECTORIES.
    # `bd_capture_prune` globs a hard-coded /tmp/bd_capture-*, sorts by mtime
    # NEWEST first, and deletes everything past `keep`. So: plant three
    # directories with an ancient mtime, and set keep to the number of real
    # ones. Every real capture then sits inside the retention window and only
    # the planted three are reachable by the pruner. An earlier draft used
    # keep=1, which made a defective tree delete this host's actual capture
    # evidence -- a gate must not destroy what it is checking is not destroyed.
    real = [d for d in pathlib.Path("/tmp").glob("bd_capture-*") if d.is_dir()]
    planted = []
    for index in range(3):
        d = pathlib.Path("/tmp") / ("bd_capture-19700101T00000%dZ-1208gate" % index)
        d.mkdir(exist_ok=True)
        (d / "marker").write_text("1208-preflight-ordering-gate", encoding="ascii")
        os.utime(d, (1, 1))
        planted.append(d)
    try:
        result = subprocess.run(
            [str(_CAPTURE)], capture_output=True, text=True, timeout=120,
            cwd=str(_REPO),
            env={**os.environ, "CAPTURE_KEEP": str(max(len(real), 1)),
                 "PATH": "%s:%s" % (shadow, os.environ["PATH"])})

        assert result.returncode == 2, (
            f"capture did not refuse: rc={result.returncode}\n{result.stdout}\n{result.stderr}")
        assert "--default-signal" in result.stderr, result.stderr
        # THE ASSERTION THAT CAN ACTUALLY FAIL. These three are the oldest
        # matches on the host, so a pruner that runs before the refusal takes
        # exactly them. Verified against the shipped capture.sh with the
        # preflight moved back after `bd_capture_prune`: all three vanish.
        survived = [d for d in planted if d.exists()]
        assert len(survived) == 3, (
            "the refusal deleted capture evidence on its way to refusing, so "
            "the preflight is running AFTER a mutating step: "
            f"{[str(d) for d in planted if not d.exists()]}")
    finally:
        for d in planted:
            try:
                (d / "marker").unlink(missing_ok=True)
                d.rmdir()
            except OSError:
                pass


def test_the_defective_launch_pattern_still_reports_an_ignored_sigint(tmp_path):
    """NEGATIVE CONTROL, and the reason every assertion in this file is
    falsifiable. This reproduces the bare pattern WITHOUT the library. If this
    ever reports a default disposition, the probe has stopped being able to
    tell the two apart and the green results below mean nothing."""
    log = tmp_path / "defective.log"
    probe = tmp_path / "probe.py"
    probe.write_text(_DISPOSITION_PROBE, encoding="utf-8")
    result = subprocess.run(
        ["bash", "-c",
         'setsid "$@" > "$0" 2>&1 &\nwait $!\n' % (),
         str(log), str(_PYTHON), str(probe)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    seen = _dispositions(log.read_text(encoding="utf-8"))
    assert seen == {"INT": "SIG_IGN", "QUIT": "SIG_IGN"}, (
        "the bare `setsid ... &` pattern no longer produces the inherited "
        f"ignore this file exists to prevent; the probe is not discriminating: {seen}")


def test_the_ordinary_launch_path_preserves_default_signal_dispositions(tmp_path):
    """THE DEFECT, ordinary path. Base behaviour was INT=SIG_IGN QUIT=SIG_IGN."""
    log = tmp_path / "ordinary.log"
    probe = tmp_path / "probe.py"
    probe.write_text(_DISPOSITION_PROBE, encoding="utf-8")
    result = _bash('run_with_heartbeat ordinary "%s" "%s" "%s"\necho "RC=$?"\n'
                   'echo "CLOSE_FDS=${#_CAPTURE_CLOSE_FDS[@]}"'
                   % (log, _PYTHON, probe), timeout=60)
    assert "RC=0" in result.stdout, result.stdout + result.stderr
    assert _branch(log.read_text(encoding="utf-8")) == "ordinary", (
        "this test did not exercise the ordinary launch path, so a defect on "
        f"that path would escape it: {log.read_text(encoding='utf-8')!r}")
    seen = _dispositions(log.read_text(encoding="utf-8"))
    assert seen == {"INT": "DEFAULT_INT_HANDLER", "QUIT": "SIG_DFL"}, (
        "run_with_heartbeat handed its wrapped command altered signal "
        f"dispositions on the ordinary launch path: {seen}")


def test_the_descriptor_closing_launch_path_preserves_them_too(tmp_path):
    """THE DEFECT, descriptor-closing path -- a REAL valid close, not a stub.

    This path is a separate `setsid` call site, so a reset applied only to the
    ordinary one leaves every capture that owns a vault descriptor defective
    while the other test goes green. The assertion on FD_CLOSED keeps that
    existing behaviour in the denominator: a reset must not be bought by
    losing the close."""
    log = tmp_path / "closefd.log"
    owned = tmp_path / "owned"
    owned.write_text("", encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text(
        'import os, sys\n'
        'sys.stdout.write("FD_CLOSED=%s\\n" % (not os.path.exists('
        '"/proc/self/fd/" + os.environ["CHECK_FD"])))\n'
        + _DISPOSITION_PROBE, encoding="utf-8")
    result = _bash(
        'exec {owned}<>"%s"\n'
        'export BD_HEARTBEAT_CLOSE_FD="$owned" CHECK_FD="$owned"\n'
        'run_with_heartbeat closefd "%s" "%s" "%s"\necho "RC=$?"\n'
        'echo "CLOSE_FDS=${#_CAPTURE_CLOSE_FDS[@]}"'
        % (owned, log, _PYTHON, probe), timeout=60)
    assert "RC=0" in result.stdout, result.stdout + result.stderr
    text = log.read_text(encoding="utf-8")
    assert _branch(text) == "close-fd", (
        f"this test did not exercise the descriptor-closing launch path: {text!r}")
    assert "FD_CLOSED=True" in text, (
        f"the descriptor-closing path stopped closing its descriptor: {text}")
    seen = _dispositions(text)
    assert seen == {"INT": "DEFAULT_INT_HANDLER", "QUIT": "SIG_DFL"}, (
        "run_with_heartbeat handed its wrapped command altered signal "
        f"dispositions on the descriptor-closing path: {seen}")


def test_a_nested_child_of_a_wrapped_lane_can_still_be_cancelled(tmp_path):
    """THE CONSEQUENCE, not the mechanism. Reporting a disposition is not the
    same claim as being killable, and it is the second one the nine row-212
    tests depend on. This stands the wrapped command where pytest stands and
    its `Popen` child where the signalled runner stands, then sends a real
    SIGINT to that grandchild and reads the wait status the wrapped process
    itself recorded. On the defective base the grandchild inherits the ignore,
    survives the signal, and the lane reports NESTED_RC=TIMEOUT."""
    log = tmp_path / "nested.log"
    pidfile = tmp_path / "nested.pid"
    probe = tmp_path / "probe.py"
    probe.write_text(_NESTED_CANCELLATION_PROBE, encoding="utf-8")
    proc = subprocess.Popen(
        ["bash", "-c", '. "%s"\nrun_with_heartbeat nested "%s" "%s" "%s" "%s"\n'
                       'echo "RC=$?"' % (_LIB, log, _PYTHON, probe, pidfile)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 30
        while time.time() < deadline and not pidfile.exists():
            time.sleep(0.05)
        assert pidfile.exists(), (
            "the wrapped lane never published its nested child's pid; the "
            "fixture did not build the shape this test asserts on")
        nested_pid = int(pidfile.read_text().strip())
        # Precondition: the grandchild is alive and is OURS to signal.
        os.kill(nested_pid, 0)

        os.kill(nested_pid, signal.SIGINT)

        out, err = proc.communicate(timeout=60)
    finally:
        # The wrapper deliberately puts the lane in its OWN session, so killing
        # the outer bash leaves the real subject running with PPID 1. Measured:
        # a failing run left a `sleep 120` alive as its own session leader. Reap
        # the published grandchild and the wrapper's group, then the wrapper.
        try:
            if pidfile.exists():
                stray = int(pidfile.read_text().strip())
                try:
                    os.killpg(os.getpgid(stray), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
        except (ValueError, OSError):
            pass
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            proc.kill()
            proc.communicate(timeout=30)
    text = log.read_text(encoding="utf-8")
    assert "NESTED_RC=-%d" % int(signal.SIGINT) in text, (
        "a nested child of a heartbeat-wrapped lane did not die from the "
        f"SIGINT it was sent -- this is the seven-host capture failure: {text}\n"
        f"stdout={out}\nstderr={err}")
