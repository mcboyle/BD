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

import collections
import hashlib
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PK = REPO_ROOT / "project-knowledge"

# If the mirror population collapses below this, the comparison has lost its
# subject and a green result would mean nothing. CLAUDE.md section 0: a gate
# that cannot see the thing it is asked about reports OK, and that is worse
# than no gate.
#
# 150 was the v3.66.818 figure and it was FAR too slack: the pairing was
# matching 255 of 258 mirrors and 255 >= 150 cleared the floor with room to
# spare, so the floor could not see a narrowing it was written to catch. A
# floor alone never can -- that is what the coverage canary below is for.
# Measured at v3.66.870: 258 distinct mirrors, 260 flat pairs.
_MIN_PLAUSIBLE_MIRRORS = 250


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _is_mirror_subject(path: Path) -> bool:
    """Is this project-knowledge file a SOURCE file that could have a mirror?

    Same predicate bd-pk-mirror uses: a .py suffix, or no suffix at all with a
    `#!` shebang. That second clause is load-bearing -- 239 of the mirrors are
    extensionless `bd-*` scripts, and the tool literally named `bd` was
    invisible to the old `startswith("bd-")` filter.

    Deliberately NOT "any basename that exists elsewhere". Five PK docs have
    same-named tracked files elsewhere with DIFFERENT content -- README.md,
    SANDBOX.md, AUTOMATION_POLICY.md, BDSUITE_CHANGELOG.md and
    DECOMPOSITION_PROGRAM_ROADMAP.md -- so the naive widening buys five instant
    permanent false positives on a defect that does not exist. This predicate
    excludes all five STRUCTURALLY rather than by a skip-list that goes stale.
    """
    if path.suffix == ".py":
        return True
    if path.suffix == "":
        try:
            return path.read_bytes()[:2] == b"#!"
        except OSError:
            return False
    return False


def _counterpart_index(root: Path) -> dict[str, list[Path]]:
    """basename -> every TRACKED file outside project-knowledge/ with that name.

    Tracked-only is load-bearing, not a convenience: it excludes the untracked
    `audit-venv/` (which carries its own README.md) and `venv/` without a
    skip-list that could go stale. It also replaces the old hardcoded
    SOURCE_DIRS triple, which had no entry for tools/audit/witnesses/ and so
    could not see cap01_witnesses.py or run01_witnesses.py.
    """
    out = subprocess.run(["git", "ls-files", "-z"], cwd=str(root),
                         capture_output=True, text=True, check=True).stdout
    idx: dict[str, list[Path]] = collections.defaultdict(list)
    for rel in out.split("\0"):
        if not rel or rel.startswith("project-knowledge/"):
            continue
        idx[rel.rsplit("/", 1)[-1]].append(root / rel)
    return idx


def mirror_pairs(root: Path = REPO_ROOT) -> list[tuple[Path, Path]]:
    """Every (mirror, counterpart) pair. FLAT -- one row per counterpart.

    Derived from the tree, never hardcoded -- a frozen list would itself go
    stale, which is the failure this file is about.

    ONE ROW PER COUNTERPART, not per mirror. The previous version `break`ed at
    the first matching SOURCE_DIR, so a file present in BOTH toolchain/bin and
    tools/ was compared against exactly one of them and the other was
    unexamined. Two basenames are in that position today (bd-audit-gate.py,
    bd-triage.py); drifting the unexamined copy left this suite 3-passed while
    bd-pk-mirror --check exited 1.

    `root` is a parameter so a fixture tree can be tested. It defaults to the
    real repo, so no caller changes.
    """
    pk = root / "project-knowledge"
    idx = _counterpart_index(root)
    pairs: list[tuple[Path, Path]] = []
    for candidate in sorted(pk.iterdir()):
        if not candidate.is_file() or not _is_mirror_subject(candidate):
            continue
        for origin in sorted(idx.get(candidate.name, [])):
            pairs.append((candidate, origin))
    return pairs


