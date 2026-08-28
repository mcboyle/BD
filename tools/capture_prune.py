#!/usr/bin/env python3
"""Prune only capture objects proven under one caller-selected root."""

from __future__ import annotations

import fnmatch
import json
import os
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from tools.safe_temp_remove import rename_verify_destroy_at


class PruneUnknown(RuntimeError):
    """The candidate population or a destructive target could not be proven."""


@dataclass(frozen=True)
class OwnedEntry:
    name: str
    identity: tuple[int, int]
    mode: int
    uid: int
    mtime_ns: int

    def record(self) -> dict[str, int | str]:
        return {
            "name": self.name,
            "device": self.identity[0],
            "inode": self.identity[1],
            "mode": self.mode,
            "uid": self.uid,
            "mtime_ns": self.mtime_ns,
        }


def _split_glob(value: str) -> tuple[str, str]:
    if not value or "\x00" in value:
        raise PruneUnknown("the advertised glob is empty or contains NUL")
    root, separator, pattern = value.rpartition("/")
    if not separator:
        root = "."
    elif not root:
        root = "/"
    if not pattern or pattern in (".", "..") or "/" in pattern:
        raise PruneUnknown(f"the advertised glob has no safe basename pattern: {value!r}")
    if any(mark in root for mark in ("*", "?", "[")):
        raise PruneUnknown(
            f"the advertised glob's directory must be literal, not patterned: {root!r}"
        )
    return root, pattern


def _entry(name: str, info: os.stat_result) -> OwnedEntry:
    return OwnedEntry(
        name=name,
        identity=(info.st_dev, info.st_ino),
        mode=stat.S_IFMT(info.st_mode),
        uid=info.st_uid,
        mtime_ns=info.st_mtime_ns,
    )


def _require_owned(entry: OwnedEntry, root_fd: int, root_device: int) -> None:
    try:
        current = os.stat(entry.name, dir_fd=root_fd, follow_symlinks=False)
    except OSError as exc:
        raise PruneUnknown(f"cannot re-identify {entry.name!r}: {exc}") from exc
    observed = _entry(entry.name, current)
    if observed != entry:
        raise PruneUnknown(
            f"{entry.name!r} changed identity after selection; expected "
            f"{entry.record()}, found {observed.record()}"
        )
    if entry.uid != os.geteuid():
        raise PruneUnknown(
            f"{entry.name!r} is uid {entry.uid}, not pruning uid {os.geteuid()}"
        )
    if entry.identity[0] != root_device:
        raise PruneUnknown(f"{entry.name!r} crosses the selected root's device")


def _archive_for(directory: OwnedEntry, root_fd: int, root_device: int) -> OwnedEntry | None:
    name = f"{directory.name}.tar.gz"
    try:
        info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PruneUnknown(f"cannot identify archive {name!r}: {exc}") from exc
    archive = _entry(name, info)
    if archive.mode != stat.S_IFREG:
        raise PruneUnknown(f"archive {name!r} is not a regular file")
    _require_owned(archive, root_fd, root_device)
    return archive


def _write_record(ledger, payload: dict) -> None:
    line = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    written = os.write(ledger, line.encode("utf-8"))
    if written != len(line.encode("utf-8")):
        raise PruneUnknown("the recovery ledger accepted only a partial write")
    os.fsync(ledger)


def _open_ledger(root_fd: int) -> tuple[int, str, tuple[int, int]]:
    for _attempt in range(20):
        name = f".bd-capture-prune-{secrets.token_hex(12)}.jsonl"
        try:
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                0o600,
                dir_fd=root_fd,
            )
        except FileExistsError:
            continue
        info = os.fstat(descriptor)
        return descriptor, name, (info.st_dev, info.st_ino)
    raise PruneUnknown("could not claim a unique recovery ledger")


def _remove_ledger(root_fd: int, ledger: int, name: str,
                   identity: tuple[int, int]) -> None:
    named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    if (named.st_dev, named.st_ino) != identity:
        raise PruneUnknown("recovery ledger identity changed; leaving it untouched")
    os.unlink(name, dir_fd=root_fd)
    if os.fstat(ledger).st_nlink != 0:
        raise PruneUnknown("recovery ledger unlink could not be proven")
    os.fsync(root_fd)


