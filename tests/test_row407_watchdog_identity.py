"""Row 407: watchdog census and collapse use logical, non-reusable identities."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bd_watchdog_identity.py"
BD_GATE_SCOPE = "module"


def _subject():
    if not SCRIPT.is_file():
        pytest.fail("bd_watchdog_identity.py is missing")
    spec = importlib.util.spec_from_file_location("row407_watchdog_identity", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _stat_row(pid: int, *, ppid: int, start_ticks: int, comm: str = "bash worker") -> str:
    tail = [
        "S",
        str(ppid),
        str(pid),
        str(pid),
        "0",
        "-1",
        "4194304",
        "0",
        "0",
        "0",
        "0",
        "1",
        "0",
        "0",
        "0",
        "20",
        "0",
        "1",
        "0",
        str(start_ticks),
        "4096",
        "10",
    ]
    assert tail[1] == str(ppid) and tail[19] == str(start_ticks)
    return f"{pid} ({comm}) {' '.join(tail)}\n"


class ProcCase:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "proc"
        self.root.mkdir()
        boot = self.root / "sys" / "kernel" / "random"
        boot.mkdir(parents=True)
        (boot / "boot_id").write_text("boot-row407\n")
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.script = self.home / "bd-watchdog.sh"
        self.script.write_text("#!/bin/bash\nwhile :; do sleep 120; done\n")
        self.bash = Path("/bin/bash").resolve(strict=True)

    def add(
        self,
        pid: int,
        *,
        ppid: int,
        start_ticks: int,
        argv: list[str] | None = None,
        cwd: Path | None = None,
        executable: Path | None = None,
        stat_text: str | None = None,
    ) -> None:
        entry = self.root / str(pid)
        entry.mkdir()
        (entry / "stat").write_text(
            stat_text
            if stat_text is not None
            else _stat_row(pid, ppid=ppid, start_ticks=start_ticks)
        )
        command = argv or ["bash", str(self.script)]
        (entry / "cmdline").write_bytes(
            b"\0".join(arg.encode("utf-8") for arg in command) + b"\0"
        )
        (entry / "cwd").symlink_to(cwd or self.home, target_is_directory=True)
        (entry / "exe").symlink_to(
            executable or self.bash,
            target_is_directory=False,
        )

    def remove(self, pid: int) -> None:
        entry = self.root / str(pid)
        if not entry.exists():
            return
        (entry / "cwd").unlink(missing_ok=True)
        (entry / "exe").unlink(missing_ok=True)
        (entry / "cmdline").unlink(missing_ok=True)
        (entry / "stat").unlink(missing_ok=True)
        entry.rmdir()

    def set_identity(
        self,
        pid: int,
        *,
        ppid: int,
        start_ticks: int,
        argv: list[str] | None = None,
    ) -> None:
        entry = self.root / str(pid)
        (entry / "stat").write_text(
            _stat_row(pid, ppid=ppid, start_ticks=start_ticks)
        )
        if argv is not None:
            (entry / "cmdline").write_bytes(
                b"\0".join(arg.encode("utf-8") for arg in argv) + b"\0"
            )


@pytest.fixture
def proc_case(tmp_path: Path) -> ProcCase:
    return ProcCase(tmp_path)


def _lineage_pids(body: dict[str, object]) -> list[list[int]]:
    return [
        [member["pid"] for member in lineage["members"]]
        for lineage in body["lineages"]
    ]


def test_parent_child_matches_form_one_logical_lineage(proc_case: ProcCase) -> None:
    """Counting processes instead of lineage roots would kill a valid re-exec parent."""

    proc_case.add(100, ppid=1, start_ticks=1000)
    proc_case.add(101, ppid=100, start_ticks=1001)
    subject = _subject()

    body = subject.inspect_watchdogs(script=proc_case.script, proc_root=proc_case.root)

    assert body["status"] == "UNIQUE"
    assert _lineage_pids(body) == [[100, 101]]
    assert body["authority_root"]["pid"] == 100


def test_independent_roots_are_duplicates_and_oldest_is_authority(
    proc_case: ProcCase,
) -> None:
    """A flat nonzero count hides the six-independent-watchdogs incident."""

    proc_case.add(100, ppid=1, start_ticks=1000)
    proc_case.add(200, ppid=1, start_ticks=2000)
    subject = _subject()

    body = subject.inspect_watchdogs(script=proc_case.script, proc_root=proc_case.root)

    assert body["status"] == "DUPLICATES"
    assert _lineage_pids(body) == [[100], [200]]
    assert body["authority_root"]["pid"] == 100
    assert body["authority_policy"] == "oldest-root-start-ticks"


def test_exact_argv_excludes_substring_lookalikes_and_resolves_relative_script(
    proc_case: ProcCase,
) -> None:
    """Substring grep counts its own probes and similarly named backup scripts."""

    proc_case.add(
        100,
        ppid=1,
        start_ticks=1000,
        argv=["bash", "bd-watchdog.sh"],
        cwd=proc_case.home,
    )
    proc_case.add(
        200,
        ppid=1,
        start_ticks=2000,
        argv=["bash", str(proc_case.script) + ".backup"],
    )
    subject = _subject()

    body = subject.inspect_watchdogs(script=proc_case.script, proc_root=proc_case.root)

    assert body["status"] == "UNIQUE"
    assert _lineage_pids(body) == [[100]]


def test_bash_option_arguments_and_non_bash_executable_do_not_match(
    proc_case: ProcCase,
) -> None:
    """A watchdog-looking option value or forged argv[0] is not an invocation."""

    proc_case.add(
        100,
        ppid=1,
        start_ticks=1000,
        argv=["bash", "--rcfile", str(proc_case.script), "-i"],
    )
    impostor = proc_case.home / "impostor"
    impostor.write_text("not bash\n")
    proc_case.add(
        200,
        ppid=1,
        start_ticks=2000,
        argv=["bash", str(proc_case.script)],
        executable=impostor,
    )
    subject = _subject()

    body = subject.inspect_watchdogs(script=proc_case.script, proc_root=proc_case.root)

    assert body["status"] == "ABSENT"


def test_malformed_identity_for_matching_argv_is_unknown_not_absent(
    proc_case: ProcCase,
) -> None:
    """Swallowing a torn stat row turns unavailable identity into zero matches."""

    proc_case.add(100, ppid=1, start_ticks=1000, stat_text="100 (bash) S 1\n")
    subject = _subject()

    body = subject.inspect_watchdogs(script=proc_case.script, proc_root=proc_case.root)

    assert body["status"] == "UNKNOWN"
    assert body["reason_code"] == "PROCESS_IDENTITY_UNREADABLE"


def test_unreadable_non_candidate_cwd_is_not_a_census_verdict(
    proc_case: ProcCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cwd receipt is required only after argv makes a PID a candidate."""

    unreadable = proc_case.home / "unreadable"
    unreadable.mkdir()
    proc_case.add(
        50,
        ppid=1,
        start_ticks=500,
        argv=["bash", str(proc_case.script) + ".backup"],
        cwd=unreadable,
    )
    proc_case.add(100, ppid=1, start_ticks=1000)
    unreadable.chmod(0)
    permission_failures = 0
    try:
        assert unreadable.stat().st_mode & 0o777 == 0
        subject = _subject()
        real_receipt = subject._path_receipt

        def unreadable_receipt(path: Path):
            nonlocal permission_failures
            if path == proc_case.root / "50" / "cwd":
                permission_failures += 1
                raise PermissionError("injected unreadable non-candidate cwd")
            return real_receipt(path)

        monkeypatch.setattr(subject, "_path_receipt", unreadable_receipt)
        with pytest.raises(PermissionError, match="injected unreadable non-candidate cwd"):
            subject._path_receipt(proc_case.root / "50" / "cwd")
        assert permission_failures == 1

        body = subject.inspect_watchdogs(
            script=proc_case.script,
            proc_root=proc_case.root,
        )
        assert body["status"] == "UNIQUE", body
        assert _lineage_pids(body) == [[100]]
        assert permission_failures == 1

        proc_case.remove(100)
        absent = subject.inspect_watchdogs(
            script=proc_case.script,
            proc_root=proc_case.root,
        )
        assert absent["status"] == "ABSENT", absent
        assert permission_failures == 1
    finally:
        unreadable.chmod(0o700)


