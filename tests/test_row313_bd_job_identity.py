"""Row 313: ``bd-job`` owns a name and a non-reusable process identity."""
from __future__ import annotations

from collections import Counter
import argparse
import errno
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import uuid


BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
BD_JOB = REPO / "toolchain" / "bin" / "bd-job"


def _load_bd_job():
    name = f"row313_bd_job_{uuid.uuid4().hex}"
    loader = importlib.machinery.SourceFileLoader(name, str(BD_JOB))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _process_receipt(pid: int, *, token: str) -> dict[str, object]:
    raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    head, tail_text = raw.rsplit(") ", 1)
    tail = tail_text.split()
    receipt = {
        "schema": 1,
        "token": token,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip(),
        "pid": int(head.split(" (", 1)[0]),
        "pgid": int(tail[2]),
        "sid": int(tail[3]),
        "start_ticks": int(tail[19]),
    }
    assert receipt["pid"] == pid
    return receipt


def _claim_path(root: Path, name: str) -> Path:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return root / (".claim-" + digest)


def _publish_running_fixture(subject, root: Path, name: str, receipt) -> None:
    job_dir = Path(subject.jdir(name))
    job_dir.mkdir(parents=True)
    (job_dir / "pid").write_text(f"{receipt['pid']}\n", encoding="ascii")
    (job_dir / "identity.json").write_text(
        json.dumps(receipt, sort_keys=True) + "\n", encoding="ascii"
    )
    _claim_path(root, name).write_text(
        f"{receipt['token']}\n", encoding="ascii"
    )


def _start_bystander() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import signal; signal.pause()"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def test_exactly_one_concurrent_starter_acquires_the_name(tmp_path, monkeypatch):
    """Force both old starters beyond the check and count real launches."""
    subject = _load_bd_job()
    subject.ROOT = str(tmp_path / "jobs")
    name = "same-name"
    job_dir = Path(subject.jdir(name))

    first_in_gap = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    first_finished = threading.Event()
    entered: list[str] = []
    gap_fires: list[str] = []
    results: list[int] = []
    errors: list[BaseException] = []
    launches: list[subprocess.Popen[bytes]] = []
    record_lock = threading.Lock()
    real_makedirs = os.makedirs
    real_popen = subprocess.Popen

    def controlled_makedirs(path, *args, **kwargs):
        real_makedirs(path, *args, **kwargs)
        if Path(path) != job_dir:
            return
        role = threading.current_thread().name
        with record_lock:
            gap_fires.append(role)
        if role == "starter-a":
            first_in_gap.set()
            assert release_first.wait(5), "starter A was never released"

    def counted_popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        with record_lock:
            launches.append(child)
        return child

    monkeypatch.setattr(subject.os, "makedirs", controlled_makedirs)
    monkeypatch.setattr(subject.subprocess, "Popen", counted_popen)

    def start(role: str, finished: threading.Event) -> None:
        try:
            with record_lock:
                entered.append(role)
            rc = subject.cmd_start(argparse.Namespace(
                name=name,
                cmd=["sh", "-c", "exit 0"],
            ))
            with record_lock:
                results.append(rc)
        except BaseException as exc:  # surfaced in the parent with all evidence
            with record_lock:
                errors.append(exc)
        finally:
            finished.set()

    first = threading.Thread(
        target=start, args=("starter-a", first_finished), name="starter-a"
    )
    second = threading.Thread(
        target=start, args=("starter-b", second_finished), name="starter-b"
    )
    first.start()
    assert first_in_gap.wait(5), "the fixture never reached the check/create gap"
    assert job_dir.is_dir(), "starter A did not create the contested directory"
    second.start()
    try:
        assert second_finished.wait(5), (
            "starter B did not settle while starter A held the forced gap"
        )
        assert not first_finished.is_set(), (
            "starter A escaped the forced gap before starter B was adjudicated"
        )
    finally:
        release_first.set()

    first.join(5)
    second.join(5)
    assert not first.is_alive() and not second.is_alive()
    for child in launches:
        child.wait(timeout=5)

    assert Counter(entered) == Counter({"starter-a": 1, "starter-b": 1})
    assert gap_fires.count("starter-a") == 1
    assert errors == []
    assert Counter(results) == Counter({0: 1, 3: 1}), (
        "both concurrent starters acquired one job name"
    )
    assert len(launches) == 1, "the losing starter launched a second process"


def test_reused_pid_bystander_is_unknown_and_never_signalled(
    tmp_path, monkeypatch, capsys
):
    subject = _load_bd_job()
    root = tmp_path / "jobs"
    subject.ROOT = str(root)
    name = "reused-pid"
    bystander = _start_bystander()
    legacy_signals: list[int] = []
    pidfd_signals: list[int] = []
    real_killpg = os.killpg
    try:
        receipt = _process_receipt(bystander.pid, token="row313-stale-token")
        assert receipt["pid"] == receipt["pgid"] == receipt["sid"]
        stale = dict(receipt)
        stale["start_ticks"] = int(receipt["start_ticks"]) - 1
        assert stale["start_ticks"] != receipt["start_ticks"]
        _publish_running_fixture(subject, root, name, stale)

        def record_legacy_signal(pgid, sig):
            if sig == 0:
                return real_killpg(pgid, sig)
            legacy_signals.append(sig)
            return None

        monkeypatch.setattr(subject.os, "killpg", record_legacy_signal)
        monkeypatch.setattr(subject.signal, "pidfd_send_signal", lambda fd, sig: (
            pidfd_signals.append(sig)
        ))
        monkeypatch.setattr(select, "select", lambda read, _write, _error, _wait: (
            (read, [], [])
        ))
        monkeypatch.setattr(subject.time, "sleep", lambda _seconds: None)

        rc = subject.cmd_kill(argparse.Namespace(name=name))
        output = capsys.readouterr().out

        assert rc == 3
        assert "UNKNOWN" in output and "identity" in output
        assert subject._state(name) == "unknown"
        assert legacy_signals == []
        assert pidfd_signals == []
        assert bystander.poll() is None, "the PID-reuse bystander was killed"
    finally:
        if bystander.poll() is None:
            bystander.kill()
        bystander.wait(timeout=5)


