#!/usr/bin/env python3
"""Prove a candidate is integrated into main from read-only Git evidence."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import NoReturn


VERSION_PATH = "bulk_downloader/__init__.py"
REGISTER_PATH = "project-knowledge/IMPROVEMENT_BACKLOG.md"
_VERSION = re.compile(r'^__version__\s*=\s*["\'](\d+\.\d+\.\d+)["\']\s*$', re.MULTILINE)
_EXPECTED_VERSION = re.compile(r"\d+\.\d+\.\d+")


@dataclass(frozen=True)
class UnknownEvidence(Exception):
    reason_code: str
    message: str


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
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
        LC_ALL="C",
    )
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
        env=run_env,
    )


def _resolve_commit(repo: Path, revision: str, reason_code: str) -> str:
    result = _git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")
    if result.returncode != 0:
        raise UnknownEvidence(reason_code, f"cannot resolve commit {revision!r}")
    return result.stdout.decode("ascii", "strict").strip()


def _show(repo: Path, commit: str, path: str, reason_code: str) -> str:
    result = _git(repo, "show", f"{commit}:{path}")
    if result.returncode != 0:
        raise UnknownEvidence(reason_code, f"cannot read {path!r} at {commit}")
    try:
        return result.stdout.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise UnknownEvidence(reason_code, f"{path!r} is not UTF-8 text") from error


def _parse_version(text: str, reason_code: str) -> tuple[str, tuple[int, int, int]]:
    matches = _VERSION.findall(text)
    if len(matches) != 1:
        raise UnknownEvidence(
            reason_code,
            f"version declaration count is {len(matches)}, expected exactly one",
        )
    version = matches[0]
    return version, tuple(int(part) for part in version.split("."))


def _parse_expected_version(version: str) -> tuple[int, int, int]:
    if _EXPECTED_VERSION.fullmatch(version) is None:
        raise UnknownEvidence(
            "EXPECTED_VERSION_INVALID",
            f"expected version must be numeric X.Y.Z, got {version!r}",
        )
    return tuple(int(part) for part in version.split("."))


def _is_ancestor(repo: Path, candidate: str, main: str) -> bool:
    result = _git(repo, "merge-base", "--is-ancestor", candidate, main)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = result.stderr.decode("utf-8", "replace").strip()
    raise UnknownEvidence(
        "ANCESTRY_UNREADABLE",
        detail or "git could not determine candidate ancestry",
    )


def _safe_tree_path(path: str) -> str:
    pure = PurePosixPath(path)
    if (
        not path
        or "\x00" in path
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise UnknownEvidence("REQUIRED_PATH_INVALID", f"unsafe required path {path!r}")
    return path


def _path_exists(repo: Path, commit: str, path: str) -> bool:
    safe = _safe_tree_path(path)
    result = _git(
        repo,
        "--literal-pathspecs",
        "ls-tree",
        "-z",
        commit,
        "--",
        safe,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise UnknownEvidence(
            "REQUIRED_PATH_UNREADABLE",
            detail or f"cannot measure required path {safe!r} at {commit}",
        )
    return bool(result.stdout)


def _row_closed_exactly_once(register: str, row: int) -> bool:
    if row <= 0:
        raise UnknownEvidence("ROW_INVALID", f"row must be positive, got {row}")
    matches = re.findall(
        rf"^\|\s*{row}\s*\|\s*([^|]+?)\s*\|",
        register,
        flags=re.MULTILINE,
    )
    if len(matches) != 1:
        return False
    return re.fullmatch(r"CLOSED(?:\s+@\d+)?", matches[0].strip()) is not None


def evaluate(
    *,
    repo: Path,
    candidate: str,
    main_ref: str,
    expected_version: str,
    row: int | None,
    required_paths: list[str],
) -> dict[str, object]:
    repo = repo.resolve(strict=True)
    expected_tuple = _parse_expected_version(expected_version)
    candidate_sha = _resolve_commit(repo, candidate, "CANDIDATE_UNREADABLE")
    main_sha = _resolve_commit(repo, main_ref, "MAIN_REF_UNREADABLE")

    candidate_text = _show(
        repo, candidate_sha, VERSION_PATH, "CANDIDATE_VERSION_UNREADABLE"
    )
    main_text = _show(repo, main_sha, VERSION_PATH, "MAIN_VERSION_UNREADABLE")
    candidate_version, candidate_tuple = _parse_version(
        candidate_text, "CANDIDATE_VERSION_INVALID"
    )
    main_version, main_tuple = _parse_version(main_text, "MAIN_VERSION_INVALID")

    paths = {
        path: _path_exists(repo, main_sha, path)
        for path in required_paths
    }
    if row is None:
        row_closed = True
    else:
        register = _show(repo, main_sha, REGISTER_PATH, "REGISTER_UNREADABLE")
        row_closed = _row_closed_exactly_once(register, row)

    evidence = {
        "candidate_is_ancestor": _is_ancestor(repo, candidate_sha, main_sha),
        "candidate_version_matches": (
            candidate_version == expected_version and candidate_tuple == expected_tuple
        ),
        "main_version_at_least_expected": main_tuple >= expected_tuple,
        "required_paths_present": all(paths.values()),
        "row_closed_exactly_once": row_closed,
    }
    verdict = "INTEGRATED" if all(evidence.values()) else "NOT_INTEGRATED"
    return {
        "verdict": verdict,
        "candidate_sha": candidate_sha,
        "main_sha": main_sha,
        "expected_version": expected_version,
        "candidate_version": candidate_version,
        "main_version": main_version,
        "row": row,
        "required_paths": paths,
        "evidence": evidence,
    }


def _emit(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    verdict = payload["verdict"]
    if verdict == "INTEGRATED":
        print(
            f"INTEGRATED candidate={payload['candidate_sha']} "
            f"main={payload['main_sha']} version={payload['expected_version']}"
        )
    elif verdict == "NOT_INTEGRATED":
        failed = [
            name for name, value in payload["evidence"].items() if not value
        ]
        print(f"NOT_INTEGRATED failed={','.join(failed)}")
    else:
        print(
            f"UNKNOWN {payload.get('reason_code', 'UNKNOWN')}: "
            f"{payload.get('message', '')}"
        )


def _unknown(error: UnknownEvidence, *, as_json: bool) -> NoReturn:
    _emit(
        {
            "verdict": "UNKNOWN",
            "reason_code": error.reason_code,
            "message": error.message,
        },
        as_json=as_json,
    )
    raise SystemExit(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--row", type=int)
    parser.add_argument("--require-path", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        payload = evaluate(
            repo=args.repo,
            candidate=args.candidate,
            main_ref=args.main_ref,
            expected_version=args.expected_version,
            row=args.row,
            required_paths=args.require_path,
        )
    except (OSError, UnicodeError) as error:
        _unknown(UnknownEvidence("LOCAL_IO_FAILED", str(error)), as_json=args.json)
    except UnknownEvidence as error:
        _unknown(error, as_json=args.json)
    _emit(payload, as_json=args.json)
    return 0 if payload["verdict"] == "INTEGRATED" else 1


if __name__ == "__main__":
    sys.exit(main())