def test_relative_matching_operand_still_requires_a_readable_cwd(
    proc_case: ProcCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skipping irrelevant cwd reads must not turn a relative candidate into absent."""

    unreadable = proc_case.home / "unreadable"
    unreadable.mkdir()
    proc_case.add(
        100,
        ppid=1,
        start_ticks=1000,
        argv=["bash", "bd-watchdog.sh"],
        cwd=unreadable,
    )
    unreadable.chmod(0)
    permission_failures = 0
    try:
        assert unreadable.stat().st_mode & 0o777 == 0
        subject = _subject()
        real_receipt = subject._path_receipt

        def unreadable_receipt(path: Path):
            nonlocal permission_failures
            if path == proc_case.root / "100" / "cwd":
                permission_failures += 1
                raise PermissionError("injected unreadable relative candidate cwd")
            return real_receipt(path)

        monkeypatch.setattr(subject, "_path_receipt", unreadable_receipt)
        with pytest.raises(PermissionError, match="injected unreadable relative candidate cwd"):
            subject._path_receipt(proc_case.root / "100" / "cwd")
        assert permission_failures == 1

        body = subject.inspect_watchdogs(
            script=proc_case.script,
            proc_root=proc_case.root,
        )
        assert body["status"] == "UNKNOWN", body
        assert body["reason_code"] == "PROCESS_IDENTITY_UNREADABLE"
        assert permission_failures == 2
    finally:
        unreadable.chmod(0o700)


def test_real_proc_unreadable_non_candidates_do_not_mask_an_absent_census(
    tmp_path: Path,
) -> None:
    """A real host must not let an unrelated unreadable cwd block the census."""

    proc_root = Path("/proc")
    unreadable_cwd_count = 0
    vanished_cwd_count = 0
    for entry in proc_root.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            (entry / "cwd").resolve(strict=True)
        except FileNotFoundError:
            vanished_cwd_count += 1
        except PermissionError:
            unreadable_cwd_count += 1
    if unreadable_cwd_count == 0:
        pytest.skip(
            "real /proc has no unreadable cwd entries; row 490 RED is unavailable"
        )

    probe_script = tmp_path / "row490-no-such-watchdog.sh"
    probe_script.write_text("#!/bin/bash\n")
    assert probe_script.is_file()
    assert unreadable_cwd_count > 0
    assert vanished_cwd_count >= 0

    body = _subject().inspect_watchdogs(script=probe_script, proc_root=proc_root)

    assert body["status"] == "ABSENT", body
    assert body["lineages"] == []
    assert body.get("reason_code") is None


@pytest.mark.parametrize(
    ("shape", "expected_status", "expected_rc"),
    (
        ("absent", "ABSENT", 1),
        ("unique", "UNIQUE", 0),
        ("unknown", "UNKNOWN", 2),
        ("duplicates", "DUPLICATES", 3),
    ),
)
def test_cli_states_have_machine_distinct_nonzero_refusal_codes(
    proc_case: ProcCase,
    shape: str,
    expected_status: str,
    expected_rc: int,
) -> None:
    """A prose-only state with exit zero can be consumed as permission."""

    if shape == "unique":
        proc_case.add(100, ppid=1, start_ticks=1000)
    elif shape == "unknown":
        proc_case.add(100, ppid=1, start_ticks=1000, stat_text="torn\n")
    elif shape == "duplicates":
        proc_case.add(100, ppid=1, start_ticks=1000)
        proc_case.add(200, ppid=1, start_ticks=2000)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--script",
            str(proc_case.script),
            "--proc-root",
            str(proc_case.root),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == expected_rc, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["status"] == expected_status


class FakeKernel:
    def __init__(
        self,
        case: ProcCase,
        *,
        drift_pid: int | None = None,
        timeout_pid: int | None = None,
    ) -> None:
        self.case = case
        self.drift_pid = drift_pid
        self.timeout_pid = timeout_pid
        self.opened: list[int] = []
        self.signalled: list[int] = []
        self.waited: list[tuple[int, float]] = []
        self.closed: list[int] = []

    def open_pidfd(self, identity) -> int:
        self.opened.append(identity.pid)
        if identity.pid == self.drift_pid:
            self.case.set_identity(
                identity.pid,
                ppid=identity.ppid,
                start_ticks=identity.start_ticks + 1,
            )
        return identity.pid + 10_000

    def send_term(self, pidfd: int) -> None:
        pid = pidfd - 10_000
        self.signalled.append(pid)

    def wait_ready(self, pidfd: int, timeout: float) -> bool:
        pid = pidfd - 10_000
        self.waited.append((pid, timeout))
        if pid == self.timeout_pid:
            return False
        self.case.remove(pid)
        return True

    def close_pidfd(self, pidfd: int) -> None:
        self.closed.append(pidfd - 10_000)


def test_explicit_collapse_signals_duplicate_lineage_leaf_first_and_adopts_authority(
    proc_case: ProcCase,
    tmp_path: Path,
) -> None:
    """Signalling a root first can orphan its active same-lineage child."""

    proc_case.add(100, ppid=1, start_ticks=1000)
    proc_case.add(101, ppid=100, start_ticks=1001)
    proc_case.add(200, ppid=1, start_ticks=2000)
    record = tmp_path / "watchdog-adoption.json"
    kernel = FakeKernel(proc_case)
    subject = _subject()

    body = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=True,
        proc_root=proc_case.root,
        settle_timeout=0.25,
        kernel=kernel,
    )

    assert body["status"] == "ADOPTED"
    assert kernel.signalled == [200]
    assert 100 not in kernel.signalled and 101 not in kernel.signalled
    assert kernel.waited == [(200, 0.25)]
    assert kernel.closed == [200]
    saved = json.loads(record.read_text())
    assert saved["boot_id"] == "boot-row407"
    assert saved["authority_root"]["pid"] == 100
    assert [member["pid"] for member in saved["members"]] == [100, 101]


def test_identity_drift_after_pidfd_open_forbids_signal_and_adoption(
    proc_case: ProcCase,
    tmp_path: Path,
) -> None:
    """A pidfd alone cannot bless a different PPID/start-tick receipt."""

    proc_case.add(100, ppid=1, start_ticks=1000)
    proc_case.add(200, ppid=1, start_ticks=500)
    record = tmp_path / "watchdog-adoption.json"
    kernel = FakeKernel(proc_case, drift_pid=100)
    subject = _subject()

    body = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=True,
        proc_root=proc_case.root,
        settle_timeout=0.25,
        kernel=kernel,
    )

    assert body["status"] == "UNKNOWN"
    assert body["reason_code"] == "PROCESS_IDENTITY_CHANGED"
    assert kernel.signalled == []
    assert kernel.closed == [100]
    assert not record.exists()


def test_exec_receipt_drift_after_pidfd_census_forbids_signal(
    proc_case: ProcCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task can exec after a census without changing PID or start ticks."""

    proc_case.add(100, ppid=1, start_ticks=1000)
    proc_case.add(200, ppid=1, start_ticks=500)
    record = tmp_path / "watchdog-adoption.json"
    kernel = FakeKernel(proc_case)
    subject = _subject()
    real_census = subject._take_census
    calls = 0

    def exec_after_second_census(*, script: Path, proc_root: Path):
        nonlocal calls
        result = real_census(script=script, proc_root=proc_root)
        calls += 1
        if calls == 2:
            proc_case.set_identity(
                100,
                ppid=1,
                start_ticks=1000,
                argv=["bash", "--version"],
            )
        return result

    monkeypatch.setattr(subject, "_take_census", exec_after_second_census)

    body = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=True,
        proc_root=proc_case.root,
        settle_timeout=0.25,
        kernel=kernel,
    )

    assert body["status"] == "UNKNOWN"
    assert body["reason_code"] == "PROCESS_IDENTITY_CHANGED"
    assert kernel.signalled == []
    assert kernel.closed == [100]
    assert not record.exists()


