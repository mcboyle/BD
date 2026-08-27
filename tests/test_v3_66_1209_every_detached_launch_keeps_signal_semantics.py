"""Every DETACHED launch in this tree hands its subject usable signals.

WHY THIS FILE EXISTS, AND WHY IT IS NOT test_v3_66_1208. v3.66.1208 fixed ONE
launch site -- `scripts/lib/heartbeat.sh` -- because that was the one capture
runs its lanes through, and all seven post-reboot fleet captures failed in the
serial lane because of it. The mechanism was never specific to that file: a
non-interactive shell starts an ASYNCHRONOUS job with SIGINT and SIGQUIT set to
SIG_IGN, `setsid` and `nohup` change session and hangup handling but never
touch dispositions, and Python PRESERVES an inherited SIG_IGN rather than
installing `default_int_handler`. Any `... &` in this tree that launches a
subject we later expect to CANCEL has the same defect.

MEASURED, NOT ASSUMED, at 674f98d9 on the fleet. Every site below was driven
through the same shell shape its real transport uses and the launched child's
own /proc/PID/status was read:

    tools/dast.sh:109          SigIgn=0x1001006  SigCgt=0     INT+QUIT ignored
    bd-sweep-run:730 (setsid)  SigIgn=0x1001007  SigCgt=0     INT+QUIT ignored
    bd-sweep-run:732 (no-setsid fallback)
                               SigIgn=0x1001007  SigCgt=0     INT+QUIT ignored
    bd-wedge-hunt:3188         SigIgn=0x1001007  SigCgt=0     INT+QUIT ignored

`SigCgt=0` is the load-bearing half: Python installed NO SIGINT handler, so it
kept what it inherited. A foreground control through the identical shell shows
`SigCgt=0x2` and no INT/QUIT in SigIgn.

THE CONSEQUENCE IS MEASURED TOO, and it is why this is not cosmetic.
`bd-sweep-run`'s runner executes the full `venv/bin/python -m pytest tests/`.
Driven through its REAL generated launch, the row-212 cancellation battery
reported 9 failed / 1 passed -- the same nine failures that took down seven
captures. Every sweep sample taken through that launcher against a tree
containing those tests carries nine false failures, which contaminates the
measurement the sweep exists to take.

WHY A SOURCE SCAN WOULD NOT DO. Backlog row 210's rule: a gate that reads text
cannot tell a reset that FIRES from one that is written down, and `env` on a
line proves nothing about the child's dispositions. Every assertion here reads
SigIgn/SigCgt out of a real launched process.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
from pathlib import Path

# Its subject is a set of launch sites and their runtime behaviour, not an
# invariant over the whole tree.
BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_PYTHON = pathlib.Path(sys.executable)

# SigIgn/SigCgt are 64-bit masks in /proc/PID/status; signal N is bit N-1.
_SIGINT_BIT = 1 << (2 - 1)
_SIGQUIT_BIT = 1 << (3 - 1)
_DEFAULT_SIGNAL_ENV = ("env", "--default-signal=HUP,INT,QUIT,PIPE")

# Reports its own dispositions from the kernel's view, not Python's.
_PROBE = r"""
import os, sys, time
fields = {}
with open("/proc/self/status", "r", encoding="ascii") as handle:
    for line in handle:
        if line.startswith(("SigIgn:", "SigCgt:")):
            key, value = line.split(":", 1)
            fields[key.strip()] = int(value.strip(), 16)
sys.stdout.write("SigIgn=0x%016x\n" % fields["SigIgn"])
sys.stdout.write("SigCgt=0x%016x\n" % fields["SigCgt"])
sys.stdout.flush()
"""


def _masks(text: str) -> tuple[int, int]:
    """Parse the probe's own report. Refuses to guess when it did not run."""
    ign = re.search(r"SigIgn=0x([0-9a-f]{16})", text)
    cgt = re.search(r"SigCgt=0x([0-9a-f]{16})", text)
    assert ign and cgt, f"the probe did not report its dispositions: {text!r}"
    return int(ign.group(1), 16), int(cgt.group(1), 16)


