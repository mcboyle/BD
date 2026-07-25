"""Portable repository discovery and repository-relative paths."""

from __future__ import annotations

import subprocess
from pathlib import Path


def discover_repo_root(start: Path | str) -> Path:
    """Return the Git worktree root containing *start*."""
    candidate = Path(start).expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    result = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"not inside a Git repository: {start}")
    return Path(result.stdout.decode("utf-8", "surrogateescape").strip()).resolve()


def normalize_repo_path(root: Path, path: Path | str) -> str:
    """Return a normalized, safe repository-relative path.

    Resolving both paths deliberately rejects tracked symlinks that leave the
    repository, rather than allowing a snapshot to hash external bytes.
    """
    repository = discover_repo_root(root)
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = repository / candidate
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(repository)
    except ValueError as error:
        raise ValueError(f"path is outside repository: {path}") from error
    return relative.as_posix()
