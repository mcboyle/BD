"""v3.66.944 -- STATIC_KB_MANIFEST.json described a tree that no longer exists.

WHAT WENT WRONG. @943 retired the `project-knowledge/` mirror of the executable
toolchain, deleting 234 tracked files. `project-knowledge/STATIC_KB_MANIFEST.json`
is a sha256 attestation over that directory and it was not regenerated, so it
went on naming every deleted file. Measured with the tool's own checker at
`66db5fe`:

    venv/bin/python toolchain/bin/bd-kb-sync check project-knowledge
    -> exit 1, DRIFT: 9 added / 243 removed / 21 changed

Three consumers read that manifest -- `bd-boot` (as a cache key), `bd-kb-sync`
and `bd-consumer-graph` -- so a manifest describing a tree that is gone is not
cosmetic.

THE PART THAT MAKES THIS WORTH A GATE RATHER THAN A ONE-OFF REGEN. Re-derived
name-by-name from git rather than from the checker's summary line:

    rev        tracked   manifest   in-manifest-not-tracked   tracked-not-in-manifest
    8e2b017      355       355                9                        9
    66db5fe      121       355              243                        9

**The pre-retirement row is the finding.** 355 == 355 exactly, and NINE files
were wrong in each direction at the same time. A check comparing COUNTS would
have reported this tree in sync; the equality was arithmetic coincidence, not
agreement. That is CLAUDE.md section 0 in its purest form, and it is why the
assertions below compare SETS and name the offending paths.

The nine were already drifting before @943 and are not its residue: eight are
tracked files the manifest never carried (`SESSION_CARRY.md`,
`AUDIT_2026_07_29.md`, `LESSONS_LEARNED_v3_66_818.md`, five `pending-specs/`),
plus the manifest itself, which is legitimately excluded. The 234 are @943's.

WHY NOTHING CAUGHT IT. Two absences, both measured at `66db5fe`:

  * `grep -rln STATIC_KB_MANIFEST tests/` -> no hits. No test had this file as
    its subject.
  * it is not in `bd-regen-order`, so no cut regenerates it and CI's
    generated-artifact sync check cannot see it.

Nothing regenerated it and nothing checked it, which is exactly how a file
stays wrong for 600 releases while three tools read it as truth.

DELIBERATELY NAME-LEVEL, NOT CONTENT-LEVEL. These assertions compare the SET OF
PATHS, not the recorded sha256 of each. A content gate here would fail on every
edit to `SESSION_CARRY.md` -- which this repo touches on most cuts -- forcing a
manifest regen into cuts that have nothing to do with it. A gate that fires that
often gets switched off, and section 0 counts over-sensitivity as a soundness
bug of equal weight to a false clean. Content drift already has an owner:
`bd-kb-sync check` reports CHANGED, and it is the operator's staging signal
rather than a merge gate.

THE DENOMINATOR IS GIT, NOT THE DISK. `bd_kb_sync.scan()`'s docstring says it
returns "every tracked file under root" and it never consults git -- it walks
the filesystem. Measured consequence: `bd-kb-sync check` reports
`__pycache__/bd-cutcpython-312.pyc` as ADDED in any tree that has ever run the
tools, so the checker can never print IN-SYNC on a working machine. That is
repaired in this cut (scan skips bytecode caches, the same shape as its existing
dot-prefix skip), and it is why the tests below enumerate `git ls-files` instead
of walking the directory.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PK = _REPO / "project-knowledge"
_MANIFEST = _PK / "STATIC_KB_MANIFEST.json"
_TOOL = _REPO / "toolchain" / "bin" / "bd-kb-sync"


def _bd_kb_sync():
    """Import the extensionless tool as a module.

    The exclusion set is READ FROM THE TOOL rather than restated here. A second
    copy of `{STATIC_KB_MANIFEST.json, PROJECT_KNOWLEDGE_UPDATE.md}` in this file
    would be one more thing that can drift from the code it describes -- the
    defect `6_MANIFEST_EXCLUSION_RULES.md` documents for the release-zip sets,
    where a prose copy carried 11 of 29 names while claiming to be verbatim.
    """
    spec = importlib.util.spec_from_loader(
        "_bd_kb_sync", importlib.machinery.SourceFileLoader("_bd_kb_sync", str(_TOOL)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tracked_pk() -> set[str]:
    """Paths under project-knowledge/, relative to it, as git sees them."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "project-knowledge/"],
        cwd=str(_REPO), capture_output=True, text=True, check=True).stdout
    prefix = "project-knowledge/"
    return {p[len(prefix):] for p in out.split("\0") if p.startswith(prefix)}


def _manifest_files() -> dict:
    return json.loads(_MANIFEST.read_text("utf-8"))["files"]


def _membership_delta(tracked: set[str], listed: set[str],
                      excluded: set[str]) -> tuple[list[str], list[str]]:
    """(listed-but-not-tracked, tracked-but-not-listed), honouring exclusions.

    EXTRACTED SO IT CAN BE TESTED DIRECTLY, for the reason @939 recorded: when a
    verdict's comparison lives only inside the test that reads it, a mutation
    severing it from its inputs escapes -- there is no detector for the detector.
    The positive control below is the detector.
    """
    ghosts = sorted(listed - tracked)
    missing = sorted(tracked - listed - excluded)
    return ghosts, missing


