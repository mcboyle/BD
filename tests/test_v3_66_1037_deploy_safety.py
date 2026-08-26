"""`scripts/deploy.sh` must not deploy into a live test run, and must never
leave the service down when it fails.

BOTH RULES COME FROM ONE INCIDENT, v3.66.1035. An orphaned pytest run on test4
was writing `.pyc` files while the deploy's step 9 tried to remove them. `rm`
failed with "Directory not empty", the script died -- and step 8 had already
STOPPED the unit, so test4 sat with its service inactive until someone looked.
The retry failed identically, because the retry re-runs the stop and hits the
same live writer. Two independent defects in one minute:

  1. Nothing checked whether a test run was in flight on the target, though a
     deploy and a suite fighting over the same `__pycache__` cannot both win.
  2. A failure inside the stopped window left production down, silently. The
     script was correct to abort; it was wrong to abort without putting the
     service back or saying loudly that it had not.

TESTED HERE BY EXTRACTION, NOT BY RUNNING THE SCRIPT. The full-script tests use
only the isolated fixture in test_deploy_script.py. Each function under test is
cut out by name on a BALANCED BRACE, never a fixed width -- CLAUDE.md section 2a
records an extractor that swallowed a closing `fi` and produced bash syntax
errors that presented as subject failures. Every extraction is `bash -n` checked
before it is used, so a broken cut fails as a broken cut.
"""
import contextlib
import pathlib
import re
import shlex
import subprocess
import sys
import time

import pytest

from shell_source import if_blocks_containing, shell_code_only

_REPO = pathlib.Path(__file__).resolve().parent.parent
_DEPLOY = _REPO / "scripts" / "deploy.sh"


def _extract(name: str) -> str:
    """The shell function `name`, cut on brace balance from its own header."""
    src = _DEPLOY.read_text(encoding="utf-8")
    start = src.index("%s()" % name)
    depth, i, opened = 0, start, False
    while i < len(src):
        if src[i] == "{":
            depth += 1
            opened = True
        elif src[i] == "}":
            depth -= 1
            if opened and depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("unbalanced braces extracting %s()" % name)



def _ps_stub(lines: list[str]) -> str:
    """A fake `ps` emitting `comm= args=` -- the real format, one row per line."""
    body = "".join('echo %s\n' % _q(l) for l in lines)
    return ('mkdir -p "$T/bin"; { echo \'#!/bin/sh\'; printf %s; } > "$T/bin/ps"; '
            'chmod +x "$T/bin/ps"; PATH="$T/bin:$PATH"\n' % _q(body))


