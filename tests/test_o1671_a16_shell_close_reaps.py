import errno
import os
import re
import signal
import threading
import time

import pytest
from tools import cockpit_shell as sh

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.skipif(not sh._PTY_OK, reason="pty unavailable")


@pytest.fixture
def session(monkeypatch, tmp_path):
    monkeypatch.setattr(sh, "_shell_pref", lambda: "1")
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path))
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    reader = sh._reader

    def held_reader(sid):
        entered.set()
        try:
            assert release.wait(5), "reader release was never signalled"
            reader(sid)
        finally:
            finished.set()

    monkeypatch.setattr(sh, "_reader", held_reader)
    sid = sh.shell_open()["session"]
    sess = sh._SESSIONS[sid]
    pid, fd = sess["pid"], sess["fd"]
    try:
        assert entered.wait(5), "shell reader never started"
        assert pid > 0 and os.waitpid(pid, os.WNOHANG) == (0, 0)
        os.fstat(fd)
        yield sid, sess, pid, fd
    finally:
        sh.shell_close(sid)
        release.set()
        assert finished.wait(5), "shell reader never finished"
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        if sess.get("fd") == fd:
            try:
                os.close(fd)
            except OSError:
                pass


def test_close_reaps_real_child(session):
    sid, _, pid, _ = session
    assert sh.shell_close(sid) == {"closed": True}
    assert sid not in sh._SESSIONS
    try:
        remaining = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return
    pytest.fail(f"SHELL-CLOSE-UNREAPED: pid={pid} waitpid={remaining}")


@pytest.fixture
def input_session(monkeypatch, tmp_path):
    monkeypatch.setattr(sh, "_shell_pref", lambda: "1")
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path))
    entered, release, finished = (threading.Event() for _ in range(3))
    reader = sh._reader

    def held_select(*args):
        entered.set()
        assert release.wait(5), "reader release never signalled"
        return [], [], []

    def observed_reader(sid):
        try:
            reader(sid)
        finally:
            finished.set()

    monkeypatch.setattr(sh._select, "select", held_select)
    monkeypatch.setattr(sh, "_reader", observed_reader)
    sid = sh.shell_open()["session"]
    sess = sh._SESSIONS[sid]
    pid, fd = sess["pid"], sess["fd"]
    try:
        assert entered.wait(5), "reader never retained the live descriptor"
        assert os.waitpid(pid, os.WNOHANG) == (0, 0)
        os.fstat(fd)
        yield sid, release, finished
    finally:
        release.set()
        assert finished.wait(5), "reader never finished"
        sh.shell_close(sid)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


def test_input_endpoint_handles_concurrent_close(input_session, monkeypatch):
    from flask import Flask
    from tools.cockpit_console import bp

    sid, reader_release, reader_finished = input_session
    app = Flask(__name__)
    app.register_blueprint(bp)
    with app.test_client() as client:
        live = client.post("/cockpit/api/shell/input", json={"session": sid, "data": "\n"})
    assert live.status_code == 200, "SHELL-INPUT-LIVE-CONTROL"
    entered, release = threading.Event(), threading.Event()
    responses = []
    audit = sh._audit

    def held_audit(session_id, data):
        if data == "input racing close":
            entered.set()
            assert release.wait(5), "input release never signalled"
        audit(session_id, data)

    monkeypatch.setattr(sh, "_audit", held_audit)

    def input_request():
        with app.test_client() as client:
            responses.append(client.post("/cockpit/api/shell/input", json={"session": sid, "data": "input racing close"}))

    thread = threading.Thread(target=input_request)
    try:
        thread.start()
        assert entered.wait(5), "input never retained the live session"
        assert sh.shell_close(sid) == {"closed": True}
        reader_release.set()
        assert reader_finished.wait(5), "reader never released descriptor ownership"
        release.set()
        thread.join(5)
        assert not thread.is_alive() and len(responses) == 1
        assert responses[0].status_code == 403, "SHELL-INPUT-CLOSE-HTTP: expected 403, got " + str(responses[0].status_code)
    finally:
        release.set()
        thread.join(5)
        assert not thread.is_alive()


def test_close_closes_pty_without_reader_cleanup(session):
    sid, _, _, fd = session
    sh.shell_close(sid)
    with pytest.raises(OSError) as error:
        os.fstat(fd)
    assert error.value.errno == errno.EBADF


