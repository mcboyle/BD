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
tracked file under project-knowledge/ have a byte-identical twin
ANYWHERE else in the tree? One is a duplicate; zero is a single source.

THE DENOMINATOR WAS toolchain/bin/* ONLY, AND THAT IS WHY IT REPORTED CLEAN.
Measured at v3.66.953: twenty tracked project-knowledge files are byte-identical
to a file under tools/ or toolchain/ -- origins this gate never looked at, so
the duplication it exists to forbid was structurally invisible to it. The
survivor exception said `project-knowledge/bd-scan.py` "has no toolchain/bin
twin", which was true as written and misleading in effect: it was byte-identical
to tools/bd-scan.py the whole time.

The predicate moved with the denominator. Matching on BASENAME asks "is there a
tool of the same name"; hashing CONTENT asks the question the docstring above
actually poses, and it survives a rename. Both halves were wrong, and fixing
only the denominator would have left a gate that still could not see a copy
filed under a different name.

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
# Byte-identical pairs that exist TODAY, frozen so no NEW one can appear. The
# list may only shrink; test_no_known_duplicate_is_stale below fails if an entry
# stops being a duplicate, so retiring a copy forces the entry out rather than
# leaving a standing licence behind.
_KNOWN_DUPLICATES: dict[str, str] = {
    "project-knowledge/audit_emit_gate.py":
        "tools/audit_emit_gate.py",
    "project-knowledge/bdenv.sh":
        "toolchain/bdenv.sh",
    "project-knowledge/body_contract.py":
        "tools/body_contract.py",
    "project-knowledge/cap01_witnesses.py":
        "tools/audit/witnesses/cap01_witnesses.py",
    "project-knowledge/capture_scrub.py":
        "tools/capture_scrub.py",
    "project-knowledge/constraint_incidence.py":
        "tools/constraint_incidence.py",
    "project-knowledge/consumer_agreement.py":
        "tools/consumer_agreement.py",
    "project-knowledge/coverage_map.py":
        "tools/coverage_map.py",
    "project-knowledge/endpoint_reachability.py":
        "tools/endpoint_reachability.py",
    "project-knowledge/install_bdsuite.sh":
        "toolchain/install_bdsuite.sh",
    "project-knowledge/l0_extract.py":
        "tools/l0_extract.py",
    "project-knowledge/reachability_ledger.py":
        "tools/reachability_ledger.py",
    "project-knowledge/render_advanced_kb.py":
        "tools/render_advanced_kb.py",
    "project-knowledge/review_merge.py":
        "tools/review_merge.py",
    "project-knowledge/run01_witnesses.py":
        "tools/audit/witnesses/run01_witnesses.py",
    "project-knowledge/run_witnesses.py":
        "tools/run_witnesses.py",
    "project-knowledge/seed_review_state.py":
        "tools/seed_review_state.py",
    "project-knowledge/staleness.py":
        "tools/staleness.py",
    "project-knowledge/verify_audit.py":
        "tools/verify_audit.py",
    "project-knowledge/witness_drift.py":
        "tools/witness_drift.py",
}

_NOT_A_MIRROR: dict[str, str] = {
    # Empty, and that is the correct state. The single entry this held --
    # project-knowledge/bd-scan.py, "a real tool with no toolchain/bin twin"
    # -- was byte-identical to tools/bd-scan.py, which the old toolchain/bin-
    # only denominator could not see. It was retired at v3.66.954; tools/ is
    # canonical. A future entry needs a reason, not a mute button.
}


def _tracked(pattern: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", pattern],
                         cwd=str(_REPO), capture_output=True, text=True).stdout
    return [p for p in out.split() if p]


def _duplicate_map() -> dict[str, str]:
    """{project-knowledge path: the tracked file elsewhere it duplicates}.

    Content-hashed over EVERY tracked file, recursively, not name-matched
    against one directory. Zero-byte files are skipped: two empty files are
    identical without one being a copy of the other.
    """
    import hashlib

    elsewhere: dict[str, str] = {}
    for rel in _tracked("*"):
        if rel.startswith("project-knowledge/"):
            continue
        try:
            blob = (_REPO / rel).read_bytes()
        except OSError:
            continue
        if not blob:
            continue
        elsewhere.setdefault(hashlib.sha256(blob).hexdigest(), rel)

    assert elsewhere, (
        "no tracked files outside project-knowledge/ -- the denominator is "
        "empty, so this assertion would pass over nothing")

    found = {}
    for rel in _tracked("project-knowledge/*"):
        try:
            blob = (_REPO / rel).read_bytes()
        except OSError:
            continue
        if not blob:
            continue
        twin = elsewhere.get(hashlib.sha256(blob).hexdigest())
        if twin is not None:
            found[rel] = twin
    return found


def test_no_project_knowledge_file_duplicates_a_tracked_file():
    """RED while the mirrors exist: 229 byte-identical duplicates."""
    duplicates = sorted(set(_duplicate_map()) - set(_KNOWN_DUPLICATES))

    assert not duplicates, (
        f"{len(duplicates)} tracked project-knowledge file(s) are "
        f"byte-identical duplicates of a tracked file elsewhere. A second copy of "
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

    Checked against the SAME content-hashed map the forbidding assertion uses,
    not against toolchain/bin by name. The previous version asked only whether
    a same-named file sat in toolchain/bin, which is how bd-scan.py held a
    survivor exception while duplicating tools/bd-scan.py.
    """
    dup = _duplicate_map()
    for rel in sorted(_NOT_A_MIRROR):
        assert rel not in dup, (
            f"{rel} is declared as 'not a mirror' but is byte-identical to "
            f"{dup[rel]}. Delete it and drop the exception.")


def test_no_known_duplicate_is_stale():
    """A frozen pair that stopped being a pair is a licence nobody is watching.

    This is what makes the baseline shrink: retiring a copy fails here until
    its entry is removed, so the list cannot quietly outlive the duplication
    it records.
    """
    dup = _duplicate_map()
    stale = sorted(set(_KNOWN_DUPLICATES) - set(dup))
    assert not stale, (
        f"{len(stale)} frozen pair(s) are no longer duplicates -- remove them "
        f"from _KNOWN_DUPLICATES: {stale}")
    assert _KNOWN_DUPLICATES, (
        "the baseline is empty -- if the duplication really reached zero, "
        "delete the baseline and this test deliberately rather than leaving "
        "an assertion that passes over nothing")


def test_the_toolchain_is_where_the_tools_live():
    """The positive half. Retiring the mirrors must not have retired the
    originals -- a tree with neither copy would pass every assertion above."""
    tools = _tracked("toolchain/bin/bd-*")
    assert len(tools) > 200, (
        f"only {len(tools)} bd-* tools under toolchain/bin; the suite is the "
        f"single source for the executable toolchain and this is far below "
        f"the population every other gate assumes")