def test_matching_identity_reaches_exact_pidfd_signal_once(
    tmp_path, monkeypatch
):
    subject = _load_bd_job()
    root = tmp_path / "jobs"
    subject.ROOT = str(root)
    name = "matching-identity"
    owned = _start_bystander()
    legacy_signals: list[int] = []
    pidfd_signals: list[tuple[int, int]] = []
    real_killpg = os.killpg
    try:
        receipt = _process_receipt(owned.pid, token="row313-matched-token")
        assert receipt["pid"] == receipt["pgid"] == receipt["sid"]
        _publish_running_fixture(subject, root, name, receipt)

        def record_legacy_signal(pgid, sig):
            if sig == 0:
                return real_killpg(pgid, sig)
            legacy_signals.append(sig)
            return None

        monkeypatch.setattr(subject.os, "killpg", record_legacy_signal)
        monkeypatch.setattr(
            subject.signal,
            "pidfd_send_signal",
            lambda fd, sig: pidfd_signals.append((fd, sig)),
        )
        monkeypatch.setattr(select, "select", lambda read, _write, _error, _wait: (
            (read, [], [])
        ))
        monkeypatch.setattr(subject.time, "sleep", lambda _seconds: None)

        assert subject.cmd_kill(argparse.Namespace(name=name)) == 0
        assert subject._state(name) == "running"
        assert legacy_signals == []
        assert len(pidfd_signals) == 1
        assert pidfd_signals[0][1] == signal.SIGTERM
        assert owned.poll() is None, "the signal recorder must not kill its control"
    finally:
        if owned.poll() is None:
            owned.kill()
        owned.wait(timeout=5)


def test_unavailable_pidfd_measurement_is_unknown_not_ok(
    tmp_path, monkeypatch, capsys
):
    subject = _load_bd_job()
    root = tmp_path / "jobs"
    subject.ROOT = str(root)
    name = "pidfd-unavailable"
    owned = _start_bystander()
    sent: list[tuple[int, int]] = []
    try:
        receipt = _process_receipt(owned.pid, token="row313-unavailable-token")
        assert receipt["pid"] == receipt["pgid"] == receipt["sid"]
        _publish_running_fixture(subject, root, name, receipt)

        monkeypatch.setattr(
            subject.os,
            "pidfd_open",
            lambda _pid, _flags=0: (_ for _ in ()).throw(
                OSError(errno.ENOSYS, "pidfd disabled by control")
            ),
            raising=False,
        )
        monkeypatch.setattr(subject.signal, "pidfd_send_signal", lambda fd, sig: (
            sent.append((fd, sig))
        ))

        assert subject._state(name) == "unknown"
        assert subject.cmd_kill(argparse.Namespace(name=name)) == 3
        output = capsys.readouterr().out
        assert "UNKNOWN" in output and "identity" in output
        assert sent == []
        assert owned.poll() is None
    finally:
        if owned.poll() is None:
            owned.kill()
        owned.wait(timeout=5)


def test_forced_kill_is_relayed_to_the_owned_process_group(
    tmp_path, monkeypatch, capsys
):
    subject = _load_bd_job()
    root = tmp_path / "jobs"
    subject.ROOT = str(root)
    name = "term-ignoring-descendant"
    ready = tmp_path / "descendant-ready"
    os.mkfifo(ready)
    child_pidfd = -1
    script = (
        "import os, signal, sys\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "with open(sys.argv[1], 'w', encoding='ascii') as stream:\n"
        "    stream.write(f'{os.getpid()}:{os.getpgrp()}:{os.getsid(0)}\\n')\n"
        "while True:\n"
        "    signal.pause()\n"
    )
    try:
        assert subject.cmd_start(argparse.Namespace(
            name=name,
            cmd=[sys.executable, "-c", script, str(ready)],
        )) == 0
        with ready.open("r", encoding="ascii") as stream:
            lines = stream.readlines()
        assert len(lines) == 1, "the fixture did not publish exactly one child"
        child_pid, child_pgid, child_sid = map(int, lines[0].strip().split(":"))
        identity = json.loads(
            (Path(subject.jdir(name)) / "identity.json").read_text(
                encoding="ascii"
            )
        )
        assert identity["pid"] == identity["pgid"] == identity["sid"]
        assert child_pid != identity["pid"]
        assert child_pgid == child_sid == identity["pid"]
        child_pidfd = os.pidfd_open(child_pid, 0)
        assert select.select([child_pidfd], [], [], 0)[0] == []

        monkeypatch.setattr(subject, "_TERM_GRACE_S", 0.05)
        assert subject.cmd_kill(argparse.Namespace(name=name)) == 0
        output = capsys.readouterr().out

        assert "SIGKILL after grace" in output
        assert select.select([child_pidfd], [], [], 5)[0] == [child_pidfd], (
            "the exact supervisor exited but left its TERM-ignoring child alive"
        )
    finally:
        if child_pidfd >= 0:
            if not select.select([child_pidfd], [], [], 0)[0]:
                signal.pidfd_send_signal(child_pidfd, signal.SIGKILL)
                assert select.select([child_pidfd], [], [], 5)[0] == [child_pidfd]
            os.close(child_pidfd)


def test_transform_control_imports_bd_job_without_asserting_identity():
    subject = _load_bd_job()
    assert subject.main.__name__ == "main"
