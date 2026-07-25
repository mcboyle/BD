"""Tracked-tree snapshots that remain sensitive to dirty worktree bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence

from .paths import discover_repo_root, normalize_repo_path
from .schemas import make_envelope


SNAPSHOT_SCHEMA = "code_intelligence.tree_snapshot"
SNAPSHOT_VERSION = 1
TOOL_VERSION = "1"


@dataclass(frozen=True)
class FileFact:
    path: str
    sha256: str
    size: int
    lines: int


@dataclass(frozen=True)
class TreeSnapshot:
    source_sha: str
    files: tuple[FileFact, ...]


def tracked_files(root: Path) -> tuple[str, ...]:
    """Return the canonical sorted set of Git-tracked repository paths."""
    repository = discover_repo_root(root)
    result = subprocess.run(
        ["git", "-C", str(repository), "ls-files", "-z", "--cached"],
        capture_output=True,
        check=True,
    )
    entries = result.stdout.split(b"\0")
    return tuple(sorted(
        entry.decode("utf-8", "surrogateescape")
        for entry in entries
        if entry
    ))


def build_snapshot(
    root: Path, include: Callable[[str], bool] | None = None
) -> TreeSnapshot:
    """Build a deterministic content snapshot of tracked files beneath *root*."""
    repository = discover_repo_root(root)
    facts: list[FileFact] = []
    for tracked_path in tracked_files(repository):
        if include is not None and not include(tracked_path):
            continue
        normalize_repo_path(repository, repository / tracked_path)
        raw = (repository / tracked_path).read_bytes()
        facts.append(FileFact(
            tracked_path,
            hashlib.sha256(raw).hexdigest(),
            len(raw),
            raw.count(b"\n") + (1 if raw and not raw.endswith(b"\n") else 0),
        ))
    digest = hashlib.sha256()
    for fact in facts:
        digest.update(
            f"{fact.path}\0{fact.sha256}\n".encode("utf-8", "surrogateescape")
        )
    return TreeSnapshot(digest.hexdigest(), tuple(facts))


def _production_predicate(root: Path) -> Callable[[str], bool]:
    from tools.l0_extract import prod_files

    production_paths = frozenset(
        path.replace(os.sep, "/") if os.sep != "/" else path
        for path in prod_files(str(root))
    )
    return production_paths.__contains__


def _load_envelope(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as source:
        payload = json.load(source)
    if not isinstance(payload, dict):
        raise ValueError("snapshot JSON must contain an object envelope")
    return payload


def _validate_envelope(payload: dict[str, object]) -> None:
    required = {
        "schema_name": str,
        "schema_version": int,
        "source_sha": str,
        "tool_version": str,
        "input_hashes": dict,
        "generated_at": str,
        "scope": str,
        "files": list,
    }
    for field, expected_type in required.items():
        if not isinstance(payload.get(field), expected_type):
            raise ValueError(f"snapshot JSON has invalid {field}")
    if payload["schema_name"] != SNAPSHOT_SCHEMA:
        raise ValueError("snapshot JSON has an unknown schema_name")
    if payload["schema_version"] != SNAPSHOT_VERSION:
        raise ValueError("snapshot JSON has an unsupported schema_version")


def _write_envelope(path: Path, envelope: dict[str, object]) -> None:
    _validate_envelope(envelope)
    encoded = (json.dumps(envelope, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as destination:
            temporary = Path(destination.name)
            destination.write(encoded)
            destination.flush()
            os.fsync(destination.fileno())
        _validate_envelope(_load_envelope(temporary))
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _deterministic_content(envelope: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in envelope.items() if key != "generated_at"}


def _compare_snapshots(expected: dict[str, object], actual: dict[str, object]) -> str | None:
    if _deterministic_content(expected) == _deterministic_content(actual):
        return None
    if expected.get("source_sha") != actual.get("source_sha"):
        return "source SHA differs"
    return "snapshot content differs"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--scope", choices=("tracked", "production"), required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--out", type=Path)
    target.add_argument("--check", type=Path)
    args = parser.parse_args(argv)

    repository = discover_repo_root(args.root)
    include = _production_predicate(repository) if args.scope == "production" else None
    snapshot = build_snapshot(repository, include)
    envelope = make_envelope(
        SNAPSHOT_SCHEMA,
        SNAPSHOT_VERSION,
        snapshot.source_sha,
        TOOL_VERSION,
        {"tracked_tree": snapshot.source_sha},
    )
    envelope.update({
        "scope": args.scope,
        "files": [asdict(fact) for fact in snapshot.files],
    })
    if args.out is not None:
        try:
            _write_envelope(args.out, envelope)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"snapshot write failed: {error}")
            return 1
        return 0

    try:
        expected = _load_envelope(args.check)
        _validate_envelope(expected)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"snapshot check failed: {error}")
        return 1
    difference = _compare_snapshots(expected, envelope)
    if difference is not None:
        print(difference)
        return 1
    print("snapshot matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