def _launch(shell_body: str, tmp_path, timeout: int = 60) -> tuple[int, int]:
    """Run SHELL_BODY under a NON-INTERACTIVE shell and read the child's masks.

    Non-interactive is the whole point: that is the shell POSIX requires to
    start an asynchronous job with INT and QUIT ignored, and it is what ssh and
    every script in this tree actually provide.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE, encoding="ascii")
    out = tmp_path / "child.out"
    script = shell_body % {"py": str(_PYTHON), "probe": str(probe), "out": str(out)}
    done = subprocess.run([*_DEFAULT_SIGNAL_ENV, "bash", "-c", script],
                          capture_output=True,
                          text=True, timeout=timeout, env=_clean_env())
    assert done.returncode == 0, done.stdout + done.stderr
    return _masks(out.read_text(encoding="ascii"))


def test_a_foreground_child_is_the_control(tmp_path):
    """NEGATIVE CONTROL, and the reason the rest is falsifiable. The SAME probe
    under the SAME non-interactive shell, run in the FOREGROUND, must show INT
    and QUIT unignored and a real SIGINT handler installed. If this ever fails,
    the probe has stopped discriminating and every result below is worthless."""
    ign, cgt = _launch('"%(py)s" "%(probe)s" > "%(out)s" 2>&1\n', tmp_path)
    assert not ign & _SIGINT_BIT, f"foreground SIGINT ignored: SigIgn=0x{ign:016x}"
    assert not ign & _SIGQUIT_BIT, f"foreground SIGQUIT ignored: SigIgn=0x{ign:016x}"
    assert cgt & _SIGINT_BIT, (
        f"foreground Python installed no SIGINT handler: SigCgt=0x{cgt:016x}")


def test_the_bare_async_pattern_still_produces_the_defect(tmp_path):
    """SEAM PROOF. The unfixed pattern, reproduced here WITHOUT any product
    file, must still ignore INT and QUIT. This is what makes a green result
    below a measurement rather than a probe that cannot fail."""
    ign, cgt = _launch('"%(py)s" "%(probe)s" > "%(out)s" 2>&1 &\nwait $!\n', tmp_path)
    assert ign & _SIGINT_BIT and ign & _SIGQUIT_BIT, (
        "the bare `... &` pattern no longer produces the inherited ignore this "
        f"file exists to prevent: SigIgn=0x{ign:016x}")
    assert not cgt & _SIGINT_BIT, f"SigCgt=0x{cgt:016x}"


def test_setsid_and_nohup_do_not_fix_it(tmp_path):
    """Named because it is the intuition that hid this for so long. `setsid`
    changes the session and `nohup` changes SIGHUP; neither touches INT or
    QUIT. Both detached shapes used in this tree still hand over the ignore."""
    for label, body in (
            ("setsid nohup", 'setsid nohup "%(py)s" "%(probe)s" > "%(out)s" 2>&1 < /dev/null &\nwait $!\n'),
            ("nohup only", 'nohup "%(py)s" "%(probe)s" > "%(out)s" 2>&1 < /dev/null &\nwait $!\n')):
        ign, _ = _launch(body, tmp_path)
        assert ign & _SIGINT_BIT and ign & _SIGQUIT_BIT, (
            f"{label} unexpectedly reset dispositions: SigIgn=0x{ign:016x}")


def test_the_remedy_restores_foreground_semantics_under_every_detached_shape(tmp_path):
    """THE CONTRACT. `env --default-signal=INT,QUIT` in front of the subject
    restores foreground semantics under each detached shape this tree uses."""
    for label, body in (
            ("setsid nohup", 'setsid nohup env --default-signal=INT,QUIT "%(py)s" "%(probe)s" > "%(out)s" 2>&1 < /dev/null &\nwait $!\n'),
            ("nohup only", 'nohup env --default-signal=INT,QUIT "%(py)s" "%(probe)s" > "%(out)s" 2>&1 < /dev/null &\nwait $!\n'),
            ("bare async", 'env --default-signal=INT,QUIT "%(py)s" "%(probe)s" > "%(out)s" 2>&1 &\nwait $!\n')):
        ign, cgt = _launch(body, tmp_path)
        assert not ign & _SIGINT_BIT, f"{label}: SIGINT still ignored, SigIgn=0x{ign:016x}"
        assert not ign & _SIGQUIT_BIT, f"{label}: SIGQUIT still ignored, SigIgn=0x{ign:016x}"
        assert cgt & _SIGINT_BIT, (
            f"{label}: no SIGINT handler installed, SigCgt=0x{cgt:016x}")


# --------------------------------------------------------------------------
# THE FOUR PRODUCT SITES. Each test below drives the REAL launcher -- the
# generator for the two toolchain tools, the shipped shell line for dast.sh --
# and reads the launched child's own /proc masks. None of them reads source
# text to decide.
# --------------------------------------------------------------------------

def _load_tool(name: str):
    """Import an extensionless toolchain tool as a module."""
    import importlib.machinery
    import importlib.util
    path = _REPO / "toolchain" / "bin" / name
    assert path.is_file(), f"{path} is missing"
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _run_generated(script: str, tmp_path, timeout: int = 60) -> tuple[int, int]:
    """Execute a launcher's GENERATED script under a non-interactive shell.

    This is the transport shape both tools really use: the text is fed to a
    remote `bash`, which is non-interactive, which is exactly the condition
    that produces the inherited ignore.
    """
    done = subprocess.run([*_DEFAULT_SIGNAL_ENV, "bash", "-s"], input=script,
                          capture_output=True,
                          text=True, timeout=timeout, cwd=str(tmp_path),
                          env=_clean_env())
    assert done.returncode == 0, done.stdout + done.stderr
    out = tmp_path / "child.out"
    deadline = __import__("time").monotonic() + 30
    while __import__("time").monotonic() < deadline:
        if out.exists() and "SigCgt" in out.read_text(encoding="ascii", errors="replace"):
            break
        __import__("time").sleep(0.02)
    assert out.exists(), f"the generated launch never produced a child report:\n{done.stdout}\n{done.stderr}"
    return _masks(out.read_text(encoding="ascii"))


def _probe_runner(tmp_path) -> str:
    """A runner body that reports its own dispositions and exits."""
    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE, encoding="ascii")
    return '#!/bin/bash\n"%s" "%s" > "%s" 2>&1\n' % (
        _PYTHON, probe, tmp_path / "child.out")


def test_bd_sweep_run_launches_its_runner_with_usable_signals(tmp_path):
    """bd-sweep-run:730. Its runner executes the full pytest suite, so an
    inherited ignore turns the nine row-212 cancellation tests into nine false
    failures in every sweep sample -- contaminating the measurement the sweep
    exists to take."""
    mod = _load_tool("bd-sweep-run")
    rundir = tmp_path / "rundir"
    rundir.mkdir()
    script = mod.build_launch(str(rundir), _probe_runner(tmp_path))
    ign, cgt = _run_generated(script, tmp_path)
    assert not ign & _SIGINT_BIT, (
        f"bd-sweep-run handed its runner an ignored SIGINT: SigIgn=0x{ign:016x}")
    assert not ign & _SIGQUIT_BIT, f"SigIgn=0x{ign:016x}"
    assert cgt & _SIGINT_BIT, (
        f"the runner installed no SIGINT handler: SigCgt=0x{cgt:016x}")


def test_bd_sweep_run_no_setsid_fallback_is_covered_too(tmp_path):
    """bd-sweep-run:732. The fallback branch is a SEPARATE launch site, so a
    fix applied only to the setsid branch leaves every host without setsid
    defective while the other test goes green. Forced by scrubbing setsid from
    PATH, which is the condition the branch exists for."""
    mod = _load_tool("bd-sweep-run")
    rundir = tmp_path / "rundir"
    rundir.mkdir()
    shadow = tmp_path / "nosetsid"
    shadow.mkdir()
    for tool in ("bash", "env", "nohup", "cat", "mkdir", "echo", "install", "rm"):
        found = subprocess.run(["command", "-v", tool], capture_output=True,
                               text=True, shell=False, executable="/bin/bash")
    script = mod.build_launch(str(rundir), _probe_runner(tmp_path))
    guarded = 'setsid() { return 127; }\nexport -f setsid\n' \
              'command() { if [ "$2" = setsid ]; then return 1; fi; builtin command "$@"; }\n' \
              'export -f command\n' + script
    ign, cgt = _run_generated(guarded, tmp_path)
    assert not ign & _SIGINT_BIT, (
        "the no-setsid fallback handed its runner an ignored SIGINT: "
        f"SigIgn=0x{ign:016x}")
    assert cgt & _SIGINT_BIT, f"SigCgt=0x{cgt:016x}"


def test_dast_launches_the_app_with_usable_signals(tmp_path):
    """tools/dast.sh:109. Its EXIT trap kills the app with the default signal,
    and an app that cannot be interrupted outlives a cancelled scan."""
    body = (_REPO / "tools" / "dast.sh").read_text(encoding="utf-8")
    # Take the WHOLE shipped line, not a substring of it. An earlier draft
    # matched on `"$PY" downloader_ui.py ... &`, which is still a substring
    # after a prefix is added -- so it would have kept running the UNFIXED
    # shape and reported RED forever while the product was already correct.
    lines = [l.strip() for l in body.splitlines()
             if "downloader_ui.py" in l and l.rstrip().endswith("&")]
    assert len(lines) == 1, (
        "tools/dast.sh no longer has a single app launch to judge; re-derive "
        f"this test rather than adjusting it: {lines}")
    marker = lines[0]
    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE, encoding="ascii")
    stub = tmp_path / "py"
    stub.write_text('#!/bin/bash\nexec "%s" "%s"\n' % (_PYTHON, probe), encoding="ascii")
    stub.chmod(0o755)
    # Run the SHIPPED launch line, with only PY and the sinks redirected.
    launched = marker.replace('> "$OUT/server.log" 2>&1',
                              '> "%s" 2>&1' % (tmp_path / "child.out"))
    assert launched != marker, "the launch line's sink was not redirected"
    ign, cgt = _launch('PY="%s"\n' % stub + launched + "\nwait $!\n", tmp_path)
    assert not ign & _SIGINT_BIT, (
        f"tools/dast.sh launched its app with SIGINT ignored: SigIgn=0x{ign:016x}")
    assert not ign & _SIGQUIT_BIT, f"SigIgn=0x{ign:016x}"
    assert cgt & _SIGINT_BIT, f"SigCgt=0x{cgt:016x}"


# --------------------------------------------------------------------------
# THE WRAPPER MUST NOT DAMAGE ITS CALLER EITHER. v3.66.1208 fixed what the
# heartbeat hands DOWN to its child. These cover what it hands BACK to the
# shell that called it, which was measured broken at 674f98d9.
# --------------------------------------------------------------------------

_HEARTBEAT = _REPO / "scripts" / "lib" / "heartbeat.sh"


def _clean_env(**extra) -> dict:
    """A subprocess environment with a KNOWN locale and no path-deciding vars.

    LC_ALL is pinned because row 178's failure class is a verdict that depends
    on the host locale, and everything here parses tool output. The descriptor
    variables are removed rather than merely unset, because capture.sh EXPORTS
    BD_HEARTBEAT_CLOSE_FD for a whole run and BASH_ENV is sourced after any
    sanitisation this test does.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("BD_HEARTBEAT_CLOSE_FD", "CAPTURE_VAULT_DIR_FD",
                        "CAPTURE_VAULT_DIR_LOCK_FD", "BASH_ENV", "ENV",
                        "BD_CAPTURE_SIGNAL_REEXEC")}
    env["LC_ALL"] = "C"
    env.update(extra)
    return env



