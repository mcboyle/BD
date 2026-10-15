"""Land-time stamps refuse ambiguous inputs; no refusal pays for a generator run.

The fixtures are real local Git clones; neither bd-bump nor bd-regen-order is
stubbed. The two positive arms, which each need one real ``--write`` stamp,
live in tests/test_row_version_at_land_stamp.py so this module, the PINNED
version-at-land shard, stays under 60 s (see tests/_land_trio.py).
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from _land_trio import (
    TOOL, _bump_committed, _file_digests, _git, _next, _receipt,
    _refused_without_writes, _snapshot, _stamp, _version, discard_tree, make_tree,
)

BD_GATE_SCOPE = "repo-wide"


@pytest.fixture(scope="module")
def _clones(request, tmp_path_factory):
    """Every arm's fresh clone, built concurrently before the first arm runs.

    Each arm still gets its own real clone; only the clone, about 1.8 s of
    each arm's 3.4 s, leaves the serial path.
    """
    wanted = sum(item.module is request.module and "tree" in item.fixturenames
                 for item in request.session.items)
    root = tmp_path_factory.mktemp("trees")

    def build(at):
        (root / str(at)).mkdir()
        return make_tree(root / str(at))

    with ThreadPoolExecutor() as pool:
        built = list(pool.map(build, range(wanted)))
    yield built
    for work in built:
        discard_tree(work)


@pytest.fixture
def tree(_clones, tmp_path):
    # A pool short of arms (-k, a worker that runs a subset) clones on demand.
    work = _clones.pop() if _clones else make_tree(tmp_path)
    yield work
    discard_tree(work)


@pytest.mark.parametrize("dirty", ["unstaged", "staged", "untracked", "deleted-release"])
def test_dirty_tree_refuses_before_any_release_write(tree, dirty):
    """Removing either index/worktree/untracked dirt checks admits unreviewed work."""
    if dirty == "deleted-release":
        (tree / "bulk_downloader/__init__.py").unlink()
    else:
        target = tree / ("new-unreviewed.txt" if dirty == "untracked" else "README.md")
        with target.open("a") as stream:
            stream.write("\nUnreviewed change\n")
    if dirty == "staged":
        _git(tree, "add", "README.md")
    _refused_without_writes(tree, "DIRTY-TREE", "--write")


def test_two_versions_ahead_refuses_with_exact_diagnostic(tree):
    """Changing >1 to >2 would silently accept a worker's double release claim."""
    _bump_committed(tree, _next(_version(tree), 2))
    _refused_without_writes(tree, "TRIO-AHEAD", "--write")


@pytest.mark.parametrize("where", ["top", "title", "body"])
def test_non_ascii_changelog_input_refuses_without_writes(tree, tmp_path, where):
    args = ["--write"]
    kwargs = {}
    if where == "top":
        path = tree / "CHANGELOG.md"
        text = path.read_text()
        text = re.sub(r"^(## v[^\n]+)", lambda match: match[1] + " caf\u00e9", text, count=1, flags=re.M)
        path.write_text(text)
        _git(tree, "add", "CHANGELOG.md")
        _git(tree, "commit", "--quiet", "-m", "Fixture non-ASCII existing title")
    elif where == "title":
        kwargs["title"] = "caf\u00e9"
    else:
        body = tmp_path / "body.txt"
        body.write_text("caf\u00e9\n")
        args += ["--body", str(body)]
    _refused_without_writes(tree, "NON-ASCII-CHANGELOG", *args, **kwargs)


def test_nonancestor_base_is_refused(tree):
    unrelated = _git(tree, "commit-tree", "HEAD^{tree}", "-m", "Unrelated root")
    _refused_without_writes(tree, "BASE-NOT-ANCESTOR", "--base", unrelated, "--write")


@pytest.mark.parametrize("tree_ahead,main_ahead", [(False, False), (True, False), (False, True)])
def test_check_uses_max_main_and_tree_without_any_write(tree, tree_ahead, main_ahead):
    old = _version(tree)
    expected = _next(old)
    if tree_ahead:
        _bump_committed(tree, _next(old))
        old = _version(tree)
        expected = _next(old)
    if main_ahead:
        original_head = _git(tree, "rev-parse", "HEAD")
        _bump_committed(tree, _next(old))
        _git(tree, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(tree, "checkout", "--quiet", "--detach", original_head)
        expected = _next(old, 2)
    assert not _receipt(tree).exists()
    before = _snapshot(tree)
    # When main is newer than HEAD, an explicit real ancestor is needed.
    cp = _stamp(tree, "--check", "--base", "HEAD")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    plans = [line for line in cp.stdout.splitlines() if line.startswith("TRIO-PLAN ")]
    assert plans == [f"TRIO-PLAN {old}->{expected} files=5"]
    assert _snapshot(tree) == before


def test_snapshot_digests_equal_serial_sha256_over_every_listed_file(tmp_path):
    """The chunked sha256sum hasher is a speed-up of the snapshot, not a different measurement."""
    names = []
    for index in range(600):  # spans several argv chunks, the last one partial
        name = f"d{index % 7}/f{index}.txt"
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_bytes(b"%d\n" % index * (index + 1))
        names.append(name)
    (tmp_path / "d0/sub").mkdir()
    names += ["d0/sub", "missing.txt"]  # a directory and an absent path are not files
    serial = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
              for name in names if (tmp_path / name).is_file()}
    assert len(serial) == 600
    assert _file_digests(tmp_path, names) == serial


def test_transform_control_imports_generator_without_judging_a_stamp():
    """The mutation control loads the actual subject but runs no decision arm."""
    loader = importlib.machinery.SourceFileLoader("version_at_land_control", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)


@pytest.mark.parametrize("receipt", [
    pytest.param(None, id="null"),
    pytest.param([], id="array"),
    pytest.param("receipt", id="string"),
    pytest.param(7, id="number"),
    pytest.param(True, id="boolean"),
    pytest.param({"status": "complete"}, id="complete-missing-context"),
    pytest.param({"status": "complete", "context": None}, id="complete-null-context"),
    pytest.param({"status": "complete", "context": []}, id="complete-array-context"),
    pytest.param({"status": "complete", "context": "main"}, id="complete-string-context"),
    pytest.param({"status": "complete", "context": 7}, id="complete-number-context"),
])
def test_malformed_receipt_is_named_unavailable_without_writes(tree, receipt):
    """Valid JSON is not a valid replay record; .get on a scalar must not crash."""
    _receipt(tree).write_text(json.dumps(receipt) + "\n")
    if isinstance(receipt, dict):
        # The former crash was inside the dirty pending-stamp path. Exercise
        # that path without paying for a stamp that cannot validate this record.
        with (tree / "README.md").open("a") as stream:
            stream.write("\nPending work with an invalid receipt\n")
    _refused_without_writes(tree, "INVALID-RECEIPT", "--write")
