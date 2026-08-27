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
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_PY = Path(sys.executable)
_TOOL = _REPO / "toolchain" / "bin" / "bd-kb-sync"
_REGEN = _REPO / "toolchain" / "bin" / "bd-regen-order"
_MANIFEST_NAME = "STATIC_KB_MANIFEST.json"


def _assert_disposable_regen_roots(roots: list[Path]) -> None:
    """Require both idempotence passes to share one non-canonical checkout."""
    assert len(roots) == 2, (
        f"expected exactly two regen launches, observed {len(roots)}: {roots}"
    )
    resolved = [root.resolve() for root in roots]
    assert resolved[0] == resolved[1], (
        f"idempotence passes used different work roots: {resolved}"
    )
    canonical = _REPO.resolve()
    escaped = [
        root for root in resolved
        if root == canonical or root.is_relative_to(canonical)
    ]
    assert not escaped, (
        "regen escaped the disposable copy and targeted the canonical checkout: "
        f"{escaped}"
    )


def _git_checkout_populations() -> tuple[list[Path], list[Path]]:
    """Return tracked and relevant untracked inputs as distinct populations."""
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    tracked_result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-C",
            str(_REPO),
            "ls-files",
            "-z",
            "--cached",
        ],
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert tracked_result.returncode == 0, tracked_result.stderr.decode(
        "utf-8", errors="replace"
    )
    untracked_result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-C",
            str(_REPO),
            "ls-files",
            "-z",
            "--others",
            "--exclude-standard",
        ],
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert untracked_result.returncode == 0, untracked_result.stderr.decode(
        "utf-8", errors="replace"
    )
    tracked = [
        Path(os.fsdecode(entry))
        for entry in tracked_result.stdout.split(b"\0")
        if entry
    ]
    visible_untracked = [
        Path(os.fsdecode(entry))
        for entry in untracked_result.stdout.split(b"\0")
        if entry
    ]
    assert len(tracked) == len(set(tracked)) > 0, (
        f"tracked copy denominator is invalid: {len(tracked)} entries, "
        f"{len(set(tracked))} unique"
    )
    assert len(visible_untracked) == len(set(visible_untracked)), (
        f"untracked copy population contains duplicates: {visible_untracked}"
    )
    assert not (set(tracked) & set(visible_untracked)), (
        "tracked and untracked checkout populations overlap"
    )
    environment_roots = {
        Path("venv"),
        Path(".venv"),
        Path("frontend/node_modules"),
        Path("frontend/dist"),
    }
    untracked = [
        path for path in visible_untracked
        if not any(
            path == root or path.is_relative_to(root)
            for root in environment_roots
        )
    ]
    files = tracked + untracked
    unsafe = [
        path for path in files
        if path.is_absolute() or path == Path(".") or ".." in path.parts
    ]
    assert not unsafe, f"Git returned unsafe checkout paths: {unsafe}"
    unavailable = [
        path for path in files
        if not (_REPO / path).is_file() and not (_REPO / path).is_symlink()
    ]
    assert not unavailable, (
        f"Git-visible copy inputs became unavailable: {unavailable}"
    )
    return tracked, untracked


def _git_in_disposable_checkout(work: Path, *arguments: str) -> None:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", "--no-optional-locks", *arguments],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0, (
        f"disposable Git setup failed for {arguments}: {result.stderr[-2000:]}"
    )