def ambiguous_origins(root: Path = REPO_ROOT) -> list[tuple[str, list[Path]]]:
    """Mirrored basenames whose MULTIPLE origins disagree with each other.

    Separate from drift on purpose: this is a fact about the two origins, not
    about the mirror, and it has a different repair.
    """
    by_name: dict[str, list[Path]] = collections.defaultdict(list)
    for mirror, origin in mirror_pairs(root):
        by_name[mirror.name].append(origin)
    return [(name, origins) for name, origins in sorted(by_name.items())
            if len(origins) > 1 and len({_sha(o) for o in origins}) > 1]


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


def test_every_true_mirror_is_in_the_pair_set():
    """COVERAGE canary -- the floor above cannot catch a narrowing that clears it.

    Re-derives the mirror population INDEPENDENTLY of mirror_pairs() and
    asserts they agree. That independence is the whole point: measured at
    v3.66.869, mirror_pairs() returned 255 while the true population was 258,
    and the floor said nothing because 255 >= 150. The three it could not see
    were `bd` (excluded by a `startswith("bd-")` name filter -- the tool is
    named `bd`), and cap01_witnesses.py / run01_witnesses.py (their origin
    tools/audit/witnesses/ had no SOURCE_DIRS entry).
    """
    out = subprocess.run(["git", "ls-files", "-z"], cwd=str(REPO_ROOT),
                         capture_output=True, text=True, check=True).stdout
    outside = {rel.rsplit("/", 1)[-1] for rel in out.split("\0")
               if rel and not rel.startswith("project-knowledge/")}
    expected = {c.name for c in sorted(PK.iterdir())
                if c.is_file() and _is_mirror_subject(c) and c.name in outside}
    actual = {m.name for m, _ in mirror_pairs()}
    assert actual == expected, (
        f"the pairing sees {len(actual)} mirrors; an independent derivation "
        f"finds {len(expected)}.\n"
        f"  missed by the pairing: {sorted(expected - actual)}\n"
        f"  claimed but not real:  {sorted(actual - expected)}\n"
        "A mirror the gate cannot see is a mirror nothing compares."
    )


def test_the_pairing_compares_every_counterpart_not_just_the_first():
    """The `break` this replaced hid the second copy of a two-origin tool.

    Behaviour assertion against the shipping tree, not a signature probe: a
    fixture-tree test would have been red merely because the old
    mirror_pairs() took no `root` kwarg (TypeError), which is an incomplete
    implementation raising the same exception as the defect -- CLAUDE.md 2a.
    Measured at v3.66.869: count was 1, and both toolchain/bin/bd-audit-gate.py
    and tools/bd-audit-gate.py exist.
    """
    names = [m.name for m, _ in mirror_pairs()]
    for dup in ("bd-audit-gate.py", "bd-triage.py"):
        origins = sorted(str(o.relative_to(REPO_ROOT))
                         for m, o in mirror_pairs() if m.name == dup)
        assert names.count(dup) == 2, (
            f"{dup} exists under BOTH toolchain/bin and tools, and the pairing "
            f"produced {names.count(dup)} row(s): {origins}. The unexamined "
            f"copy can drift with this suite still green."
        )


def test_the_pairing_admits_dotless_tools_and_excludes_same_named_docs():
    """Both directions of the predicate, on the real tree.

    The widening had to admit `bd` without admitting README.md. Asserting only
    the first direction would pass against a predicate that admits everything,
    which is the over-correction: five PK docs have same-named tracked files
    elsewhere with different content, and a gate that flags them goes instantly
    and permanently red on a defect that does not exist.
    """
    names = {m.name for m, _ in mirror_pairs()}
    for admitted in ("bd", "cap01_witnesses.py", "run01_witnesses.py"):
        assert admitted in names, (
            f"{admitted} is a real mirror and the pairing does not see it.")
    for excluded in ("README.md", "SANDBOX.md", "AUTOMATION_POLICY.md",
                     "BDSUITE_CHANGELOG.md",
                     "DECOMPOSITION_PROGRAM_ROADMAP.md"):
        assert excluded not in names, (
            f"{excluded} is a DOC that happens to share a basename with a "
            f"tracked file elsewhere; their contents differ by design. "
            f"Admitting it makes this suite permanently red.")
    # PK-ONLY must stay HEALTHY, not become "unknown therefore fail". 18 PK
    # files have no counterpart anywhere; the temptation when adding a third
    # verdict is to call absence inconclusive, which destroys the instrument.
    assert "render_check.py" not in names, (
        "render_check.py is PK-only -- it has no counterpart, which is not a "
        "drift and must not be reported as one.")


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


