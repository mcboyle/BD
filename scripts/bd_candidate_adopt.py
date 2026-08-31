#!/usr/bin/env python3
"""Validate a completed candidate replay from immutable read-only evidence."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import sys
from typing import NoReturn

from bd_candidate_replay import (
    FsIdentity,
    ReplayFailure,
    _candidate_commits,
    _common_git_dir,
    _fingerprint,
    _git_text,
    _identity_at,
    _resolve_commit,
)


SCHEMA = 1
MAX_MANIFEST_BYTES = 1024 * 1024
TOP_LEVEL_KEYS = {
    "schema",
    "state",
    "token",
    "manifest",
    "owner",
    "repo",
    "common_git_dir",
    "source",
    "output",
    "merge_base",
    "main_ref",
    "main_sha",
    "candidate_commits",
}


@dataclass(frozen=True)
class AdoptionUnknown(Exception):
    reason_code: str
    message: str


def _unknown(reason_code: str, message: str) -> NoReturn:
    raise AdoptionUnknown(reason_code, message)


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        _unknown("MANIFEST_MALFORMED", f"{label} must be a JSON object")
    return value


def _exact_keys(
    value: dict[str, object],
    expected: set[str],
    label: str,
) -> None:
    actual = set(value)
    if actual != expected:
        _unknown(
            "MANIFEST_MALFORMED",
            f"{label} keys differ: missing={sorted(expected - actual)!r} "
            f"extra={sorted(actual - expected)!r}",
        )


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        _unknown("MANIFEST_MALFORMED", f"{label} must be a non-empty string")
    return value


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _unknown("MANIFEST_MALFORMED", f"{label} must be a nonnegative integer")
    return value


def _recorded_identity(value: object, label: str) -> FsIdentity:
    body = _object(value, label)
    _exact_keys(body, {"device", "inode", "mode"}, label)
    return FsIdentity(
        device=_integer(body["device"], f"{label}.device"),
        inode=_integer(body["inode"], f"{label}.inode"),
        mode=_integer(body["mode"], f"{label}.mode"),
    )


def _path_record(value: object, label: str) -> tuple[Path, FsIdentity]:
    body = _object(value, label)
    _exact_keys(body, {"path", "identity"}, label)
    path = _absolute_path(body["path"], f"{label}.path")
    return path, _recorded_identity(body["identity"], f"{label}.identity")


def _absolute_path(value: object, label: str) -> Path:
    path = Path(_string(value, label))
    if not path.is_absolute():
        _unknown("MANIFEST_MALFORMED", f"{label} must be absolute")
    return path


def _validate_manifest(payload: dict[str, object]) -> None:
    schema = payload.get("schema")
    if schema != SCHEMA:
        _unknown(
            "MANIFEST_SCHEMA_UNSUPPORTED",
            f"manifest schema {schema!r} is unsupported",
        )
    if payload.get("state") != "REPLAYED":
        _unknown(
            "MANIFEST_INCOMPLETE",
            f"manifest state {payload.get('state')!r} is not REPLAYED",
        )
    _exact_keys(payload, TOP_LEVEL_KEYS, "manifest")
    token = _string(payload["token"], "token")
    if len(token) != 64:
        _unknown("MANIFEST_MALFORMED", "token is not a 256-bit hex claim")
    try:
        int(token, 16)
    except ValueError:
        _unknown("MANIFEST_MALFORMED", "token is not hexadecimal")

    manifest = _object(payload["manifest"], "manifest identity")
    _exact_keys(
        manifest,
        {"path", "identity", "parent_identity"},
        "manifest identity",
    )
    _absolute_path(manifest["path"], "manifest.path")
    _recorded_identity(manifest["identity"], "manifest.identity")
    _recorded_identity(
        manifest["parent_identity"],
        "manifest.parent_identity",
    )

    owner = _object(payload["owner"], "owner")
    _exact_keys(owner, {"boot_id", "pid", "ppid", "start_ticks"}, "owner")
    _string(owner["boot_id"], "owner.boot_id")
    _integer(owner["pid"], "owner.pid")
    _integer(owner["ppid"], "owner.ppid")
    _integer(owner["start_ticks"], "owner.start_ticks")

    _path_record(payload["repo"], "repo")
    _path_record(payload["common_git_dir"], "common_git_dir")

    source = _object(payload["source"], "source")
    _exact_keys(source, {"path", "identity", "head", "state_sha256"}, "source")
    _absolute_path(source["path"], "source.path")
    _recorded_identity(source["identity"], "source.identity")
    _string(source["head"], "source.head")
    _string(source["state_sha256"], "source.state_sha256")

    output = _object(payload["output"], "output")
    _exact_keys(
        output,
        {"path", "identity", "git_dir", "head", "state_sha256"},
        "output",
    )
    _absolute_path(output["path"], "output.path")
    _recorded_identity(output["identity"], "output.identity")
    _path_record(output["git_dir"], "output.git_dir")
    _string(output["head"], "output.head")
    _string(output["state_sha256"], "output.state_sha256")

    _string(payload["merge_base"], "merge_base")
    _string(payload["main_ref"], "main_ref")
    _string(payload["main_sha"], "main_sha")
    commits = payload["candidate_commits"]
    if not isinstance(commits, list) or not all(
        isinstance(commit, str) and commit for commit in commits
    ):
        _unknown(
            "MANIFEST_MALFORMED",
            "candidate_commits must be a list of non-empty strings",
        )


def _canonical_final_path(path: Path) -> Path:
    if not path.is_absolute():
        path = Path.cwd() / path
    parent = path.parent.resolve(strict=True)
    return parent / path.name


def _read_manifest(
    path: Path,
) -> tuple[dict[str, object], FsIdentity, FsIdentity]:
    path = _canonical_final_path(path)
    before_path = path.lstat()
    if not stat.S_ISREG(before_path.st_mode):
        _unknown("MANIFEST_NOT_REGULAR", f"manifest is not a regular file: {path}")
    parent_identity = _identity_at(path.parent)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        _unknown("MANIFEST_UNREADABLE", str(error))
    try:
        before = os.fstat(descriptor)
        if before.st_size > MAX_MANIFEST_BYTES:
            _unknown("MANIFEST_MALFORMED", "manifest exceeds the size limit")
        chunks: list[bytes] = []
        offset = 0
        while offset < before.st_size:
            chunk = os.pread(descriptor, before.st_size - offset, offset)
            if not chunk:
                _unknown("MANIFEST_UNSTABLE", "manifest was truncated while read")
            chunks.append(chunk)
            offset += len(chunk)
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            _unknown("MANIFEST_UNSTABLE", "manifest changed while it was read")
        descriptor_identity = FsIdentity.from_stat(after)
        if FsIdentity.from_stat(path.lstat()) != descriptor_identity:
            _unknown("MANIFEST_UNSTABLE", "manifest path changed while it was read")
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8", "strict"))
        except (UnicodeError, json.JSONDecodeError) as error:
            _unknown("MANIFEST_MALFORMED", str(error))
        payload = _object(payload, "manifest")
        _validate_manifest(payload)
        return payload, descriptor_identity, parent_identity
    finally:
        os.close(descriptor)


def _path_identity_matches(path: Path, expected: FsIdentity) -> bool:
    try:
        return _identity_at(path) == expected
    except OSError as error:
        _unknown(
            "EVIDENCE_PATH_UNREADABLE",
            f"cannot measure required evidence path {path}: {error}",
        )


def evaluate(*, manifest_path: Path) -> dict[str, object]:
    canonical_manifest = _canonical_final_path(manifest_path)
    payload, manifest_identity, parent_identity = _read_manifest(canonical_manifest)

    manifest_record = _object(payload["manifest"], "manifest identity")
    recorded_manifest = Path(_string(manifest_record["path"], "manifest.path"))
    recorded_manifest_identity = _recorded_identity(
        manifest_record["identity"],
        "manifest.identity",
    )
    recorded_parent_identity = _recorded_identity(
        manifest_record["parent_identity"],
        "manifest.parent_identity",
    )
    repo, repo_identity = _path_record(payload["repo"], "repo")
    common_git, common_git_identity = _path_record(
        payload["common_git_dir"],
        "common_git_dir",
    )

    source_record = _object(payload["source"], "source")
    source = Path(_string(source_record["path"], "source.path"))
    source_identity = _recorded_identity(
        source_record["identity"],
        "source.identity",
    )
    output_record = _object(payload["output"], "output")
    output = Path(_string(output_record["path"], "output.path"))
    output_identity = _recorded_identity(
        output_record["identity"],
        "output.identity",
    )
    output_git_record = _object(output_record["git_dir"], "output.git_dir")
    output_git = Path(
        _string(output_git_record["path"], "output.git_dir.path")
    )
    output_git_identity = _recorded_identity(
        output_git_record["identity"],
        "output.git_dir.identity",
    )

    manifest_path_matches = recorded_manifest == canonical_manifest
    manifest_identity_matches = manifest_identity == recorded_manifest_identity
    manifest_parent_matches = parent_identity == recorded_parent_identity
    repo_path_matches = _path_identity_matches(repo, repo_identity)
    source_path_matches = _path_identity_matches(source, source_identity)
    output_path_matches = _path_identity_matches(output, output_identity)
    common_path_matches = _path_identity_matches(common_git, common_git_identity)
    output_git_matches = _path_identity_matches(output_git, output_git_identity)

    repository_matches = False
    source_unchanged = False
    output_unchanged = False
    main_ref_unchanged = False
    merge_base_matches = False
    candidate_commits_match = False
    if all(
        (
            repo_path_matches,
            source_path_matches,
            output_path_matches,
            common_path_matches,
            output_git_matches,
        )
    ):
        actual_output_git = Path(
            _git_text(
                output,
                "rev-parse",
                "--path-format=absolute",
                "--absolute-git-dir",
            )
        ).resolve(strict=True)
        repository_matches = (
            _common_git_dir(repo) == common_git
            and _common_git_dir(source) == common_git
            and _common_git_dir(output) == common_git
            and actual_output_git == output_git
        )
        if repository_matches:
            recorded_source_head = _string(source_record["head"], "source.head")
            recorded_main_sha = _string(payload["main_sha"], "main_sha")
            source_commit = _resolve_commit(
                source,
                recorded_source_head,
                "SOURCE_HEAD_UNREADABLE",
            )
            main_commit = _resolve_commit(
                repo,
                recorded_main_sha,
                "MAIN_SHA_UNREADABLE",
            )
            actual_merge_base = _git_text(
                repo,
                "merge-base",
                source_commit,
                main_commit,
            )
            merge_base_matches = (
                source_commit == recorded_source_head
                and main_commit == recorded_main_sha
                and actual_merge_base == payload["merge_base"]
            )
            candidate_commits_match = _candidate_commits(
                source,
                actual_merge_base,
                source_commit,
            ) == payload["candidate_commits"]
            source_unchanged = (
                _resolve_commit(source, "HEAD", "SOURCE_HEAD_UNREADABLE")
                == source_record["head"]
                and _fingerprint(source) == source_record["state_sha256"]
            )
            output_unchanged = (
                _resolve_commit(output, "HEAD", "OUTPUT_HEAD_UNREADABLE")
                == output_record["head"]
                and _fingerprint(output) == output_record["state_sha256"]
            )
            main_ref_unchanged = (
                _resolve_commit(
                    repo,
                    _string(payload["main_ref"], "main_ref"),
                    "MAIN_REF_UNREADABLE",
                )
                == payload["main_sha"]
            )

    repo_path_matches = repo_path_matches and _path_identity_matches(
        repo,
        repo_identity,
    )
    source_path_matches = source_path_matches and _path_identity_matches(
        source,
        source_identity,
    )
    output_path_matches = output_path_matches and _path_identity_matches(
        output,
        output_identity,
    )
    common_path_matches = common_path_matches and _path_identity_matches(
        common_git,
        common_git_identity,
    )
    output_git_matches = output_git_matches and _path_identity_matches(
        output_git,
        output_git_identity,
    )
    repository_matches = repository_matches and all(
        (
            repo_path_matches,
            source_path_matches,
            output_path_matches,
            common_path_matches,
            output_git_matches,
        )
    )

    final_payload, final_manifest_identity, final_parent_identity = _read_manifest(
        canonical_manifest
    )
    manifest_contents_match = final_payload == payload
    manifest_identity_matches = (
        manifest_identity_matches
        and final_manifest_identity == recorded_manifest_identity
    )
    manifest_parent_matches = (
        manifest_parent_matches
        and final_parent_identity == recorded_parent_identity
    )

    evidence = {
        "manifest_path_matches": manifest_path_matches,
        "manifest_identity_matches": manifest_identity_matches,
        "manifest_parent_matches": manifest_parent_matches,
        "manifest_contents_match": manifest_contents_match,
        "repository_matches": repository_matches,
        "source_unchanged": source_unchanged,
        "output_unchanged": output_unchanged,
        "main_ref_unchanged": main_ref_unchanged,
        "merge_base_matches": merge_base_matches,
        "candidate_commits_match": candidate_commits_match,
    }
    verdict = "ADOPTABLE" if all(evidence.values()) else "NOT_ADOPTABLE"
    return {
        "verdict": verdict,
        "manifest": str(canonical_manifest),
        "evidence": evidence,
    }


def _emit(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    if payload["verdict"] == "ADOPTABLE":
        print(f"ADOPTABLE manifest={payload['manifest']}")
    elif payload["verdict"] == "NOT_ADOPTABLE":
        failed = [
            name for name, value in payload["evidence"].items() if not value
        ]
        print(f"NOT_ADOPTABLE failed={','.join(failed)}")
    else:
        print(
            f"UNKNOWN {payload.get('reason_code', 'UNKNOWN')}: "
            f"{payload.get('message', '')}"
        )


def _emit_unknown(error: AdoptionUnknown, *, as_json: bool) -> NoReturn:
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
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        payload = evaluate(manifest_path=args.manifest)
    except AdoptionUnknown as error:
        _emit_unknown(error, as_json=args.json)
    except ReplayFailure as error:
        _emit_unknown(
            AdoptionUnknown("EVIDENCE_UNREADABLE", error.message),
            as_json=args.json,
        )
    except (OSError, UnicodeError, ValueError) as error:
        _emit_unknown(
            AdoptionUnknown("LOCAL_IO_FAILED", str(error)),
            as_json=args.json,
        )
    _emit(payload, as_json=args.json)
    return 0 if payload["verdict"] == "ADOPTABLE" else 1


if __name__ == "__main__":
    sys.exit(main())
