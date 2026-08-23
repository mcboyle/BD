"""v3.66.1087 -- backlog 3: a long job's liveness is not its progress.

THE GAP, and it is measured rather than imagined. `bd-jobs` answers "is this
process still there" with alive(), which compares /proc start times. That is the
right predicate for the orphan problem the tool was built for, and it is the
WRONG one for the question an operator actually asks about a long run, which is
"is it still doing anything".

MEASURED ON test6, 2026-08-13, at f154aef, during a full-suite sweep at -n 48:
a pytest master sat at `[ 99%]` for 462 seconds with `[gw37] node down: Not
properly terminated` as its last output, one defunct child it never reaped, and
a 1-minute load average of 0.06. alive() said LIVE for every second of that,
truthfully. Two of three samples at that worker count ended the same way. That
is exactly CLAUDE.md section 10's paragraph -- "a long job reports 99% and then
hangs there forever, which is exactly what a dead xdist worker looks like" --
reproduced on demand.

WHY A LOG MTIME AND NOT A PERCENTAGE. Section 10 bans estimated progress and
says to report the countable instead: "the last stage the log actually recorded,
and the wall-clock since the last line was written". A percentage is a
prediction; a file's mtime is a fact, it needs no cooperation from the job, and
it is precisely the signal that distinguished a wedged run from a slow one here.

THREE STATES, AND THE THIRD IS THE POINT. UNKNOWN is not a soft PROGRESSING: a
job with no log recorded, or whose log has gone, cannot be judged, and saying
so is the whole of CLAUDE.md section 0. Each of the three is asserted reachable
below, because a branch nothing can reach is dead code that reads as a safety
feature.

WHAT THIS DOES NOT DO. STALLED is a REPORT, not a verdict -- `list` stays
read-only and exits 0 either way. A legitimately quiet job (one that computes
for ten minutes without printing) reads as STALLED, which is why it must not
gate anything: an over-sensitive gate gets switched off, and section 0 calls
that a soundness bug rather than a safe default.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import collections
import errno
import importlib.machinery
import importlib.util
import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-jobs"
_ProcessReceipt = collections.namedtuple(
    "_ProcessReceipt", "pid state ppid pgid session starttime")


def _load(name="bd_jobs_progress"):
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(_TOOL)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _process_fields(pid):
    """Read the identity and group authority in one procfs observation."""
    try:
        raw = pathlib.Path("/proc", str(pid), "stat").read_text("utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return None
    try:
        after = raw[raw.rindex(")") + 1:].split()
        return _ProcessReceipt(
            int(pid), after[0], int(after[1]), int(after[2]), int(after[3]),
            int(after[19]))
    except (IndexError, ValueError):
        return None


@pytest.mark.parametrize(
    "raw",
    ["", "123 malformed", "123 (short) S 1 2"],
    ids=["empty", "missing-paren", "truncated-fields"],
)
def test_process_fields_treats_transient_malformed_procfs_as_unavailable(
        monkeypatch, raw):
    """A torn procfs observation is absence of evidence, not a test crash."""
    monkeypatch.setattr(pathlib.Path, "read_text", lambda *_a, **_k: raw)
    assert _process_fields(123) is None


def _capture_group_receipt(process):
    """Capture group authority while this worker's exact child is alive."""
    assert process.poll() is None, (
        f"pid {process.pid} exited before its teardown receipt was captured")
    receipt = _process_fields(process.pid)
    assert receipt is not None, (
        f"live Popen pid {process.pid} has no readable identity")
    assert receipt.ppid == os.getpid(), (
        f"refusing receipt for pid {process.pid}: parent is {receipt.ppid}")
    assert receipt.pgid == receipt.pid == receipt.session, (
        f"refusing receipt for pid {process.pid}: pgid/session are "
        f"{receipt.pgid}/{receipt.session}")
    assert receipt.state != "Z", (
        f"pid {process.pid} was already a zombie during receipt capture")
    return receipt