def _copy_checkout_for_regen(destination: Path) -> Path:
    """Copy current test inputs and create an owned index for Git-based scans."""
    canonical = _REPO.resolve()
    resolved_destination = destination.resolve()
    assert resolved_destination != canonical
    assert not resolved_destination.is_relative_to(canonical), (
        f"disposable checkout was placed inside the canonical tree: {destination}"
    )
    assert not destination.exists(), f"disposable checkout already exists: {destination}"

    tracked, untracked = _git_checkout_populations()
    # SCAFFOLDING IS NOT PART OF THE SUBJECT, AND IT IS FILTERED AT THE SOURCE.
    # bd-codex-cut.sh and bd-verify-cut.sh create ABSOLUTE symlinks for venv,
    # frontend/node_modules and frontend/dist so a fresh worktree can run at all.
    # They are gitignored tooling artifacts, not repository content, and including
    # them made this test refuse its own harness. bd-prepush.sh already excludes
    # exactly this set for exactly this reason; the list is kept identical.
    # Filtering `tracked` and `untracked` HERE rather than only the copy list keeps
    # every downstream consumer consistent -- the copy, the `add` pathspec and the
    # indexed_files reconciliation must quantify over ONE population, and filtering
    # only one of them is how this first failed.
    _SCAFFOLD = ("venv", "frontend/node_modules", "frontend/dist")

    def _is_scaffold(path) -> bool:
        text = Path(path).as_posix()
        return any(text == s or text.startswith(s + "/") for s in _SCAFFOLD)

    _before = len(tracked) + len(untracked)
    tracked = [p for p in tracked if not _is_scaffold(p)]
    untracked = [p for p in untracked if not _is_scaffold(p)]
    files = tracked + untracked
    assert files and len(files) == len(set(files))
    # The exclusion must not be able to empty the population it filters, and it
    # must actually be an exclusion rather than a no-op rename.
    assert 0 <= _before - len(files) < _before, (
        f"scaffolding filter removed {_before - len(files)} of {_before} paths"
    )
    destination.mkdir(parents=True)
    copied = 0
    for relative in files:
        source = _REPO / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            link_target = os.readlink(source)
            assert not Path(link_target).is_absolute(), (
                f"absolute symlink cannot be isolated: {relative} -> {link_target}"
            )
            os.symlink(link_target, target)
            assert target.resolve(strict=False).is_relative_to(resolved_destination), (
                f"symlink escapes disposable checkout: {relative} -> {link_target}"
            )
        else:
            shutil.copy2(source, target)
        copied += 1

    assert copied == len(files) > 0, (
        f"copied {copied} of {len(files)} Git-visible checkout files"
    )
    _git_in_disposable_checkout(destination, "init", "--quiet")
    for start in range(0, len(tracked), 256):
        stop = min(len(tracked), start + 256)
        chunk = [path.as_posix() for path in tracked[start:stop]]
        assert chunk
        _git_in_disposable_checkout(
            destination, "add", "--intent-to-add", "--force", "--", *chunk
        )

    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    indexed = subprocess.run(
        ["git", "--no-optional-locks", "ls-files", "-z"],
        cwd=destination,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert indexed.returncode == 0, indexed.stderr.decode("utf-8", errors="replace")
    indexed_files = {
        Path(os.fsdecode(entry))
        for entry in indexed.stdout.split(b"\0")
        if entry
    }
    assert indexed_files == set(tracked), (
        f"disposable Git index does not match tracked denominator: "
        f"missing={sorted(set(tracked) - indexed_files)}, "
        f"extra={sorted(indexed_files - set(tracked))}"
    )
    unindexed = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "ls-files",
            "-z",
            "--others",
            "--exclude-standard",
        ],
        cwd=destination,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert unindexed.returncode == 0, unindexed.stderr.decode(
        "utf-8", errors="replace"
    )
    unindexed_files = {
        Path(os.fsdecode(entry))
        for entry in unindexed.stdout.split(b"\0")
        if entry
    }
    assert unindexed_files == set(untracked), (
        f"disposable untracked population does not match its source: "
        f"missing={sorted(set(untracked) - unindexed_files)}, "
        f"extra={sorted(unindexed_files - set(untracked))}"
    )

    required = [
        Path("toolchain/bin/bd-regen-order"),
        Path("project-knowledge") / _MANIFEST_NAME,
    ]
    assert len(required) == 2
    assert set(required) <= set(tracked), (
        f"required regen inputs are absent from the tracked denominator: {required}"
    )
    for relative in required:
        copied_path = destination / relative
        canonical_path = _REPO / relative
        assert copied_path.is_file(), f"required regen input was not copied: {relative}"
        assert copied_path.read_bytes() == canonical_path.read_bytes(), (
            f"copied regen input differs from canonical bytes: {relative}"
        )
    return destination


def _regen_environment(owned_root: Path) -> dict[str, str]:
    """Confine generator home, cache, bytecode and temporary state."""
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    for name in ("home", "tmp", "cache", "pycache"):
        (owned_root / name).mkdir()
    env.update(
        {
            "BD_HOME": str(owned_root / "home"),
            "HOME": str(owned_root / "home"),
            "TMPDIR": str(owned_root / "tmp"),
            "XDG_CACHE_HOME": str(owned_root / "cache"),
            "PYTHONPYCACHEPREFIX": str(owned_root / "pycache"),
        }
    )
    return env


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


