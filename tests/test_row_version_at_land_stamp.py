"""The two positive land-time stamps: one real ``--write`` over a reviewed tree.

Each arm needs the full bd-regen-order chain, measured at about 100 s per
stamp on a 24-core host, so it lives apart from
tests/test_row_version_at_land.py (the refusal arms, same helpers) and shards
on its own (tools/ci_shards.py resolves it by glob; it is not PINNED).
Neither bd-bump nor bd-regen-order is stubbed; the positive case cannot pass
over fake generator output. The chain is paid once per module:
the fixture clones, brings the generated baseline current and stamps, and each
test then works on a private byte copy of that stamped tree. The receipt names
no path, so a copy replays exactly as the tree it was taken from.
"""
from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import pytest

from _land_trio import (
    RELEASE_PATHS, TITLE, _STAMP_ITEM_S, _WHOLE_TREE_STEP_S, _clean_generated_baseline, _git,
    _next, _receipt, _refused_without_writes, _snapshot, _stamp, _version, discard_tree,
    make_tree,
)

BD_GATE_SCOPE = "repo-wide"
BODY = "Reviewed behavior is stamped at landing.\n"


@pytest.fixture(scope="module")
def stamped(tmp_path_factory):
    """One reviewed clone, baseline current, stamped once with BODY."""
    base = tmp_path_factory.mktemp("stamped")
    work = make_tree(base)
    _clean_generated_baseline(work)
    old = _version(work)
    head = _git(work, "rev-parse", "HEAD")
    body = base / "body.txt"
    body.write_text(BODY)
    cp = _stamp(work, "--body", str(body), "--write", timeout=_WHOLE_TREE_STEP_S)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    yield SimpleNamespace(work=work, old=old, new=_next(old), head=head, stamp=cp,
                          status=_git(work, "status", "--porcelain"))
    discard_tree(work)


@pytest.fixture
def tree(stamped, tmp_path):
    """A private byte copy of the stamped tree and of its private BD_HOME."""
    work = tmp_path / "work"
    shutil.copytree(stamped.work, work, symlinks=True)
    home = stamped.work.parent / (stamped.work.name + "-home")
    if home.is_dir():
        shutil.copytree(home, work.parent / (work.name + "-home"), symlinks=True)
    # The copied index carries the source's stat data; one status refreshes
    # it before any snapshot is taken, and proves the copy is the stamped tree.
    assert _git(work, "status", "--porcelain") == stamped.status
    yield work
    discard_tree(work)


@pytest.fixture
def body(tmp_path):
    path = tmp_path / "body.txt"
    path.write_text(BODY)
    return path


@pytest.mark.timeout(_STAMP_ITEM_S)
def test_real_stamp_stages_exactly_five_paths_and_replays_byte_identically(stamped, tree, body):
    """Omitting a generator, moving an extra path, or rebumping a replay fails here."""
    old, new, head, cp = stamped.old, stamped.new, stamped.head, stamped.stamp
    args = ["--body", str(body), "--write"]
    assert cp.returncode == 0, cp.stdout + cp.stderr
    changed = _git(tree, "diff", "--cached", "--name-only").splitlines()
    assert len(changed) == 5, changed
    assert set(changed) == RELEASE_PATHS
    assert _git(tree, "diff", "--name-only") == ""
    assert _git(tree, "ls-files", "--others", "--exclude-standard") == ""
    assert _git(tree, "rev-parse", "HEAD") == head, "stamp must not commit implicitly"
    assert _version(tree) == new
    manifest = json.loads((tree / "project-knowledge/STATIC_KB_MANIFEST.json").read_text())
    assert manifest["version_context"] == "v" + new
    pin_index = json.loads((tree / "PIN_INDEX.json").read_text())
    pins = [pin for pin in pin_index["pins"] if pin["form"] == "version"]
    assert [(pin["file"], pin["value"]) for pin in pins] == [
        ("tests/test_settings_center_slice4.py", new)]
    changelog = (tree / "CHANGELOG.md").read_text()
    assert f"## v{new} - {TITLE}\n\n{body.read_text()}" in changelog
    assert changelog.count(f"## v{new} - ") == 1
    stamp_lines = [line for line in cp.stdout.splitlines() if line.startswith("TRIO-STAMPED ")]
    assert stamp_lines == [f"TRIO-STAMPED {old}->{new} files=5 tree={_git(tree, 'write-tree')}"]
    assert cp.stdout.splitlines().count(f"trio: {old}->{new} (bd-land-trio)") == 1
    assert _receipt(tree).is_file(), "stamp needs a private Git receipt for safe replay"
    before = _snapshot(tree)
    replay = _stamp(tree, *args)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert _snapshot(tree) == before, "identical replay must not change bytes/index/refs"
    check = _stamp(tree, "--body", str(body), "--check")
    assert check.returncode == 0, check.stdout + check.stderr
    assert _snapshot(tree) == before



@pytest.mark.timeout(_STAMP_ITEM_S)
def test_pending_stamp_rejects_changed_inputs_head_main_and_foreign_dirt(stamped, tree, body):
    """A replay receipt cannot authorize a different request or integration tree."""
    cp = stamped.stamp
    args = ["--body", str(body), "--write"]
    assert cp.returncode == 0, cp.stdout + cp.stderr

    _refused_without_writes(tree, "DIRTY-TREE", *args, title="Different title")
    body.write_text("Different body.\n")
    _refused_without_writes(tree, "DIRTY-TREE", *args)
    body.write_text(BODY)

    foreign = tree / "unreviewed-after-stamp.txt"
    foreign.write_text("Not covered by the receipt.\n")
    _refused_without_writes(tree, "DIRTY-TREE", *args)
    foreign.unlink()

    main = _git(tree, "rev-parse", "origin/main")
    moved_main = _git(tree, "commit-tree", "origin/main^{tree}", "-p", main,
                      "-m", "Sibling landed after stamp")
    _git(tree, "update-ref", "refs/remotes/origin/main", moved_main)
    _refused_without_writes(tree, "STALE-STAMP", *args)
    _git(tree, "update-ref", "refs/remotes/origin/main", main)

    head = _git(tree, "rev-parse", "HEAD")
    moved_head = _git(tree, "commit-tree", "HEAD^{tree}", "-p", head,
                      "-m", "Reviewed HEAD changed after stamp")
    _git(tree, "update-ref", "HEAD", moved_head)
    before = _snapshot(tree)
    cp = _stamp(tree, *args)
    assert cp.returncode == 3, cp.stdout + cp.stderr
    assert _snapshot(tree) == before
    _git(tree, "update-ref", "HEAD", head)

    receipt = _receipt(tree)
    receipt_bytes = receipt.read_bytes()
    pending = json.loads(receipt_bytes)
    pending["status"] = "applying"
    receipt.write_text(json.dumps(pending))
    _refused_without_writes(tree, "INCOMPLETE-STAMP", *args)
    receipt.write_bytes(receipt_bytes)

    changelog = tree / "CHANGELOG.md"
    original = changelog.read_bytes()
    changelog.write_bytes(original + b"\nUnreviewed staged edit.\n")
    _git(tree, "add", "CHANGELOG.md")
    _refused_without_writes(tree, "DIRTY-TREE", *args)