@pytest.mark.parametrize(
    ("shape", "rewrite_after_census", "expected_signalled"),
    (
        ("one-target", 2, []),
        ("two-targets", 3, [101]),
    ),
)
def test_boot_id_change_after_pidfd_receipt_reaches_the_caller_unchanged(
    proc_case: ProcCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
    rewrite_after_census: int,
    expected_signalled: list[int],
) -> None:
    """A kernel reboot diagnostic must not be replaced by stale receipt state."""

    proc_case.add(200, ppid=1, start_ticks=500)
    proc_case.add(100, ppid=1, start_ticks=1000)
    if shape == "two-targets":
        proc_case.add(101, ppid=100, start_ticks=1001)
    record = tmp_path / "watchdog-adoption.json"
    kernel = FakeKernel(proc_case)
    subject = _subject()
    real_census = subject._take_census
    real_receipt = subject._receipt_after_pidfd
    census_calls = 0
    receipt_errors: list[object] = []

    def rewrite_boot_after_selected_census(*, script: Path, proc_root: Path):
        nonlocal census_calls
        result = real_census(script=script, proc_root=proc_root)
        census_calls += 1
        if census_calls == rewrite_after_census:
            (proc_case.root / "sys" / "kernel" / "random" / "boot_id").write_text(
                "boot-row407-restarted\n"
            )
        return result

    def observe_receipt(target, *, proc_root: Path):
        try:
            return real_receipt(target, proc_root=proc_root)
        except subject.WatchdogUnknown as error:
            receipt_errors.append(error)
            raise

    monkeypatch.setattr(subject, "_take_census", rewrite_boot_after_selected_census)
    monkeypatch.setattr(subject, "_receipt_after_pidfd", observe_receipt)

    body = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=True,
        proc_root=proc_case.root,
        settle_timeout=0.25,
        kernel=kernel,
    )

    assert census_calls == rewrite_after_census
    assert len(receipt_errors) == 1
    assert receipt_errors[0].reason_code == "BOOT_ID_CHANGED"
    assert receipt_errors[0].message == "kernel boot ID changed after pidfd acquisition"
    assert body["status"] == "UNKNOWN"
    assert body["reason_code"] == "BOOT_ID_CHANGED", body
    assert body["message"] == "kernel boot ID changed after pidfd acquisition"
    assert kernel.signalled == expected_signalled
    assert not record.exists()