def test_a_mirror_whose_origins_disagree_is_reported_as_ambiguous():
    """The third verdict, and it needs its OWN message -- not the drift one.

    Comparing every counterpart instead of the first creates a case the old
    logic could not express: the two origins of a two-copy tool disagreeing
    with EACH OTHER. That is not mirror drift, and the drift remediation
    (`cp` origin -> PK) is the wrong repair for it -- it would pick one origin
    arbitrarily and silently discard the other's changes.

    Note what this asserts and what it does not. It does NOT declare that
    tools/bd-audit-gate.py must stay byte-equal to toolchain/bin/bd-audit-gate
    .py forever; nothing in the tree declares that, and creating an undeclared
    invariant as a side effect of fixing the `break` would be its own defect.
    It says: when the origins differ, SAY SO, name both with their shas, and
    ask for a reconciliation rather than a re-sync. Measured at v3.66.870: both
    two-origin tools have all copies byte-identical, so this is green today.
    """
    ambiguous = ambiguous_origins()

    if ambiguous:
        lines = [
            f"{len(ambiguous)} mirrored basename(s) have ORIGINS THAT DISAGREE "
            f"WITH EACH OTHER.",
            "",
            "This is not mirror drift and `cp <origin> project-knowledge/` is "
            "the WRONG repair -- it would pick one origin arbitrarily and "
            "discard the other's changes. Reconcile the two origins first, "
            "then re-sync the mirror from the reconciled copy.",
            "",
        ]
        for name, origins in ambiguous:
            lines.append(f"  {name}:")
            for o in sorted(origins):
                lines.append(f"    {o.relative_to(REPO_ROOT)}  {_sha(o)}")
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


def test_ambiguous_detection_fires_on_a_tree_that_has_one():
    """The real tree has NO ambiguous case, so the branch above is unconstrained
    by it -- a mutation battery proved that directly: disabling the detection
    entirely left the suite green. A verdict nothing can exercise is not a
    verdict, so this drives it against a synthetic tree.

    That is what the `root` parameter is for. Both directions in one fixture:
    identical origins must stay SILENT, disagreeing origins must be REPORTED.
    Asserting only the second would pass against a detector that fires always,
    which is the over-correction -- it would make every two-origin tool a
    permanent failure.
    """
    import subprocess as _sp
    import tempfile

    def _tree(td: str, tools_body: bytes):
        root = Path(td)
        for d in ("project-knowledge", "toolchain/bin", "tools"):
            (root / d).mkdir(parents=True, exist_ok=True)
        (root / "project-knowledge" / "x.py").write_bytes(b"A\n")
        (root / "toolchain" / "bin" / "x.py").write_bytes(b"A\n")
        (root / "tools" / "x.py").write_bytes(tools_body)
        # git ls-files is the counterpart index, so the fixture must be a repo
        # with the files STAGED -- an unstaged file is invisible to it, and a
        # fixture the instrument cannot see would make this test vacuous.
        _sp.run(["git", "init", "-q"], cwd=td, check=True)
        _sp.run(["git", "add", "-A"], cwd=td, check=True)
        listed = _sp.run(["git", "ls-files"], cwd=td, capture_output=True,
                         text=True, check=True).stdout.split()
        assert "tools/x.py" in listed, listed
        return root

    with tempfile.TemporaryDirectory() as td:
        root = _tree(td, b"A\n")            # origins AGREE
        assert [p[0].name for p in mirror_pairs(root)] == ["x.py", "x.py"], (
            "the fixture did not produce two counterparts; the case this test "
            "exists to exercise is not present.")
        assert ambiguous_origins(root) == [], (
            "identical origins were reported as ambiguous -- the detector "
            "fires on the mere existence of two origins, which would make "
            "every two-copy tool a permanent failure.")

    with tempfile.TemporaryDirectory() as td:
        root = _tree(td, b"B\n")            # origins DISAGREE
        found = ambiguous_origins(root)
        assert [n for n, _ in found] == ["x.py"], (
            "origins with different content were not reported as ambiguous: "
            "%r" % (found,))
        assert len(found[0][1]) == 2, found