# ── the instrument, before anything that depends on it ───────────────────────

def test_the_comparison_actually_compares():
    """Positive control. Synthetic sets whose answer is not in doubt."""
    ghosts, missing = _membership_delta(
        tracked={"kept", "unlisted", "skipme"},
        listed={"kept", "deleted"},
        excluded={"skipme"})
    assert ghosts == ["deleted"], (
        f"the listed-but-gone direction returned {ghosts!r}; that direction is "
        f"the entire subject of this file -- 243 entries naming deleted files.")
    assert missing == ["unlisted"], (
        f"the tracked-but-unlisted direction returned {missing!r}; without it a "
        f"manifest could shrink to nothing and still read as agreeing.")
    assert _membership_delta({"a"}, {"a"}, set()) == ([], []), (
        "identical sets reported a delta -- a gate that fires on a clean tree "
        "gets switched off, which section 0 weighs equally with a false clean.")


def test_both_sides_are_non_empty():
    """Every assertion below passes vacuously over an empty set.

    Stated as its own test rather than as a guard inside the others, because a
    guard that fails takes its own assertion down with it and the reason gets
    read as the finding.
    """
    tracked, listed = _tracked_pk(), set(_manifest_files())
    assert tracked, (
        "git lists no tracked files under project-knowledge/ -- the denominator "
        "is empty and this file proves nothing about the manifest.")
    assert listed, (
        "the manifest records no files at all; bd-kb-sync's own empty-denominator "
        "guard (its exit 2) is the right instrument for that state, not this one.")


def test_the_manifest_header_agrees_with_its_own_body():
    """RED on pristine: `file_count` reads 363 over a `files` dict holding 355.

    Free to check and nothing ever did. `write_manifest` derives the field
    (`"file_count": len(files)`), so the only way they diverge is an edit that
    touched one and not the other -- which means any reader trusting the header
    is off by eight with no way to notice. CLAUDE.md section 1's rule about
    stating a count's denominator, violated inside a single document.
    """
    doc = json.loads(_MANIFEST.read_text("utf-8"))
    assert doc["file_count"] == len(doc["files"]), (
        f"manifest header says file_count={doc['file_count']} but the files dict "
        f"holds {len(doc['files'])}. Regenerate with `bd-kb-sync seed "
        f"project-knowledge --version v<ver>`; do not edit the field by hand.")


# ── the gate ─────────────────────────────────────────────────────────────────

def test_the_manifest_names_no_file_that_is_not_tracked():
    """RED on pristine at 66db5fe: 243 entries, 234 of them @943's mirror files."""
    mod = _bd_kb_sync()
    ghosts, _ = _membership_delta(_tracked_pk(), set(_manifest_files()), mod.UNTRACKED)
    assert not ghosts, (
        f"{len(ghosts)} manifest entr(ies) name a file that is not tracked. The "
        f"manifest is a sha256 attestation over project-knowledge/, and bd-boot "
        f"keys a cache off it, so an entry for a deleted file is an attestation "
        f"about nothing. Regenerate it in the cut that deleted them.\n  "
        + "\n  ".join(ghosts[:20])
        + (f"\n  ... and {len(ghosts) - 20} more" if len(ghosts) > 20 else ""))


def test_every_tracked_file_is_in_the_manifest():
    """RED on pristine: 8 files the manifest never carried.

    The other direction, and the one a count-comparison cannot see at all: at
    8e2b017 both totals read 355 while nine files were wrong each way.
    """
    mod = _bd_kb_sync()
    _, missing = _membership_delta(_tracked_pk(), set(_manifest_files()), mod.UNTRACKED)
    assert not missing, (
        f"{len(missing)} tracked file(s) under project-knowledge/ are absent from "
        f"the manifest, so nothing attests to their content:\n  "
        + "\n  ".join(missing))


def test_the_exclusion_set_is_read_from_the_tool_and_is_small():
    """The exclusions are control artifacts, not a place to hide failures.

    If this set ever grows to swallow real content, the two gates above go quiet
    without anyone editing them -- so the set's SIZE is asserted, not just its
    membership.
    """
    mod = _bd_kb_sync()
    assert mod.UNTRACKED == {"STATIC_KB_MANIFEST.json", "PROJECT_KNOWLEDGE_UPDATE.md"}, (
        f"bd-kb-sync's UNTRACKED set is {sorted(mod.UNTRACKED)!r}. Both members "
        f"are control/output artifacts of the manifest mechanism itself. Adding "
        f"content to this set exempts it from both gates above.")


# ── the scanner's denominator ────────────────────────────────────────────────

