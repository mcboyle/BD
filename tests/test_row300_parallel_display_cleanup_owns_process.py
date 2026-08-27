"""ROW 300: display-test cleanup is bound to the process it started."""

from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

import test_capture_provides_a_display as display_test


BD_GATE_SCOPE = "repo-wide"


@dataclass
class _ForeignDisplay:
    process: subprocess.Popen[bytes]
    number: int
    lock: Path
    socket: Path


class _LateDisplayPath:
    """Map the cited test's fixed :71 paths after the race has fired."""

    def __init__(self, kind: str, holder: list[_ForeignDisplay]) -> None:
        self._kind = kind
        self._holder = holder

    def _resolved(self) -> Path:
        assert len(self._holder) == 1, (
            "precondition: the foreign display must start exactly once before "
            "its lock or socket is used"
        )
        foreign = self._holder[0]
        return foreign.lock if self._kind == "lock" else foreign.socket

    def exists(self) -> bool:
        # The first call is the vulnerable check-before-use window.  The real
        # foreign server is inserted by the intercepted helper invocation.
        return bool(self._holder) and self._resolved().exists()

    def read_text(self, **kwargs: object) -> str:
        return self._resolved().read_text(**kwargs)

    def unlink(self) -> None:
        self._resolved().unlink()


def _recover_exact_process_identity(pid: int, start_ticks: int) -> None:
    """Mutation-test backstop: reap only the exact test-started identity."""
    identity = display_test._proc_identity(pid)
    if identity is None or identity[0] != start_ticks or identity[1] == "Z":
        return
    pidfd = os.pidfd_open(pid, 0)
    try:
        identity = display_test._proc_identity(pid)
        assert identity is not None and identity[0] == start_ticks, (
            "owned recovery identity changed after pidfd binding"
        )
        signal.pidfd_send_signal(pidfd, signal.SIGTERM)
        poller = select.poll()
        poller.register(pidfd, select.POLLIN)
        assert poller.poll(5000), (
            f"owned recovery could not terminate pid {pid}"
        )
    finally:
        os.close(pidfd)


