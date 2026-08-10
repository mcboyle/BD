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


def relative_to_repo(repository: Path, path: Path | str) -> str:
    """`normalize_repo_path` with the repository root ALREADY discovered.

    Same rule, same rejection: resolving both paths deliberately rejects tracked
    symlinks that leave the repository, rather than allowing a snapshot to hash
    external bytes. The only difference is that it does not re-derive the root.

    That matters because `discover_repo_root` forks `git rev-parse`, and
    `build_snapshot` validated one path per tracked file inside its loop with a
    CONSTANT root -- measured at 3224 forks and 6.66s for a single
    `build_snapshot(REPO)` call, one fork per tracked file, for an answer that
    cannot change between iterations.
    """
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = repository / candidate
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(repository)
    except ValueError as error:
        raise ValueError(f"path is outside repository: {path}") from error
    return relative.as_posix()


def normalize_repo_path(root: Path, path: Path | str) -> str:
    """Return a normalized, safe repository-relative path.

    Behaviour is unchanged -- it still discovers the root from *root* on every
    call, because callers pass an arbitrary starting point rather than a known
    worktree root. Callers that already hold the root should use
    `relative_to_repo` instead.
    """
    return relative_to_repo(discover_repo_root(root), path)
