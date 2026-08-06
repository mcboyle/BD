"""Enumerating the repo's SOURCE files, without excluding most of them.

@918, and it is the denominator half of CLAUDE.md section 0. Three retirement
gates asked "does anything still execute against this dead thing?" over

    git ls-files -z -- '*.py' '*.sh'

which is not the source in this repo. Measured at v3.66.918: that glob returns
2180 files, while 473 further tracked files are python- or shell-shebang
scripts with NO EXTENSION -- the whole `toolchain/bin` bd-* suite and its
project-knowledge mirror. Every one of them was outside the denominator of
every gate that used it, and the gates reported clean.

That is not hypothetical. Three tools retired before v3.66.858 survived as
extensionless tracked files for 59 releases with all three gates green; see
v3.66.917, which deleted them.

TWO MISTAKES, AND THE SECOND IS THE ONE THAT LOOKS FIXED.

1. **The glob.** Fixed by enumerating everything and typing each file.

2. **The ROUTING.** Every one of these gates branches on `path.suffix ==
   ".py"` to decide between an AST walk (which correctly excludes docstrings)
   and a line scan (which cannot). Widening the denominator WITHOUT fixing
   that predicate lets an extensionless python script in and then reads it as
   if it were shell -- so its docstrings and its prose become "executable
   references" and the gate fails honest, live tools for their FILE
   EXTENSION. Measured: routing naively flags 4 live tools across 2 gates;
   routing on content flags 2 tools on 1 gate, and both of those turned out to
   be real stale references rather than false positives.

   A gate made over-sensitive is not a safer gate. CLAUDE.md section 0's
   inverse defect: one that cries wolf gets switched off.

So `kind` here is what the file IS, not what it is named, and callers must
branch on `kind`, never on the suffix.

    from tracked_source import tracked_source_files

    files = tracked_source_files(REPO_ROOT)
    assert len(files) > 100, "denominator collapsed"
    for rel, kind in files:
        ...  # branch on kind

Returns [] when git is unavailable; the caller decides whether that is a skip
or a failure, because "I could not look" is a third state and it is not a pass.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# Read only enough to see an interpreter. A shebang is by definition the first
# line, and some tracked assets are large.
_SHEBANG_BYTES = 200


def source_kind(path: Path, rel: str) -> str | None:
    """"python", "shell", or None for anything that is not source.

    Extension first (cheap and unambiguous), then the shebang for the
    extensionless case. A file with ANY other suffix is not source here --
    a .md or .json naming a dead tool is prose, which these gates deliberately
    permit.
    """
    if rel.endswith(".py"):
        return "python"
    if rel.endswith(".sh"):
        return "shell"
    if Path(rel).suffix:
        return None
    try:
        with open(path, "rb") as fh:
            first = fh.readline(_SHEBANG_BYTES)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    # `#!/usr/bin/env python3` and `#!/usr/bin/python` both say python; treat
    # every other interpreter as shell, which is the branch that line-scans.
    return "python" if b"python" in first else "shell"


def tracked_source_files(repo_root: Path) -> list[tuple[str, str]]:
    """[(relpath, kind)] for every TRACKED python or shell source file.

    Tracked, not walked: the box has sibling worktrees holding older checkouts,
    and a tree walk finds those and reports this repo as still referencing
    something it removed. That reasoning is inherited verbatim from the gates
    this helper replaces -- it was right, and only their file-type filter was
    wrong.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(repo_root), capture_output=True, timeout=120,
        )
    except OSError:
        # A non-zero exit is not the only way this fails: an unreadable or
        # missing cwd makes subprocess raise FileNotFoundError before git ever
        # runs, and a missing git binary raises too. Returning [] for the exit
        # code alone while propagating these would hand the caller an exception
        # where it expected the "I could not look" answer -- which is the state
        # this function exists to be able to report.
        return []
    if proc.returncode != 0:
        return []
    out = []
    for rel in proc.stdout.decode("utf-8", "replace").split("\0"):
        if not rel:
            continue
        path = repo_root / rel
        if not path.is_file():
            continue
        kind = source_kind(path, rel)
        if kind:
            out.append((rel, kind))
    return out