def _group_exists(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        raise
    return True


def _recorded_group_members(receipt):
    """Census only the group in the session proven by the live receipt."""
    members = []
    conflicts = []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        identity = _process_fields(int(entry.name))
        if identity is None or identity.pgid != receipt.pgid:
            continue
        if identity.session != receipt.session:
            conflicts.append(identity)
        else:
            members.append(identity)
    assert not conflicts, (
        f"refusing reused process group {receipt.pgid}: current sessions are "
        f"{sorted(item.session for item in conflicts)}")
    return members


def _signal_recorded_group(receipt):
    """Signal the receipted session/group, or prove it already disappeared."""
    members = _recorded_group_members(receipt)
    if not members:
        assert not _group_exists(receipt.pgid), (
            f"process group {receipt.pgid} exists outside recorded session "
            f"{receipt.session}")
        return

    try:
        os.killpg(receipt.pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as exc:
        if exc.errno != errno.ESRCH:
            raise


def _checked_settle(process, receipt, timeout=10.0):
    """Reap the exact child and independently settle its recorded group."""
    assert process.pid == receipt.pid, (
        f"Popen pid {process.pid} does not match receipt pid {receipt.pid}")
    assert receipt.ppid == os.getpid(), (
        f"receipt for pid {receipt.pid} belongs to parent {receipt.ppid}")
    assert receipt.pgid == receipt.pid == receipt.session, (
        f"receipt for pid {receipt.pid} does not own its session/group")

    leader_exited = process.poll() is not None
    if leader_exited:
        process.wait(timeout=0)
    else:
        current = _process_fields(receipt.pid)
        assert current is not None, (
            f"live Popen pid {receipt.pid} has no readable identity")
        assert (current.ppid, current.pgid, current.session,
                current.starttime) == (
                    receipt.ppid, receipt.pgid, receipt.session,
                    receipt.starttime), (
            f"pid {receipt.pid} identity changed after receipt capture")
        assert current.state != "Z", (
            f"pid {receipt.pid} was an unwaited zombie before teardown")

    _signal_recorded_group(receipt)
    if not leader_exited:
        process.wait(timeout=timeout)
    assert process.returncode is not None, (
        f"pid {receipt.pid} was not checked through its exact Popen")

    deadline = time.monotonic() + timeout
    while True:
        if not _group_exists(receipt.pgid):
            return
        remaining = deadline - time.monotonic()
        assert remaining > 0, (
            f"reaped pid {receipt.pid}, but its recorded process group "
            f"{receipt.pgid} still exists")
        time.sleep(min(0.02, remaining))


@pytest.fixture
def jobs(tmp_path):
    mod = _load()
    mod.JOBS_DIR = tmp_path / "bd-jobs"
    mod.JOBS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    mod.JOBS_DIR.chmod(0o700)

    # Observe the production launch object without replacing subprocess.Popen
    # process-wide. The imported tool and pytest share the stdlib subprocess
    # module, so patching mod.subprocess.Popen would also patch every fixture in
    # this worker until teardown.
    spawned = []
    real_spawn = mod._LocalLaunch.spawn

    def observed_spawn(launch, user_argv):
        result = real_spawn(launch, user_argv)
        if (launch.proc is not None
                and all(launch.proc is not item[0] for item in spawned)):
            spawned.append((launch.proc, _capture_group_receipt(launch.proc)))
        return result

    mod._LocalLaunch.spawn = observed_spawn
    try:
        yield mod
    finally:
        mod._LocalLaunch.spawn = real_spawn
        failures = []
        for process, receipt in reversed(spawned):
            try:
                _checked_settle(process, receipt)
            except Exception as exc:
                failures.append(f"pid {process.pid}: {exc}")
        assert not failures, "jobs fixture teardown was incomplete: " + "; ".join(failures)


def test_progress_is_unknown_when_no_log_was_recorded(jobs):
    """An entry from before this feature, or a job launched without a log.
    UNKNOWN is a third state and it must not read as healthy."""
    state, age = jobs.progress({"pid": os.getpid()})
    assert state == "UNKNOWN", state
    assert age is None


def test_progress_is_unknown_when_the_log_has_gone(jobs, tmp_path):
    """A recorded path that no longer resolves cannot be judged either. This is
    the branch that would be easiest to collapse into PROGRESSING by accident,
    and collapsing it would mean a deleted log reads as a healthy job."""
    state, age = jobs.progress({"pid": os.getpid(),
                                "log": str(tmp_path / "not-there.log")})
    assert state == "UNKNOWN", state
    assert age is None


def test_progress_is_progressing_for_a_log_written_just_now(jobs, tmp_path):
    log = tmp_path / "fresh.log"
    log.write_text("still going\n", encoding="utf-8")
    state, age = jobs.progress({"pid": os.getpid(), "log": str(log)})
    assert state == "PROGRESSING", state
    assert age is not None and age < 60, age


def test_progress_is_stalled_for_a_log_nothing_has_touched(jobs, tmp_path):
    """The wedge, reproduced without waiting for one: backdate the mtime past
    the threshold. This is the state that was invisible on test6 for 462s."""
    log = tmp_path / "wedged.log"
    log.write_text("[ 99%]\n", encoding="utf-8")
    old = time.time() - 4000
    os.utime(log, (old, old))
    state, age = jobs.progress({"pid": os.getpid(), "log": str(log)},
                               stalled_after=300)
    assert state == "STALLED", state
    assert age is not None and age > 300, age


def test_the_threshold_is_a_parameter_not_a_constant(jobs, tmp_path):
    """The same log is PROGRESSING or STALLED depending on what you asked, so a
    caller with a legitimately quiet job can widen it rather than switch the
    signal off. Both directions asserted: a threshold that only ever tightens
    would be the over-sensitive failure section 0 names."""
    log = tmp_path / "quiet.log"
    log.write_text("x\n", encoding="utf-8")
    old = time.time() - 500
    os.utime(log, (old, old))
    assert jobs.progress({"pid": os.getpid(), "log": str(log)},
                         stalled_after=300)[0] == "STALLED"
    assert jobs.progress({"pid": os.getpid(), "log": str(log)},
                         stalled_after=900)[0] == "PROGRESSING"


def test_checked_teardown_waits_for_its_child_and_group():
    """Mutation catcher: signalling without wait leaves a procfs zombie."""
    process = subprocess.Popen(
        ["sleep", "30"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid = process.pid
    try:
        receipt = _capture_group_receipt(process)
        _checked_settle(process, receipt)
        assert process.returncode == -signal.SIGKILL, process.returncode
        assert _process_fields(pid) is None, (
            f"checked teardown retained procfs identity for pid {pid}")
    finally:
        if process.poll() is None:
            os.killpg(pid, signal.SIGKILL)
            process.wait(timeout=10)


def test_checked_teardown_settles_group_after_its_leader_exits(tmp_path):
    """The child receipt still owns cleanup after its shell leader exits."""
    marker = tmp_path / "descendant.pid"
    read_fd, write_fd = os.pipe()
    process = subprocess.Popen(
        [sys.executable, "-c", """
import os
import pathlib
import sys
import time
os.read(int(sys.argv[1]), 1)
child = os.fork()
if child:
    pathlib.Path(sys.argv[2]).write_text(str(child), encoding="utf-8")
    os._exit(0)
time.sleep(30)
""", str(read_fd), str(marker)],
        pass_fds=(read_fd,),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(read_fd)
    read_fd = None
    pid = process.pid
    child_pid = None
    try:
        receipt = _capture_group_receipt(process)

        os.write(write_fd, b"go\n")
        os.close(write_fd)
        write_fd = None
        deadline = time.monotonic() + 10
        while True:
            leader_identity = _process_fields(pid)
            if (marker.exists() and leader_identity is not None
                    and leader_identity.state == "Z"):
                break
            assert time.monotonic() < deadline, (
                f"leader {pid} did not exit with a live descendant")
            time.sleep(0.01)
        assert process.returncode is None, (
            "the regression must make checked teardown reap the exact Popen")
        child_pid = int(marker.read_text("utf-8"))
        child_identity = _process_fields(child_pid)
        assert child_identity is not None
        assert (child_identity.pgid, child_identity.session) == (pid, pid), (
            child_identity)
        assert _group_exists(pid), f"leader {pid} left no live process group"

        _checked_settle(process, receipt)
        assert process.returncode == 0
        assert not _group_exists(pid), (
            f"checked teardown retained exited leader {pid}'s process group")
    finally:
        if read_fd is not None:
            os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        # Independent test cleanup: only signal while the recorded descendant
        # still proves that this worker's child-led session owns the group.
        if child_pid is not None:
            child_identity = _process_fields(child_pid)
            if (child_identity is not None
                    and (child_identity.pgid, child_identity.session)
                    == (pid, pid)):
                os.killpg(pid, signal.SIGKILL)
        if process.poll() is None:
            os.killpg(pid, signal.SIGKILL)
            process.wait(timeout=10)
        cleanup_deadline = time.monotonic() + 10
        while _group_exists(pid) and time.monotonic() < cleanup_deadline:
            time.sleep(0.02)
        assert not _group_exists(pid), (
            f"independent cleanup retained test process group {pid}")


def test_checked_teardown_accepts_a_cleanly_completed_group():
    """A captured child that exits without descendants is already settled."""
    read_fd, write_fd = os.pipe()
    process = subprocess.Popen(
        [sys.executable, "-c",
         "import os, sys; os.read(int(sys.argv[1]), 1)", str(read_fd)],
        pass_fds=(read_fd,),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(read_fd)
    try:
        receipt = _capture_group_receipt(process)
        os.write(write_fd, b"go\n")
        os.close(write_fd)
        write_fd = None
        assert process.wait(timeout=10) == 0
        assert not _group_exists(process.pid)
        _checked_settle(process, receipt)
        assert not _group_exists(process.pid)
    finally:
        if write_fd is not None:
            os.close(write_fd)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


def test_a_locally_launched_job_gets_a_log_and_records_its_path(jobs, tmp_path):
    """THE SEAM, not the components. cmd_run sent stdout and stderr to DEVNULL,
    so a registered job produced no output anywhere -- nothing to read, and
    nothing for progress() to measure. Assert the file exists AND holds what
    the job printed, because a path recorded over an empty file would satisfy a
    weaker check while measuring nothing."""
    class _Args:
        host = None
        purpose = "test-heartbeat"
        command = ["--", "echo hello-from-the-job; sleep 2"]
        script = None

    rc = jobs.cmd_run(_Args())
    assert rc == 0, rc

    entries = jobs.load_all()
    assert len(entries) == 1, entries
    entry = entries[0]
    assert entry.get("log"), "cmd_run recorded no log path: %r" % (entry,)

    log = pathlib.Path(entry["log"])
    deadline = time.time() + 20
    while time.time() < deadline and "hello-from-the-job" not in log.read_text(
            encoding="utf-8", errors="replace"):
        time.sleep(0.2)
    body = log.read_text(encoding="utf-8", errors="replace")
    assert "hello-from-the-job" in body, (
        "the job's stdout did not reach the recorded log: %r" % body)

    state, age = jobs.progress(entry)
    assert state == "PROGRESSING", (state, age)


def test_list_names_the_state_and_the_countable_never_a_percentage(jobs, tmp_path, capsys):
    """Section 10: report the countable. The rendered line must carry the
    seconds since the last write, and must not invent a percentage."""
    log = tmp_path / "wedged.log"
    log.write_text("[ 99%]\n", encoding="utf-8")
    old = time.time() - 4000
    os.utime(log, (old, old))

    entry = jobs.register(os.getpid(), "a wedged run", "pytest tests/",
                          log=str(log))
    assert entry.get("log") == str(log)

    class _Args:
        pass

    jobs.cmd_list(_Args())
    out = capsys.readouterr().out
    assert "STALLED" in out, out
    assert "4000s" in out or "3999s" in out or "4001s" in out, (
        "the line does not carry the wall-clock since the last write: %r" % out)
    assert "%" not in out.replace("[ 99%]", ""), (
        "list invented a percentage; section 10 forbids estimated progress")