def test_collapse_refuses_to_signal_the_calling_lineage(
    proc_case: ProcCase,
    tmp_path: Path,
) -> None:
    """A newer accidental duplicate must not make collapse signal this invocation."""

    caller_pid = os.getpid()
    parent_pid = os.getppid()
    other_pid = max(caller_pid, parent_pid) + 100_000
    assert caller_pid > 0 and parent_pid > 0 and caller_pid != parent_pid
    proc_case.add(parent_pid, ppid=1, start_ticks=1000)
    proc_case.add(caller_pid, ppid=parent_pid, start_ticks=1001)
    proc_case.add(other_pid, ppid=1, start_ticks=2000)
    subject = _subject()
    census = subject.inspect_watchdogs(script=proc_case.script, proc_root=proc_case.root)
    assert _lineage_pids(census) == [[parent_pid, caller_pid], [other_pid]]
    assert census["authority_root"]["pid"] == parent_pid
    assert census["authority_policy"] == "oldest-root-start-ticks"
    kernel = FakeKernel(proc_case)
    record = tmp_path / "watchdog-adoption.json"

    body = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=True,
        proc_root=proc_case.root,
        settle_timeout=0.25,
        kernel=kernel,
    )

    assert body["status"] == "REFUSED", body
    assert body["reason_code"] == "CALLING_LINEAGE_WOULD_BE_SIGNALED"
    assert str(caller_pid) in body["message"]
    assert subject._exit_code(body, action=True) == 3
    assert kernel.signalled == []
    assert not record.exists()