def prune(keep: int, advertised_glob: str) -> list[str]:
    if keep < 1:
        raise PruneUnknown("a retention below one is destructive by definition")
    root_text, pattern = _split_glob(advertised_glob)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        root_fd = os.open(root_text, flags)
    except OSError as exc:
        raise PruneUnknown(f"cannot open advertised root {root_text!r}: {exc}") from exc
    ledger = None
    try:
        root_info = os.fstat(root_fd)
        root_device = root_info.st_dev
        candidates: list[OwnedEntry] = []
        for item in os.scandir(root_fd):
            if not fnmatch.fnmatchcase(item.name, pattern):
                continue
            try:
                info = item.stat(follow_symlinks=False)
            except OSError as exc:
                raise PruneUnknown(f"cannot identify candidate {item.name!r}: {exc}") from exc
            candidate = _entry(item.name, info)
            if candidate.mode != stat.S_IFDIR:
                continue
            _require_owned(candidate, root_fd, root_device)
            candidates.append(candidate)
        candidates.sort(key=lambda item: (item.mtime_ns, item.name), reverse=True)
        victims = candidates[keep:]
        if not victims:
            return []

        planned: list[tuple[OwnedEntry, OwnedEntry | None]] = []
        for directory in victims:
            _require_owned(directory, root_fd, root_device)
            planned.append(
                (directory, _archive_for(directory, root_fd, root_device))
            )

        ledger, ledger_name, ledger_identity = _open_ledger(root_fd)
        _write_record(ledger, {
            "schema": "bd-capture-prune-ledger/1",
            "state": "PLANNED",
            "advertised_glob": advertised_glob,
            "keep": keep,
            "root": {
                "device": root_info.st_dev,
                "inode": root_info.st_ino,
                "uid": root_info.st_uid,
            },
            "victims": [
                {
                    "directory": directory.record(),
                    "archive": archive.record() if archive else None,
                }
                for directory, archive in planned
            ],
        })
        # fsyncing the file proves its bytes, not its directory entry. The
        # recovery name must itself be durable before the first removal.
        os.fsync(root_fd)

        removed: list[str] = []
        for directory, archive in planned:
            _require_owned(directory, root_fd, root_device)
            if archive is not None:
                _require_owned(archive, root_fd, root_device)
            ok, reason = rename_verify_destroy_at(
                root_fd, directory.name, expected_identity=directory.identity
            )
            if not ok:
                raise PruneUnknown(
                    f"directory {directory.name!r} was not removed: {reason}"
                )
            _write_record(ledger, {
                "state": "DIRECTORY_REMOVED",
                "directory": directory.record(),
            })
            if archive is not None:
                ok, reason = rename_verify_destroy_at(
                    root_fd, archive.name, expected_identity=archive.identity
                )
                if not ok:
                    raise PruneUnknown(
                        f"archive {archive.name!r} was not removed: {reason}"
                    )
                _write_record(ledger, {
                    "state": "ARCHIVE_REMOVED",
                    "archive": archive.record(),
                })
            else:
                try:
                    os.stat(
                        f"{directory.name}.tar.gz",
                        dir_fd=root_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    pass
                else:
                    raise PruneUnknown(
                        f"archive {directory.name + '.tar.gz'!r} appeared "
                        "after the removal plan; it was left untouched"
                    )
            removed.append(os.path.join(root_text, directory.name))

        _write_record(ledger, {"state": "COMPLETE", "removed": removed})
        for path in removed:
            print(f"pruned {path}")
        sys.stdout.flush()
        _remove_ledger(root_fd, ledger, ledger_name, ledger_identity)
        return removed
    finally:
        if ledger is not None:
            os.close(ledger)
        os.close(root_fd)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: capture_prune.py KEEP GLOB", file=sys.stderr)
        return 2
    try:
        keep = int(argv[1])
    except ValueError:
        print(f"bd_capture_prune: UNKNOWN -- invalid keep count {argv[1]!r}", file=sys.stderr)
        return 2
    try:
        prune(keep, argv[2])
    except (PruneUnknown, OSError) as exc:
        print(f"bd_capture_prune: UNKNOWN -- {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
