"""Kill the processes a test leaves running when pytest-timeout cuts it off (H404).

The precut runs ``--timeout=240 --timeout-method=signal``.  The alarm raises
``Failed`` inside the test, which cuts the test off but not its processes: a
``Popen`` child the test never waited for keeps running, and
``subprocess.run`` kills only its direct child, so a grandchild (a shelled
``bd-tool-lint``) is orphaned to init and keeps burning CPU on the pool host.

A test has no process group of its own to kill (pytest shares one with the
xdist controller), so the session tags its processes instead, the way
Jenkins' process-tree killer does: every process it starts inherits
``PYTEST_REAP_TOKEN``.  When a setup, call or teardown phase ends in a
pytest-timeout failure, every process started during that phase that carries
this session's token or descends from this process gets SIGTERM, then
SIGKILL after a bounded wait.  A process whose parent chain reaches a tagged
process started before the phase (a fixture's server) is left to its owner.

Out of reach: a child that drops the token from its environment and is then
orphaned, and the ``thread`` timeout method, which exits the worker before any
hook runs.  Without /proc the plugin does nothing.
"""

from __future__ import annotations

import os
import signal
import time
from uuid import uuid4

import pytest


_KEY = "PYTEST_REAP_TOKEN"
_TIMEOUT = "from pytest-timeout"  # pytest_timeout.PYTEST_FAILURE_MESSAGE
_TERM_WAIT = 2.0
_KILL_WAIT = 1.0
_POLL = 0.05
_ROUNDS = 2  # the second catches what the first round's processes forked
_needle = b""
_hz = 0


def pytest_configure(config):
    global _needle, _hz
    mine = f"{os.getpid()}-"
    if not os.environ.get(_KEY, "").startswith(mine):
        # an xdist worker inherits its controller's token: each session tags its own
        os.environ[_KEY] = mine + uuid4().hex[:12]
    _needle = f"{_KEY}={os.environ[_KEY]}".encode()
    if os.path.exists("/proc/self/stat") and hasattr(time, "CLOCK_BOOTTIME"):
        _hz = os.sysconf("SC_CLK_TCK")


def _stat(pid):
    """(state, ppid, start tick) of a live pid, or None."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            rest = f.read().rsplit(b")", 1)[1].split()
        return rest[0].decode(), int(rest[1]), int(rest[19])
    except (OSError, IndexError, ValueError):
        return None


def _tagged(pid):
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            return _needle in f.read().split(b"\0")
    except OSError:
        return False


def _mark():
    """Where a phase starts: (last pid allocated, boot-clock tick), or None."""
    if not _hz:
        return None
    try:
        with open("/proc/sys/kernel/ns_last_pid", "rb") as f:
            last = int(f.read())
    except (OSError, ValueError):
        last = None
    return last, int(time.clock_gettime(time.CLOCK_BOOTTIME) * _hz)


def _victims(mark):
    """{pid: start tick} of the processes this phase started and left running."""
    last, t0 = mark
    table = {}
    for name in os.listdir("/proc"):
        if name.isdigit() and (st := _stat(int(name))) is not None:
            table[int(name)] = st
    me, above = os.getpid(), set()
    p = table.get(me, (None, 0))[1]
    while p in table and p not in above:
        above.add(p)
        p = table[p][1]

    def new(pid):
        # a tick is 10 ms: a process that shares the phase's first tick is new
        # only if its pid was allocated after the phase started
        start = table[pid][2]
        return start > t0 or (start == t0 and (last is None or pid > last))

    def owner(pid):
        q = table[pid][1]
        for _ in range(len(table)):  # the table is not one snapshot: never loop
            if q not in table or q in above:
                break
            if q == me:
                return "me"
            if not new(q):
                return "older" if _tagged(q) else None
            q = table[q][1]
        return None  # orphaned: init or a subreaper took it

    victims = {}
    for pid, (state, _, start) in table.items():
        if pid == me or pid in above or state in "ZX" or not new(pid):
            continue
        who = owner(pid)
        if who == "me" or (who is None and _tagged(pid)):
            victims[pid] = start
    return victims


def _alive(pid, start):
    st = _stat(pid)
    return st is not None and st[2] == start and st[0] not in "ZX"


def _signal(pid, start, sig):
    """Signal pid only while it is still the process that started at `start`."""
    try:
        fd = os.pidfd_open(pid)  # pins the process: a recycled pid cannot be hit
    except ProcessLookupError:
        return
    except (AttributeError, OSError):
        fd = None
    try:
        if _alive(pid, start):
            if fd is None:
                os.kill(pid, sig)
            else:
                signal.pidfd_send_signal(fd, sig)
    except OSError:
        pass
    finally:
        if fd is not None:
            os.close(fd)


def _wait(procs, seconds):
    deadline = time.monotonic() + seconds
    while True:
        left = {pid: start for pid, start in procs.items() if _alive(pid, start)}
        if not left or time.monotonic() >= deadline:
            return left
        time.sleep(_POLL)


def _cmd(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return " ".join(f.read().decode(errors="replace").replace("\0", " ").split())[:160]
    except OSError:
        return "?"


def _reap(item, when, mark):
    lines, left = [], {}
    for _ in range(_ROUNDS):
        victims = _victims(mark)
        if not victims:
            break
        lines += [f"  {pid} {_cmd(pid)}" for pid in sorted(victims)]
        for pid, start in victims.items():
            _signal(pid, start, signal.SIGTERM)
        left = _wait(victims, _TERM_WAIT)
        for pid, start in left.items():
            _signal(pid, start, signal.SIGKILL)
        left = _wait(left, _KILL_WAIT)
    if lines:
        text = [f"H404: the timed-out {when} left {len(lines)} process(es); SIGTERM, then SIGKILL:"]
        text += lines
        text += [f"STILL ALIVE: {pid} {_cmd(pid)}" for pid in sorted(left)]
        item.add_report_section(when, "timeout-reap", "\n".join(text) + "\n")


def _reaping(item, when):
    mark = _mark()
    try:
        return (yield)
    except pytest.fail.Exception as exc:
        if mark is not None and _TIMEOUT in str(exc):
            try:
                _reap(item, when, mark)
            except Exception as err:  # the timeout stays the test's outcome
                item.add_report_section(when, "timeout-reap", f"H404: reap failed: {err!r}\n")
        raise


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    return (yield from _reaping(item, "setup"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    return (yield from _reaping(item, "call"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    return (yield from _reaping(item, "teardown"))