def test_close_tolerates_already_reaped_child(session):
    sid, _, pid, fd = session
    os.kill(pid, signal.SIGKILL)
    assert os.waitpid(pid, 0)[0] == pid
    assert sh.shell_close(sid) == {"closed": True}
    assert sh.shell_close(sid) == {"closed": True}
    with pytest.raises(OSError) as error:
        os.fstat(fd)
    assert error.value.errno == errno.EBADF


def test_reader_does_not_close_reused_fd(monkeypatch, tmp_path):
    monkeypatch.setattr(sh, "_shell_pref", lambda: "1")
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path))
    entered, release, finished = (threading.Event() for _ in range(3))
    reader = sh._reader

    def held_select(*args):
        entered.set()
        assert release.wait(5), "select release was never signalled"
        return [], [], []

    def observed_reader(sid):
        try:
            reader(sid)
        finally:
            finished.set()

    monkeypatch.setattr(sh._select, "select", held_select)
    monkeypatch.setattr(sh, "_reader", observed_reader)
    sid = sh.shell_open()["session"]
    pid, fd = (sh._SESSIONS[sid][key] for key in ("pid", "fd"))
    replacement = None
    try:
        assert entered.wait(5), "reader never reached select"
        sh.shell_close(sid)
        replacement = os.open(os.devnull, os.O_RDONLY)
        if replacement != fd:
            os.dup2(replacement, fd)
            os.close(replacement)
            replacement = fd
        release.set()
        assert finished.wait(5), "reader never finished"
        os.fstat(replacement)  # delayed reader must not close a reused descriptor
    finally:
        release.set()
        assert finished.wait(5), "reader never finished"
        sh.shell_close(sid)
        if replacement is not None:
            os.close(replacement)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


def test_close_reports_bounded_reap_timeout(monkeypatch):
    fd = os.open(os.devnull, os.O_RDONLY)
    calls = []
    sess = {"pid": 123456789, "fd": fd, "alive": True, "lock": threading.Lock()}
    monkeypatch.setattr(sh, "_SESSIONS", {"timeout": sess})
    monkeypatch.setattr(sh.os, "kill", lambda pid, sig: calls.append((pid, sig)))

    def unreaped(pid, options):
        calls.append((pid, options))
        return 0, 0

    monkeypatch.setattr(sh.os, "waitpid", unreaped)
    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(sh.time, "monotonic", lambda: next(ticks))
    with pytest.raises(sh.ShellError, match="shell child did not exit after SIGKILL"):
        sh.shell_close("timeout")
    assert calls == [(123456789, signal.SIGKILL), (123456789, os.WNOHANG)]
    with pytest.raises(OSError) as error:
        os.fstat(fd)
    assert error.value.errno == errno.EBADF


