#!/usr/bin/env python3
"""Census, explicitly collapse, and adopt watchdogs by durable identity."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import secrets
import select
import signal
import stat
import sys
from typing import NoReturn, Protocol


SCHEMA = 1
MAX_RECORD_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ProcessIdentity:
    boot_id: str
    pid: int
    ppid: int
    start_ticks: int
    argv: tuple[str, ...]
    script: str

    def record(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "ppid": self.ppid,
            "start_ticks": self.start_ticks,
            "argv": list(self.argv),
        }


@dataclass(frozen=True)
class Lineage:
    root: ProcessIdentity
    members: tuple[ProcessIdentity, ...]


@dataclass(frozen=True)
class Census:
    status: str
    boot_id: str
    script: str
    lineages: tuple[Lineage, ...] = ()
    reason_code: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class ExistingRecord:
    payload: dict[str, object]
    raw: bytes
    identity: tuple[int, int, int]


@dataclass(frozen=True)
class WatchdogUnknown(Exception):
    reason_code: str
    message: str


class Kernel(Protocol):
    def open_pidfd(self, identity: ProcessIdentity) -> int: ...

    def send_term(self, pidfd: int) -> None: ...

    def wait_ready(self, pidfd: int, timeout: float) -> bool: ...

    def close_pidfd(self, pidfd: int) -> None: ...


class LinuxKernel:
    def open_pidfd(self, identity: ProcessIdentity) -> int:
        return os.pidfd_open(identity.pid, 0)

    def send_term(self, pidfd: int) -> None:
        signal.pidfd_send_signal(pidfd, signal.SIGTERM, None, 0)

    def wait_ready(self, pidfd: int, timeout: float) -> bool:
        poller = select.poll()
        poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
        timeout_ms = max(0, int(timeout * 1000))
        return bool(poller.poll(timeout_ms))

    def close_pidfd(self, pidfd: int) -> None:
        os.close(pidfd)


def _unknown(reason_code: str, message: str) -> NoReturn:
    raise WatchdogUnknown(reason_code, message)


def _read_boot_id(proc_root: Path) -> str:
    path = proc_root / "sys" / "kernel" / "random" / "boot_id"
    try:
        boot_id = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        _unknown("BOOT_ID_UNREADABLE", str(error))
    if not boot_id:
        _unknown("BOOT_ID_UNREADABLE", "kernel boot ID is empty")
    return boot_id


def _parse_stat(raw: str, expected_pid: int) -> tuple[int, int, int]:
    close = raw.rfind(")")
    open_paren = raw.find("(")
    if open_paren <= 0 or close <= open_paren:
        raise ValueError("stat row has no complete command field")
    try:
        parsed_pid = int(raw[:open_paren].strip())
        fields = raw[close + 1 :].split()
        ppid = int(fields[1])
        start_ticks = int(fields[19])
    except (IndexError, ValueError) as error:
        raise ValueError("stat row lacks PID, PPID, or start ticks") from error
    if parsed_pid != expected_pid:
        raise ValueError(
            f"stat PID {parsed_pid} does not match proc entry {expected_pid}"
        )
    return parsed_pid, ppid, start_ticks


def _decode_argv(raw: bytes) -> tuple[str, ...]:
    if not raw:
        return ()
    fields = raw.split(b"\0")
    if fields[-1] == b"":
        fields.pop()
    return tuple(os.fsdecode(field) for field in fields)


def _bash_script_operand(argv: tuple[str, ...]) -> str | None:
    if len(argv) < 2 or Path(argv[0]).name != "bash":
        return None
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            index += 1
            break
        if not argument.startswith(("-", "+")) or argument in {"-", "+"}:
            break
        if argument.startswith("--"):
            if argument in {"--command", "--help", "--version"}:
                return None
            index += 1
            continue
        options = argument[1:]
        if "c" in options:
            return None
        if argument in {"-O", "+O"}:
            index += 2
        else:
            index += 1
    if index >= len(argv):
        return None
    return argv[index]


def _resolved_operand(operand: str, cwd: Path) -> Path:
    path = Path(operand)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve(strict=False)


def _read_matching_process(
    *,
    proc_root: Path,
    pid: int,
    canonical_script: Path,
    boot_id: str,
) -> ProcessIdentity | None:
    entry = proc_root / str(pid)
    try:
        stat_before_raw = (entry / "stat").read_text(encoding="ascii")
        argv = _decode_argv((entry / "cmdline").read_bytes())
        cwd = (entry / "cwd").resolve(strict=True)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as error:
        _unknown("PROCESS_IDENTITY_UNREADABLE", f"PID {pid}: {error}")

    operand = _bash_script_operand(argv)
    if operand is None or _resolved_operand(operand, cwd) != canonical_script:
        return None
    try:
        before = _parse_stat(stat_before_raw, pid)
        stat_after_raw = (entry / "stat").read_text(encoding="ascii")
        after = _parse_stat(stat_after_raw, pid)
    except (OSError, UnicodeError, ValueError) as error:
        _unknown("PROCESS_IDENTITY_UNREADABLE", f"PID {pid}: {error}")
    if before != after:
        _unknown(
            "PROCESS_IDENTITY_CHANGED",
            f"PID {pid} changed identity during census",
        )
    _, ppid, start_ticks = after
    return ProcessIdentity(
        boot_id=boot_id,
        pid=pid,
        ppid=ppid,
        start_ticks=start_ticks,
        argv=argv,
        script=str(canonical_script),
    )


def _build_lineages(
    identities: tuple[ProcessIdentity, ...],
) -> tuple[Lineage, ...]:
    by_pid = {identity.pid: identity for identity in identities}
    grouped: dict[int, list[ProcessIdentity]] = {}
    for identity in identities:
        current = identity
        visited: set[int] = set()
        while current.ppid in by_pid:
            if current.pid in visited:
                _unknown("PROCESS_LINEAGE_CYCLE", "matching PPID graph contains a cycle")
            visited.add(current.pid)
            current = by_pid[current.ppid]
        grouped.setdefault(current.pid, []).append(identity)
    lineages: list[Lineage] = []
    for root_pid, members in grouped.items():
        ordered = tuple(sorted(members, key=lambda item: (item.start_ticks, item.pid)))
        lineages.append(Lineage(root=by_pid[root_pid], members=ordered))
    return tuple(
        sorted(
            lineages,
            key=lambda lineage: (lineage.root.start_ticks, lineage.root.pid),
        )
    )


def _take_census(*, script: Path, proc_root: Path) -> Census:
    try:
        canonical_script = script.resolve(strict=True)
        proc_root = proc_root.resolve(strict=True)
    except OSError as error:
        return Census(
            status="UNKNOWN",
            boot_id="",
            script=str(script),
            reason_code="CENSUS_PATH_UNREADABLE",
            message=str(error),
        )
    try:
        boot_before = _read_boot_id(proc_root)
        identities: list[ProcessIdentity] = []
        for entry in sorted(
            (item for item in proc_root.iterdir() if item.name.isdecimal()),
            key=lambda item: int(item.name),
        ):
            identity = _read_matching_process(
                proc_root=proc_root,
                pid=int(entry.name),
                canonical_script=canonical_script,
                boot_id=boot_before,
            )
            if identity is not None:
                identities.append(identity)
        boot_after = _read_boot_id(proc_root)
        if boot_before != boot_after:
            _unknown("BOOT_ID_CHANGED", "kernel boot ID changed during census")
        lineages = _build_lineages(tuple(identities))
    except WatchdogUnknown as error:
        return Census(
            status="UNKNOWN",
            boot_id=locals().get("boot_before", ""),
            script=str(canonical_script),
            reason_code=error.reason_code,
            message=error.message,
        )
    except OSError as error:
        return Census(
            status="UNKNOWN",
            boot_id=locals().get("boot_before", ""),
            script=str(canonical_script),
            reason_code="PROCESS_CENSUS_UNREADABLE",
            message=str(error),
        )
    status = "ABSENT" if not lineages else "UNIQUE"
    if len(lineages) > 1:
        status = "DUPLICATES"
    return Census(
        status=status,
        boot_id=boot_before,
        script=str(canonical_script),
        lineages=lineages,
    )


def _authority(lineages: tuple[Lineage, ...]) -> Lineage | None:
    if not lineages:
        return None
    return max(
        lineages,
        key=lambda lineage: (lineage.root.start_ticks, lineage.root.pid),
    )


def _census_payload(census: Census) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": census.status,
        "boot_id": census.boot_id,
        "script": census.script,
        "lineages": [
            {
                "root": lineage.root.record(),
                "members": [member.record() for member in lineage.members],
            }
            for lineage in census.lineages
        ],
    }
    authority = _authority(census.lineages)
    payload["authority_root"] = authority.root.record() if authority else None
    if census.reason_code:
        payload["reason_code"] = census.reason_code
        payload["message"] = census.message or ""
    return payload


def inspect_watchdogs(*, script: Path, proc_root: Path = Path("/proc")) -> dict[str, object]:
    return _census_payload(_take_census(script=script, proc_root=proc_root))


def _record_payload(census: Census, lineage: Lineage) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "boot_id": census.boot_id,
        "script": census.script,
        "authority_root": lineage.root.record(),
        "members": [member.record() for member in lineage.members],
    }


def _record_bytes(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _member_from_record(
    value: object,
    *,
    boot_id: str,
    script: str,
) -> ProcessIdentity:
    if not isinstance(value, dict) or set(value) != {
        "pid",
        "ppid",
        "start_ticks",
        "argv",
    }:
        _unknown("ADOPTION_RECORD_UNREADABLE", "process record keys are invalid")
    pid = value["pid"]
    ppid = value["ppid"]
    start_ticks = value["start_ticks"]
    argv = value["argv"]
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(ppid, int)
        or isinstance(ppid, bool)
        or ppid < 0
        or not isinstance(start_ticks, int)
        or isinstance(start_ticks, bool)
        or start_ticks < 0
        or not isinstance(argv, list)
        or not argv
        or not all(isinstance(argument, str) for argument in argv)
    ):
        _unknown("ADOPTION_RECORD_UNREADABLE", "process record values are invalid")
    return ProcessIdentity(
        boot_id=boot_id,
        pid=pid,
        ppid=ppid,
        start_ticks=start_ticks,
        argv=tuple(argv),
        script=script,
    )


def _read_existing_record(
    record: Path,
) -> ExistingRecord | None:
    try:
        metadata = record.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        _unknown("ADOPTION_RECORD_UNREADABLE", str(error))
    if not stat.S_ISREG(metadata.st_mode):
        _unknown("ADOPTION_RECORD_UNREADABLE", "adoption record is not regular")
    try:
        descriptor = os.open(record, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as error:
        _unknown("ADOPTION_RECORD_UNREADABLE", str(error))
    try:
        held = os.fstat(descriptor)
        if held.st_size > MAX_RECORD_BYTES:
            _unknown("ADOPTION_RECORD_UNREADABLE", "adoption record is too large")
        raw = os.pread(descriptor, held.st_size, 0)
        after = os.fstat(descriptor)
        if (
            len(raw) != held.st_size
            or (held.st_dev, held.st_ino, held.st_size, held.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (after.st_dev, after.st_ino) != (record.lstat().st_dev, record.lstat().st_ino)
        ):
            _unknown("ADOPTION_RECORD_UNREADABLE", "adoption record changed while read")
        try:
            payload = json.loads(raw.decode("utf-8", "strict"))
        except (UnicodeError, json.JSONDecodeError) as error:
            _unknown("ADOPTION_RECORD_UNREADABLE", str(error))
        if not isinstance(payload, dict) or set(payload) != {
            "schema",
            "boot_id",
            "script",
            "authority_root",
            "members",
        }:
            _unknown("ADOPTION_RECORD_UNREADABLE", "adoption record keys are invalid")
        return ExistingRecord(
            payload=payload,
            raw=raw,
            identity=(after.st_dev, after.st_ino, after.st_mode),
        )
    finally:
        os.close(descriptor)


def _existing_authority(
    existing: ExistingRecord | None,
    census: Census,
) -> Lineage | None:
    if existing is None:
        return None
    payload = existing.payload
    if (
        payload.get("schema") != SCHEMA
        or payload.get("boot_id") != census.boot_id
        or payload.get("script") != census.script
        or not isinstance(payload.get("members"), list)
    ):
        _unknown("ADOPTION_RECORD_UNREADABLE", "adoption record header is invalid")
    members = tuple(
        _member_from_record(
            member,
            boot_id=census.boot_id,
            script=census.script,
        )
        for member in payload["members"]
    )
    root = _member_from_record(
        payload.get("authority_root"),
        boot_id=census.boot_id,
        script=census.script,
    )
    for lineage in census.lineages:
        if lineage.root == root and lineage.members == members:
            return lineage
    _unknown(
        "ADOPTION_RECORD_STALE",
        "existing adoption record does not name a current exact lineage",
    )


def _identity_set(census: Census) -> set[ProcessIdentity]:
    return {
        member
        for lineage in census.lineages
        for member in lineage.members
    }


def _depth(member: ProcessIdentity, lineage: Lineage) -> int:
    by_pid = {identity.pid: identity for identity in lineage.members}
    depth = 0
    current = member
    while current.ppid in by_pid:
        depth += 1
        current = by_pid[current.ppid]
    return depth


def _unknown_payload(reason_code: str, message: str) -> dict[str, object]:
    return {
        "status": "UNKNOWN",
        "reason_code": reason_code,
        "message": message,
    }


def _refused(reason_code: str, message: str) -> dict[str, object]:
    return {
        "status": "REFUSED",
        "reason_code": reason_code,
        "message": message,
    }


def _parent_still_owned(
    parent: Path,
    parent_fd: int,
    identity: tuple[int, int],
) -> bool:
    try:
        held = os.fstat(parent_fd)
        named = parent.lstat()
    except OSError:
        return False
    return (held.st_dev, held.st_ino) == identity == (named.st_dev, named.st_ino)


def _cleanup_owned_path(
    parent: Path,
    parent_fd: int,
    parent_identity: tuple[int, int],
    name: str,
    identity: tuple[int, int],
) -> None:
    if not _parent_still_owned(parent, parent_fd, parent_identity):
        return
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) == identity:
            os.unlink(name, dir_fd=parent_fd)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _publish_record(record: Path, payload: dict[str, object]) -> None:
    parent = record.parent.resolve(strict=True)
    record = parent / record.name
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    temp_name = f".{record.name}.tmp.{os.getpid()}.{secrets.token_hex(8)}"
    temp_fd = -1
    temp_identity: tuple[int, int] | None = None
    parent_identity: tuple[int, int] | None = None
    linked = False
    try:
        parent_stat = os.fstat(parent_fd)
        path_parent = parent.lstat()
        parent_identity = (parent_stat.st_dev, parent_stat.st_ino)
        if parent_identity != (
            path_parent.st_dev,
            path_parent.st_ino,
        ):
            raise OSError("adoption-record parent identity changed")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        temp_fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
        held = os.fstat(temp_fd)
        temp_identity = (held.st_dev, held.st_ino)
        data = _record_bytes(payload)
        view = memoryview(data)
        while view:
            count = os.write(temp_fd, view)
            if count <= 0:
                raise OSError("short adoption-record write")
            view = view[count:]
        os.fsync(temp_fd)
        current = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != temp_identity:
            raise OSError("adoption-record temporary identity changed")
        if not _parent_still_owned(parent, parent_fd, parent_identity):
            raise OSError("adoption-record parent identity changed before link")
        os.link(
            temp_name,
            record.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        linked = True
        final = os.stat(record.name, dir_fd=parent_fd, follow_symlinks=False)
        if (final.st_dev, final.st_ino) != temp_identity:
            raise OSError("adoption-record final identity changed")
        if not _parent_still_owned(parent, parent_fd, parent_identity):
            raise OSError("adoption-record parent identity changed before fsync")
        os.fsync(parent_fd)
        _cleanup_owned_path(
            parent,
            parent_fd,
            parent_identity,
            temp_name,
            temp_identity,
        )
    except BaseException:
        if temp_identity is not None and parent_identity is not None:
            if linked:
                _cleanup_owned_path(
                    parent,
                    parent_fd,
                    parent_identity,
                    record.name,
                    temp_identity,
                )
            _cleanup_owned_path(
                parent,
                parent_fd,
                parent_identity,
                temp_name,
                temp_identity,
            )
        raise
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        os.close(parent_fd)


def _acquire_action_lock(record: Path) -> int:
    lock = record.with_name(record.name + ".lock")
    descriptor = os.open(
        lock,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _adopt_locked(
    *,
    script: Path,
    record: Path,
    collapse: bool,
    proc_root: Path,
    settle_timeout: float,
    kernel: Kernel,
) -> dict[str, object]:
    census = _take_census(script=script, proc_root=proc_root)
    if census.status == "UNKNOWN":
        return _unknown_payload(census.reason_code or "CENSUS_UNKNOWN", census.message or "")
    if census.status == "ABSENT":
        return _refused("WATCHDOG_ABSENT", "no matching watchdog lineage exists")
    try:
        existing = _read_existing_record(record)
        prior_authority = _existing_authority(existing, census)
    except WatchdogUnknown as error:
        return _unknown_payload(error.reason_code, error.message)
    authority = prior_authority or _authority(census.lineages)
    assert authority is not None

    if census.status == "DUPLICATES" and not collapse:
        return _refused(
            "DUPLICATES_REQUIRE_EXPLICIT_COLLAPSE",
            "independent watchdog lineages require explicit collapse permission",
        )

    if census.status == "DUPLICATES":
        expected_alive = _identity_set(census)
        duplicate_members: list[tuple[Lineage, ProcessIdentity]] = []
        for lineage in census.lineages:
            if lineage == authority:
                continue
            ordered = sorted(
                lineage.members,
                key=lambda item: (_depth(item, lineage), item.start_ticks, item.pid),
                reverse=True,
            )
            duplicate_members.extend((lineage, member) for member in ordered)

        for lineage, target in duplicate_members:
            try:
                pidfd = kernel.open_pidfd(target)
            except BaseException as error:
                return _unknown_payload("PIDFD_OPEN_FAILED", repr(error))
            outcome: dict[str, object] | None = None
            settled = False
            try:
                current = _take_census(script=script, proc_root=proc_root)
                if current.status == "UNKNOWN":
                    outcome = _unknown_payload(
                        current.reason_code or "CENSUS_UNKNOWN",
                        current.message or "",
                    )
                else:
                    by_pid = {
                        member.pid: member
                        for current_lineage in current.lineages
                        for member in current_lineage.members
                    }
                    if by_pid.get(target.pid) != target:
                        outcome = _unknown_payload(
                            "PROCESS_IDENTITY_CHANGED",
                            f"PID {target.pid} changed after pidfd acquisition",
                        )
                    elif _identity_set(current) != expected_alive:
                        outcome = _unknown_payload(
                            "COLLAPSE_CENSUS_CHANGED",
                            "watchdog census changed during duplicate collapse",
                        )
                    else:
                        current_lineage = next(
                            item
                            for item in current.lineages
                            if target in item.members
                        )
                        if current_lineage.root != lineage.root:
                            outcome = _unknown_payload(
                                "PROCESS_LINEAGE_CHANGED",
                                f"PID {target.pid} changed logical lineage",
                            )
                if outcome is None:
                    kernel.send_term(pidfd)
                    if not kernel.wait_ready(pidfd, settle_timeout):
                        outcome = _unknown_payload(
                            "PROCESS_SETTLEMENT_TIMEOUT",
                            f"PID {target.pid} pidfd was not ready before timeout",
                        )
                    else:
                        settled = True
            except BaseException as error:
                outcome = _unknown_payload("PIDFD_OPERATION_FAILED", repr(error))
            finally:
                try:
                    kernel.close_pidfd(pidfd)
                except BaseException as error:
                    outcome = _unknown_payload("PIDFD_CLOSE_FAILED", repr(error))
            if outcome is not None:
                return outcome
            if settled:
                expected_alive.remove(target)

        settled_census = _take_census(script=script, proc_root=proc_root)
        if settled_census.status == "UNKNOWN":
            return _unknown_payload(
                settled_census.reason_code or "CENSUS_UNKNOWN",
                settled_census.message or "",
            )
        if (
            settled_census.status != "UNIQUE"
            or len(settled_census.lineages) != 1
            or settled_census.lineages[0] != authority
        ):
            return _unknown_payload(
                "COLLAPSE_SETTLEMENT_CHANGED",
                "post-collapse census did not prove the retained exact lineage",
            )
        census = settled_census

    final_census = _take_census(script=script, proc_root=proc_root)
    if final_census.status == "UNKNOWN":
        return _unknown_payload(
            final_census.reason_code or "CENSUS_UNKNOWN",
            final_census.message or "",
        )
    if (
        final_census.status != "UNIQUE"
        or len(final_census.lineages) != 1
        or final_census.lineages[0] != authority
    ):
        return _unknown_payload(
            "ADOPTION_CENSUS_CHANGED",
            "final census did not prove the selected exact lineage",
        )
    census = final_census

    desired = _record_payload(census, authority)
    desired_bytes = _record_bytes(desired)
    if existing is not None:
        try:
            current_existing = _read_existing_record(record)
        except WatchdogUnknown as error:
            return _unknown_payload("ADOPTION_RECORD_CHANGED", error.message)
        if (
            current_existing is None
            or current_existing.identity != existing.identity
            or current_existing.raw != existing.raw
        ):
            return _unknown_payload(
                "ADOPTION_RECORD_CHANGED",
                "existing adoption record changed before idempotent return",
            )
        if existing.raw != desired_bytes:
            return _unknown_payload(
                "ADOPTION_RECORD_DIFFERENT",
                "existing valid adoption record is not byte-identical",
            )
        return {
            "status": "ADOPTED",
            "idempotent": True,
            "record": str(record),
            "authority_root": authority.root.record(),
        }
    try:
        _publish_record(record, desired)
    except BaseException as error:
        return _unknown_payload("ADOPTION_RECORD_PUBLISH_FAILED", repr(error))
    return {
        "status": "ADOPTED",
        "idempotent": False,
        "record": str(record),
        "authority_root": authority.root.record(),
    }


def adopt_watchdog(
    *,
    script: Path,
    record: Path,
    collapse: bool,
    proc_root: Path = Path("/proc"),
    settle_timeout: float = 5.0,
    kernel: Kernel | None = None,
) -> dict[str, object]:
    if settle_timeout < 0:
        return _refused("SETTLE_TIMEOUT_INVALID", "settle timeout must be nonnegative")
    try:
        parent = record.parent.resolve(strict=True)
        record = parent / record.name
        lock_fd = _acquire_action_lock(record)
    except BaseException as error:
        return _unknown_payload("ACTION_LOCK_FAILED", repr(error))
    try:
        return _adopt_locked(
            script=script,
            record=record,
            collapse=collapse,
            proc_root=proc_root,
            settle_timeout=settle_timeout,
            kernel=kernel or LinuxKernel(),
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _emit(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    status = payload["status"]
    if status in {"UNIQUE", "ADOPTED"}:
        print(f"{status} authority={payload.get('authority_root')}")
    elif status in {"ABSENT", "DUPLICATES"}:
        print(status)
    else:
        print(
            f"{status} {payload.get('reason_code', 'UNKNOWN')}: "
            f"{payload.get('message', '')}"
        )


def _exit_code(payload: dict[str, object], *, action: bool) -> int:
    status = payload["status"]
    if status in {"UNIQUE", "ADOPTED"}:
        return 0
    if status == "ABSENT" or (
        action and payload.get("reason_code") == "WATCHDOG_ABSENT"
    ):
        return 1
    if status == "UNKNOWN":
        return 2
    return 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument("--adopt-record", type=Path)
    parser.add_argument("--collapse", action="store_true")
    parser.add_argument("--settle-timeout", type=float, default=5.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.collapse and args.adopt_record is None:
        parser.error("--collapse requires --adopt-record")
    if args.adopt_record is None:
        payload = inspect_watchdogs(script=args.script, proc_root=args.proc_root)
        action = False
    else:
        payload = adopt_watchdog(
            script=args.script,
            record=args.adopt_record,
            collapse=args.collapse,
            proc_root=args.proc_root,
            settle_timeout=args.settle_timeout,
        )
        action = True
    _emit(payload, as_json=args.json)
    return _exit_code(payload, action=action)


if __name__ == "__main__":
    sys.exit(main())
