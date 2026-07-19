"""v3.66.742 — bat_lint / sh_lint walk only the tree they certify.

THE ONE CONFIRMED >8s-ALONE ROUTE of the 740 capture: /api/dev/bat_lint.
Root cause: `_repo_root().rglob("*.bat")`. The RELEASE ZIP's tree is ~2.5k
files, but the lints run against the INSTALL DIR on stash — which accretes
everything the zip never ships: the service venv (tens of thousands of
files), frontend node_modules, __pycache__ everywhere, and overlay orphans
(the overlay never deletes). `rglob` walks all of it to find a handful of
.bat files. The route was slow because its denominator was the wrong tree.

Fix: prune at descend time (an os.walk that never enters
`_MANIFEST_EXCLUDE_DIRS`), reusing the SAME "not source" set the manifest
verifier already declares — the lints and the manifest now certify the same
denominator. This is not just a speedup: a .bat inside venv/ is not
operator-authored and not shipped; flagging it was a false subject.

NOTE the asymmetry with zip_manifest_check: THAT check keeps its full walk
on purpose — its whole job is spotting files that should not be there.
Pruning the manifest check would create a blind gate; pruning the lints
aligns them with their actual subject.
"""
from __future__ import annotations

import os
import stat
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bulk_downloader.dev_suite.release_lint as rl


def _mk(root, rel, data=b"@echo off\r\n", exe=False):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    if exe:
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return p


def test_bat_lint_does_not_descend_into_excluded_dirs(monkeypatch, tmp_path):
    """A .bat under venv/ or node_modules/ is runtime accretion, not a
    shipped script — it must be outside the lint's denominator entirely."""
    _mk(str(tmp_path), "install.bat")                       # real subject
    _mk(str(tmp_path), "venv/junk.bat", b"caf\xc3\xa9\r\n")  # non-ASCII bait
    _mk(str(tmp_path), "frontend/node_modules/x/y.bat")
    _mk(str(tmp_path), "__pycache__/z.bat")
    from pathlib import Path
    monkeypatch.setattr(rl, "_repo_root", lambda: Path(str(tmp_path)))

    r = rl.bat_lint()

    files = {row["file"] for row in r["files"]}
    assert files == {"install.bat"}, (
        f"lint walked outside its subject: {sorted(files)} — the install dir's "
        "runtime accretions (venv/node_modules/__pycache__) are why "
        "/api/dev/bat_lint blew its 8s budget on a quiet app"
    )
    assert r["file_count"] == 1


def test_sh_lint_does_not_descend_into_excluded_dirs(monkeypatch, tmp_path):
    """Same walk, same disease, same fix (release_lint.py line-171 twin)."""
    _mk(str(tmp_path), "setup.sh", b"#!/bin/sh\necho ok\n", exe=True)
    _mk(str(tmp_path), "venv/bin/activate.sh", b"#!/bin/sh\r\n")
    _mk(str(tmp_path), ".git/hooks/pre-commit.sh", b"#!/bin/sh\n")
    from pathlib import Path
    monkeypatch.setattr(rl, "_repo_root", lambda: Path(str(tmp_path)))

    r = rl.sh_lint()

    files = {row["file"] for row in r["files"]}
    assert files == {"setup.sh"}, f"lint walked outside its subject: {sorted(files)}"


def test_real_subjects_still_linted_at_depth(monkeypatch, tmp_path):
    """Pruning must not shrink the REAL denominator: a shipped script in a
    nested source dir is still found, and its issues still reported."""
    _mk(str(tmp_path), "scripts/deep/nested/run.bat", b"caf\xc3\xa9\r\n")
    from pathlib import Path
    monkeypatch.setattr(rl, "_repo_root", lambda: Path(str(tmp_path)))

    r = rl.bat_lint()

    assert r["file_count"] == 1
    row = r["files"][0]
    assert row["file"].replace(os.sep, "/") == "scripts/deep/nested/run.bat"
    assert not row["ok"] and any("non-ASCII" in i for i in row["issues"])


def test_lint_exclusions_are_the_manifest_exclusions():
    """One declared 'not source' set, two consumers. If the lints grow a
    private exclusion list it will drift from the manifest's and the two
    checks will certify different trees under one name."""
    assert rl._LINT_WALK_EXCLUDE_DIRS == rl._MANIFEST_EXCLUDE_DIRS
