"""No bd-jobs remote stage may wait without a bound.

THE DEFECT (backlog row 217). All five synchronous remote stages passed
`ConnectTimeout=15` and NO post-connect deadline, so a session that CONNECTED
and then went silent wedged the caller indefinitely. Same shape as the xdist
drain livelock this repository spent 2026-08-24 dissecting: an unbounded wait
with nothing above it, where "still working" and "never going to finish" look
identical from outside.

WHY THE FAKE SSH MUST CONNECT FIRST. ConnectTimeout already bounds "the host
never answered", so a fake that merely hangs would be caught by the EXISTING
bound and prove nothing about the new one. The fake therefore writes and fsyncs
a marker before it stalls, and every arm asserts that marker exists -- otherwise
a green result could mean the transport was never reached at all.

ONE TOTAL BUDGET, NOT FIVE. Five independent 60s stage timeouts would be a 300s
worst case nobody declared, and adding a sixth stage later would silently extend
it. A settlement reserve is held back so the last stage can still reap what it
started; a deadline that leaves nothing for cleanup produces exactly the orphan
it was added to prevent.
"""
from __future__ import annotations

import importlib.machinery
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "toolchain" / "bin" / "bd-jobs"
jobs = importlib.machinery.SourceFileLoader("bd_jobs_1223", str(TOOL)).load_module()

_FAKE_SSH = '''#!/usr/bin/env python3
import os, sys, time
with open(os.environ["FAKE_SSH_MARKER"], "w") as fh:
    fh.write(" ".join(sys.argv[1:])[:400])
    fh.flush()
    os.fsync(fh.fileno())
mode = os.environ.get("FAKE_SSH_MODE", "hang")
if mode == "hang":
    time.sleep(float(os.environ.get("FAKE_SSH_HANG_S", "600")))
sys.stdout.write(os.environ.get("FAKE_SSH_STDOUT", ""))
sys.exit(int(os.environ.get("FAKE_SSH_RC", "0")))
'''