def _caller(body: str, tmp_path, timeout: int = 90) -> subprocess.CompletedProcess:
    script = '. "%s"\n%s\n' % (_HEARTBEAT, body)
    return subprocess.run([*_DEFAULT_SIGNAL_ENV, "bash", "-c", script],
                          capture_output=True,
                          text=True, timeout=timeout,
                          env=_clean_env())


def test_the_wrapper_gives_the_caller_its_own_traps_back(tmp_path):
    """A caller's cleanup handler must survive the wrapper.

    Measured broken at 674f98d9: `trap - INT TERM HUP` resets to the DEFAULT,
    which is not what the caller had, so a script that installed cleanup for
    temporary data lost it the moment it wrapped one stage."""
    log = tmp_path / "lane.log"
    result = _caller(
        "trap 'echo CALLER-INT-RAN' INT\n"
        "trap 'echo CALLER-TERM-RAN' TERM\n"
        'run_with_heartbeat lane "%s" true >/dev/null\n'
        'echo "AFTER_INT=$(trap -p INT)"\n'
        'echo "AFTER_TERM=$(trap -p TERM)"\n' % log, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CALLER-INT-RAN" in result.stdout, (
        f"the caller's INT handler did not survive the wrapper: {result.stdout!r}")
    assert "CALLER-TERM-RAN" in result.stdout, (
        f"the caller's TERM handler did not survive the wrapper: {result.stdout!r}")