def _q(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _step_block(code: str, n: int) -> str:
    """Everything between `STEP=n` and the next `STEP=` marker."""
    start = code.index("STEP=%d" % n)
    nxt = code.find("STEP=", start + 1)
    return code[start:nxt if nxt != -1 else len(code)]

def _run(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    syntax = subprocess.run(["bash", "-n", "-c", script], capture_output=True, text=True)
    assert syntax.returncode == 0, (
        "the EXTRACTION is broken, not the subject: %s" % syntax.stderr)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", **(env or {})})


# ── the preflight ────────────────────────────────────────────────────────────
#
# THESE USE REAL PROCESSES, not a stubbed `ps`. Detection reads
# /proc/<pid>/exe to decide whether a run belongs to THIS install dir, and no
# fake `ps` can produce a /proc entry -- a stub here could only ever test a
# reimplementation of the logic. The earlier stubbed version of these tests
# passed against a detector that answered DETECTED on an idle host, which is
# section 6's rule about a harness that cannot represent the failure.

_SLEEPER = "def test_sleep():\n    import time; time.sleep(25)\n"
# no conftest in the fake dir, so nothing chdirs the run away from it --
# which is exactly the xdist-master shape the detector targets.


def _fake_install_dir(tmp_path):
    """A directory holding a slow test. No fake venv: detection keys on CWD, so
    all the harness must do is run a real pytest WITH ITS CWD THERE.

    An earlier version symlinked a python into $D/venv/bin to fake an install.
    It could not work -- the symlink is not a venv, so pytest was unimportable
    and the process exited 1 before anything could observe it.
    """
    d = tmp_path / "install"
    (d / "tests").mkdir(parents=True)
    (d / "tests" / "test_sleeper.py").write_text(_SLEEPER, encoding="utf-8")
    return d


@contextlib.contextmanager
def _pytest_running_in(d):
    """A REAL pytest whose cwd is `d` -- the xdist-master shape the detector
    targets. No conftest lives there, so nothing chdirs it away."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "pytest", "tests/test_sleeper.py",
         "-q", "-p", "no:cacheprovider"],
        cwd=str(d), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):
            cwd = pathlib.Path("/proc", str(proc.pid), "cwd")
            try:
                if cwd.resolve() == d.resolve():
                    break
            except OSError:
                pass
            time.sleep(0.05)
        else:
            proc.kill()
            pytest.skip("could not get a pytest running with cwd in the sandbox")
        yield proc
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _ask(d):
    fn = _extract("_running_pytest")
    out = _run('DIR=%s\n' % shlex.quote(str(d)) + fn +
               '\n_running_pytest && echo DETECTED || echo NONE')
    return out.stdout.strip()


@pytest.mark.slow
def test_a_pytest_running_against_this_install_dir_is_detected(tmp_path):
    d = _fake_install_dir(tmp_path)
    with _pytest_running_in(d):
        assert _ask(d) == "DETECTED"


@pytest.mark.slow
def test_a_pytest_running_against_a_DIFFERENT_dir_is_ignored(tmp_path):
    """OVER-SENSITIVITY CONTROL, and the one that keeps deploy.sh testable.

    Its own suite drives it against a sandbox while itself running under the
    repo's venv. A detector scoped to "any pytest anywhere" refused that, and
    took 23 tests in test_deploy_script.py red with it -- found only because
    bd-band-derive named that file and the first hand-picked band omitted it.
    """
    running = _fake_install_dir(tmp_path)
    other = tmp_path / "somewhere-else"
    other.mkdir()
    with _pytest_running_in(running):
        assert _ask(other) == "NONE"


def test_a_SHELL_mentioning_pytest_in_the_install_dir_is_not_flagged(tmp_path):
    """The comm filter's own case, and it is the real-world one.

    `ssh host 'cd ~/BulkDownloader && ... venv/bin/python -m pytest ...'` leaves
    a BASH process whose cwd is the install dir and whose argv says "-m pytest".
    Without the comm filter that bash is indistinguishable from a live suite,
    and the deploy refuses on a host where nothing is running. A mutant dropping
    the filter escaped until this test existed, because every other case here
    puts the mentioning process somewhere other than $DIR.
    """
    d = tmp_path / "install"
    d.mkdir()
    # `; sleep 25` alone is NOT enough: bash tail-call EXECs its last command,
    # so the process replaces itself with `sleep` and "-m pytest" leaves its
    # argv entirely. The harness then proves nothing and the mutant escapes --
    # which it did, twice. The trailing `true` keeps bash alive as bash.
    proc = subprocess.Popen(
        ["bash", "-c", ": venv/bin/python -m pytest tests/; sleep 25; true"],
        cwd=str(d), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.5)
        # ASSERT THE HARNESS BUILT THE SHAPE before asserting the verdict, or a
        # pass means "nothing was there to flag" rather than "it was not flagged".
        ps = subprocess.run(["ps", "-eo", "pid=,comm=,args="],
                            capture_output=True, text=True).stdout
        mine = [l for l in ps.splitlines()
                if str(proc.pid) in l.split()[:1] and "-m pytest" in l]
        assert mine, (
            "the harness failed to produce a NON-python process mentioning "
            "pytest in the install dir, so this test cannot fail:\n%s"
            % "\n".join(l for l in ps.splitlines() if "-m pytest" in l))
        assert not mine[0].split()[1].startswith("python"), (
            "the harness process IS a python -- it must be a shell: %s" % mine[0])

        assert _ask(d) == "NONE", (
            "a shell that merely MENTIONS pytest, sitting in the install dir, "
            "was read as a live test run -- the deploy would refuse on an idle host")
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_an_idle_host_is_not_flagged(tmp_path):
    assert _ask(_fake_install_dir(tmp_path)) == "NONE"


def test_the_detector_cannot_match_its_own_command_line():
    """CLAUDE.md section 5's trap. Self-match is the small half; the big half is
    matching any process whose command line merely MENTIONS the pattern --
    which on this fleet includes the invoking shell."""
    code = shell_code_only(_DEPLOY)
    assert "pgrep" not in code, (
        "deploy.sh uses pgrep, which matches its own command line")
    fn = _extract("_running_pytest")
    assert "[ ]pytest" in fn or "[p]ytest" in fn, (
        "the pattern is spelled literally, so the matcher matches itself: %s" % fn)
    assert "comm=" in fn, (
        "matching args alone flags any shell that MENTIONS pytest -- measured "
        "DETECTED on an idle host at v3.66.1037")
    assert "/proc/" in fn and "cwd" in fn, (
        "detection is not scoped to the install dir, so a deploy to a sandbox "
        "is refused by an unrelated run elsewhere on the host")
    assert "CANNOT DETECT" in fn, (
        "the detector does not state its blind spot. A serial run chdirs away "
        "and is invisible here; a clean answer that does not say so reads as a "
        "guarantee it cannot give")


def test_the_preflight_refuses_before_anything_is_mutated():
    """Exit 2 is the refusal code, and refusal must precede the first side effect."""
    code = shell_code_only(_DEPLOY)
    assert code.index("_running_pytest") < code.index("git reset --hard"), (
        "the pytest preflight runs AFTER the tree is reset -- a refusal that "
        "late has already mutated the thing it was refusing to touch")

    # if-blocks, NOT a hand-rolled character window. The first version looked
    # for "refuse " within 900 characters and a mutant swapping the refusal for
    # a printf ESCAPED, because the window reached the NEXT precondition's
    # refuse. blocks_containing then returned only the header line, because it
    # is loop-scoped by design -- hence enclosing_if, added in the same cut.
    guards = [b for b in if_blocks_containing(code, "_running_pytest")
              if b.lstrip().startswith("if ")]
    assert guards, "no conditional block guards on _running_pytest"
    assert any("refuse" in b for b in guards), (
        "the preflight detects a live run but does not refuse (exit 2): %s" % guards)


def test_the_preflight_runs_after_the_install_dir_is_resolved():
    """It is scoped to $DIR, so asking before $DIR exists compares against an
    empty string and matches nothing -- a check that cannot see its subject."""
    code = shell_code_only(_DEPLOY)
    assert code.index('DIR="$(cd "$DIR" && pwd -P)"') < code.index("_running_pytest"), (
        "the preflight is asked before $DIR is resolved, so its scope check is "
        "against an empty prefix and can never fire")


# ── the stopped-window recovery ──────────────────────────────────────────────

def _exit_definitions() -> str:
    return "\n".join(_extract(name) for name in ("cleanup", "on_exit", "die"))


def _service_stubs() -> str:
    return (
        'mkdir -p "$T/bin"\n'
        'printf "#!/bin/sh\\necho SUDO \\$* >> $T/calls\\nexit 0\\n" > "$T/bin/sudo"\n'
        'printf "#!/bin/sh\\necho SYSTEMCTL \\$* >> $T/calls\\nexit 0\\n" > "$T/bin/systemctl"\n'
        'chmod +x "$T/bin/sudo" "$T/bin/systemctl"; PATH="$T/bin:$PATH"\n'
    )


def _recorded_service_calls(tmp_path) -> str:
    path = tmp_path / "calls"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def test_exit_guard_restarts_the_service_once_when_die_exits(tmp_path):
    fn = _exit_definitions()
    stub = _service_stubs()
    out = _run(
        'T=%s; ' % shlex.quote(str(tmp_path)) + stub
        + 'STEP=9; GTMP=""; SERVICE_STOPPED=1; DEPLOY_SUCCEEDED=0; '
        + 'EXIT_HANDLER_RUNNING=0\n' + fn
        + '\ntrap on_exit EXIT\ndie "bytecode sweep failed"',
    )
    calls = _recorded_service_calls(tmp_path)
    assert out.returncode == 1, out
    assert calls.count("start bulkdownloader") == 1, (
        "die() did not trigger exactly one EXIT recovery attempt after the unit "
        "had been stopped:\n%s\n%s\n%s" % (calls, out.stdout, out.stderr))
    assert "RESTARTED-PARTIAL-DEPLOY" in out.stderr, out


def test_exit_guard_does_not_touch_the_service_when_it_was_never_stopped(tmp_path):
    fn = _exit_definitions()
    stub = _service_stubs()
    out = _run(
        'T=%s; ' % shlex.quote(str(tmp_path)) + stub
        + 'STEP=0; GTMP=""; SERVICE_STOPPED=0; DEPLOY_SUCCEEDED=0; '
        + 'EXIT_HANDLER_RUNNING=0\n' + fn
        + '\ntrap on_exit EXIT\ndie "a precondition failed"',
    )
    calls = _recorded_service_calls(tmp_path)
    assert "start bulkdownloader" not in calls, (
        "the EXIT guard started the service after a failure OUTSIDE the stopped "
        "window:\n%s\n%s\n%s" % (calls, out.stdout, out.stderr))


def test_exit_guard_cannot_reenter_recovery_when_die_exits(tmp_path):
    """Simulate die() exiting while the EXIT handler is already active."""
    fn = _exit_definitions()
    stub = _service_stubs()
    out = _run(
        'T=%s; ' % shlex.quote(str(tmp_path)) + stub
        + 'STEP=9; GTMP=""; SERVICE_STOPPED=1; DEPLOY_SUCCEEDED=0; '
        + 'EXIT_HANDLER_RUNNING=1\n' + fn
        + '\ntrap on_exit EXIT\ndie "nested failure"',
    )
    calls = _recorded_service_calls(tmp_path)
    assert out.returncode == 1, out
    assert "start bulkdownloader" not in calls, (
        "the already-active EXIT handler re-entered service recovery when die() "
        "exited:\n%s\n%s\n%s" % (calls, out.stdout, out.stderr))


def test_every_stopped_flag_assignment_is_covered_by_the_exit_guard():
    code = shell_code_only(_DEPLOY)
    armed = [m.start() for m in re.finditer(
        r"(?m)^SERVICE_STOPPED=1(?:\s+#.*)?$", code)]
    assert len(armed) == 1, (
        "the exact stopped-window assignment denominator changed: %s" % armed)
    trap_at = code.index("trap on_exit EXIT")
    success_at = code.index("DEPLOY_SUCCEEDED=1")
    assert trap_at < armed[0] < success_at, (
        "a SERVICE_STOPPED=1 path exists outside the installed EXIT guard and "
        "recorded success boundary")
    handler = _extract("on_exit")
    assert '"${SERVICE_STOPPED:-0}" = 1' in handler, (
        "the EXIT handler does not inspect the state that opens the exposure window")
    assert '"${DEPLOY_SUCCEEDED:-0}" != 1' in handler, (
        "the EXIT handler does not distinguish failure from recorded success")


def test_exit_guard_transform_control_only_checks_shell_syntax():
    parsed = subprocess.run(
        ["bash", "-n", str(_DEPLOY)], capture_output=True, text=True)
    assert parsed.returncode == 0, parsed.stderr


def test_the_stopped_flag_is_set_at_the_stop_and_cleared_at_the_start():
    """The flag is what makes recovery conditional; an unset flag is a silent
    down, and a never-cleared one restarts on failures long past the window."""
    code = shell_code_only(_DEPLOY)
    # STEP BLOCKS, not a character window. The first version of this assertion
    # was `code[stop:stop + 700]` -- and `test_source_windows_do_not_shift`
    # caught it on the very next band, correctly: a fixed window breaks the
    # moment anyone adds a line above what it asserts on. Section 2a says
    # remove the window rather than raise the baseline, so it is cut on the
    # STEP= markers, which are real delimiters the script already maintains.
    assert "SERVICE_STOPPED=1" in _step_block(code, 8), (
        "step 8 stops the unit without recording that it did, so die() cannot "
        "know whether it owes a restart")
    assert "SERVICE_STOPPED=0" in code, (
        "the flag is never cleared, so a failure after the service is back up "
        "would restart an already-running unit")
