"""A wrapper must not silently alter the subject it bounds or carries.

TWO TOOLS, ONE CONTRACT, AND TWO DIFFERENT HONEST ANSWERS TO IT.

`bd-wedge-hunt` CARRIES a script to a remote host and `bd-run` BOUNDS a command
locally. Both sat between a caller and a subject, and both changed the subject
in a way the caller could not see. One of those is fixable and is fixed here;
the other is not fixable without breaking a different declared contract, so it
is DECLARED instead. Recording both in one file is the point: the shared defect
is the silence, not the mechanism.

ROW 226 -- THE CARRIER. `ssh()` passed the whole generated script as the remote
COMMAND ARGV, so the REMOTE LOGIN SHELL interpreted it and the tool's behaviour
depended on whatever shell the remote account happened to use. Measured on test3
against a real tcsh 6.24.10 login shell: the quoted heredoc delimiter was
handled differently, so `BDWEDGEEOF`, the background launch and the
`echo LAUNCHED=` line were WRITTEN INTO runner.sh instead of executed. Nothing
launched and the call still returned rc=0 with empty output -- a transport that
looks successful while the work never started. `bd-sweep-run:756` already states
this contract ("Every remote command is a SCRIPT ON STDIN") and implements it;
this call site was the one place that did not follow it.

THAT tcsh RUN IS NOT REPRODUCED HERE, and this file does not pretend otherwise:
tcsh is not installed on the integrator host. What is asserted instead is the
STRUCTURAL property that makes the remote shell irrelevant -- the script travels
on stdin and the remote command is literally `bash -s`. That is exactly row
226's stated acceptance criterion, and it is decidable without any particular
remote shell being present.

ROW 227 -- THE BOUND. coreutils `timeout` RESETS inherited signal dispositions
before exec, so a subject run under `bd-run --max-seconds` cannot observe an
ignore its parent held. Measured on test5: parent SigIgn=0x1001007, the same
command through `timeout` 0x1001000 -- bits 0,1,2 gone, which is exactly
HUP/INT/QUIT. `timeout --foreground` and `timeout -s TERM` were measured too and
erase it identically, so no mode of that tool preserves it.

WHY 227 IS DECLARED RATHER THAN FIXED, stated here because a future reader will
otherwise "fix" it and break something quieter. The alternative is bd-run owning
the kill itself, and `test_only_bd_gc_and_bd_jobs_hold_a_destructive_verb`
deliberately forbids that -- these diagnostic tools must stay safe to run when
you do not yet know what is wrong. Trading a visible, declared limitation for a
destructive verb inside a diagnostic tool is the worse bargain. So the
limitation is said out loud in the log the run owns, the same idiom as
CAPTURE-HEARTBEAT-UNARMED. This matters in production rather than in principle:
`bd-sweep-run:636` runs pytest through this wrapper, so without the declaration a
whole-fleet suite would produce weaker signal evidence than its own direct
probes suggest.
"""
from __future__ import annotations

import os
import pathlib
import re
import signal
import subprocess
import sys

import pytest

BD_GATE_SCOPE = "module"

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEDGE_HUNT = ROOT / "toolchain" / "bin" / "bd-wedge-hunt"
BD_RUN = ROOT / "toolchain" / "bin" / "bd-run"

SENTINEL = "install -d -m 700 /tmp/bd-wrapper-probe && echo LAUNCHED=$$\n"
DEFAULT_SIGNAL_ENV = ("env", "--default-signal=HUP,INT,QUIT,PIPE")