def test_collapse_refuses_when_the_direct_parent_is_the_matching_watchdog(
    proc_case: ProcCase,
    tmp_path: Path,
) -> None:
    """The Python caller need not itself match for collapse to kill its Bash parent."""

    parent_pid = os.getppid()
    other_pid = max(os.getpid(), parent_pid) + 100_000
    assert parent_pid > 0 and other_pid != parent_pid
    proc_case.add(other_pid, ppid=1, start_ticks=500)
    proc_case.add(parent_pid, ppid=1, start_ticks=1000)
    kernel = FakeKernel(proc_case)
    record = tmp_path / "watchdog-adoption.json"
    subject = _subject()
    census = subject.inspect_watchdogs(script=proc_case.script, proc_root=proc_case.root)
    assert _lineage_pids(census) == [[other_pid], [parent_pid]]
    assert census["authority_root"]["pid"] == other_pid

    body = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=True,
        proc_root=proc_case.root,
        settle_timeout=0.25,
        kernel=kernel,
    )

    assert body["status"] == "REFUSED", body
    assert body["reason_code"] == "CALLING_LINEAGE_WOULD_BE_SIGNALED"
    assert str(parent_pid) in body["message"]
    assert kernel.signalled == []
    assert not record.exists()


def test_collapse_requires_the_current_and_parent_pids_when_current_is_a_match(
    proc_case: ProcCase,
    tmp_path: Path,
) -> None:
    """Missing caller ancestry in the census is UNKNOWN, never permission to signal."""

    caller_pid = os.getpid()
    parent_pid = os.getppid()
    other_pid = max(caller_pid, parent_pid) + 100_000
    proc_case.add(caller_pid, ppid=parent_pid, start_ticks=1001)
    proc_case.add(other_pid, ppid=1, start_ticks=2000)
    kernel = FakeKernel(proc_case)
    record = tmp_path / "watchdog-adoption.json"
    subject = _subject()

    body = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=True,
        proc_root=proc_case.root,
        settle_timeout=0.25,
        kernel=kernel,
    )

    assert body["status"] == "UNKNOWN", body
    assert body["reason_code"] == "CALLING_LINEAGE_UNREADABLE"
    assert subject._exit_code(body, action=True) == 2
    assert kernel.signalled == []
    assert not record.exists()