def test_subprocesses_use_the_running_repository_interpreter(
    monkeypatch, tmp_path: Path,
):
    """A Git worktree need not contain its own untracked ``venv`` directory."""
    assert _PY == Path(sys.executable)
    probe = subprocess.run(
        [str(_PY), "-c", "from pathlib import Path; import sys; "
         "print(Path(sys.executable).resolve())"],
        capture_output=True, text=True, timeout=30,
    )
    assert probe.returncode == 0, probe.stderr
    assert Path(probe.stdout.strip()) == Path(sys.executable).resolve()

    # Replay the binding under a different executable identity. This makes a
    # worktree-local ``venv/bin/python`` substitution observably wrong even in
    # the canonical checkout, where that path and sys.executable coincide.
    alternate = tmp_path / "interpreter-selected-by-the-runner"
    with monkeypatch.context() as patch:
        patch.setattr(sys, "executable", str(alternate))
        replayed = runpy.run_path(
            str(Path(__file__).resolve()), run_name="_kb_replay"
        )
    assert replayed["_PY"] == alternate


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
    canonical_manifest = _REPO / "project-knowledge" / _MANIFEST_NAME
    canonical_before = canonical_manifest.read_bytes()
    assert canonical_before, "canonical manifest precondition is empty"

    with tempfile.TemporaryDirectory(prefix="bd_regen_idempotence_") as raw_tmp:
        owned_root = Path(raw_tmp)
        work = _copy_checkout_for_regen(owned_root / "checkout")
        env = _regen_environment(owned_root)
        m = work / "project-knowledge" / _MANIFEST_NAME
        assert m.read_bytes() == canonical_before, (
            "disposable manifest did not start with the canonical bytes"
        )

        first = subprocess.run(
            [str(_PY), str(_REGEN), "--work", str(work)],
            capture_output=True, text=True, timeout=900, env=env)
        assert first.returncode == 0, (first.stdout + first.stderr)[-2000:]
        settled = _sha(m)

        _tick()
        second = subprocess.run(
            [str(_PY), str(_REGEN), "--work", str(work)],
            capture_output=True, text=True, timeout=900, env=env)
        assert second.returncode == 0, (second.stdout + second.stderr)[-2000:]
        assert _sha(m) == settled, (
            "bd-regen-order rewrote the manifest on a settled tree. CI runs the "
            "chain and then `git status --porcelain`, so this fails every PR.")

    assert canonical_manifest.read_bytes() == canonical_before, (
        "the disposable regen test changed the canonical manifest"
    )


def test_the_idempotence_test_runs_both_regens_in_one_disposable_copy(monkeypatch):
    """The safety gate must not reproduce the checkout mutation it detects."""
    real_run = subprocess.run
    observed_roots: list[Path] = []
    observed_shapes: list[tuple[bool, bool]] = []
    ticks: list[None] = []
    canonical_manifest = _REPO / "project-knowledge" / _MANIFEST_NAME
    before = canonical_manifest.read_bytes()
    assert before, "canonical manifest precondition is empty"

    def recording_run(argv, *args, **kwargs):
        if len(argv) >= 2 and Path(argv[1]).name == _REGEN.name:
            work_flags = [index for index, value in enumerate(argv) if value == "--work"]
            assert work_flags == [2], f"regen command has no unique --work: {argv}"
            assert len(argv) == 4, f"unexpected regen command shape: {argv}"
            work = Path(argv[3])
            observed_roots.append(work)
            observed_shapes.append(
                (
                    (work / "toolchain" / "bin" / "bd-regen-order").is_file(),
                    (work / "project-knowledge" / _MANIFEST_NAME).is_file(),
                )
            )
            return subprocess.CompletedProcess(argv, 0, "regen intercepted\n", "")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_run)
    monkeypatch.setattr(
        sys.modules[__name__], "_tick", lambda: ticks.append(None)
    )

    test_the_real_regen_chain_is_idempotent()

    assert len(ticks) == 1, f"expected exactly one inter-run tick, got {len(ticks)}"
    assert len(observed_shapes) == 2
    assert observed_shapes == [(True, True), (True, True)], (
        f"regen work-root preconditions were not built twice: {observed_shapes}"
    )
    assert canonical_manifest.read_bytes() == before, (
        "the isolation regression test itself changed the canonical manifest"
    )
    _assert_disposable_regen_roots(observed_roots)


def test_disposable_root_verdict_rejects_the_canonical_checkout():
    """Negative control: the filed two-pass shape reaches the refusal."""
    roots = [_REPO, _REPO]
    assert len(roots) == 2 and all(root == _REPO for root in roots)
    with pytest.raises(
        AssertionError,
        match="regen escaped the disposable copy and targeted the canonical checkout",
    ):
        _assert_disposable_regen_roots(roots)


def test_transform_control_imports_without_asserting_regen_isolation():
    """A valid work-root transform still imports when isolation is not judged."""
    import importlib

    imported = importlib.import_module(__name__)
    assert imported._REGEN.is_file()
