#!/usr/bin/env python3
"""Resolve the one checkout authorized for a bd tool invocation."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import subprocess
import sys


class WorkTreeResolutionError(RuntimeError):
    """No trustworthy checkout authority is available."""


def _validated_checkout(value: str, source: str) -> Path:
    if not value or "\0" in value or value != value.strip():
        raise WorkTreeResolutionError(f"{source} is empty or has ambiguous whitespace")
    candidate = Path(value)
    if not candidate.is_dir():
        raise WorkTreeResolutionError(f"{source} does not name a directory: {candidate}")
    candidate = candidate.resolve()
    try:
        found = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkTreeResolutionError(
            f"cannot validate {source} with Git: {candidate}"
        ) from exc
    if found.returncode != 0 or not found.stdout.strip():
        raise WorkTreeResolutionError(f"{source} is not a Git checkout: {candidate}")
    checkout = Path(found.stdout.strip()).resolve()
    if checkout != candidate:
        raise WorkTreeResolutionError(
            f"{source} names {candidate}, not canonical Git top level {checkout}"
        )
    return checkout


def _read_pointer(pointer: Path) -> str:
    try:
        info = pointer.lstat()
    except OSError as exc:
        raise WorkTreeResolutionError(
            f"installed pointer metadata is unreadable: {pointer}"
        ) from exc
    if not stat.S_ISREG(info.st_mode) or pointer.is_symlink():
        raise WorkTreeResolutionError(f"installed pointer is not a regular file: {pointer}")
    if info.st_uid != os.getuid() or info.st_nlink != 1:
        raise WorkTreeResolutionError(
            f"installed pointer has unsafe ownership or link count: {pointer}"
        )
    try:
        raw = pointer.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise WorkTreeResolutionError(f"installed pointer is unreadable UTF-8: {pointer}") from exc
    if raw.endswith("\n"):
        raw = raw[:-1]
    if "\n" in raw or "\r" in raw:
        raise WorkTreeResolutionError(f"installed pointer must contain exactly one line: {pointer}")
    if not raw or raw != raw.strip():
        raise WorkTreeResolutionError(
            f"installed pointer is empty or has ambiguous whitespace: {pointer}"
        )
    return raw


def resolve_work_tree(
    tool_file: str | os.PathLike[str],
    *,
    explicit: str | None = None,
) -> Path:
    """Return the canonical Git top level or raise WorkTreeResolutionError."""
    if explicit is not None:
        return _validated_checkout(explicit, "explicit checkout")
    if "BD_WORK_TREE" in os.environ:
        return _validated_checkout(os.environ["BD_WORK_TREE"], "BD_WORK_TREE")

    physical = Path(tool_file).resolve()
    pointer = physical.parent / ".bd-work-tree"
    try:
        pointer.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise WorkTreeResolutionError(
            f"installed pointer metadata is unreadable: {pointer}"
        ) from exc
    else:
        return _validated_checkout(_read_pointer(pointer), str(pointer))

    # Source execution is deliberately narrow: only an actual
    # <git-top>/toolchain/bin/* physical file may infer its containing checkout.
    if physical.parent.name == "bin" and physical.parent.parent.name == "toolchain":
        candidate = physical.parent.parent.parent
        checkout = _validated_checkout(str(candidate), "source tool layout")
        if physical.parent == checkout / "toolchain" / "bin":
            return checkout
    raise WorkTreeResolutionError(f"no checkout authority for physical tool {physical}")


def main(argv: list[str] | None = None) -> int:
    """Print the root and return 0, or print a named refusal and return 2."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", "--work", dest="explicit", default=None)
    args = parser.parse_args(argv)
    try:
        root = resolve_work_tree(__file__, explicit=args.explicit)
    except WorkTreeResolutionError as exc:
        print(f"BD-WORK-TREE-UNRUNNABLE: {exc}", file=sys.stderr)
        return 2
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