def test_collapse_without_calling_lineage_keeps_oldest_and_signals_other_members(
    proc_case: ProcCase,
    tmp_path: Path,
) -> None:
    """Self protection is not a disguised disablement of legitimate collapse."""

    proc_case.add(100, ppid=1, start_ticks=1000)
    proc_case.add(200, ppid=1, start_ticks=2000)
    proc_case.add(201, ppid=200, start_ticks=2001)
    kernel = FakeKernel(proc_case)
    record = tmp_path / "watchdog-adoption.json"
    subject = _subject()

    body = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=True,
        proc_root=proc_case.root,
        settle_timeout=0.25,
        kernel=kernel,
    )

    assert body["status"] == "ADOPTED"
    assert body["authority_root"]["pid"] == 100
    assert body["authority_policy"] == "oldest-root-start-ticks"
    assert kernel.signalled == [201, 200]
    saved = json.loads(record.read_text())
    assert saved["authority_policy"] == "oldest-root-start-ticks"


def test_pidfd_readiness_timeout_forbids_adoption_without_numeric_polling(
    proc_case: ProcCase,
    tmp_path: Path,
) -> None:
    """Numeric-PID polling after SIGTERM can observe a recycled process as settlement."""

    proc_case.add(100, ppid=1, start_ticks=1000)
    proc_case.add(200, ppid=1, start_ticks=500)
    record = tmp_path / "watchdog-adoption.json"
    kernel = FakeKernel(proc_case, timeout_pid=100)
    subject = _subject()

    body = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=True,
        proc_root=proc_case.root,
        settle_timeout=0.25,
        kernel=kernel,
    )

    assert body["status"] == "UNKNOWN"
    assert body["reason_code"] == "PROCESS_SETTLEMENT_TIMEOUT"
    assert kernel.signalled == [100]
    assert kernel.waited == [(100, 0.25)]
    assert kernel.closed == [100]
    assert not record.exists()


def test_unique_census_publishes_complete_no_overwrite_adoption_record(
    proc_case: ProcCase,
    tmp_path: Path,
) -> None:
    """Publishing before unique proof makes the manifest another wrong-green marker."""

    proc_case.add(200, ppid=1, start_ticks=2000)
    record = tmp_path / "watchdog-adoption.json"
    subject = _subject()

    first = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=False,
        proc_root=proc_case.root,
        settle_timeout=0.25,
    )
    original = record.read_bytes()
    second = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=False,
        proc_root=proc_case.root,
        settle_timeout=0.25,
    )

    assert first["status"] == "ADOPTED"
    assert second["status"] == "ADOPTED"
    assert second["idempotent"] is True
    assert record.read_bytes() == original
    saved = json.loads(original)
    assert set(saved) == {
        "schema",
        "boot_id",
        "script",
        "authority_policy",
        "authority_root",
        "members",
    }


def test_new_duplicate_after_initial_unique_census_forbids_publication(
    proc_case: ProcCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-lock-style census is stale evidence by the time a record is linked."""

    proc_case.add(200, ppid=1, start_ticks=2000)
    record = tmp_path / "watchdog-adoption.json"
    subject = _subject()
    real_census = subject._take_census
    calls = 0

    def add_duplicate_after_first_census(*, script: Path, proc_root: Path):
        nonlocal calls
        result = real_census(script=script, proc_root=proc_root)
        calls += 1
        if calls == 1:
            proc_case.add(300, ppid=1, start_ticks=3000)
        return result

    monkeypatch.setattr(subject, "_take_census", add_duplicate_after_first_census)

    body = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=False,
        proc_root=proc_case.root,
        settle_timeout=0.25,
    )

    assert body["status"] == "UNKNOWN"
    assert body["reason_code"] == "ADOPTION_CENSUS_CHANGED"
    assert not record.exists()


def test_idempotent_adoption_revalidates_existing_record_inode_before_return(
    proc_case: ProcCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equal bytes in a replacement inode are not the locked record first read."""

    proc_case.add(200, ppid=1, start_ticks=2000)
    record = tmp_path / "watchdog-adoption.json"
    subject = _subject()
    first = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=False,
        proc_root=proc_case.root,
        settle_timeout=0.25,
    )
    assert first["status"] == "ADOPTED"
    real_read = subject._read_existing_record
    calls = 0

    def replace_after_first_read(path: Path):
        nonlocal calls
        result = real_read(path)
        calls += 1
        if calls == 1:
            retained = path.read_bytes()
            path.unlink()
            path.write_bytes(retained)
        return result

    monkeypatch.setattr(subject, "_read_existing_record", replace_after_first_read)

    body = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=False,
        proc_root=proc_case.root,
        settle_timeout=0.25,
    )

    assert body["status"] == "UNKNOWN"
    assert body["reason_code"] == "ADOPTION_RECORD_CHANGED"


