"""Every tracked Python source must parse. No exceptions, no silent skips.

Why this exists: every AST tool in this repo swallows per-file SyntaxErrors.
`tools/dependency_graph.py` returned None; `tools/decomp/import_graph_gate.py`
reported PASS and exit 0. A file that will not parse therefore contributes no
edges and no symbols, and every gate downstream reports clean over a
denominator that structurally excludes it -- truthfully, and uselessly
(CLAUDE.md 0). Exactly one file in the tree did this, and the import graph came
out one edge short while the tooling said everything was fine.

The other gates now fail closed (see tests/test_dependency_graph_fails_closed.py
and tests/test_import_graph_gate_fails_closed.py). This test is the direct
statement of the same invariant: the tree parses, so their denominators are
whole.

SCOPE NOTE -- the floor is the running interpreter, deliberately.
An earlier draft also shelled out to a real Python 3.11 to assert the tree
parsed there, because the sandbox's bare `python3` is 3.11 and tools reaching
for it silently selected it. That was treating the symptom. The cause is fixed
where it belongs: toolchain/bin/bd-cut, tools/sast.sh and tools/dast.sh now
resolve the work-tree venv, so nothing selects 3.11 any more. Asserting a floor
no interpreter in the pipeline runs would be a denominator drifting away from
its subject -- and it FAILED on any host without python3.11 installed, which is
every stock Ubuntu 24.04 box including the operator's.

(For the record, since it is the thing most likely to be "simplified" back in:
`ast.parse(..., feature_version=(3, 11))` does NOT restore 3.11's f-string
restriction under 3.12 -- measured. There is no in-process way to ask the
older question. It costs a subprocess or it costs nothing.)
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# A floor, not a count. Pinning the exact number would fire on every added
# file -- a gate that cries wolf gets switched off. This only catches a query
# that has collapsed (wrong cwd, git failure), which is the real failure mode.
_MIN_PLAUSIBLE_TRACKED = 100


def tracked_python_files() -> list[str]:
    """Repo-relative paths of every tracked ``*.py``, sorted.

    Raises rather than returning a short list, so a broken query can never be
    mistaken for a clean tree.
    """
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "cannot enumerate tracked sources: git ls-files exited %d: %s"
            % (proc.returncode, proc.stderr.decode("utf-8", "replace"))
        )
    files = sorted(
        chunk.decode("utf-8") for chunk in proc.stdout.split(b"\0") if chunk
    )
    if len(files) < _MIN_PLAUSIBLE_TRACKED:
        raise AssertionError(
            "tracked-source denominator is implausibly small (%d < %d); the "
            "ls-files query is broken, not the tree"
            % (len(files), _MIN_PLAUSIBLE_TRACKED)
        )
    return files


def _self_relpath() -> str:
    return Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()


def self_inclusion_problem(files: list[str]) -> str | None:
    """Return a message if the denominator cannot see this very file.

    Self-inclusion is the canary for "the query is rooted where it claims".
    It has one legitimate miss: this file is brand new and not yet tracked.
    That state is *derived* from git rather than assumed -- git must still
    report the file as present-but-untracked in this working tree, which is
    what proves the rooting. Anything else is a broken query.
    """
    rel = _self_relpath()
    if rel in files:
        return None
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", rel],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0 and proc.stdout.strip().startswith("??"):
        return None
    return (
        "the tracked-source denominator does not contain this test file (%s) "
        "and git does not report it as untracked-but-present either. The query "
        "is not rooted at the repository, so its 'all sources parse' result "
        "describes some other set of files." % rel
    )


def _format(failures: list[tuple[str, int | None, str]], total: int) -> str:
    head = "Python %s failed to parse %d of %d tracked sources:" % (
        ".".join(str(n) for n in __import__("sys").version_info[:3]),
        len(failures),
        total,
    )
    body = "\n".join(
        "    %s:%s: %s" % (rel, "?" if line is None else line, msg)
        for rel, line, msg in failures
    )
    return head + "\n" + body


def test_the_denominator_contains_this_file():
    """If the query cannot see its own test, it cannot see anything reliably."""
    problem = self_inclusion_problem(tracked_python_files())
    assert problem is None, problem


def test_all_tracked_sources_parse():
    files = tracked_python_files()
    failures: list[tuple[str, int | None, str]] = []
    for rel in files:
        path = REPO_ROOT / rel
        try:
            ast.parse(path.read_bytes(), filename=rel)
        except SyntaxError as exc:
            failures.append((rel, exc.lineno, exc.msg))
        except OSError as exc:
            failures.append((rel, None, "%s: %s" % (type(exc).__name__, exc)))
    assert not failures, _format(failures, len(files))
