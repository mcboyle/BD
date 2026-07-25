"""Deterministic JSON artifacts and atomic directory comparison utilities."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_OMIT_KEYS = frozenset({"generated_at"})
_MALFORMED = object()


@dataclass(frozen=True, order=True)
class ArtifactDifference:
    """One deterministic difference between two artifact directories."""

    state: str
    path: str


def _without_keys(value: object, omit_keys: frozenset[str]) -> object:
    if isinstance(value, Mapping):
        return {
            key: _without_keys(item, omit_keys)
            for key, item in value.items()
            if key not in omit_keys
        }
    if isinstance(value, list):
        return [_without_keys(item, omit_keys) for item in value]
    if isinstance(value, tuple):
        return [_without_keys(item, omit_keys) for item in value]
    return value


def canonical_bytes(
    value: object,
    *,
    omit_keys: frozenset[str] = _DEFAULT_OMIT_KEYS,
) -> bytes:
    """Serialize JSON deterministically, optionally omitting nondeterministic keys."""
    canonical = json.dumps(
        _without_keys(value, omit_keys),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (canonical + "\n").encode("utf-8")


def atomic_write_json(
    path: Path,
    value: object,
    validator: Callable[[object], None],
) -> None:
    """Validate and durably replace *path* without exposing partial JSON."""
    validator(value)
    payload = canonical_bytes(value, omit_keys=frozenset())
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def artifact_hash(value: object) -> str:
    """Return the SHA-256 identity of an artifact without its generation time."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _artifact_members(directory: Path) -> dict[str, Path]:
    if not directory.is_dir():
        return {}
    return {
        path.relative_to(directory).as_posix(): path
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _read_json(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _MALFORMED


def compare_artifact_dirs(
    left: Path,
    right: Path,
    *,
    ignore_generation_time: bool = True,
) -> tuple[ArtifactDifference, ...]:
    """Compare JSON artifact members, retaining only actionable differences."""
    invalid_subjects = tuple(
        ArtifactDifference("unverifiable", label)
        for label, directory in (("left", left), ("right", right))
        if not directory.is_dir()
    )
    if invalid_subjects:
        return invalid_subjects
    left_members = _artifact_members(left)
    right_members = _artifact_members(right)
    differences: list[ArtifactDifference] = []

    for relative_path in sorted(set(left_members) | set(right_members)):
        left_path = left_members.get(relative_path)
        right_path = right_members.get(relative_path)
        if left_path is None:
            if _read_json(right_path) is _MALFORMED:
                differences.append(ArtifactDifference("malformed", relative_path))
            else:
                differences.append(ArtifactDifference("unexpected", relative_path))
            continue
        if right_path is None:
            if _read_json(left_path) is _MALFORMED:
                differences.append(ArtifactDifference("malformed", relative_path))
            else:
                differences.append(ArtifactDifference("missing", relative_path))
            continue

        left_value = _read_json(left_path)
        right_value = _read_json(right_path)
        if left_value is _MALFORMED or right_value is _MALFORMED:
            differences.append(ArtifactDifference("malformed", relative_path))
            continue
        omit_keys = _DEFAULT_OMIT_KEYS if ignore_generation_time else frozenset()
        if canonical_bytes(left_value, omit_keys=omit_keys) != canonical_bytes(
            right_value, omit_keys=omit_keys
        ):
            differences.append(ArtifactDifference("stale", relative_path))

    return tuple(sorted(differences))


def main(argv: Sequence[str] | None = None) -> int:
    """Run deterministic artifact directory comparisons."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare", help="compare two artifact directories")
    compare.add_argument("--left", type=Path, required=True)
    compare.add_argument("--right", type=Path, required=True)
    compare.add_argument(
        "--ignore-generation-time",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    arguments = parser.parse_args(argv)

    differences = compare_artifact_dirs(
        arguments.left,
        arguments.right,
        ignore_generation_time=arguments.ignore_generation_time,
    )
    for difference in differences:
        print(f"{difference.state} {difference.path}")
    return 0 if not differences else 1


if __name__ == "__main__":
    raise SystemExit(main())