def test_scan_does_not_enumerate_bytecode_caches(tmp_path):
    """RED on pristine: `__pycache__` does not start with a dot, so scan() walks it.

    Measured consequence at 66db5fe: `bd-kb-sync check project-knowledge` lists
    `__pycache__/bd-cutcpython-312.pyc` as ADDED, so the checker cannot report
    IN-SYNC on any machine that has run the tools -- and a `pin` taken there
    would write bytecode into the attestation.
    """
    mod = _bd_kb_sync()
    (tmp_path / "real.md").write_text("content", encoding="utf-8")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "x.cpython-312.pyc").write_bytes(b"\x00\x01")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "__pycache__").mkdir()
    (tmp_path / "nested" / "__pycache__" / "y.pyc").write_bytes(b"\x00")

    got = set(mod.scan(str(tmp_path)))
    assert "real.md" in got, (
        f"scan() lost a real file while excluding caches -- {sorted(got)!r}. An "
        f"exclusion that eats content is worse than the noise it removes.")
    assert not [p for p in got if "__pycache__" in p], (
        f"scan() enumerated bytecode: {sorted(p for p in got if '__pycache__' in p)!r}")


def test_scan_still_excludes_its_own_control_artifacts(tmp_path):
    """Regression guard on the pre-existing behaviour the fix must not disturb."""
    mod = _bd_kb_sync()
    for name in ("STATIC_KB_MANIFEST.json", "PROJECT_KNOWLEDGE_UPDATE.md"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    (tmp_path / "kept.md").write_text("x", encoding="utf-8")
    got = set(mod.scan(str(tmp_path)))
    assert got == {"kept.md"}, sorted(got)


# ── the tool's own verdict, end to end ───────────────────────────────────────

def test_the_tools_diff_actually_diffs():
    """Positive control for `bd_kb_sync.diff`, added to close a mutation escape.

    The battery severed diff()'s first return value from its inputs -- replacing
    the set subtraction with a constant empty list -- and the band STAYED GREEN,
    because the only assertion reading it required that list to be empty, which a
    constant empty list satisfies perfectly. That is the third row of CLAUDE.md
    section 6's table: a check whose passing state is indistinguishable from its
    broken state. Every "must be empty" assertion needs a sibling proving the
    producer can be non-empty. The mutant is described rather than spelled, per
    section 0 -- a doc that quotes the defect puts the defect back in the file.
    """
    mod = _bd_kb_sync()
    added, removed, changed = mod.diff(
        {"kept": {"sha256": "a"}, "extra": {"sha256": "b"}},
        {"kept": {"sha256": "a"}, "gone": {"sha256": "c"}})
    assert added == ["extra"], (
        f"diff() reported added={added!r} for a file present only in the walk; "
        f"the membership gate below reads this list and cannot tell a real "
        f"empty from a broken one.")
    assert removed == ["gone"], f"diff() reported removed={removed!r}"
    assert changed == [], (
        f"diff() reported changed={changed!r} for two identical shas -- the "
        f"gate below deliberately ignores `changed`, so a false positive here "
        f"would be invisible there and would fire in bd-kb-sync instead.")

    assert mod.diff({"k": {"sha256": "x"}}, {"k": {"sha256": "y"}}) == ([], [], ["k"]), (
        "diff() did not detect a content change; `changed` is bd-kb-sync's "
        "staging signal and this file's reason for not gating on it.")


def test_the_tools_own_walk_agrees_with_git_on_membership():
    """The gates above enumerate git; this one enumerates the way the TOOL does.

    It exists to catch a disagreement between the two denominators -- precisely
    the gap that let `__pycache__/bd-cutcpython-312.pyc` through, where git said
    one thing and `os.walk` said another and nothing compared them.

    THIS TEST WAS A CONTENT GATE FOR ONE BAND RUN, AND THE BAND KILLED IT. It
    first shelled out to `bd-kb-sync check` and asserted exit 0, which folds
    `changed` into the verdict. It went red on `SESSION_CARRY.md CHANGED` --
    because this very cut edited the register after seeding the manifest. The
    file's own docstring argues that a content gate here fires on most cuts and
    would get switched off; the last assertion in it was a content gate. CLAUDE.md
    section 0: the fix reproduces the shape of the defect, and the author is the
    last person able to see it. It now reads `diff()`'s three lists and asserts
    on two of them, so `changed` stays what bd-kb-sync intends it to be -- the
    operator's staging signal, not a merge gate.
    """
    mod = _bd_kb_sync()
    current = mod.scan(str(_PK))
    recorded = _manifest_files()
    assert current and recorded, (
        f"empty denominator: scan()={len(current)} manifest={len(recorded)}")

    added, removed, changed = mod.diff(current, recorded)
    assert not added, (
        f"the tool's walk finds {len(added)} file(s) the manifest does not "
        f"record: {added[:20]}. If these are tracked, reseed; if they are "
        f"generated, they belong in bd-kb-sync's PRUNE_DIRS/UNTRACKED, not in "
        f"an attestation.")
    assert not removed, (
        f"the manifest records {len(removed)} file(s) the tool's walk cannot "
        f"find: {removed[:20]}")
    # `changed` is deliberately NOT asserted on -- see the docstring.


BD_GATE_SCOPE = "repo-wide"
