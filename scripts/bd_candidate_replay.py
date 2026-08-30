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
import shutil
import stat
import subprocess
import sys
from typing import NoReturn


@dataclass(frozen=True)
class ReplayFailure(Exception):
    reason_code: str
    message: str
    exit_code: int = 2


@dataclass(frozen=True)
class UntrackedEntry:
    relative: str
    mode: int
    kind: str


def _git_result(
    cwd: Path,
    *args: str,
    input_bytes: bytes | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        input=input_bytes,
        capture_output=True,
        check=False,
        env=env,
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


def _require_quiescent_source(source: Path) -> None:
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
    env = dict(os.environ)
    env.setdefault("GIT_COMMITTER_NAME", "BulkDownloader Candidate Replay")
    env.setdefault("GIT_COMMITTER_EMAIL", "candidate-replay@example.invalid")
    for commit in commits:
        result = _git_result(output, "cherry-pick", commit, env=env)
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


def _remove_output(repo: Path, output: Path) -> None:
    result = _git_result(repo, "worktree", "remove", "--force", str(output))
    if result.returncode != 0 or output.exists() or output.is_symlink():
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise ReplayFailure(
            "OUTPUT_CLEANUP_FAILED",
            detail or f"failed to remove replay output {output}",
        )


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
    output = output.resolve(strict=False)
    if output.exists() or output.is_symlink():
        raise ReplayFailure("OUTPUT_EXISTS", f"output path already exists: {output}")
    _refuse_if_nested_output(source, output)
    if _common_git_dir(repo) != _common_git_dir(source):
        raise ReplayFailure(
            "REPOSITORY_MISMATCH", "repo and source do not share a Git common directory"
        )
    _require_quiescent_source(source)

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

    source_before = _fingerprint(source)
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
        raise ReplayFailure("NO_CANDIDATE_CHANGES", "source contains no candidate work")

    created = False
    try:
        add_result = _git_result(
            repo, "worktree", "add", "--detach", str(output), main_sha
        )
        if add_result.returncode != 0:
            detail = add_result.stderr.decode("utf-8", "replace").strip()
            raise ReplayFailure(
                "OUTPUT_CREATE_FAILED",
                detail or f"could not create output worktree {output}",
            )
        created = True
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
        return {
            "status": "REPLAYED",
            "source_head": source_head,
            "merge_base": merge_base,
            "main_sha": main_sha,
            "replayed_head": replayed_head,
            "source_state_sha256": source_before,
            "output_state_sha256": _fingerprint(output),
            "output": str(output),
            "candidate_commits": commits,
        }
    except ReplayFailure:
        if created:
            _remove_output(repo, output)
        if _fingerprint(source) != source_before:
            raise ReplayFailure(
                "SOURCE_CHANGED_DURING_REPLAY",
                "source fingerprint changed while a failed replay was rolled back",
            )
        raise


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

