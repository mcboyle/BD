"""O1789 dev-run-bounds: /api/dev/run concurrency cap, history trim, output buffer.

F4: ``start_run`` had no concurrency cap and ``_trim_history`` evicted the oldest
records even while they were RUNNING, so a live process lost its record and could
no longer be cancelled ("unknown run_id") until the 1 h outer cap fired.
F5: the output loop appended each line before the 1 MB check and re-summed the
whole buffer per line -- quadratic in line count and unbounded in memory.

Processes are fakes (row350 pattern): no real child is ever launched. F5 measures
work deterministically by counting ``len()`` calls on the streamed lines rather
than asserting elapsed time.
"""
from __future__ import annotations

import os
import signal
import threading
import time

import pytest


BD_GATE_SCOPE = "module"


class _FakeProcess:
    """A held process whose stdout blocks until released (cancel or teardown)."""

    _next_pid = 70000

    def __init__(self, lines=None):
        _FakeProcess._next_pid += 1
        self.pid = _FakeProcess._next_pid
        self.returncode = None
        self.released = threading.Event()
        self._lines = None if lines is None else iter(lines)
        self.consumed = 0
        self.stdout = self

    def __iter__(self):
        return self

    def __next__(self):
        if self._lines is not None:
            try:
                line = next(self._lines)
            except StopIteration:
                self.returncode = 0
                raise
            self.consumed += 1
            return line
        assert self.released.wait(10), "fake process was never released"
        raise StopIteration

    def close(self):
        pass

    def poll(self):
        return self.returncode

    def wait(self):
        if self._lines is None:
            assert self.released.wait(10)
        return self.returncode

    def release(self, returncode=0):
        if self.returncode is None:
            self.returncode = returncode
        self.released.set()


@pytest.fixture
def fake_runs(monkeypatch):
    from bulk_downloader import dev_tools as subject

    procs: list[_FakeProcess] = []
    by_fd: dict[int, _FakeProcess] = {}
    pending_lines: list = []

    def popen(*_args, **_kwargs):
        proc = _FakeProcess(pending_lines.pop(0) if pending_lines else None)
        procs.append(proc)
        return proc

    def pidfd_open(pid, flags=0):
        fd = os.open(os.devnull, os.O_RDONLY)
        by_fd[fd] = next(p for p in procs if p.pid == pid)
        return fd

    def deliver(fd, sig):
        by_fd[fd].release(-int(sig))

    monkeypatch.setattr(subject, "_build_cmd", lambda *_a: ["fake-command"])
    monkeypatch.setattr(subject.subprocess, "Popen", popen)
    monkeypatch.setattr(subject.os, "pidfd_open", pidfd_open, raising=False)
    monkeypatch.setattr(signal, "pidfd_send_signal", deliver, raising=False)
    # Fake pids must never reach a real killpg on the test host.
    monkeypatch.setattr(subject, "_reap_worker_error", lambda _proc: "reaped")
    with subject._runs_lock:
        subject._runs.clear()
    try:
        yield subject, procs, pending_lines
    finally:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for proc in list(procs):  # includes a Popen that landed late
                proc.release()
            with subject._runs_lock:
                if not any(r["state"] == "running" for r in subject._runs):
                    break
            time.sleep(0.01)
        with subject._runs_lock:
            for run in subject._runs:
                subject._close_run_identity(run)
            subject._runs.clear()


def _await_pid(subject, run_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = subject.run_status(run_id)
        if status and status.get("pid"):
            return status
        time.sleep(0.005)
    raise AssertionError(f"run {run_id} never acquired its process")


def _await_settled(subject, run_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = subject.run_status(run_id)
        if status and status["state"] != "running":
            return status
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} never settled")


def test_concurrent_runs_are_capped_and_every_live_run_stays_cancellable(fake_runs):
    subject, _procs, _ = fake_runs
    accepted, refused = [], []
    for _ in range(52):
        started = subject.start_run("tests/probe.py")
        if started.get("ok"):
            accepted.append(started["run_id"])
            _await_pid(subject, started["run_id"])
        else:
            refused.append(started)
    assert refused, (
        f"F4: {len(accepted)} concurrent dev runs accepted with no cap")
    assert "concurrent" in refused[0]["error"], refused[0]
    for run_id in accepted:
        assert subject.run_status(run_id) is not None, (
            f"F4: live run {run_id} evicted from history while running")
    assert subject.cancel_run(accepted[0]) == {"ok": True}

    # Negative control: the cap releases -- a cancelled slot admits a new run.
    again = subject.start_run("tests/probe.py")
    assert again.get("ok") is True, again
    _await_pid(subject, again["run_id"])


def test_trim_never_evicts_a_running_run_but_still_bounds_finished_ones(fake_runs):
    subject, _procs, _ = fake_runs
    live = subject.start_run("tests/probe.py")["run_id"]
    _await_pid(subject, live)
    finished = [{"run_id": f"done{i:03d}", "state": "done", "output": ""}
                for i in range(60)]
    with subject._runs_lock:
        subject._runs.extend(finished)
        subject._trim_history()
        kept = list(subject._runs)
    assert subject.run_status(live) is not None, (
        "F4: _trim_history evicted a RUNNING run; it is now uncancellable")
    assert subject.cancel_run(live) == {"ok": True}
    # Negative control: finished history is still bounded and drops oldest first.
    assert len(kept) <= subject._MAX_HISTORY
    assert kept[-1]["run_id"] == "done059"
    assert "done000" not in {r["run_id"] for r in kept}


class _CountedLine(str):
    calls = 0

    def __len__(self):
        _CountedLine.calls += 1
        return str.__len__(self)


def test_output_buffer_work_is_linear_and_keeps_the_tail(fake_runs):
    subject, procs, pending = fake_runs
    n_lines = 3000
    lines = [_CountedLine(f"{i:06d}" + "x" * 393 + "\n") for i in range(n_lines)]
    pending.append(lines)
    _CountedLine.calls = 0
    run_id = subject.start_run("tests/probe.py")["run_id"]
    status = _await_settled(subject, run_id)
    calls = _CountedLine.calls
    assert procs[-1].consumed == n_lines, "stdout was not drained to EOF"
    assert status["state"] == "done", status
    assert calls <= 4 * n_lines, (
        f"F5: buffer accounting is quadratic: len() called {calls} times "
        f"for {n_lines} lines")
    out = status["output"]
    assert out.startswith("[output truncated to 1MB] ...\n")
    assert out.endswith(lines[-1])
    assert len(out) <= 1_000_000


def test_under_cap_output_is_published_whole(fake_runs):
    subject, _procs, pending = fake_runs
    lines = [f"line {i}\n" for i in range(50)]
    pending.append(lines)
    run_id = subject.start_run("tests/probe.py")["run_id"]
    status = _await_settled(subject, run_id)
    assert status["output"] == "".join(lines)


def test_route_refuses_over_cap_with_429(fake_runs, fresh_app):
    subject, _procs, _ = fake_runs
    codes = []
    for _ in range(52):
        r = fresh_app.post("/api/dev/run",
                           json={"target": "tests/probe.py", "kind": "file"})
        codes.append(r.status_code)
        if r.status_code == 202:
            _await_pid(subject, r.get_json()["run_id"])
        else:
            body = r.get_json()
            assert body["ok"] is False and "concurrent" in body["error"], body
            break
    assert codes[-1] == 429, f"F4: route accepted {codes.count(202)} runs, no 429"
    assert set(codes[:-1]) == {202}, codes
