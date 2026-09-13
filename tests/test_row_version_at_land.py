"""Land-time stamps refuse ambiguous inputs and change exactly the release set.

The fixtures are real local Git clones; neither bd-bump nor bd-regen-order is
stubbed. In particular the positive case cannot pass over fake generator output.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "toolchain/bin/bd-land-trio"
RELEASE_PATHS = {
    "bulk_downloader/__init__.py",
    "tests/test_settings_center_slice4.py",
    "CHANGELOG.md",
    "PIN_INDEX.json",
    "project-knowledge/STATIC_KB_MANIFEST.json",
}
TITLE = "Land the reviewed feature"


def _env(work):
    env = dict(os.environ)
    for key in ("BD_INSTALL_DIR", "PYTHONPATH", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    env.update(BD_HOME=str(work.parent / (work.name + "-home")),
               BD_DISABLE_KEEPALIVE="1", PYTHONDONTWRITEBYTECODE="1",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="Trio fixture", GIT_AUTHOR_EMAIL="trio@example.invalid",
               GIT_COMMITTER_NAME="Trio fixture", GIT_COMMITTER_EMAIL="trio@example.invalid")
    return env


def _run(work, argv, *, ok=False, timeout=180):
    cp = subprocess.run(argv, cwd=work, env=_env(work), capture_output=True,
                        text=True, timeout=timeout)
    if ok:
        assert cp.returncode == 0, cp.stdout + cp.stderr
    return cp


def _git(work, *args):
    return _run(work, ["git", *args], ok=True).stdout.strip()


def _stamp(work, *args, title=TITLE):
    return _run(work, [sys.executable, str(TOOL), "--work", str(work),
                       "--title", title, *args])


def _version(work):
    text = (work / "bulk_downloader/__init__.py").read_text()
    return re.search(r'__version__ = "(3\.66\.\d+)"', text).group(1)


def _next(version, amount=1):
    return "3.66." + str(int(version.rsplit(".", 1)[1]) + amount)


def _receipt(work):
    path = Path(_git(work, "rev-parse", "--git-path", "bd-land-trio.json"))
    return path if path.is_absolute() else work / path


def _snapshot(work):
    """Tracked/untracked bytes plus index and refs; exclude Git's read caches."""
    names = _git(work, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    files = {name: hashlib.sha256((work / name).read_bytes()).hexdigest()
             for name in names if (work / name).is_file()}
    index = Path(_git(work, "rev-parse", "--git-path", "index"))
    if not index.is_absolute():
        index = work / index
    return (files, index.read_bytes(), _git(work, "rev-parse", "HEAD"),
            _git(work, "rev-parse", "origin/main"),
            _receipt(work).read_bytes() if _receipt(work).exists() else None)


@pytest.fixture
def tree(tmp_path):
    work = tmp_path / "work"
    _run(tmp_path, ["git", "clone", "--quiet", "--shared", "--no-checkout",
                    str(ROOT), str(work)], ok=True)
    head = _git(ROOT, "rev-parse", "HEAD")
    _git(work, "checkout", "--quiet", "--detach", head)
    _git(work, "update-ref", "refs/remotes/origin/main", head)
    # The host interpreter carries runtime dependencies; no shared env is edited.
    # Git excludes this link and every generator's private runtime output.
    (work / ".git/info/exclude").write_text("\n/venv\n")
    (work / "venv").symlink_to(Path(sys.executable).parent.parent, target_is_directory=True)
    assert _git(work, "status", "--porcelain") == ""
    return work


def _clean_generated_baseline(work):
    """A real reviewed tree has its derived artifacts current before stamping."""
    _run(work, [sys.executable, str(work / "toolchain/bin/bd-regen-order"),
                "--work", str(work)], ok=True)
    _git(work, "add", "--all")
    if _git(work, "diff", "--cached", "--name-only"):
        _git(work, "commit", "--quiet", "-m", "Fixture reviewed generated baseline")
    assert _git(work, "status", "--porcelain") == ""


def _bump_committed(work, version):
    _run(work, [sys.executable, str(work / "toolchain/bin/bd-bump"), version,
                "--work", str(work), "--title", "Fixture prior release", "--write"], ok=True)
    _git(work, "add", "--all")
    _git(work, "commit", "--quiet", "-m", "Fixture prior release")


def _refused_without_writes(work, reason, *args, **kwargs):
    before = _snapshot(work)
    cp = _stamp(work, *args, **kwargs)
    assert cp.returncode == 3, cp.stdout + cp.stderr
    assert cp.stderr.splitlines()[0] == "TRIO-REFUSED " + reason, cp.stderr
    assert not any(line.startswith("TRIO-STAMPED ") for line in cp.stdout.splitlines())
    assert _snapshot(work) == before, "refusal changed file bytes, index, HEAD or main"


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


def test_real_stamp_stages_exactly_five_paths_and_replays_byte_identically(tree, tmp_path):
    """Omitting a generator, moving an extra path, or rebumping a replay fails here."""
    _clean_generated_baseline(tree)
    old = _version(tree)
    new = _next(old)
    head = _git(tree, "rev-parse", "HEAD")
    body = tmp_path / "body.txt"
    body.write_text("Reviewed behavior is stamped at landing.\n")
    args = ["--body", str(body), "--write"]
    cp = _stamp(tree, *args)
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



def test_pending_stamp_rejects_changed_inputs_head_main_and_foreign_dirt(tree, tmp_path):
    """A replay receipt cannot authorize a different request or integration tree."""
    _clean_generated_baseline(tree)
    body = tmp_path / "body.txt"
    body.write_text("Original reviewed body.\n")
    args = ["--body", str(body), "--write"]
    cp = _stamp(tree, *args)
    assert cp.returncode == 0, cp.stdout + cp.stderr

    _refused_without_writes(tree, "DIRTY-TREE", *args, title="Different title")
    body.write_text("Different body.\n")
    _refused_without_writes(tree, "DIRTY-TREE", *args)
    body.write_text("Original reviewed body.\n")

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