def _start_exclusively_allocated_xvfb(lock: Path) -> _ForeignDisplay:
    xvfb = shutil.which("Xvfb")
    assert xvfb is not None, "precondition: Xvfb is required for the display gate"

    read_fd, write_fd = os.pipe()
    process = subprocess.Popen(
        [
            xvfb,
            "-displayfd",
            str(write_fd),
            "-screen",
            "0",
            "320x240x24",
            "-nolisten",
            "tcp",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        pass_fds=(write_fd,),
    )
    os.close(write_fd)
    try:
        ready, _, _ = select.select([read_fd], [], [], 10)
        assert ready, "precondition: Xvfb -displayfd never reported readiness"
        raw_number = os.read(read_fd, 32).strip()
    finally:
        os.close(read_fd)

    if not raw_number:
        stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
        process.wait(timeout=5)
        raise AssertionError(
            f"precondition: Xvfb did not allocate a display: {stderr}"
        )
    assert raw_number.isdigit(), (
        f"precondition: Xvfb returned a nonnumeric display: {raw_number!r}"
    )

    number = int(raw_number)
    socket = Path(f"/tmp/.X11-unix/X{number}")
    assert process.poll() is None, "precondition: the foreign Xvfb already exited"
    assert socket.exists(), "precondition: the foreign Xvfb created no socket"
    # Xvfb's exclusive -displayfd mode does not publish the traditional lock
    # file on this host.  Supply the exact lock-file shape consumed by the
    # cited cleanup, in the gate's owned tmp_path, while retaining the real X
    # socket and real server process as the survival verdict.
    lock.write_text(f"{process.pid}\n", encoding="ascii")
    assert lock.is_file(), "precondition: the foreign display lock was not built"
    lock_pid = lock.read_text(encoding="ascii").strip()
    assert lock_pid == str(process.pid), (
        "precondition: the exclusive X display lock does not name the foreign "
        f"process: lock={lock_pid!r} process={process.pid}"
    )
    probe = subprocess.run(
        ["xdpyinfo", "-display", f":{number}"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    assert probe.returncode == 0, (
        "precondition: the exclusively allocated foreign display is not usable: "
        + probe.stderr.decode(errors="replace")
    )
    return _ForeignDisplay(process, number, lock, socket)


@pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed")
def test_cited_cleanup_leaves_a_foreign_display_alive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Force the check/use race and require the cited test not to kill the winner."""

    holder: list[_ForeignDisplay] = []
    helper_calls: list[tuple[str, ...]] = []
    kill_calls: list[str] = []
    real_path = Path

    def mapped_path(value: object) -> Path | _LateDisplayPath:
        rendered = os.fspath(value)
        if rendered == "/tmp/.X71-lock":
            return _LateDisplayPath("lock", holder)
        if rendered == "/tmp/.X11-unix/X71":
            return _LateDisplayPath("socket", holder)
        return real_path(value)

    class _SubprocessBoundary:
        DEVNULL = subprocess.DEVNULL

        @staticmethod
        def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if args[:2] == ["bash", "-c"]:
                helper_calls.append(tuple(args))
                assert len(helper_calls) == 1, (
                    "precondition: the cited helper invocation must fire exactly once"
                )
                holder.append(_start_exclusively_allocated_xvfb(tmp_path / "foreign.lock"))
                return subprocess.CompletedProcess(args, 0, ":71\n", "")
            if args and args[0] == "kill":
                assert len(args) == 2 and args[1].isdigit(), args
                kill_calls.append(args[1])
            return subprocess.run(args, **kwargs)

    monkeypatch.setattr(display_test, "Path", mapped_path)
    monkeypatch.setattr(display_test, "subprocess", _SubprocessBoundary)

    target_failure = ""
    try:
        try:
            display_test.test_bd_start_display_really_yields_a_usable_display()
        except AssertionError as exc:
            target_failure = str(exc)

        assert len(holder) == 1, (
            f"precondition: expected one foreign display, observed {len(holder)}"
        )
        foreign = holder[0]
        assert len(helper_calls) == 1, helper_calls
        try:
            foreign.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            survived = True
        else:
            survived = False

        assert survived, (
            "foreign display DIED: the cited test killed the bare PID from a "
            f"lock it did not own (display=:{foreign.number}, pid="
            f"{foreign.process.pid}, kill_calls={kill_calls})"
        )
        assert kill_calls == [], (
            f"foreign display survived despite bare-PID kill calls: {kill_calls}"
        )
        assert "ownership receipt" in target_failure, (
            "the cited test must refuse a display whose process identity it "
            f"cannot prove it started; failure was {target_failure!r}"
        )
    finally:
        if holder and holder[0].process.poll() is None:
            holder[0].process.terminate()
            holder[0].process.wait(timeout=5)


@pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed")
def test_cleanup_terminates_the_exact_display_process_this_test_started() -> None:
    """Negative control: identity confinement must not disable owned cleanup."""
    pid = start_ticks = number = -1
    claim_path: Path | None = None
    try:
        with display_test._owned_bd_start_display() as owned:
            pid = owned.pid
            start_ticks = owned.start_ticks
            number = owned.number
            claim_path = Path(f"/tmp/bd-display-test-X{number}.claim")
            assert display_test._owned_process_alive(owned), (
                "precondition: the owned display process is not alive"
            )
            assert Path(f"/tmp/.X11-unix/X{number}").exists(), (
                "precondition: the owned display published no X socket"
            )
            probe = subprocess.run(
                ["xdpyinfo", "-display", f":{number}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            assert probe.returncode == 0, (
                "precondition: the owned display is not usable: "
                + probe.stderr.decode(errors="replace")
            )

        assert (pid, start_ticks, number) != (-1, -1, -1), (
            "precondition: the owned-display context did not yield"
        )
        observed = display_test._proc_identity(pid)
        assert observed is None or observed[0] != start_ticks or observed[1] == "Z", (
            f"cleanup left the exact owned display identity alive: pid={pid} "
            f"start={start_ticks} observed={observed}"
        )
        assert claim_path is not None and not claim_path.exists(), (
            f"cleanup retained the exact owned claim: {claim_path}"
        )
    finally:
        if pid > 1 and start_ticks > 0:
            _recover_exact_process_identity(pid, start_ticks)


def test_display_claim_is_atomic_and_preserves_an_existing_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A competing test's claim is neither acquired nor rewritten."""
    blocker_fd = -1
    blocker_path: Path | None = None
    blocker_identity: tuple[int, int] | None = None
    returned: object | None = None
    refusal = ""
    for candidate in range(4000, 5000):
        candidate_path = Path(f"/tmp/bd-display-test-X{candidate}.claim")
        try:
            blocker_fd = os.open(
                candidate_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            )
        except FileExistsError:
            continue
        blocker_path = candidate_path
        info = os.fstat(blocker_fd)
        blocker_identity = (info.st_dev, info.st_ino)
        os.write(blocker_fd, b"foreign-claim\n")
        os.fsync(blocker_fd)
        break
    assert blocker_path is not None and blocker_identity is not None, (
        "precondition: no exclusive blocker claim could be created from 1000 candidates"
    )
    assert blocker_path.read_bytes() == b"foreign-claim\n"
    monkeypatch.setattr(display_test, "_DISPLAY_CANDIDATES", (candidate,))

    try:
        try:
            returned = display_test._claim_unused_display()
        except AssertionError as exc:
            refusal = str(exc)

        assert returned is None, "an existing display claim was acquired"
        assert "none of 1 atomically claimed display candidates" in refusal, refusal
        current = blocker_path.stat(follow_symlinks=False)
        assert (current.st_dev, current.st_ino) == blocker_identity, (
            "the existing display claim's identity changed"
        )
        assert blocker_path.read_bytes() == b"foreign-claim\n", (
            "the existing display claim was rewritten"
        )
    finally:
        if isinstance(returned, display_test._DisplayClaim):
            os.close(returned.fd)
        os.close(blocker_fd)
        current = blocker_path.stat(follow_symlinks=False)
        assert (current.st_dev, current.st_ino) == blocker_identity
        blocker_path.unlink()


def test_transform_control_imports_display_owner_without_asserting_cleanup() -> None:
    """Import-only control for the valid cleanup-confinement transform."""
    assert callable(display_test._owned_bd_start_display)