def test_a_caller_that_deliberately_ignores_a_signal_keeps_ignoring_it(tmp_path):
    """`trap '' SIG` is a DECISION, not an absence of one, and the wrapper must
    not overwrite it with the default. This is the case a naive save/restore
    gets wrong, because `trap -p` prints nothing for a default and
    `trap -- '' SIGX` for an ignore -- the two are not interchangeable."""
    log = tmp_path / "lane.log"
    result = _caller(
        "trap '' INT\n"
        'run_with_heartbeat lane "%s" true >/dev/null\n'
        'echo "AFTER=$(trap -p INT)"\n' % log, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "AFTER=trap -- '' SIGINT" in result.stdout, (
        "the wrapper replaced the caller's deliberate ignore with the default: "
        f"{result.stdout!r}")


def test_a_lane_whose_signals_cannot_be_armed_says_so(tmp_path):
    """THE CASE THAT CANNOT BE REPAIRED, so it must be ANNOUNCED.

    A shell cannot un-ignore a signal it inherited as SIG_IGN at startup:
    `trap` silently does nothing, the wrapper's handlers never install, and a
    stopped lane can outlive it -- 4 lane processes left alive, measured. There
    is no in-shell remedy, and CLAUDE.md A7 is explicit that an unmeasurable
    bound is UNKNOWN rather than OK. Silence here would be the wrapper claiming
    a bound it does not have."""
    log = tmp_path / "lane.log"
    script = '. "%s"\nrun_with_heartbeat det "%s" true >/dev/null\n' % (_HEARTBEAT, log)
    # `trap ''` in the PARENT makes bash hand the child an inherited ignore,
    # which is exactly how a nohup'd or detached capture is started.
    result = subprocess.run(
        [*_DEFAULT_SIGNAL_ENV, "bash", "-c",
         "trap '' INT HUP; bash -c %s" % __import__("shlex").quote(script)],
        capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    announced = "CAPTURE-HEARTBEAT-UNARMED" in result.stderr
    assert announced, (
        "the wrapper could not arm its own stop handlers and said nothing; a "
        f"lane may outlive it silently: {result.stderr!r}")
    assert "CAPTURE-HEARTBEAT-UNARMED" in log.read_text(encoding="utf-8"), (
        "the warning reached stderr but not the stage log; the archive is what "
        "survives a capture")


_CAPTURE_SH = _REPO / "capture.sh"
# HUP=1 -> bit0, INT=2 -> bit1, QUIT=3 -> bit2, TERM=15 -> bit14.
_STOP_MASK = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 14)


def _capture_with_ignored(signals: str, extra_env=None, timeout: int = 90):
    """Run capture.sh from a shell that IGNORES `signals`.

    NOTE: no `timeout(1)` anywhere in this path. coreutils `timeout` RESETS
    inherited ignores before exec'ing its child -- measured, SigIgn 0x1003
    becomes 0x1000 -- so wrapping the subject in it silently deletes the very
    condition under test. An earlier draft of this test did exactly that and
    reported a false green.
    """
    env = _clean_env(**(extra_env or {}))
    return subprocess.run(
        [*_DEFAULT_SIGNAL_ENV, "bash", "-c",
         "trap '' %s; exec ./capture.sh --workers=1" % signals],
        capture_output=True, text=True, cwd=str(_REPO), env=env, timeout=timeout)


def test_capture_reexecs_itself_when_handed_ignored_stop_signals(tmp_path):
    """THE ONLY PLACE THIS CAN BE FIXED.

    A shell cannot un-ignore a signal it inherited as SIG_IGN, so every trap
    capture.sh and run_with_heartbeat install never arms and a stopped lane
    outlives its wrapper -- 4 processes, measured. `env --default-signal`
    resets a CHILD, so the repair is to become that child exactly once, before
    the traps that depend on it exist. Capture is launched detached by design,
    so this is the ordinary case."""
    result = _capture_with_ignored("INT HUP")
    assert "re-exec" in result.stderr, (
        "capture.sh accepted inherited ignored signals without re-execing, so "
        f"none of its stage bounds can arm: {result.stderr[:400]!r}")
    assert "SigIgn=0x" in result.stderr, result.stderr[:400]


def test_the_reexec_is_bounded_to_exactly_one_hop(tmp_path):
    """A capture that re-execs itself forever is worse than one that runs
    unarmed. With the guard already set, the probe must be skipped entirely
    and the run must proceed."""
    result = _capture_with_ignored("INT HUP",
                                   extra_env={"BD_CAPTURE_SIGNAL_REEXEC": "1"})
    assert "re-exec" not in result.stderr, (
        "capture.sh re-execed a second time despite its guard; this is the "
        f"loop the guard exists to prevent: {result.stderr[:400]!r}")


def test_an_ordinary_foreground_capture_does_not_reexec(tmp_path):
    """NEGATIVE CONTROL. The repair must fire ONLY on the defective condition.
    A capture started normally has nothing to repair, and re-execing it would
    be an unexplained extra process in every operator's run."""
    result = subprocess.run(
        [*_DEFAULT_SIGNAL_ENV, "bash", "-c", "exec ./capture.sh --workers=1"],
        capture_output=True, text=True, cwd=str(_REPO), timeout=90,
        env=_clean_env())
    assert "re-exec" not in result.stderr, (
        f"capture.sh re-execed with nothing to repair: {result.stderr[:400]!r}")


def test_timeout_erases_the_condition_so_no_test_may_wrap_the_subject_in_it():
    """A TRAP THIS FILE FELL INTO, pinned so nobody repeats it.

    coreutils `timeout` resets inherited signal dispositions before exec'ing.
    Any test that runs the subject under `timeout` therefore CANNOT observe an
    inherited ignore, and will report green whether or not the repair exists.
    This asserts the erasure is real, so the reason the tests above avoid
    `timeout` stays measured rather than remembered."""
    probe = "awk '/^SigIgn:/ {print $2}' /proc/self/status"
    direct = subprocess.run([*_DEFAULT_SIGNAL_ENV, "bash", "-c",
                             "trap '' INT HUP; %s" % probe],
                            capture_output=True, text=True, timeout=30)
    viat = subprocess.run([*_DEFAULT_SIGNAL_ENV, "bash", "-c",
                           "trap '' INT HUP; timeout 10 bash -c %s"
                           % __import__("shlex").quote(probe)],
                          capture_output=True, text=True, timeout=30)
    assert int(direct.stdout.strip(), 16) & _STOP_MASK, direct.stdout
    assert not int(viat.stdout.strip(), 16) & _STOP_MASK, (
        "coreutils timeout no longer erases inherited ignores; the tests above "
        f"may now be wrapped in it after all: {viat.stdout!r}")
