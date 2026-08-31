#!/usr/bin/env python3
"""Replay a row candidate onto main without rewriting its source worktree.

The source worker is immutable evidence.  All commit and dirty-state replay is
performed in a newly-created linked worktree.  A failed replay removes only
that new output and verifies that the source fingerprint did not change.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import stat
import subprocess
import sys
from typing import NoReturn


@dataclass
class ReplayFailure(Exception):
    reason_code: str
    message: str
    exit_code: int = 2


@dataclass(frozen=True)
class UntrackedEntry:
    relative: str
    mode: int
    kind: str


@dataclass(frozen=True)
class FsIdentity:
    device: int
    inode: int
    mode: int

    @classmethod
    def from_stat(cls, metadata: os.stat_result) -> "FsIdentity":
        return cls(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            mode=metadata.st_mode,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "device": self.device,
            "inode": self.inode,
            "mode": self.mode,
        }


@dataclass
class ReplayClaim:
    path: Path
    parent: Path
    token: str
    fd: int
    parent_fd: int
    path_identity: FsIdentity
    parent_identity: FsIdentity
    owner: dict[str, object]

    def close(self) -> None:
        for descriptor in (self.fd, self.parent_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


@dataclass(frozen=True)
class OutputOwnership:
    output_identity: FsIdentity | None
    git_dir: Path
    git_dir_identity: FsIdentity


def _git_environment(*, committer: bool = False) -> dict[str, str]:
    """Build a Git environment that cannot inherit repository selectors."""

    run_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    run_env.update(
        GIT_OPTIONAL_LOCKS="0",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
    )
    if committer:
        run_env.update(
            GIT_COMMITTER_NAME="BulkDownloader Candidate Replay",
            GIT_COMMITTER_EMAIL="candidate-replay@example.invalid",
        )
    return run_env


def _git_result(
    cwd: Path,
    *args: str,
    input_bytes: bytes | None = None,
    committer: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        input=input_bytes,
        capture_output=True,
        check=False,
        env=_git_environment(committer=committer),
    )


def _git_bytes(cwd: Path, *args: str) -> bytes:
    result = _git_result(cwd, *args)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise ReplayFailure("GIT_READ_FAILED", detail or f"git {' '.join(args)} failed")
    return result.stdout


def _git_text(cwd: Path, *args: str) -> str:
    return _git_bytes(cwd, *args).decode("utf-8", "strict").strip()


def _resolve_commit(cwd: Path, revision: str, reason_code: str) -> str:
    result = _git_result(cwd, "rev-parse", "--verify", f"{revision}^{{commit}}")
    if result.returncode != 0:
        raise ReplayFailure(reason_code, f"cannot resolve commit {revision!r}")
    return result.stdout.decode("ascii", "strict").strip()


def _safe_relative(raw: bytes) -> str:
    relative = os.fsdecode(raw)
    pure = PurePosixPath(relative)
    if not relative or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ReplayFailure("UNSAFE_UNTRACKED_PATH", f"unsafe untracked path {relative!r}")
    return relative


def _untracked_entries(source: Path) -> list[UntrackedEntry]:
    raw_paths = _git_bytes(
        source, "ls-files", "--others", "--exclude-standard", "-z"
    )
    entries: list[UntrackedEntry] = []
    for raw in filter(None, raw_paths.split(b"\0")):
        relative = _safe_relative(raw)
        path = source / relative
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISREG(metadata.st_mode):
            kind = "file"
        elif stat.S_ISLNK(metadata.st_mode):
            kind = "symlink"
        else:
            raise ReplayFailure(
                "UNSUPPORTED_UNTRACKED_TYPE",
                f"untracked path {relative!r} is not a regular file or symlink",
            )
        entries.append(UntrackedEntry(relative=relative, mode=mode, kind=kind))
    return entries


def _fingerprint(source: Path) -> str:
    digest = hashlib.sha256()

    def add(label: str, value: bytes) -> None:
        encoded = label.encode("ascii")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    add("head", _git_bytes(source, "rev-parse", "HEAD"))
    add(
        "status",
        _git_bytes(
            source,
            "status",
            "--porcelain=v2",
            "-z",
            "--untracked-files=all",
        ),
    )
    add("index", _git_bytes(source, "ls-files", "--stage", "-z"))
    add(
        "cached",
        _git_bytes(
            source,
            "diff",
            "--cached",
            "--binary",
            "--full-index",
            "HEAD",
            "--",
        ),
    )
    add(
        "worktree",
        _git_bytes(source, "diff", "--binary", "--full-index", "--"),
    )
    for entry in _untracked_entries(source):
        path = source / entry.relative
        add("untracked-path", os.fsencode(entry.relative))
        add("untracked-mode", oct(entry.mode).encode("ascii"))
        add("untracked-kind", entry.kind.encode("ascii"))
        if entry.kind == "symlink":
            add("untracked-content", os.fsencode(os.readlink(path)))
        else:
            add("untracked-content", path.read_bytes())
    return digest.hexdigest()


def _common_git_dir(worktree: Path) -> Path:
    text = _git_text(
        worktree, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    return Path(text).resolve()


def _require_supported_source_state(source: Path) -> None:
    unresolved = _git_text(source, "diff", "--name-only", "--diff-filter=U")
    if unresolved:
        raise ReplayFailure("SOURCE_HAS_CONFLICTS", "source index has unresolved paths")
    for marker in (
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "REBASE_HEAD",
        "rebase-apply",
        "rebase-merge",
    ):
        marker_path = Path(_git_text(source, "rev-parse", "--git-path", marker))
        if marker_path.exists():
            raise ReplayFailure(
                "SOURCE_OPERATION_IN_PROGRESS",
                f"source has an in-progress Git operation ({marker})",
            )

    for entry in filter(None, _git_bytes(source, "ls-files", "--stage", "-z").split(b"\0")):
        metadata, separator, raw_path = entry.partition(b"\t")
        if not separator:
            raise ReplayFailure(
                "SOURCE_INDEX_UNREADABLE",
                "source index contains an unparseable staged entry",
            )
        mode = metadata.split(b" ", 1)[0]
        if mode == b"160000":
            path = os.fsdecode(raw_path)
            raise ReplayFailure(
                "SOURCE_HAS_SUBMODULE",
                f"source index contains unsupported gitlink {path!r}",
            )

    ita_visible = _git_bytes(
        source,
        "diff",
        "--cached",
        "--raw",
        "--ita-visible-in-index",
        "HEAD",
        "--",
    )
    ita_invisible = _git_bytes(
        source,
        "diff",
        "--cached",
        "--raw",
        "--ita-invisible-in-index",
        "HEAD",
        "--",
    )
    if ita_visible != ita_invisible:
        raise ReplayFailure(
            "SOURCE_HAS_INTENT_TO_ADD",
            "source index contains unsupported intent-to-add entries",
        )


def _stable_fingerprint(source: Path) -> str:
    first = _fingerprint(source)
    second = _fingerprint(source)
    if first != second:
        raise ReplayFailure(
            "SOURCE_NOT_QUIESCENT",
            "source changed between two complete snapshots",
        )
    return second


def _candidate_commits(source: Path, merge_base: str, source_head: str) -> list[str]:
    raw = _git_text(
        source,
        "rev-list",
        "--reverse",
        "--topo-order",
        f"{merge_base}..{source_head}",
    )
    commits = raw.splitlines() if raw else []
    for commit in commits:
        parents = _git_text(source, "rev-list", "--parents", "-n", "1", commit).split()
        if len(parents) > 2:
            raise ReplayFailure(
                "CANDIDATE_CONTAINS_MERGE",
                f"candidate commit {commit} is a merge and cannot be replayed unambiguously",
            )
    return commits


def _cherry_pick(output: Path, commits: list[str]) -> None:
    for commit in commits:
        result = _git_result(output, "cherry-pick", commit, committer=True)
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise ReplayFailure(
                "CHERRY_PICK_CONFLICT",
                detail or f"candidate commit {commit} did not replay cleanly",
                3,
            )


def _apply_patch(output: Path, patch: bytes, *, staged: bool) -> None:
    if not patch:
        return
    args = ["apply"]
    if staged:
        args.append("--index")
    args.extend(("--binary", "--whitespace=nowarn", "-"))
    result = _git_result(output, *args, input_bytes=patch)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        state = "staged" if staged else "unstaged"
        raise ReplayFailure(
            "DIRTY_REPLAY_CONFLICT",
            detail or f"{state} source changes did not replay cleanly",
            3,
        )


def _require_safe_destination(output: Path, relative: str) -> Path:
    destination = output / relative
    current = output
    for part in PurePosixPath(relative).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ReplayFailure(
                "UNTRACKED_DESTINATION_CONFLICT",
                f"output parent for {relative!r} is a symlink",
                3,
            )
        if current.exists() and not current.is_dir():
            raise ReplayFailure(
                "UNTRACKED_DESTINATION_CONFLICT",
                f"output parent for {relative!r} is not a directory",
                3,
            )
    if destination.exists() or destination.is_symlink():
        raise ReplayFailure(
            "UNTRACKED_DESTINATION_CONFLICT",
            f"untracked path {relative!r} collides with current main",
            3,
        )
    return destination


def _copy_untracked(
    source: Path, output: Path, entries: list[UntrackedEntry]
) -> None:
    for entry in entries:
        source_path = source / entry.relative
        destination = _require_safe_destination(output, entry.relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if entry.kind == "symlink":
            os.symlink(os.readlink(source_path), destination)
        else:
            shutil.copyfile(source_path, destination, follow_symlinks=False)
            os.chmod(destination, entry.mode, follow_symlinks=False)


def _identity_at(path: Path) -> FsIdentity:
    return FsIdentity.from_stat(path.lstat())


def _identity_at_dirfd(parent_fd: int, name: str) -> FsIdentity:
    metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    return FsIdentity.from_stat(metadata)


def _write_descriptor_json(fd: int, payload: dict[str, object]) -> None:
    encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    view = memoryview(encoded)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write while persisting replay manifest")
        view = view[written:]


def _read_descriptor_json(fd: int) -> dict[str, object]:
    metadata = os.fstat(fd)
    raw = os.pread(fd, metadata.st_size, 0)
    payload = json.loads(raw.decode("utf-8", "strict"))
    if not isinstance(payload, dict):
        raise ValueError("replay manifest is not a JSON object")
    return payload


def _process_start_ticks() -> int:
    raw = Path("/proc/self/stat").read_text(encoding="ascii")
    close = raw.rfind(")")
    fields = raw[close + 2 :].split()
    if close < 0 or len(fields) < 20:
        raise OSError("cannot parse /proc/self/stat")
    return int(fields[19])


def _owner_payload() -> dict[str, object]:
    return {
        "boot_id": Path("/proc/sys/kernel/random/boot_id")
        .read_text(encoding="ascii")
        .strip(),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "start_ticks": _process_start_ticks(),
    }


def _claim_payload(claim: ReplayClaim, *, state: str) -> dict[str, object]:
    return {
        "schema": 1,
        "state": state,
        "token": claim.token,
        "manifest": {
            "path": str(claim.path),
            "identity": claim.path_identity.as_dict(),
            "parent_identity": claim.parent_identity.as_dict(),
        },
        "owner": claim.owner,
    }


def _claim_still_owned(claim: ReplayClaim) -> tuple[bool, str]:
    try:
        if FsIdentity.from_stat(os.fstat(claim.parent_fd)) != claim.parent_identity:
            return False, "held parent-directory identity changed"
        if _identity_at(claim.parent) != claim.parent_identity:
            return False, "manifest parent path no longer names the held directory"
        if FsIdentity.from_stat(os.fstat(claim.fd)) != claim.path_identity:
            return False, "held manifest descriptor identity changed"
        if _identity_at_dirfd(claim.parent_fd, claim.path.name) != claim.path_identity:
            return False, "manifest final path no longer names the held inode"
        if _identity_at(claim.path) != claim.path_identity:
            return False, "manifest path no-follow identity changed"
        payload = _read_descriptor_json(claim.fd)
        if payload.get("token") != claim.token:
            return False, "manifest claim token changed"
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        return False, f"manifest ownership could not be revalidated: {error}"
    return True, ""


def _acquire_claim(output: Path) -> ReplayClaim:
    token = secrets.token_hex(32)
    owner = _owner_payload()
    parent = output.parent
    parent_fd = os.open(
        parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
    )
    fd = -1
    claim: ReplayClaim | None = None
    try:
        parent_identity = FsIdentity.from_stat(os.fstat(parent_fd))
        if _identity_at(parent) != parent_identity:
            raise ReplayFailure(
                "OUTPUT_PARENT_CHANGED",
                "output parent changed while the replay claim was acquired",
            )
        path = parent / f".{output.name}.bd-replay.json"
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            fd = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError as error:
            raise ReplayFailure(
                "OUTPUT_CLAIMED",
                f"replay transaction record already exists: {path}",
            ) from error
        path_identity = FsIdentity.from_stat(os.fstat(fd))
        if not stat.S_ISREG(path_identity.mode):
            raise ReplayFailure(
                "OUTPUT_CLAIM_UNSAFE",
                "replay transaction record is not a regular file",
            )
        claim = ReplayClaim(
            path=path,
            parent=parent,
            token=token,
            fd=fd,
            parent_fd=parent_fd,
            path_identity=path_identity,
            parent_identity=parent_identity,
            owner=owner,
        )
        _write_descriptor_json(claim.fd, _claim_payload(claim, state="CLAIMED"))
        os.fsync(claim.fd)
        owned, reason = _claim_still_owned(claim)
        if not owned:
            raise ReplayFailure("OUTPUT_CLAIM_CHANGED", reason)
        if _identity_at(parent) != parent_identity:
            raise ReplayFailure(
                "OUTPUT_PARENT_CHANGED",
                "output parent changed before claim directory fsync",
            )
        os.fsync(parent_fd)
        return claim
    except BaseException as primary:
        notes: list[str] = []
        if claim is not None:
            owned, reason = _claim_still_owned(claim)
            if owned:
                try:
                    os.unlink(claim.path.name, dir_fd=claim.parent_fd)
                    os.fsync(claim.parent_fd)
                except OSError as cleanup_error:
                    notes.append(
                        "failed to remove acquired replay claim: "
                        f"{cleanup_error!r}"
                    )
            else:
                notes.append(f"retained replay claim: {reason}")
        elif fd >= 0:
            notes.append(
                "retained replay claim because token ownership was not established"
            )
        if fd >= 0:
            try:
                os.close(fd)
            except OSError as close_error:
                notes.append(f"replay claim close failed: {close_error!r}")
        try:
            os.close(parent_fd)
        except OSError as close_error:
            notes.append(f"replay parent close failed: {close_error!r}")
        add_note = getattr(primary, "add_note", None)
        if add_note is not None:
            for note in notes:
                add_note(note)
        raise


def _capture_output_ownership(output: Path) -> OutputOwnership:
    output_identity = _identity_at(output)
    if not stat.S_ISDIR(output_identity.mode):
        raise ReplayFailure(
            "OUTPUT_IDENTITY_UNSAFE",
            "created replay output is not a directory",
        )
    git_dir = Path(
        _git_text(output, "rev-parse", "--path-format=absolute", "--absolute-git-dir")
    )
    git_dir = git_dir.resolve(strict=True)
    git_dir_identity = _identity_at(git_dir)
    if not stat.S_ISDIR(git_dir_identity.mode):
        raise ReplayFailure(
            "OUTPUT_GIT_DIR_UNSAFE",
            "created replay Git directory is not a directory",
        )
    return OutputOwnership(
        output_identity=output_identity,
        git_dir=git_dir,
        git_dir_identity=git_dir_identity,
    )


def _registered_output_ownership(
    common_git_dir: Path,
    output: Path,
) -> OutputOwnership | None:
    registrations = common_git_dir / "worktrees"
    try:
        entries = list(registrations.iterdir())
    except FileNotFoundError:
        return None
    matches: list[OutputOwnership] = []
    for entry in entries:
        entry_identity = _identity_at(entry)
        if not stat.S_ISDIR(entry_identity.mode):
            raise ReplayFailure(
                "OUTPUT_REGISTRATION_UNSAFE",
                f"worktree registration is not a directory: {entry}",
            )
        gitdir_file = entry / "gitdir"
        try:
            gitdir_identity = _identity_at(gitdir_file)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(gitdir_identity.mode):
            raise ReplayFailure(
                "OUTPUT_REGISTRATION_UNSAFE",
                f"worktree gitdir receipt is not regular: {gitdir_file}",
            )
        raw_target = gitdir_file.read_text(encoding="utf-8").strip()
        if not raw_target:
            raise ReplayFailure(
                "OUTPUT_REGISTRATION_UNSAFE",
                f"worktree gitdir receipt is empty: {gitdir_file}",
            )
        target = Path(raw_target)
        if not target.is_absolute():
            target = entry / target
        registered_output = target.parent.resolve(strict=False)
        if registered_output == output:
            matches.append(
                OutputOwnership(
                    output_identity=None,
                    git_dir=entry,
                    git_dir_identity=entry_identity,
                )
            )
    if len(matches) > 1:
        raise ReplayFailure(
            "OUTPUT_REGISTRATION_AMBIGUOUS",
            f"multiple Git registrations name replay output {output}",
        )
    return matches[0] if matches else None


def _output_still_owned(
    claim: ReplayClaim,
    output: Path,
    ownership: OutputOwnership,
) -> tuple[bool, str]:
    claimed, reason = _claim_still_owned(claim)
    if not claimed:
        return False, reason
    try:
        if ownership.output_identity is None:
            if output.exists() or output.is_symlink():
                return False, "unregistered output path appeared before rollback"
        elif _identity_at(output) != ownership.output_identity:
            return False, "output final-path no-follow identity changed"
        if _identity_at(ownership.git_dir) != ownership.git_dir_identity:
            return False, "registered output Git-dir identity changed"
    except OSError as error:
        return False, f"output ownership could not be revalidated: {error}"
    return True, ""


def _unlink_owned_claim(claim: ReplayClaim) -> str | None:
    owned, reason = _claim_still_owned(claim)
    if not owned:
        return f"retained replay claim: {reason}"
    try:
        os.unlink(claim.path.name, dir_fd=claim.parent_fd)
        os.fsync(claim.parent_fd)
    except OSError as error:
        return f"failed to remove owned replay claim: {error}"
    return None


def _rollback_owned(
    claim: ReplayClaim,
    repo: Path,
    output: Path,
    ownership: OutputOwnership | None,
    *,
    release_claim_with_unowned_output: bool = False,
    release_absent_claim: bool = True,
) -> list[str]:
    notes: list[str] = []
    output_absent = not output.exists() and not output.is_symlink()
    if ownership is not None:
        owned, reason = _output_still_owned(claim, output, ownership)
        if not owned:
            notes.append(f"retained replay output: {reason}")
            return notes
        result = _git_result(repo, "worktree", "remove", "--force", str(output))
        registration_exists = (
            ownership.git_dir.exists() or ownership.git_dir.is_symlink()
        )
        if (
            output.exists()
            or output.is_symlink()
            or registration_exists
        ):
            detail = result.stderr.decode("utf-8", "replace").strip()
            notes.append(
                "failed to remove identity-owned replay output: "
                + (detail or str(output))
            )
            return notes
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            notes.append(
                "Git reported a cleanup error after exact output and registration "
                f"absence was proven: {detail or result.returncode}"
            )
        output_absent = True
        release_absent_claim = True
    if (
        (output_absent and release_absent_claim)
        or release_claim_with_unowned_output
    ):
        note = _unlink_owned_claim(claim)
        if note:
            notes.append(note)
    else:
        notes.append("retained unowned replay output and transaction claim")
    return notes


def _finalize_claim(
    claim: ReplayClaim,
    manifest: dict[str, object],
) -> None:
    owned, reason = _claim_still_owned(claim)
    if not owned:
        raise ReplayFailure("OUTPUT_CLAIM_CHANGED", reason)
    _write_descriptor_json(claim.fd, manifest)
    os.fsync(claim.fd)
    owned, reason = _claim_still_owned(claim)
    if not owned:
        raise ReplayFailure("OUTPUT_CLAIM_CHANGED", reason)
    if _identity_at(claim.parent) != claim.parent_identity:
        raise ReplayFailure(
            "OUTPUT_PARENT_CHANGED",
            "output parent changed before replay manifest directory fsync",
        )
    os.fsync(claim.parent_fd)


def _refuse_if_nested_output(source: Path, output: Path) -> None:
    if output == source or output.is_relative_to(source):
        raise ReplayFailure(
            "OUTPUT_INSIDE_SOURCE",
            "output must not be the source worktree or one of its descendants",
        )


def replay(
    *,
    repo: Path,
    source: Path,
    expect_head: str,
    main_ref: str,
    output: Path,
) -> dict[str, object]:
    repo = repo.resolve(strict=True)
    source = source.resolve(strict=True)
    if not output.is_absolute():
        output = Path.cwd() / output
    output_parent = output.parent.resolve(strict=True)
    output = output_parent / output.name
    _refuse_if_nested_output(source, output)
    claim = _acquire_claim(output)
    ownership: OutputOwnership | None = None
    source_before: str | None = None
    release_claim_with_unowned_output = False
    worktree_add_attempted = False
    registration_absence_proven = False
    try:
        if output.exists() or output.is_symlink():
            release_claim_with_unowned_output = True
            raise ReplayFailure(
                "OUTPUT_EXISTS",
                f"output path already exists without this transaction: {output}",
            )
        repo_common_git = _common_git_dir(repo)
        source_common_git = _common_git_dir(source)
        if repo_common_git != source_common_git:
            raise ReplayFailure(
                "REPOSITORY_MISMATCH",
                "repo and source do not share a Git common directory",
            )
        if _registered_output_ownership(repo_common_git, output) is not None:
            raise ReplayFailure(
                "OUTPUT_REGISTRATION_EXISTS",
                "a pre-existing Git worktree registration names the output",
            )
        _require_supported_source_state(source)

        source_head = _resolve_commit(source, "HEAD", "SOURCE_HEAD_UNREADABLE")
        expected = _resolve_commit(source, expect_head, "EXPECTED_HEAD_UNREADABLE")
        if source_head != expected:
            raise ReplayFailure(
                "SOURCE_HEAD_MISMATCH",
                f"source HEAD is {source_head}, expected {expected}",
            )
        main_sha = _resolve_commit(repo, main_ref, "MAIN_REF_UNREADABLE")
        merge_base = _git_text(repo, "merge-base", source_head, main_sha)
        if not merge_base:
            raise ReplayFailure("NO_MERGE_BASE", "source and main have no merge base")

        source_before = _stable_fingerprint(source)
        staged_patch = _git_bytes(
            source,
            "diff",
            "--cached",
            "--binary",
            "--full-index",
            "HEAD",
            "--",
        )
        unstaged_patch = _git_bytes(
            source, "diff", "--binary", "--full-index", "--"
        )
        untracked = _untracked_entries(source)
        commits = _candidate_commits(source, merge_base, source_head)
        if not commits and not staged_patch and not unstaged_patch and not untracked:
            raise ReplayFailure(
                "NO_CANDIDATE_CHANGES",
                "source contains no candidate work",
            )
        if _fingerprint(source) != source_before:
            raise ReplayFailure(
                "SOURCE_NOT_QUIESCENT",
                "source changed while replay inputs were captured",
            )

        repo_identity = _identity_at(repo)
        source_identity = _identity_at(source)
        common_git_identity = _identity_at(repo_common_git)
        worktree_add_attempted = True
        add_result = _git_result(
            repo, "worktree", "add", "--detach", str(output), main_sha
        )
        if output.exists() or output.is_symlink():
            ownership = _capture_output_ownership(output)
        else:
            ownership = _registered_output_ownership(repo_common_git, output)
            registration_absence_proven = ownership is None
        if add_result.returncode != 0:
            detail = add_result.stderr.decode("utf-8", "replace").strip()
            raise ReplayFailure(
                "OUTPUT_CREATE_FAILED",
                detail or f"could not create output worktree {output}",
            )
        if ownership is None:
            raise ReplayFailure(
                "OUTPUT_CREATE_FAILED",
                f"Git reported success without creating output {output}",
            )
        _cherry_pick(output, commits)
        _apply_patch(output, staged_patch, staged=True)
        _apply_patch(output, unstaged_patch, staged=False)
        _copy_untracked(source, output, untracked)
        source_after = _fingerprint(source)
        if source_after != source_before:
            raise ReplayFailure(
                "SOURCE_CHANGED_DURING_REPLAY",
                "source fingerprint changed while replay was in progress",
            )
        replayed_head = _resolve_commit(output, "HEAD", "OUTPUT_HEAD_UNREADABLE")
        output_state = _fingerprint(output)
        manifest = _claim_payload(claim, state="REPLAYED")
        manifest.update(
            {
                "repo": {
                    "path": str(repo),
                    "identity": repo_identity.as_dict(),
                },
                "common_git_dir": {
                    "path": str(repo_common_git),
                    "identity": common_git_identity.as_dict(),
                },
                "source": {
                    "path": str(source),
                    "identity": source_identity.as_dict(),
                    "head": source_head,
                    "state_sha256": source_before,
                },
                "output": {
                    "path": str(output),
                    "identity": ownership.output_identity.as_dict(),
                    "git_dir": {
                        "path": str(ownership.git_dir),
                        "identity": ownership.git_dir_identity.as_dict(),
                    },
                    "head": replayed_head,
                    "state_sha256": output_state,
                },
                "merge_base": merge_base,
                "main_ref": main_ref,
                "main_sha": main_sha,
                "candidate_commits": commits,
            }
        )
        _finalize_claim(claim, manifest)
        return {
            "status": "REPLAYED",
            "source_head": source_head,
            "merge_base": merge_base,
            "main_ref": main_ref,
            "main_sha": main_sha,
            "replayed_head": replayed_head,
            "source_state_sha256": source_before,
            "output_state_sha256": output_state,
            "output": str(output),
            "manifest": str(claim.path),
            "candidate_commits": commits,
            "filesystem_identities": {
                "manifest": claim.path_identity.as_dict(),
                "manifest_parent": claim.parent_identity.as_dict(),
                "repo": repo_identity.as_dict(),
                "source": source_identity.as_dict(),
                "common_git_dir": common_git_identity.as_dict(),
                "output": ownership.output_identity.as_dict(),
                "output_git_dir": ownership.git_dir_identity.as_dict(),
            },
        }
    except BaseException as primary:
        try:
            notes = _rollback_owned(
                claim,
                repo,
                output,
                ownership,
                release_claim_with_unowned_output=(
                    release_claim_with_unowned_output
                ),
                release_absent_claim=(
                    not worktree_add_attempted
                    or registration_absence_proven
                ),
            )
        except BaseException as cleanup_error:
            notes = [f"replay rollback raised secondary error: {cleanup_error!r}"]
        if source_before is not None:
            try:
                if _fingerprint(source) != source_before:
                    notes.append("source changed while failed replay was rolled back")
            except BaseException as evidence_error:
                notes.append(
                    "source could not be revalidated after failed replay: "
                    f"{evidence_error!r}"
                )
        add_note = getattr(primary, "add_note", None)
        if add_note is not None:
            for note in notes:
                add_note(note)
        raise
    finally:
        claim.close()


def _emit(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    if payload.get("status") == "REPLAYED":
        print(
            "REPLAYED "
            f"source={payload['source_head']} main={payload['main_sha']} "
            f"output={payload['output']}"
        )
    else:
        print(
            f"{payload['status']} {payload.get('reason_code', 'UNKNOWN')}: "
            f"{payload.get('message', '')}"
        )


def _fail(error: ReplayFailure, *, as_json: bool) -> NoReturn:
    status = "CONFLICT" if error.exit_code == 3 else "REFUSED"
    _emit(
        {
            "status": status,
            "reason_code": error.reason_code,
            "message": error.message,
        },
        as_json=as_json,
    )
    raise SystemExit(error.exit_code)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--expect-head", required=True)
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        payload = replay(
            repo=args.repo,
            source=args.source,
            expect_head=args.expect_head,
            main_ref=args.main_ref,
            output=args.output,
        )
    except (OSError, UnicodeError) as error:
        _fail(ReplayFailure("LOCAL_IO_FAILED", str(error)), as_json=args.json)
    except ReplayFailure as error:
        _fail(error, as_json=args.json)
    _emit(payload, as_json=args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