def _fake_ssh(tmp_path: pathlib.Path) -> pathlib.Path:
    """An `ssh` that records what it was ASKED to run and what it was FED.

    It deliberately does not execute anything. The question is which channel the
    script travelled on, and a fake that ran the command would answer a
    different one.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    fake = bindir / "ssh"
    fake.write_text(
        "#!/bin/bash\n"
        "printf '%%s\\n' \"$@\" > %s\n"
        "cat > %s\n"
        "exit 0\n" % (tmp_path / "argv.txt", tmp_path / "stdin.txt"),
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return bindir


def _drive_ssh(tmp_path: pathlib.Path, script: str):
    """Call the SHIPPED ssh() with a fake ssh on PATH; return (argv, stdin)."""
    bindir = _fake_ssh(tmp_path)
    driver = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_loader('wh', None)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "mod.__dict__['__file__'] = %r\n"
        "exec(compile(open(%r).read(), %r, 'exec'), mod.__dict__)\n"
        "r = mod.ssh('probe-host', %r, timeout=30)\n"
        "print('RC=%%s' %% r.returncode)\n"
        % (str(WEDGE_HUNT), str(WEDGE_HUNT), str(WEDGE_HUNT), script)
    )
    env = dict(os.environ)
    env["PATH"] = "%s%s%s" % (bindir, os.pathsep, env.get("PATH", ""))
    proc = subprocess.run([sys.executable, "-c", driver], capture_output=True,
                          text=True, env=env, timeout=90)
    assert proc.returncode == 0, proc.stderr[-1500:]
    argv_file = tmp_path / "argv.txt"
    stdin_file = tmp_path / "stdin.txt"
    assert argv_file.exists(), (
        "the fake ssh was never invoked, so this control measured nothing: %s"
        % proc.stderr[-800:])
    return (argv_file.read_text(encoding="utf-8").splitlines(),
            stdin_file.read_text(encoding="utf-8") if stdin_file.exists() else "")


def test_the_remote_script_travels_on_stdin_not_as_login_shell_argv(tmp_path):
    """ROW 226: the remote command must be `bash -s`, the script must be data.

    Both halves are asserted because either alone can be satisfied by an
    implementation that still hands the script to the login shell: argv could end
    with `bash -s` while the script is ALSO in argv, and the script could reach
    stdin while argv still carries a copy.
    """
    argv, stdin = _drive_ssh(tmp_path, SENTINEL)

    assert argv, "precondition: the fake ssh recorded no argv at all"
    assert argv[-1] == "bash -s", (
        "the remote command is not `bash -s`, so the remote LOGIN SHELL "
        "interprets whatever follows and this tool's behaviour depends on the "
        "remote account's shell (row 226). argv was: %r" % (argv,))
    assert SENTINEL in stdin, (
        "the script did not arrive on stdin; it was: %r" % (stdin[:200],))
    joined = "\n".join(argv)
    assert SENTINEL.strip() not in joined, (
        "the script is STILL present in argv, so the login shell can still see "
        "and reinterpret it even though stdin also carries a copy: %r" % (argv,))


def test_the_transport_carries_a_construct_a_foreign_login_shell_would_mangle(tmp_path):
    """The payload really does contain the shape that broke under tcsh.

    Without this the row-226 fix could be "correct" over a payload that no shell
    would disagree about, which would make the contract untested rather than
    satisfied. The heredoc with a QUOTED delimiter is the exact construct the
    measured tcsh run mishandled, so its presence in the shipped builder is the
    precondition that gives the stdin transport its purpose.
    """
    source = WEDGE_HUNT.read_text(encoding="utf-8")
    assert "<<'BDWEDGEEOF'" in source, (
        "the launch builder no longer uses a quoted heredoc; if the payload "
        "shape changed, re-derive whether the stdin transport is still the "
        "right fix rather than assuming it")

    argv, stdin = _drive_ssh(tmp_path, "cat > x <<'BDWEDGEEOF'\nbody\nBDWEDGEEOF\n")
    assert argv[-1] == "bash -s"
    assert "BDWEDGEEOF" in stdin, (
        "the heredoc payload must reach bash on stdin, not the login shell")


def _bd_run(tmp_path, args, ignore_signals):
    """Run bd-run in a child that optionally holds HUP/INT/QUIT ignored."""
    driver = (
        "import signal, subprocess, sys\n"
        "if %r:\n"
        "    for s in (signal.SIGHUP, signal.SIGINT, signal.SIGQUIT):\n"
        "        signal.signal(s, signal.SIG_IGN)\n"
        "r = subprocess.run([sys.executable, %r] + %r, capture_output=True, text=True)\n"
        "sys.stdout.write(r.stdout)\n"
        "sys.stderr.write(r.stderr)\n"
        "raise SystemExit(r.returncode)\n"
        % (bool(ignore_signals), str(BD_RUN), list(args))
    )
    return subprocess.run([*DEFAULT_SIGNAL_ENV, sys.executable, "-c", driver],
                          capture_output=True,
                          text=True, cwd=str(ROOT), timeout=180)


def _mask_probe(tmp_path) -> pathlib.Path:
    probe = tmp_path / "mask.py"
    probe.write_text(
        "import re\n"
        "m = re.search(r'^SigIgn:\\s*([0-9a-f]+)', "
        "open('/proc/self/status').read(), re.M)\n"
        "print('SUBJECT_SIGIGN=0x%s' % m.group(1))\n",
        encoding="utf-8")
    return probe


def _newest_log(logdir: pathlib.Path, label: str) -> pathlib.Path:
    logs = sorted(logdir.glob("%s-*.log" % label),
                  key=lambda p: p.stat().st_mtime)
    assert logs, "bd-run wrote no log for label %r" % label
    return logs[-1]


def test_a_capped_run_declares_the_signal_dispositions_its_cap_erased(tmp_path):
    """ROW 227: the declaration must be present, TRUE, and specific.

    Asserting the sentence alone would accept a decorative banner, so the
    subject's own observed mask is read out of the same log and required to
    actually differ from the parent's in exactly the bits the sentence names.
    """
    logdir = tmp_path / "logs"
    probe = _mask_probe(tmp_path)
    proc = _bd_run(tmp_path,
                   ["--max-seconds", "60", "--label", "capped",
                    "--dir", str(logdir), "--", sys.executable, str(probe)],
                   ignore_signals=True)
    assert proc.returncode == 0, proc.stderr[-1200:]
    body = _newest_log(logdir, "capped").read_text(encoding="utf-8")
    said = proc.stdout

    assert "BD-RUN-CAP-RESETS-SIGNALS" in said, (
        "a capped run said nothing about the dispositions its cap erased, so a "
        "reader cannot tell this run is not signal evidence (row 227):\n%s"
        % said[:600])
    declaration = [ln for ln in said.splitlines()
                   if "BD-RUN-CAP-RESETS-SIGNALS" in ln][0]
    for name in ("HUP", "INT", "QUIT"):
        assert name in declaration, (
            "the declaration did not name %s even though the parent held it "
            "ignored: %r" % (name, declaration))

    assert "BD-RUN-CAP-RESETS-SIGNALS" not in body, (
        "the declaration was written INTO the subject's log. That makes this "
        "tool alter the artifact its consumers parse -- the exact defect this "
        "cut fixes, one layer up. bd-sweep-run's selftest builds a fixture for "
        "an EMPTY log and CI caught this in the first draft.")

    parent = re.search(r"parent SigIgn=0x([0-9a-fA-F]+)", said)
    subject = re.search(r"SUBJECT_SIGIGN=0x([0-9a-fA-F]+)", body)
    assert parent and subject, (
        "could not read the parent mask from the declaration and the subject "
        "mask from the log, so the declaration's truth is unmeasured: "
        "said=%r log=%r" % (said[:300], body[:300]))
    parent_mask, subject_mask = int(parent.group(1), 16), int(subject.group(1), 16)
    assert parent_mask & 0x7 == 0x7, (
        "precondition: the parent must really hold HUP/INT/QUIT ignored, else "
        "this test proves nothing. parent=0x%x" % parent_mask)
    assert subject_mask & 0x7 == 0, (
        "the subject still holds HUP/INT/QUIT ignored, so the declaration is "
        "false and the cap did not erase them after all. subject=0x%x"
        % subject_mask)


def test_the_declaration_does_not_cry_wolf_when_nothing_was_ignored(tmp_path):
    """OVER-SENSITIVITY CONTROL. A banner on every capped run teaches readers to
    skip it, so the sentence must distinguish "nothing relevant was ignored"
    from a real erasure -- and must not name a signal the parent never held."""
    logdir = tmp_path / "logs"
    probe = _mask_probe(tmp_path)
    proc = _bd_run(tmp_path,
                   ["--max-seconds", "60", "--label", "quiet",
                    "--dir", str(logdir), "--", sys.executable, str(probe)],
                   ignore_signals=False)
    assert proc.returncode == 0, proc.stderr[-1200:]
    said = [ln for ln in proc.stdout.splitlines()
            if "BD-RUN-CAP-RESETS-SIGNALS" in ln]
    assert said, proc.stdout[:600]
    assert "nothing relevant was ignored" in said[0], (
        "the declaration claimed an erasure that could not have happened: %r"
        % said[0])
    assert "ERASED FOR THE SUBJECT" not in said[0], said[0]
    body = _newest_log(logdir, "quiet").read_text(encoding="utf-8")
    assert "BD-RUN-CAP-RESETS-SIGNALS" not in body, "the log must stay pure"


def test_an_uncapped_run_carries_no_declaration_because_nothing_is_erased(tmp_path):
    """The other direction: no cap means no `timeout`, so no erasure and no
    sentence. A declaration that appeared unconditionally would be describing
    the tool rather than the run."""
    logdir = tmp_path / "logs"
    probe = _mask_probe(tmp_path)
    proc = _bd_run(tmp_path,
                   ["--label", "uncapped", "--dir", str(logdir),
                    "--", sys.executable, str(probe)],
                   ignore_signals=True)
    assert proc.returncode == 0, proc.stderr[-1200:]
    body = _newest_log(logdir, "uncapped").read_text(encoding="utf-8")
    assert "BD-RUN-CAP-RESETS-SIGNALS" not in body, body[:400]
    assert "BD-RUN-CAP-RESETS-SIGNALS" not in proc.stdout, proc.stdout[:400]
    subject = re.search(r"SUBJECT_SIGIGN=0x([0-9a-fA-F]+)", body)
    assert subject, body[:400]
    assert int(subject.group(1), 16) & 0x7 == 0x7, (
        "without a cap the subject must still see the parent's ignores; if it "
        "does not, something OTHER than timeout is resetting them and this "
        "cut's explanation is wrong. subject=0x%s" % subject.group(1))


def test_an_unreadable_mask_reports_every_erasable_signal_not_none():
    """FAIL-CLOSED. An unreadable /proc mask cannot prove the subject is
    unaffected, so it must report the whole erasable set. Returning [] there
    would turn "cannot tell" into "nothing happened", which is the exact
    fail-open shape this repository keeps finding."""
    namespace: dict = {}
    exec(compile(BD_RUN.read_text(encoding="utf-8"), str(BD_RUN), "exec"),
         namespace)
    erased = namespace["_erased_by_cap"]
    assert erased("UNKNOWN") == ["HUP", "INT", "QUIT"]
    assert erased("not-a-mask") == ["HUP", "INT", "QUIT"]
    assert erased("0xzzzz") == ["HUP", "INT", "QUIT"]
    assert erased("0x0000000001001000") == []
    assert erased("0x0000000001001007") == ["HUP", "INT", "QUIT"]
    assert erased("0x0000000000000002") == ["INT"]


def test_the_capped_run_still_actually_caps(tmp_path):
    """The declaration must not have cost the bound it describes."""
    logdir = tmp_path / "logs"
    proc = _bd_run(tmp_path,
                   ["--max-seconds", "2", "--label", "stillcaps",
                    "--dir", str(logdir), "--", "sleep", "60"],
                   ignore_signals=False)
    assert proc.returncode == 124, (
        "a run that blew its cap did not report 124: rc=%s\n%s"
        % (proc.returncode, proc.stdout[-800:]))
    assert "CAPPED at 2s" in proc.stdout, proc.stdout[-800:]
