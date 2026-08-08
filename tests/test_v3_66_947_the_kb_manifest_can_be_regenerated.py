"""v3.66.947 -- regenerating the static-KB manifest was not idempotent.

REGISTER ITEM 35, and the reason it could not simply be "wire it into
bd-regen-order". @944 gated STATIC_KB_MANIFEST.json's membership but nothing
REGENERATES it, so the staleness it fixed recurs one cut late -- as a red band
blaming the wrong cut rather than an automatic refresh. The obvious fix is a
CHAIN entry. It cannot be, and the reason is a defect CLAUDE.md section 0 names
in its own text:

  "A manifest pin once hashed bytes that included a wall-clock `generated`
   field, so an unchanged tree 'changed' every run. Two sessions nearly
   reconciled a diff that did not exist. A gate that cries wolf gets switched
   off, so over-sensitivity is a soundness bug, not a safe default. Attest over
   CONTENT, not bytes."

Measured at 3f7bc1a on an unchanged tree, seeding three times:

    before  = d1065b4a405b7e4c
    reseed1 = 2981568f6e6042c5
    reseed2 = 56e52a5667ce9942

Every run a different file, and the only difference is `generated`. CI's
generated-artifact check is `bd-regen-order` followed by `git status
--porcelain`, so adding the manifest to the chain in that state would fail
EVERY pull request. That is why it was never wired in, and the register recorded
the symptom without the cause.

TWO LIVE CONSEQUENCES BEYOND THE WIRING, both measured rather than reasoned:

  * `bd-boot` hashes this file as a cache key (`sha256sum ... | cut -c1-12`),
    so a reseed that changed nothing still invalidated its kbsync phase.
  * `bd-boot` also reads `generated` from two manifests to decide which is
    FRESHER. A wall-clock stamp answers "when did someone last run the tool",
    which is not the question -- a reseed over identical content made a stale
    KB look newer than a fresh one.

THE FIX MAKES THE FIELD HONEST RATHER THAN REMOVING IT. `generated` is preserved
when the `files` mapping is unchanged, and moves when it changes. It then records
when the CONTENT last changed, which is what bd-boot's comparison actually wants
and what section 0 means by attesting over content. Deleting the field would
have broken the freshness comparison outright; freezing it would have broken the
other direction. Both halves are asserted below, because a fix proven in only
one direction is the default mistake here and it is invisible -- everything is
green either way.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PY = _REPO / "venv" / "bin" / "python"
_TOOL = _REPO / "toolchain" / "bin" / "bd-kb-sync"
_REGEN = _REPO / "toolchain" / "bin" / "bd-regen-order"
_MANIFEST_NAME = "STATIC_KB_MANIFEST.json"


def _seed(root: Path, version: str = "v9.9.9") -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_PY), str(_TOOL), "seed", str(root), "--version", version],
        capture_output=True, text=True, timeout=300)


def _tick():
    """Cross a whole-second boundary before the next seed.

    THIS FILE READ BACKWARDS ON ITS FIRST RUN WITHOUT IT, and the reason is
    worth more than the tests. `generated` is stamped with
    `isoformat(timespec="seconds")`, so two seeds inside the same second produce
    an IDENTICAL value. On pristine source the idempotence assertion therefore
    PASSED -- over a defect that is live -- while the two "the stamp must still
    move" guards FAILED, which is the exact inverse of the truth. A run seconds
    apart, which is every real one, differs every time; measured at 3f7bc1a:
    d1065b4a405b7e4c -> 2981568f6e6042c5 -> 56e52a5667ce9942.

    Without the tick these assertions are not merely wrong, they are FLAKY:
    green or red depending on whether the run straddles a second boundary. A
    test whose verdict depends on the clock is worse than no test.
    """
    import time
    time.sleep(1.1)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _tree() -> Path:
    d = Path(tempfile.mkdtemp(prefix="kbstable_"))
    (d / "a.md").write_text("alpha\n", encoding="utf-8")
    (d / "b.md").write_text("beta\n", encoding="utf-8")
    return d


# ── idempotence, which is what the chain entry requires ──────────────────────

def test_reseeding_an_unchanged_tree_is_byte_identical():
    """RED on pristine: three seeds, three different files.

    This is the whole blocker. `bd-regen-order` is followed in CI by
    `git status --porcelain` over the generated artifacts, so a chain entry that
    rewrites the file every run fails every PR.
    """
    d = _tree()
    m = d / _MANIFEST_NAME
    _seed(d)
    first = _sha(m)
    _tick()
    _seed(d)
    second = _sha(m)
    _tick()
    _seed(d)
    third = _sha(m)
    assert first == second == third, (
        f"seeding an unchanged tree produced {first}, {second}, {third}. A "
        f"generated artifact that differs from itself cannot be gated by "
        f"`git status` -- CLAUDE.md section 0's cries-wolf defect, which it "
        f"records having already cost two sessions once.")


def test_the_generated_stamp_is_preserved_when_content_is_unchanged():
    """The mechanism behind the assertion above, asserted directly.

    Separate from the byte comparison so a future change that stabilises the
    file some OTHER way (dropping the field, freezing it to a constant) is still
    measured against what the field is supposed to mean.
    """
    d = _tree()
    m = d / _MANIFEST_NAME
    _seed(d)
    before = json.loads(m.read_text())["generated"]
    _tick()
    _seed(d)
    after = json.loads(m.read_text())["generated"]
    assert before == after, (
        f"`generated` moved from {before!r} to {after!r} with no content change")
    assert before, "`generated` is empty; bd-boot compares two of these to "\
                   "decide which manifest is fresher and would see nothing"


def test_the_generated_stamp_MOVES_when_content_changes():
    """The direction that must NOT be lost, and the reason the field survives.

    A fix that froze `generated` to a constant would satisfy every assertion
    above and silently break bd-boot's freshness comparison -- a stale KB would
    read as current forever. Proving only the stability half is the default
    mistake and it is invisible: both states are green.
    """
    d = _tree()
    m = d / _MANIFEST_NAME
    _seed(d)
    before = json.loads(m.read_text())["generated"]
    (d / "c.md").write_text("gamma\n", encoding="utf-8")
    _tick()
    _seed(d)
    doc = json.loads(m.read_text())
    assert doc["generated"] != before, (
        "`generated` did NOT move after a real content change. bd-boot reads "
        "this field from two manifests to decide which is fresher, so a frozen "
        "stamp makes a stale KB read as current indefinitely.")
    assert "c.md" in doc["files"], "the new file never entered the manifest"
    assert doc["file_count"] == len(doc["files"]) == 3


def test_a_changed_file_moves_the_stamp_even_at_the_same_count():
    """Content, not cardinality.

    A `files` comparison keyed on length would pass every test above while
    missing an edit in place -- the same denominator mistake @944 found when the
    manifest and the tree both read 355 with nine files wrong each way.
    """
    d = _tree()
    m = d / _MANIFEST_NAME
    _seed(d)
    before = json.loads(m.read_text())
    (d / "a.md").write_text("alpha CHANGED\n", encoding="utf-8")
    _tick()
    _seed(d)
    after = json.loads(m.read_text())
    assert after["file_count"] == before["file_count"] == 2, "count should not move"
    assert after["files"]["a.md"]["sha256"] != before["files"]["a.md"]["sha256"]
    assert after["generated"] != before["generated"], (
        "an in-place edit left `generated` untouched -- the staleness check is "
        "keyed on the file COUNT rather than on content")


# ── the chain entry itself ───────────────────────────────────────────────────

def _chain_labels() -> list[str]:
    src = _REGEN.read_text("utf-8")
    import ast
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == "CHAIN" for t in node.targets):
            continue
        return [e.elts[0].value for e in node.value.elts]
    return []


def test_the_manifest_is_in_the_regen_chain():
    """RED on pristine: item 35's actual ask.

    Read from the CHAIN literal with AST rather than by grepping the file: the
    module docstring names several artifacts, and a text search would count
    those (CLAUDE.md section 0 -- a comment is inside the denominator of every
    gate that reads source text).
    """
    labels = _chain_labels()
    assert labels, "could not read CHAIN out of bd-regen-order"
    assert any("KB" in l or "MANIFEST" in l.upper() for l in labels), (
        f"the static-KB manifest is not regenerated by bd-regen-order, so it "
        f"goes stale on any project-knowledge change and the @944 gate catches "
        f"it one cut late, blaming the wrong cut. Chain is: {labels}")


def test_the_manifest_step_is_last():
    """It hashes project-knowledge/, so nothing that could write there may
    follow it. Nothing in the chain does today -- this pins that."""
    labels = _chain_labels()
    kb = [l for l in labels if "KB" in l or "MANIFEST" in l.upper()]
    assert kb, "no manifest step to position"
    assert labels[-1] == kb[0], (
        f"the manifest step must run LAST; chain ends with {labels[-1]!r}")


def test_the_real_regen_chain_is_idempotent():
    """The end-to-end property CI depends on, framed correctly.

    THE FIRST VERSION OF THIS TEST ASSERTED THE WRONG THING and the band caught
    it. It ran the chain ONCE and required the manifest to be unchanged -- which
    is only true on a tree that has already been regenerated since its last
    project-knowledge edit. This cut edits SESSION_CARRY.md, so the first regen
    legitimately updated the manifest and the assertion failed over CORRECT
    behaviour. "The artifact never changes" is a property of the author's
    discipline; "regenerating twice changes nothing" is the property of the TOOL,
    and it is the one CI's `git status` check actually rests on.

    So: regenerate, snapshot, regenerate again, compare. The tick guarantees the
    second run is in a different wall-clock second, without which this passes
    over the very defect it exists to catch.
    """
    m = _REPO / "project-knowledge" / _MANIFEST_NAME
    first = subprocess.run(
        [str(_PY), str(_REGEN), "--work", str(_REPO)],
        capture_output=True, text=True, timeout=900)
    assert first.returncode == 0, (first.stdout + first.stderr)[-2000:]
    settled = _sha(m)

    _tick()
    second = subprocess.run(
        [str(_PY), str(_REGEN), "--work", str(_REPO)],
        capture_output=True, text=True, timeout=900)
    assert second.returncode == 0, (second.stdout + second.stderr)[-2000:]
    assert _sha(m) == settled, (
        "bd-regen-order rewrote the manifest on a settled tree. CI runs the "
        "chain and then `git status --porcelain`, so this fails every PR.")
