"""Row 350: successful job APIs must agree with their durable lifecycle state."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import locale
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import time


BD_GATE_SCOPE = "module"


def _linux_process_start(pid: int) -> str:
    raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    tail = raw.rsplit(")", 1)[1].split()
    assert len(tail) > 19, "the live-process fixture has no start-time field"
    return tail[19]


def _linux_boot_id() -> str:
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
        encoding="ascii"
    ).strip()
    assert boot_id and boot_id.lower() != "unknown"
    return boot_id


def _seed_capture(bd_app, tmp_path: Path, sid: str, marker: dict) -> None:
    draft = tmp_path / f"{sid}.template-draft.json"
    assert not draft.exists(), "the no-draft recovery precondition is false"
    marker = {
        "profile_dir": str(tmp_path / f"{sid}-profile"),
        "wacz": str(tmp_path / f"{sid}.wacz"),
        "draft": str(draft),
        "display": ":99",
        **marker,
    }
    bd_app.s_cfg[sid] = {
        "name": sid,
        "login_url": "https://row350.invalid/",
        "template_onboarding": "capture_required",
        "template_capture": marker,
    }


def test_recycled_capture_pid_reaches_age_recovery(
    fresh_app, tmp_path
):
    import bulk_downloader.app as bd_app

    pid = os.getpid()
    actual_start = _linux_process_start(pid)
    recycled_start = str(int(actual_start) + 1)
    assert recycled_start != actual_start
    os.kill(pid, 0)
    sid = "row350-recycled-capture"
    _seed_capture(
        bd_app,
        tmp_path,
        sid,
        {
            "pid": pid,
            "pid_start": recycled_start,
            "boot_id": _linux_boot_id(),
            "started_at": time.time() - (10 * 40 * 60),
        },
    )

    response = fresh_app.get(f"/api/sites/{sid}/template_status")
    body = response.get_json()

    assert response.status_code == 200, response.get_data(as_text=True)
    assert body["capture_in_flight"] is False, {
        "pid": pid,
        "live_replacement_start": actual_start,
        "recorded_capture_start": recycled_start,
        "age_seconds": 10 * 40 * 60,
        "api_result": body,
    }
    assert "template_capture" not in bd_app.s_cfg[sid]


def test_matching_capture_identity_survives_the_same_old_age(
    fresh_app, tmp_path
):
    import bulk_downloader.app as bd_app

    pid = os.getpid()
    actual_start = _linux_process_start(pid)
    sid = "row350-owned-capture"
    _seed_capture(
        bd_app,
        tmp_path,
        sid,
        {
            "pid": pid,
            "pid_start": actual_start,
            "boot_id": _linux_boot_id(),
            "started_at": time.time() - (10 * 40 * 60),
        },
    )

    response = fresh_app.get(f"/api/sites/{sid}/template_status")

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["capture_in_flight"] is True
    assert bd_app.s_cfg[sid]["template_capture"]["pid_start"] == actual_start


def test_capture_launch_persists_the_process_identity(
    fresh_app, tmp_path, monkeypatch
):
    import bulk_downloader.app as bd_app
    from tools import onboard_site_template

    sid = "row350-capture-launch"
    bd_app.s_cfg[sid] = {
        "name": sid,
        "login_url": "https://row350.invalid/",
    }
    info = {
        "profile_dir": str(tmp_path / "profile"),
        "wacz": str(tmp_path / "capture.wacz"),
        "draft": str(tmp_path / "capture.template-draft.json"),
        "display": ":99",
    }
    launches: list[tuple[dict, bool]] = []
    monkeypatch.setattr(
        onboard_site_template,
        "plan_site",
        lambda _cfg: {
            "template_onboarding": "capture_required",
            "template_auto_detect_mode": "capture",
            "auto_teach_first_run": False,
        },
    )
    monkeypatch.setattr(
        onboard_site_template,
        "build_capture_command",
        lambda _sid, _url, _display: dict(info),
    )
    monkeypatch.setattr(
        onboard_site_template,
        "run_capture_flow",
        lambda received, *, run: launches.append((received, run)) or os.getpid(),
    )

    response = fresh_app.post(
        f"/api/sites/{sid}/template_onboard", json={"run": True}
    )
    marker = bd_app.s_cfg[sid]["template_capture"]

    assert response.status_code == 200, response.get_data(as_text=True)
    assert launches == [(info, True)]
    assert marker["pid"] == os.getpid()
    assert marker["pid_start"] == _linux_process_start(os.getpid())
    assert marker["boot_id"] == _linux_boot_id()


class _DormantWorker:
    starts: list[str] = []

    def __init__(self, *, target, daemon, name):
        self.target = target
        self.daemon = daemon
        self.name = name

    def start(self) -> None:
        self.starts.append(self.name)


def _reset_mass_import(subject, db_path: Path, monkeypatch) -> None:
    # Replace rather than clear shared objects so another test in this worker
    # cannot inherit our temporary database or lifecycle state.
    monkeypatch.setattr(subject._db, "DB_PATH", str(db_path))
    monkeypatch.setattr(subject, "_TABLE_READY", False)
    monkeypatch.setattr(subject, "_jobs", {})
    monkeypatch.setattr(_DormantWorker, "starts", [])


def test_mass_import_admission_counts_and_inserts_under_one_lock(
    tmp_path, monkeypatch
):
    from bulk_downloader import mass_import as subject

    _reset_mass_import(subject, tmp_path / "admission.sqlite", monkeypatch)
    real_thread = threading.Thread
    monkeypatch.setattr(subject, "_ensure_table", lambda: None)
    monkeypatch.setattr(subject, "_persist", lambda *_args: None)
    monkeypatch.setattr(subject.threading, "Thread", _DormantWorker)
    with subject._jobs_lock:
        subject._jobs.update({
            "existing-a": {"state": "running"},
            "existing-b": {"state": "running"},
        })
    assert subject._running_jobs_count() == 2

    rendezvous = threading.Barrier(2)
    observed_counts: list[int] = []

    def controlled_count() -> int:
        with subject._jobs_lock:
            count = sum(
                job.get("state") == "running"
                for job in subject._jobs.values()
            )
        observed_counts.append(count)
        try:
            rendezvous.wait(timeout=0.25)
        except threading.BrokenBarrierError:
            pass
        return count

    monkeypatch.setattr(subject, "_running_jobs_count", controlled_count)
    results: dict[str, dict] = {}

    def start(role: str) -> None:
        results[role] = subject.start_import(
            site_id=role,
            urls=[f"https://row350.invalid/{role}"],
            load_urls_fn=lambda *_args, **_kwargs: (1, 0, 0),
        )

    callers = [real_thread(target=start, args=(role,), name=f"caller-{role}")
               for role in ("a", "b")]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(5)

    assert all(not caller.is_alive() for caller in callers)
    assert len(observed_counts) == 2 and min(observed_counts) >= 2
    accepted = sum(result["ok"] is True for result in results.values())
    running = subject._running_jobs_count()
    assert accepted == 1, {
        "results": results,
        "observed_counts": observed_counts,
        "running_jobs": running,
        "cap": subject._MAX_CONCURRENT_JOBS,
    }
    assert running == subject._MAX_CONCURRENT_JOBS
    assert len(_DormantWorker.starts) == 1


def _create_legacy_mass_import_table(path: Path) -> None:
    with sqlite3.connect(path) as cx:
        cx.execute("""
            CREATE TABLE mass_imports (
                job_id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL,
                state TEXT NOT NULL,
                total INTEGER NOT NULL DEFAULT 0,
                processed INTEGER NOT NULL DEFAULT 0,
                added INTEGER NOT NULL DEFAULT 0,
                dupes INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                error TEXT DEFAULT '',
                started_ts REAL NOT NULL,
                finished_ts REAL
            )
        """)
        cx.execute(
            """INSERT INTO mass_imports
               (job_id, site_id, state, total, started_ts)
               VALUES ('prior-run', 'prior-site', 'running', 1, ?)""",
            (time.time() - 60,),
        )


def test_first_use_recovery_crashes_only_prior_run_jobs(
    tmp_path, monkeypatch
):
    from bulk_downloader import mass_import as subject

    database = tmp_path / "first-use.sqlite"
    _create_legacy_mass_import_table(database)
    _reset_mass_import(subject, database, monkeypatch)
    real_thread = threading.Thread
    real_db_conn = subject._db.db_conn
    monkeypatch.setattr(subject.threading, "Thread", _DormantWorker)

    a_transaction_committed = threading.Event()
    b_at_recovery_update = threading.Event()
    allow_b_update = threading.Event()
    update_roles: list[str] = []

    class _ConnectionProxy:
        def __init__(self, connection, role: str):
            self._connection = connection
            self._role = role
            self.did_recovery_update = False

        def execute(self, sql, *args, **kwargs):
            normalized = " ".join(str(sql).split())
            if (normalized.startswith("UPDATE mass_imports")
                    and "WHERE state = 'running'" in normalized):
                self.did_recovery_update = True
                update_roles.append(self._role)
                if self._role == "recover-b":
                    b_at_recovery_update.set()
                    assert allow_b_update.wait(5), (
                        "recovery B reached its update but was never released"
                    )
            return self._connection.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._connection, name)

    @contextmanager
    def controlled_db_conn():
        role = threading.current_thread().name
        manager = real_db_conn()
        connection = manager.__enter__()
        proxy = _ConnectionProxy(connection, role)
        try:
            yield proxy
        except BaseException:
            manager.__exit__(*sys.exc_info())
            raise
        else:
            manager.__exit__(None, None, None)
            if role == "recover-a" and proxy.did_recovery_update:
                a_transaction_committed.set()
                b_at_recovery_update.wait(0.35)

    monkeypatch.setattr(subject._db, "db_conn", controlled_db_conn)
    start_results: list[dict] = []
    errors: list[BaseException] = []

    def start_a() -> None:
        try:
            start_results.append(subject.start_import(
                site_id="current-site",
                urls=["https://row350.invalid/current"],
                load_urls_fn=lambda *_args, **_kwargs: (1, 0, 0),
            ))
        except BaseException as exc:
            errors.append(exc)

    def recover_b() -> None:
        try:
            subject._ensure_table()
        except BaseException as exc:
            errors.append(exc)

    thread_a = real_thread(target=start_a, name="recover-a")
    thread_b = real_thread(target=recover_b, name="recover-b")
    thread_a.start()
    assert a_transaction_committed.wait(5), (
        "recovery A never committed its first-use transaction"
    )
    thread_b.start()
    thread_a.join(5)
    assert not thread_a.is_alive(), "starter A did not publish its current job"
    allow_b_update.set()
    thread_b.join(5)

    assert not thread_b.is_alive()
    assert errors == []
    assert len(start_results) == 1 and start_results[0]["ok"] is True
    assert Counter(update_roles) == Counter({"recover-a": 1}), (
        "table recovery ran more than once during concurrent first use"
    )
    current_id = start_results[0]["job_id"]
    assert subject._jobs[current_id]["state"] == "running"
    with sqlite3.connect(database) as cx:
        durable = dict(zip(
            ("job_id", "state", "error"),
            cx.execute(
                "SELECT job_id, state, error FROM mass_imports WHERE job_id = ?",
                (current_id,),
            ).fetchone(),
        ))
        prior = cx.execute(
            "SELECT state, error FROM mass_imports WHERE job_id = 'prior-run'"
        ).fetchone()

    assert prior == ("crashed", "process restarted")
    assert durable["state"] == "running", {
        "start_result": start_results[0],
        "memory_state": subject._jobs[current_id]["state"],
        "durable_state": durable["state"],
        "durable_error": durable["error"],
        "recovery_updates": Counter(update_roles),
    }
    assert durable["error"] == ""

    # Re-enter the once-only initializer with a current-run row already
    # durable. Its run identity, not timing luck, keeps it out of restart
    # recovery while the legacy row above remains crashed.
    subject._TABLE_READY = False
    subject._ensure_table()
    with sqlite3.connect(database) as cx:
        after_reentry = cx.execute(
            "SELECT state, error FROM mass_imports WHERE job_id = ?",
            (current_id,),
        ).fetchone()
    assert after_reentry == ("running", "")


class _FakeProcess:
    def __init__(self, pid: int, returncode):
        self.pid = pid
        self.returncode = returncode

    def poll(self):
        return self.returncode


def _dev_run(run_id: str, process: _FakeProcess, pidfd: int) -> dict:
    return {
        "run_id": run_id,
        "target": "tests/row350.py",
        "kind": "file",
        "state": "running",
        "output": "",
        "started": time.time(),
        "finished": None,
        "returncode": None,
        "pid": process.pid,
        "_process": process,
        "_pidfd": pidfd,
    }


def test_dev_cancel_does_not_signal_or_overwrite_a_completed_identity(
    monkeypatch
):
    from bulk_downloader import dev_tools as subject

    identity_fd = os.open("/dev/null", os.O_RDONLY)
    process = _FakeProcess(4242, 0)
    run = _dev_run("recycled", process, identity_fd)
    with subject._runs_lock:
        subject._runs[:] = [run]
    numeric_signals: list[tuple[int, int]] = []

    def signal_replacement(pid: int, sig: int) -> None:
        numeric_signals.append((pid, sig))
        run["state"] = "done"
        run["returncode"] = 0

    monkeypatch.setattr(subject.os, "kill", signal_replacement)
    try:
        result = subject.cancel_run("recycled")

        assert result["ok"] is False, {
            "cancel_result": result,
            "signalled_replacement": numeric_signals,
            "final_state": run["state"],
            "returncode": run["returncode"],
        }
        assert numeric_signals == []
        assert run["state"] == "done"
        assert run["returncode"] == 0
    finally:
        try:
            os.close(identity_fd)
        except OSError:
            pass
        with subject._runs_lock:
            subject._runs.clear()


def test_dev_cancel_matching_identity_signals_pidfd_once(monkeypatch):
    from bulk_downloader import dev_tools as subject

    identity_fd = os.open("/dev/null", os.O_RDONLY)
    process = _FakeProcess(4343, None)
    run = _dev_run("owned", process, identity_fd)
    with subject._runs_lock:
        subject._runs[:] = [run]
    pidfd_signals: list[tuple[int, int]] = []
    numeric_signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        signal,
        "pidfd_send_signal",
        lambda fd, sig: pidfd_signals.append((fd, sig)),
        raising=False,
    )
    monkeypatch.setattr(
        subject.os,
        "kill",
        lambda pid, sig: numeric_signals.append((pid, sig)),
    )
    try:
        result = subject.cancel_run("owned")

        assert result == {"ok": True}
        assert pidfd_signals == [(identity_fd, signal.SIGTERM)]
        assert numeric_signals == []
        assert run["state"] == "cancelled"
    finally:
        try:
            os.close(identity_fd)
        except OSError:
            pass
        with subject._runs_lock:
            subject._runs.clear()


def test_dev_start_holds_identity_through_cancel_and_worker_settlement(
    monkeypatch
):
    from bulk_downloader import dev_tools as subject

    identity_fd = os.open("/dev/null", os.O_RDONLY)
    output_entered = threading.Event()
    process_released = threading.Event()
    worker_waited = threading.Event()
    opened: list[tuple[int, int]] = []
    sent: list[tuple[int, int]] = []

    class _BlockingOutput:
        def __iter__(self):
            return self

        def __next__(self):
            output_entered.set()
            assert process_released.wait(5), "fake process was never released"
            raise StopIteration

    class _SpawnedProcess:
        pid = 4545
        returncode = None
        stdout = _BlockingOutput()

        def poll(self):
            return self.returncode

        def wait(self):
            assert process_released.wait(5)
            worker_waited.set()
            return self.returncode

    process = _SpawnedProcess()
    monkeypatch.setattr(subject, "_build_cmd", lambda *_args: ["fake-command"])
    monkeypatch.setattr(
        subject.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr(
        subject.os,
        "pidfd_open",
        lambda pid, flags=0: opened.append((pid, flags)) or identity_fd,
        raising=False,
    )

    def deliver(fd: int, sig: int) -> None:
        sent.append((fd, sig))
        process.returncode = -int(sig)
        process_released.set()

    monkeypatch.setattr(
        signal, "pidfd_send_signal", deliver, raising=False
    )
    with subject._runs_lock:
        subject._runs.clear()
    try:
        started = subject.start_run("tests/row350.py")
        assert started["ok"] is True
        assert output_entered.wait(5), "worker never reached its held process"
        run_id = started["run_id"]
        before = subject.run_status(run_id)
        assert before["state"] == "running" and before["pid"] == process.pid
        assert not any(key.startswith("_") for key in before)

        assert subject.cancel_run(run_id) == {"ok": True}
        assert worker_waited.wait(5), "worker did not observe process settlement"
        for _ in range(100):
            after = subject.run_status(run_id)
            if after["returncode"] is not None:
                break
            time.sleep(0.01)

        assert opened == [(process.pid, 0)]
        assert sent == [(identity_fd, signal.SIGTERM)]
        assert after["state"] == "cancelled"
        assert after["returncode"] == -signal.SIGTERM
        assert not any(key.startswith("_") for key in after)
    finally:
        process_released.set()
        try:
            os.close(identity_fd)
        except OSError:
            pass
        with subject._runs_lock:
            subject._runs.clear()


def _await_dev_run(subject, run_id: str, predicate, *, seconds: float = 10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status = subject.run_status(run_id)
        if status is not None and predicate(status):
            return status
        threading.Event().wait(0.01)
    status = subject.run_status(run_id)
    raise AssertionError(f"dev run did not reach the required state: {status!r}")


def _same_linux_process_is_alive(pid: int, start: str) -> bool:
    try:
        return _linux_process_start(pid) == start
    except (FileNotFoundError, ProcessLookupError):
        return False


def _path_has_exact_text(path: Path, expected: str) -> bool:
    try:
        return path.read_text(encoding="ascii") == expected
    except FileNotFoundError:
        return False


def test_dev_run_arbitrary_output_cannot_orphan_its_owned_child(
    monkeypatch, tmp_path
):
    """An ambient ASCII locale must not make the output reader abandon pytest."""
    from bulk_downloader import dev_tools as subject

    marker = tmp_path / "bad-byte-written"
    release = tmp_path / "release-bad-byte"
    child = (
        "import os, pathlib, sys, time\n"
        "marker, release = map(pathlib.Path, sys.argv[1:])\n"
        "marker.write_text('bad-byte-writes=1\\n', encoding='ascii')\n"
        "while not release.exists(): time.sleep(0.01)\n"
        "os.write(1, b'\\xff\\n')\n"
        "for _ in range(64): os.write(1, b'x' * 4096)\n"
        "raise SystemExit(23)\n"
    )
    monkeypatch.setattr(
        subject,
        "_build_cmd",
        lambda *_args: [sys.executable, "-c", child, str(marker), str(release)],
    )
    real_popen = subject.subprocess.Popen
    popen_calls = []

    def popen_under_ascii_locale(*args, **kwargs):
        previous = locale.setlocale(locale.LC_CTYPE)
        try:
            locale.setlocale(locale.LC_CTYPE, "C")
            process = real_popen(*args, **kwargs)
        finally:
            locale.setlocale(locale.LC_CTYPE, previous)
        popen_calls.append(process)
        return process

    monkeypatch.setattr(subject.subprocess, "Popen", popen_under_ascii_locale)
    with subject._runs_lock:
        subject._runs.clear()
    pid = -1
    process = None
    try:
        run_id = subject.start_run("tests/row445.py")["run_id"]
        _await_dev_run(
            subject,
            run_id,
            lambda status: status["pid"] is not None
            and _path_has_exact_text(marker, "bad-byte-writes=1\n"),
        )
        assert marker.read_text(encoding="ascii") == "bad-byte-writes=1\n"
        assert len(popen_calls) == 1, "the forced-locale Popen seam did not fire once"
        process = popen_calls[0]
        pid = process.pid
        start = _linux_process_start(pid)
        assert _same_linux_process_is_alive(pid, start), (
            "the child did not exist before the injected byte was released"
        )

        release.write_text("release\n", encoding="ascii")
        status = _await_dev_run(
            subject, run_id, lambda current: current["state"] != "running"
        )
        survivors = int(_same_linux_process_is_alive(pid, start))
        decode_failures = status["output"].count("UnicodeDecodeError")
        assert survivors == 0, (
            "DEV-RUN CHILD SURVIVED OUTPUT DECODER FAILURE: "
            f"pid={pid} survivors={survivors} state={status['state']} "
            f"returncode={status['returncode']} "
            f"UnicodeDecodeError_count={decode_failures}"
        )
        assert decode_failures == 0, status["output"]
        assert "\ufffd" in status["output"], status["output"]
        assert status["state"] == "failed"
        assert status["returncode"] == 23
    finally:
        if process is not None and pid > 0 and process.poll() is None:
            os.killpg(pid, signal.SIGKILL)
            process.wait(timeout=5)
        with subject._runs_lock:
            subject._runs.clear()


def test_dev_run_genuine_reader_failure_reaps_before_terminal_verdict(
    monkeypatch, tmp_path
):
    """The encoding fix must not leave a different reader exception fail-open."""
    from bulk_downloader import dev_tools as subject

    marker = tmp_path / "reader-child-live"
    child = (
        "import pathlib, sys, time\n"
        "pathlib.Path(sys.argv[1]).write_text('live=1\\n', encoding='ascii')\n"
        "time.sleep(300)\n"
    )
    monkeypatch.setattr(
        subject,
        "_build_cmd",
        lambda *_args: [sys.executable, "-c", child, str(marker)],
    )
    real_popen = subject.subprocess.Popen
    injected = {"count": 0, "pid": -1, "start": None}
    handles = []

    class FailingReader:
        def __init__(self, stream, process):
            self._stream = stream
            self._process = process

        def __iter__(self):
            return self

        def __next__(self):
            deadline = time.monotonic() + 5
            while (not _path_has_exact_text(marker, "live=1\n")
                   and time.monotonic() < deadline):
                threading.Event().wait(0.01)
            assert marker.read_text(encoding="ascii") == "live=1\n"
            injected["pid"] = self._process.pid
            injected["start"] = _linux_process_start(self._process.pid)
            assert _same_linux_process_is_alive(
                self._process.pid, injected["start"]
            )
            injected["count"] += 1
            raise RuntimeError("injected reader death")

        def close(self):
            self._stream.close()

    def popen_with_failing_reader(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        handles.append(process.stdout)
        process.stdout = FailingReader(process.stdout, process)
        return process

    monkeypatch.setattr(subject.subprocess, "Popen", popen_with_failing_reader)
    with subject._runs_lock:
        subject._runs.clear()
    try:
        run_id = subject.start_run("tests/row445-reader.py")["run_id"]
        status = _await_dev_run(
            subject, run_id, lambda current: current["state"] != "running"
        )
        assert injected["count"] == 1, "the reader-death injection did not fire once"
        assert status["state"] == "error"
        assert "RuntimeError: injected reader death" in status["output"]
        assert status["cleanup_state"] == "reaped"
        assert not _same_linux_process_is_alive(
            injected["pid"], injected["start"]
        ), "worker error became terminal before its exact child was reaped"
    finally:
        for handle in handles:
            handle.close()
        pid = injected["pid"]
        if pid > 0 and injected["start"] is not None \
                and _same_linux_process_is_alive(pid, injected["start"]):
            os.killpg(pid, signal.SIGKILL)
        with subject._runs_lock:
            subject._runs.clear()


def test_dev_run_clean_exit_keeps_the_childs_true_returncode(
    monkeypatch, tmp_path
):
    from bulk_downloader import dev_tools as subject

    marker = tmp_path / "clean-child-live"
    release = tmp_path / "release-clean-child"
    child = (
        "import pathlib, sys, time\n"
        "marker, release = map(pathlib.Path, sys.argv[1:])\n"
        "marker.write_text('clean=1\\n', encoding='ascii')\n"
        "while not release.exists(): time.sleep(0.01)\n"
        "print('clean output')\n"
        "raise SystemExit(17)\n"
    )
    monkeypatch.setattr(
        subject,
        "_build_cmd",
        lambda *_args: [sys.executable, "-c", child, str(marker), str(release)],
    )
    with subject._runs_lock:
        subject._runs.clear()
    try:
        run_id = subject.start_run("tests/row445-clean.py")["run_id"]
        status = _await_dev_run(
            subject,
            run_id,
            lambda current: current["pid"] is not None
            and _path_has_exact_text(marker, "clean=1\n"),
        )
        pid = status["pid"]
        start = _linux_process_start(pid)
        assert marker.read_text(encoding="ascii") == "clean=1\n"
        assert _same_linux_process_is_alive(pid, start)
        release.write_text("release\n", encoding="ascii")
        status = _await_dev_run(
            subject, run_id, lambda current: current["state"] != "running"
        )
        assert status["state"] == "failed"
        assert status["returncode"] == 17
        assert "clean output" in status["output"]
        assert "[worker error]" not in status["output"]
        assert not _same_linux_process_is_alive(pid, start)
    finally:
        with subject._runs_lock:
            subject._runs.clear()


def test_transform_control_only_imports_the_lifecycle_modules():
    from bulk_downloader import app_sites_teach, dev_tools, mass_import

    assert callable(app_sites_teach._capture_marker_stale)
    assert callable(dev_tools.cancel_run)
    assert callable(mass_import.start_import)
