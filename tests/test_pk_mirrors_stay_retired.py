"""The executable toolchain has ONE copy. project-knowledge/ does not mirror it.

WHY THE MIRRORS EXISTED, AND WHY THEY DO NOT ANYMORE. `project-knowledge/`
carried a byte-identical duplicate of most of `toolchain/bin` so the directory
could be uploaded as a self-contained bundle. The operator confirmed on
2026-08-07 that it is not used that way, which removed the only reason for a
second copy of an executable.

WHAT THE SECOND COPY COST, on the record. At v3.66.818
`project-knowledge/bd-guardcheck` reported "0 ok, 0 drifted, 0 missing" and
EXITED 0 while the real tool reported 7 ok: a cut had repaired one copy of a
two-copy tool and the tree reported success. A drift gate kept them in sync,
but a sync gate cannot make a stale copy correct -- it only makes both copies
agree on whichever one someone edited. v3.66.929 paid the same cost again.

THIS GATE'S SUBJECT IS DUPLICATION, NOT A COUNT. A pinned number would go
stale on the next tool added and would say nothing about whether a NEW
duplicate had appeared. The question asked here is the durable one: does any
tracked file under project-knowledge/ have byte-identical twin in
toolchain/bin? One is a duplicate; zero is a single source.

THE SURVIVOR IS DECLARED, NOT ASSUMED. `project-knowledge/bd-scan.py` has no
twin in toolchain/bin -- it is a real tool, not a copy, referenced from six
documents and from `project-knowledge/bd-rev`. The retirement's own rule was
that every deletion be VERIFIED byte-identical to its origin first, and that
rule is exactly what stopped this file being swept along with the mirrors.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# Files under project-knowledge/ that are NOT mirrors and must survive. A
# reason each, not a mute button -- and the test below FAILS if one of these
# ever stops existing, so a stale entry cannot quietly excuse a future
# duplicate of the same name.
_NOT_A_MIRROR = {
    "project-knowledge/bd-scan.py":
        "A real tool with no toolchain/bin twin: the zip-era L0 battery driver "
        "that normalises defect_patterns/bandit/vulture output into "
        "SCAN_FINDINGS.json. Referenced from six tracked documents and from "
        "project-knowledge/bd-rev. Its sandbox-era defaults are a separate "
        "question (item 3); its existence is not.",
}


def _tracked(pattern: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", pattern],
                         cwd=str(_REPO), capture_output=True, text=True).stdout
    return [p for p in out.split() if p]


def test_no_project_knowledge_file_duplicates_a_toolchain_tool():
    """RED while the mirrors exist: 229 byte-identical duplicates."""
    toolchain = {Path(p).name: _REPO / p for p in _tracked("toolchain/bin/*")}
    assert toolchain, (
        "no tracked files under toolchain/bin -- the denominator is empty, so "
        "this assertion would pass over nothing")

    duplicates = []
    for rel in _tracked("project-knowledge/*"):
        twin = toolchain.get(Path(rel).name)
        if twin is None:
            continue
        src = _REPO / rel
        try:
            if src.read_bytes() == twin.read_bytes():
                duplicates.append(rel)
        except OSError:
            continue

    assert not duplicates, (
        f"{len(duplicates)} tracked project-knowledge file(s) are "
        f"byte-identical duplicates of a toolchain/bin tool. A second copy of "
        f"an executable is a second thing to repair, and a sync gate cannot "
        f"make a stale copy correct -- it only makes both agree on whichever "
        f"one was edited. First ten: {sorted(duplicates)[:10]}")


def test_the_declared_survivors_still_exist():
    """An exception for a file that is gone is a licence nobody is watching."""
    missing = [rel for rel in _NOT_A_MIRROR if not (_REPO / rel).is_file()]
    assert not missing, (
        f"_NOT_A_MIRROR names file(s) that no longer exist: {missing}. Remove "
        f"the entry -- a standing exception for a path that cannot arise will "
        f"silently excuse a future duplicate of the same name.")


def test_no_survivor_is_actually_a_duplicate():
    """The other direction: a declared survivor must genuinely have no twin.

    If one ever gains a byte-identical counterpart in toolchain/bin, the
    exception above would hide exactly the duplication this file exists to
    forbid.
    """
    for rel in sorted(_NOT_A_MIRROR):
        twin = _REPO / "toolchain" / "bin" / Path(rel).name
        if not twin.is_file():
            continue
        assert (_REPO / rel).read_bytes() != twin.read_bytes(), (
            f"{rel} is declared as 'not a mirror' but is byte-identical to "
            f"{twin}. Delete it and drop the exception.")


def test_the_toolchain_is_where_the_tools_live():
    """The positive half. Retiring the mirrors must not have retired the
    originals -- a tree with neither copy would pass every assertion above."""
    tools = _tracked("toolchain/bin/bd-*")
    assert len(tools) > 200, (
        f"only {len(tools)} bd-* tools under toolchain/bin; the suite is the "
        f"single source for the executable toolchain and this is far below "
        f"the population every other gate assumes")