@pytest.fixture
def live_shell(monkeypatch, tmp_path):
    """A real shell with the real reader. Teardown drains the PTY slave first (a write blocked on a full PTY queue
    only ends when someone reads it -- not when the slave closes), then kills what the test started and the shell."""
    monkeypatch.setattr(sh, "_shell_pref", lambda: "1")
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path))
    sid = sh.shell_open()["session"]
    state = {"sid": sid, "pid": sh._SESSIONS[sid]["pid"], "started": [], "slave": None, "writer": None}
    try:
        yield state
    finally:
        _drain(state)
        for p in (*state["started"], state["pid"]):
            try:
                os.kill(p, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            sh.shell_close(sid)
        except sh.ShellError:
            pass
        try:
            os.waitpid(state["pid"], 0)
        except ChildProcessError:
            pass


def _drain(state):
    """Read the PTY slave until the blocked input writer finishes (bounded); returns whether it finished."""
    writer, slave = state["writer"], state["slave"]
    if writer is None or slave is None:
        return True
    fd = os.open(slave, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
    try:
        deadline = time.monotonic() + 10
        while writer.is_alive() and time.monotonic() < deadline:
            if _select_readable(fd):
                try:
                    os.read(fd, 65536)
                except BlockingIOError:
                    pass
            writer.join(0)
    finally:
        os.close(fd)
    state["writer"] = None
    return not writer.is_alive()


def _select_readable(fd):
    import select

    return bool(select.select([fd], [], [], 0.2)[0])


def _within(fn, seconds):
    out = []
    worker = threading.Thread(target=lambda: out.append(fn()), daemon=True)
    worker.start()
    worker.join(seconds)
    return not worker.is_alive(), out


def test_blocked_input_write_does_not_hold_poll_or_close(live_shell):
    # A raw-mode program that never reads stdin: a large input fills the PTY queue and the write blocks (pre-existing,
    # BASE too). That writer must not hold the session lock, or poll/close -- and close's reap -- wait behind it.
    sid, pid = live_shell["sid"], live_shell["pid"]
    sh.shell_input(sid, "stty raw -echo; sh -c 'echo SPID:$$; exec sleep 60'\n")
    deadline, match = time.monotonic() + 10, None
    while match is None and time.monotonic() < deadline:
        match = re.search(r"SPID:(\d+)", sh.shell_poll(sid, 0)["data"])
        time.sleep(0.05)
    assert match, "raw-mode program never started"
    spid = int(match[1])
    live_shell["started"].append(spid)
    live_shell["slave"] = os.readlink(f"/proc/{spid}/fd/0")

    def flood():
        try:
            sh.shell_input(sid, "x" * 262144)
        except sh.ShellError:
            pass

    writer = live_shell["writer"] = threading.Thread(target=flood, daemon=True)
    writer.start()
    writer.join(1.0)
    assert writer.is_alive(), "positive control: the input write did not block, so this probe cannot see the lock"
    done, _ = _within(lambda: sh.shell_poll(sid, 0), 3)
    assert done, "SHELL-POLL-BLOCKED-BY-INPUT: poll waited on a lock held across a blocking input write"
    done, closed = _within(lambda: sh.shell_close(sid), 5)
    assert done and closed == [{"closed": True}], "SHELL-CLOSE-BLOCKED-BY-INPUT: close waited on the input writer"
    try:
        remaining = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        remaining = None
    assert remaining is None, f"SHELL-CLOSE-UNREAPED: pid={pid} waitpid={remaining}"
    assert _drain(live_shell), "input writer still blocked after its PTY queue was drained"


def test_signal_racing_close_is_a_shell_error(monkeypatch):
    # The same race as input/close (r1): close cleared fd after signal passed _require. BASE and r2: TypeError -> 500.
    sess = {"pid": 123456789, "fd": None, "alive": True, "lock": threading.Lock(), "last": 0.0}
    monkeypatch.setattr(sh, "_shell_pref", lambda: "1")
    monkeypatch.setattr(sh, "_SESSIONS", {"racing": sess})
    with pytest.raises(sh.ShellError, match="no such shell session"):
        sh.shell_signal("racing", "INT")


def test_input_write_never_lands_on_a_reused_fd(input_session, monkeypatch, tmp_path):
    # close runs after input released the lock but before its write; the old fd number is then reused by another
    # file. The write must go to the PTY it checked (a dup), never to whatever now owns that number.
    sid, reader_release, reader_finished = input_session
    old_fd = sh._SESSIONS[sid]["fd"]
    victim = tmp_path / "victim"
    real_write = os.write
    seen = {}

    def racing_write(fd, data):
        if not seen:
            done, _ = _within(lambda: sh.shell_close(sid), 3)
            seen["closed"] = done
            if done:
                reader_release.set()
                assert reader_finished.wait(5), "reader never finished"
                reused = os.open(victim, os.O_WRONLY | os.O_CREAT, 0o600)
                if reused != old_fd:
                    os.dup2(reused, old_fd)
                    os.close(reused)
                seen["reused"] = True
        return real_write(fd, data)

    monkeypatch.setattr(sh.os, "write", racing_write)
    try:
        try:
            sh.shell_input(sid, "must not reach the reused fd")
        except sh.ShellError:
            pass
    finally:
        monkeypatch.setattr(sh.os, "write", real_write)
        if seen.get("reused"):
            os.close(old_fd)
    assert seen.get("closed"), "SHELL-CLOSE-BLOCKED-BY-INPUT: close waited on the input writer"
    assert victim.read_bytes() == b"", "SHELL-INPUT-WROTE-REUSED-FD"


def test_input_and_signal_leak_no_descriptor(input_session):
    sid = input_session[0]
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(20):
        sh.shell_input(sid, "\n")
        sh.shell_signal(sid, "INT")
    after = len(os.listdir("/proc/self/fd"))
    sh.shell_close(sid)  # the held reader (input_session) only finishes once the session is gone
    assert after == before, "SHELL-WRITE-FD-LEAK"