def _fake_ssh(tmp_path, monkeypatch, *, mode="hang", stdout="", rc=0):
    """A fake ssh that ESTABLISHES the session before it stalls."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    fake = bindir / "ssh"
    fake.write_text(_FAKE_SSH, encoding="utf-8")
    fake.chmod(0o755)
    marker = tmp_path / "connected"
    monkeypatch.setenv("PATH", "%s:%s" % (bindir, os.environ["PATH"]))
    monkeypatch.setenv("FAKE_SSH_MARKER", str(marker))
    monkeypatch.setenv("FAKE_SSH_MODE", mode)
    monkeypatch.setenv("FAKE_SSH_STDOUT", stdout)
    monkeypatch.setenv("FAKE_SSH_RC", str(rc))
    # BOUNDED SO THE ABSENCE OF A BOUND IS ALSO BOUNDED. If the fake stalled for
    # its 600s default, a mutant that DELETES the deadline would not fail here --
    # it would be killed by the 240s item timeout, which is a different
    # instrument reporting a different fact. 30s is comfortably past every
    # deadline this module sets (worst case 3s) and comfortably inside the bound.
    monkeypatch.setenv("FAKE_SSH_HANG_S", "30")
    return marker


def test_the_budget_is_one_total_not_one_per_stage():
    """PRECONDITION, and the one that separates a real total from five timeouts.

    Checking only the FIRST slice cannot tell the two apart -- a per-stage
    deadline hands out a full slice every time and looks identical on call one.
    So the clock is advanced between stages and the second slice must have SHRUNK
    by what the first stage spent. That is the whole meaning of "one total".
    """
    now = [100.0]
    saved, jobs.time.monotonic = jobs.time.monotonic, lambda: now[0]
    try:
        d = jobs._RemoteDeadline(total=10.0, reserve=2.0)
        first = d.slice_for_stage()
        now[0] = 103.0
        second = d.slice_for_stage()
    finally:
        jobs.time.monotonic = saved

    assert first == 8.0, first
    assert second == 5.0, (
        "the second stage got %r, not the 5.0s the first stage left it -- the "
        "budget was reset rather than shared" % (second,))
    assert d.total == 10.0 and d.reserve == 2.0


def test_the_deadline_reads_no_clock_until_a_stage_runs():
    """The instrument must not perturb what it measures.

    bd-jobs' own tests drive tuned fake clocks -- iter([0.0, 0.0, 11.0]) in
    test_scp_timeout_with_a_surviving_group_is_unknown_and_names_ownership -- so
    a deadline that read time.monotonic() in __init__ would consume a tick and
    silently shift every later measurement. It cost exactly one debugging cycle
    to find; this pins it.
    """
    ticks = []
    real = time.monotonic

    def counted():
        ticks.append(1)
        return real()

    d = jobs._RemoteDeadline()
    assert ticks == [], "constructing a deadline must not read the clock"
    saved, jobs.time.monotonic = jobs.time.monotonic, counted
    try:
        d.remaining()
        assert ticks, "the budget never started even when a stage asked for it"
    finally:
        jobs.time.monotonic = saved


def test_a_spent_budget_refuses_to_start_a_stage():
    """A stage that cannot run inside its budget must not be STARTED.

    Starting it and bounding it out immediately produces a side effect nobody
    can describe, which is the opposite of what a deadline is for.
    """
    d = jobs._RemoteDeadline(total=1.0, reserve=5.0)
    assert d.slice_for_stage() is None
    r, complete, note = jobs._run_remote(["ssh", "nowhere", "true"], d,
                                         phase="the probe")
    assert r.returncode == 124, r
    assert "not started" in r.stderr, r.stderr
    assert complete is True and "nothing needs reaping" in note


def test_an_established_session_that_goes_silent_is_bounded(tmp_path, monkeypatch):
    """THE RED CASE, and the whole reason row 217 exists.

    ConnectTimeout already bounds a host that never answers. This one ANSWERS
    and then says nothing, which before v3.66.1223 wedged the caller forever.
    """
    marker = _fake_ssh(tmp_path, monkeypatch, mode="hang")
    d = jobs._RemoteDeadline(total=4.0, reserve=1.0)

    started = time.monotonic()
    r, complete, note = jobs._run_remote(
        ["ssh", "somewhere", "sleep 600"], d, phase="the installed-tool probe")
    elapsed = time.monotonic() - started

    assert marker.is_file(), (
        "the fake ssh never recorded a connection, so this arm proves nothing "
        "about the POST-CONNECT bound -- it may have been ConnectTimeout")
    assert r.returncode == 124, (r.returncode, r.stderr)
    assert elapsed < 15, "the stage was not bounded; it ran %.1fs" % elapsed
    assert "did not answer" in r.stderr, r.stderr
    assert complete, "the bounded-out stage left its process group unreaped: %s" % note


def test_a_correct_stage_is_not_slowed_or_failed(tmp_path, monkeypatch):
    """OVER-SENSITIVITY CONTROL. A bound that fires on correct work is a
    soundness bug, not a safe default (CLAUDE.md A5)."""
    marker = _fake_ssh(tmp_path, monkeypatch, mode="ok", stdout="job-id\n", rc=0)
    d = jobs._RemoteDeadline()
    r, complete, note = jobs._run_remote(
        ["ssh", "somewhere", "true"], d, phase="the installed-tool probe")
    assert marker.is_file()
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert r.stdout == "job-id\n", r.stdout
    assert complete and note == ""


def test_the_bounded_stage_leaves_no_orphan(tmp_path, monkeypatch):
    """The reaping half. A deadline that abandons its child trades a wedged
    caller for a stray process, which is not an improvement."""
    _fake_ssh(tmp_path, monkeypatch, mode="hang")
    fake_path = str(tmp_path / "fakebin" / "ssh")
    d = jobs._RemoteDeadline(total=3.0, reserve=1.0)
    r, complete, _ = jobs._run_remote(
        [fake_path, "somewhere", "sleep 600"], d, phase="the probe")
    assert r.returncode == 124
    assert complete, "settlement was not proven"

    # Read /proc directly rather than shelling out to ps: v3.66.1213 measured
    # subprocess.run putting its own ps child in the CALLER'S process group, so
    # the instrument counted itself.
    leftover = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            cmdline = Path("/proc", entry, "cmdline").read_bytes()
        except OSError:
            continue
        # MATCH THE FAKE'S ABSOLUTE PATH, not the words "ssh" and "sleep".
        # A substring match caught this test's OWN pytest and shell command
        # lines, which mention both -- the same self-matching footgun that made
        # a `pkill -f` kill its own monitor earlier in this session.
        if fake_path.encode() in cmdline:
            leftover.append(entry)
    assert not leftover, "the bounded-out ssh survived: pids %r" % leftover


def test_every_remote_stage_goes_through_the_funnel():
    """STRUCTURAL FLOOR. A stage reverted to a bare subprocess.run would be
    unbounded again and nothing above would notice.

    Scoped to the ssh argv builder, so the tool's LOCAL subprocess calls -- the
    bounded ps census at _ORPHAN_PS_TIMEOUT_S, the real job launch -- are not
    swept up by a blanket ban they were never part of.
    """
    source = TOOL.read_text(encoding="utf-8")
    offenders = [
        line.strip() for line in source.splitlines()
        if "subprocess.run(" in line and "_ssh_argv" in line
    ]
    assert not offenders, (
        "remote stage(s) bypass _run_remote and are unbounded again: %r"
        % offenders)
    assert source.count("_run_remote(") >= 6, (
        "expected the funnel definition plus five stage call sites; found %d"
        % source.count("_run_remote("))


def test_cancellation_identity_survives_a_bounded_stage(monkeypatch):
    """An operator's Ctrl-C must arrive as ITSELF, carrying what is retained.

    Row 217 names "exception replacement" as a mutant because it is the easy
    mistake: wrapping settlement in a `try` and raising something new there turns
    a cancellation into a generic failure, and the caller can no longer tell an
    operator apart from a broken transport. The EXACT object must propagate, and
    the retained remote script and the unproven cleanup must ride on it as notes
    rather than replacing it.
    """
    events = []
    primary = KeyboardInterrupt("operator cancelled the launch")

    class Cancelled:
        pid = 424242
        returncode = None

        def communicate(self, timeout):
            events.append(("communicate", timeout))
            raise primary

    monkeypatch.setattr(jobs.subprocess, "Popen", lambda argv, **kw: Cancelled())
    monkeypatch.setattr(
        jobs, "_terminate_owned_popen_group",
        lambda proc: (False, "pid 424242 / process group 424242 still exists"))

    d = jobs._RemoteDeadline(total=90.0, reserve=20.0)
    with pytest.raises(KeyboardInterrupt) as excinfo:
        jobs._run_remote(["ssh", "somewhere", "launch"], d,
                         phase="the authenticated launch",
                         retained="/tmp/bd-jobs-copy-abc.sh")

    assert excinfo.value is primary, (
        "the cancellation was REPLACED: got %r" % (excinfo.value,))
    assert len(events) == 1 and events[0][0] == "communicate"
    assert 0 < events[0][1] <= 70.0, events[0][1]
    notes = "\n".join(getattr(primary, "__notes__", []))
    assert "/tmp/bd-jobs-copy-abc.sh" in notes, notes
    assert "cleanup is UNKNOWN" in notes, notes
    assert "still exists" in notes, notes


def test_a_bounded_cleanup_retains_and_reports_unknown_not_removal(
        tmp_path, monkeypatch):
    """UNKNOWN MUST NOT BE LAUNDERED INTO A REFUSAL OR A SUCCESS.

    Cleanup is a POST-EFFECT stage: the copy is already on the target. When the
    session goes silent nobody measured whether the quarantine happened, so the
    only honest answer is RETAINED plus UNKNOWN plus the exact pathname. Row 217
    names this mutant last because it is the one that looks like tidiness --
    reporting "removed" costs nothing until an operator goes looking for a file
    this tool said it had cleaned up.
    """
    marker = _fake_ssh(tmp_path, monkeypatch, mode="hang")
    d = jobs._RemoteDeadline(total=3.0, reserve=1.0)

    started = time.monotonic()
    removed, detail = jobs._remove_remote_copy(
        "somewhere", "/opt/bd/toolchain/bin/bd-jobs",
        "/tmp/bd-jobs-copy-xyz.sh", ("present", 1234, 5678, "abc123"),
        "req-1223", "nonce-1223", "attempt-1223", d)
    elapsed = time.monotonic() - started

    assert marker.is_file(), (
        "the fake ssh never connected, so this proves nothing about a "
        "POST-CONNECT bound")
    assert elapsed < 15, "cleanup was not bounded; it ran %.1fs" % elapsed
    assert removed is False, "an unmeasured cleanup was reported as a removal"
    assert "RETAINED /tmp/bd-jobs-copy-xyz.sh" in detail, detail
    assert "UNKNOWN" in detail, detail