def test_existing_adoption_remains_authority_when_a_newer_duplicate_appears(
    proc_case: ProcCase,
    tmp_path: Path,
) -> None:
    """Always choosing newest would let a duplicate steal previously proven authority."""

    proc_case.add(100, ppid=1, start_ticks=1000)
    record = tmp_path / "watchdog-adoption.json"
    subject = _subject()
    adopted = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=False,
        proc_root=proc_case.root,
        settle_timeout=0.25,
    )
    assert adopted["status"] == "ADOPTED"
    proc_case.add(200, ppid=1, start_ticks=2000)
    kernel = FakeKernel(proc_case)

    collapsed = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=True,
        proc_root=proc_case.root,
        settle_timeout=0.25,
        kernel=kernel,
    )

    assert collapsed["status"] == "ADOPTED"
    assert collapsed["idempotent"] is True
    assert kernel.signalled == [200]
    assert json.loads(record.read_text())["authority_root"]["pid"] == 100


def test_duplicates_without_explicit_collapse_refuse_and_write_nothing(
    proc_case: ProcCase,
    tmp_path: Path,
) -> None:
    """Adoption permission must never imply process-signalling permission."""

    proc_case.add(100, ppid=1, start_ticks=1000)
    proc_case.add(200, ppid=1, start_ticks=2000)
    record = tmp_path / "watchdog-adoption.json"
    subject = _subject()

    body = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=False,
        proc_root=proc_case.root,
        settle_timeout=0.25,
    )

    assert body["status"] == "REFUSED"
    assert body["reason_code"] == "DUPLICATES_REQUIRE_EXPLICIT_COLLAPSE"
    assert not record.exists()


def test_malformed_existing_record_is_retained_and_forbids_replacement(
    proc_case: ProcCase,
    tmp_path: Path,
) -> None:
    """Atomic publication is unsafe if it overwrites unresolved prior evidence."""

    proc_case.add(200, ppid=1, start_ticks=2000)
    record = tmp_path / "watchdog-adoption.json"
    original = b"{not-json\n"
    record.write_bytes(original)
    subject = _subject()

    body = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=False,
        proc_root=proc_case.root,
        settle_timeout=0.25,
    )

    assert body["status"] == "UNKNOWN"
    assert body["reason_code"] == "ADOPTION_RECORD_UNREADABLE"
    assert record.read_bytes() == original


@pytest.mark.parametrize("fail_at", (1, 2), ids=("file-fsync", "directory-fsync"))
def test_adoption_fsync_failure_cannot_publish_an_adoptable_record(
    proc_case: ProcCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_at: int,
) -> None:
    """A failed durability boundary cannot leave a success-shaped final marker."""

    proc_case.add(200, ppid=1, start_ticks=2000)
    record = tmp_path / "watchdog-adoption.json"
    subject = _subject()
    real_fsync = subject.os.fsync
    calls = 0

    def fail_selected_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError(f"injected fsync failure {fail_at}")
        real_fsync(fd)

    monkeypatch.setattr(subject.os, "fsync", fail_selected_fsync)

    body = subject.adopt_watchdog(
        script=proc_case.script,
        record=record,
        collapse=False,
        proc_root=proc_case.root,
        settle_timeout=0.25,
    )

    assert body["status"] == "UNKNOWN"
    assert body["reason_code"] == "ADOPTION_RECORD_PUBLISH_FAILED"
    assert not record.exists()
    assert not list(tmp_path.glob(".watchdog-adoption.json.tmp.*"))
