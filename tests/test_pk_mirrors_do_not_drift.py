"""A mirrored file must AGREE with its original, not merely exist beside it.

`project-knowledge/` carries a second copy of much of the executable toolchain
so the directory can be uploaded as a self-contained bundle. Those copies are
not generated at build time -- they were committed once and then left behind,
and nothing compared them again.

The cost of that is not hypothetical. At v3.66.818, with the tree otherwise
green:

    venv/bin/python project-knowledge/bd-guardcheck --tree .
      exit=0   "0 ok, 0 drifted, 0 missing."
    venv/bin/python toolchain/bin/bd-guardcheck   --tree .
      exit=0   "7 ok, 0 drifted, 0 missing, 0 unpinned."

The project-knowledge copy is the pre-v3.66.818 build -- the exact defect
CLAUDE.md section 2 records as FIXED, still shipping, still exiting 0 while
certifying nothing. A cut repaired one copy of a two-copy tool and the tree
reported success.

WHY THIS TEST EXISTS AND AN `ls` DOES NOT:

An earlier pass asked whether every toolchain tool HAS a mirror, using set
subtraction over filenames, and concluded mirroring was optional because two
names were unmatched. That question is not this question. Existence was never
the risk; AGREEMENT was. The instrument fixes the denominator, the predicate
fixes the subject -- and comparing names answers about the denominator while
saying nothing about the subject. This test compares content.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PK = REPO_ROOT / "project-knowledge"

# Where a mirrored basename may legitimately originate. Order matters only for
# reporting; a basename is expected to resolve in exactly one of these.
SOURCE_DIRS = (
    REPO_ROOT / "toolchain" / "bin",
    REPO_ROOT / "tools",
    REPO_ROOT / "tools" / "decomp",
)

# If the mirror population collapses below this, the comparison has lost its
# subject and a green result would mean nothing. CLAUDE.md section 0: a gate
# that cannot see the thing it is asked about reports OK, and that is worse
# than no gate. Measured at v3.66.818: 245 bd-* mirrors + 42 .py mirrors.
_MIN_PLAUSIBLE_MIRRORS = 150


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def mirror_pairs() -> list[tuple[Path, Path]]:
    """Every project-knowledge file that also exists under a source dir.

    Derived from the tree, never hardcoded -- a frozen list would itself go
    stale, which is the failure this file is about.
    """
    pairs: list[tuple[Path, Path]] = []
    for candidate in sorted(PK.iterdir()):
        if not candidate.is_file():
            continue
        if not (candidate.name.startswith("bd-") or candidate.suffix == ".py"):
            continue
        for src_dir in SOURCE_DIRS:
            origin = src_dir / candidate.name
            if origin.is_file():
                pairs.append((candidate, origin))
                break
    return pairs


def test_the_mirror_set_is_large_enough_to_be_meaningful():
    """Denominator canary. Runs before the comparison it protects."""
    pairs = mirror_pairs()
    assert len(pairs) >= _MIN_PLAUSIBLE_MIRRORS, (
        f"only {len(pairs)} mirror pairs found under {PK}; expected at least "
        f"{_MIN_PLAUSIBLE_MIRRORS}. Either the mirrors were deliberately "
        f"removed -- in which case delete this test in the same cut, and say "
        f"so -- or the pairing logic stopped matching and every drift check "
        f"below is now passing over an empty set."
    )


def test_no_project_knowledge_mirror_has_drifted_from_its_origin():
    pairs = mirror_pairs()
    drifted = [
        (mirror, origin)
        for mirror, origin in pairs
        if _sha(mirror) != _sha(origin)
    ]
    if drifted:
        lines = [
            f"{len(drifted)} of {len(pairs)} project-knowledge mirrors disagree "
            f"with the file they mirror.",
            "",
            "A drifted mirror is worse than a missing one: it runs, it exits 0, "
            "and it answers a question about a version of the tool that is no "
            "longer the one anybody edits.",
            "",
        ]
        for mirror, origin in drifted:
            lines.append(
                f"  {mirror.relative_to(REPO_ROOT)}  {_sha(mirror)}"
                f"  !=  {origin.relative_to(REPO_ROOT)}  {_sha(origin)}"
            )
        lines += [
            "",
            "Re-sync from the origin (the copy under toolchain/bin or tools is "
            "authoritative -- the mirrors froze at the initial import):",
            "",
        ]
        for mirror, origin in drifted:
            lines.append(
                f"  cp {origin.relative_to(REPO_ROOT)} "
                f"{mirror.relative_to(REPO_ROOT)}"
            )
        pytest.fail("\n".join(lines))


def test_the_known_guardcheck_regression_specifically_cannot_return():
    """A named regression test for the instance that motivated the gate.

    The general test above would catch this too. This one names it so that a
    future reader who deletes the mirrors, or narrows the pairing, is told
    exactly which failure they are re-opening.
    """
    mirror = PK / "bd-guardcheck"
    origin = REPO_ROOT / "toolchain" / "bin" / "bd-guardcheck"
    if not mirror.is_file():
        pytest.skip("project-knowledge/bd-guardcheck no longer exists")
    assert origin.is_file(), "toolchain/bin/bd-guardcheck is missing"
    assert _sha(mirror) == _sha(origin), (
        "project-knowledge/bd-guardcheck has drifted from toolchain/bin again. "
        "The pre-v3.66.818 build of this tool reports '0 ok, 0 drifted, "
        "0 missing' and EXITS 0 on a clean tree -- it cannot see the seven "
        "files it certifies and reports success anyway. A zero-in-every-bucket "
        "summary is a failure signal, not a pass."
    )
