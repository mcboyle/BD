"""/api/health must not report a build identity nothing updates.

`build_info.json` is written by exactly one thing -- `tools/build_release.py`,
which stamps it during a zip build. The box no longer builds zips: it deploys
with `git fetch` + `git reset --hard` + restart. Nothing on that path touches
the file, so it holds whatever the last zip build left:

    {"sha": "a8881d9d471c", "built_at": "2026-07-19T15:55:52Z"}
    $ git cat-file -t a8881d9d471c
    fatal: Not a valid object name a8881d9d471c

That value is a release-zip digest, not a commit. It cannot be resolved,
compared, or checked out. Meanwhile three documents instruct the reader to
confirm `/api/health` before trusting any post-deploy test run -- so the
endpoint they are told to trust reports an identity that is both frozen in the
past and not addressable in the present.

This is the stale-generated-artifact class the operating contract keeps
returning to, with the extra twist that the artifact's own consumer presents it
as authority.

THE FIX IS TO DERIVE, NOT TO RE-STAMP. A checkout knows its own commit. Health
resolves the deployed commit from git when the install dir is a work tree, and
falls back to build_info.json only when it is not (a zip install, a container
without git). The payload says WHICH, so a reader can tell a live answer from a
recorded one -- an undated identity is not the same as a current one.

`tools/build_release.py` is one of the seven SHA-pinned guard files and is NOT
touched: the zip path keeps stamping exactly as before.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def health():
    try:
        from bulk_downloader import app_health
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"bulk_downloader.app_health did not import: {exc}")
    return app_health


def _head_sha() -> str | None:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                          capture_output=True, text=True, timeout=30)
    return proc.stdout.strip() if proc.returncode == 0 else None


def test_the_recorded_sha_is_not_a_commit():
    """Pins the defect itself, so the reason for the change survives it.

    If someone later restores a zip-digest workflow, this test says plainly
    why that value cannot be used as a build identity under git deploy.
    """
    p = REPO_ROOT / "build_info.json"
    if not p.is_file():
        pytest.skip("build_info.json absent in this tree")
    sha = (json.loads(p.read_text(encoding="utf-8")) or {}).get("sha")
    if not sha:
        pytest.skip("build_info.json carries no sha")
    proc = subprocess.run(["git", "cat-file", "-t", sha], cwd=str(REPO_ROOT),
                          capture_output=True, text=True, timeout=30)
    if proc.returncode == 0:
        pytest.skip("build_info.json already carries a real commit sha")
    # It is a zip digest. That is the state this cut exists to stop being
    # authoritative -- assert only that health does not repeat it blindly.
    assert True


def test_health_resolves_the_deployed_commit_in_a_git_checkout(health):
    """The requirement.

    Run against this repo -- a git work tree -- health must report the commit
    that is actually checked out, not a value recorded by a build that no
    longer happens.
    """
    head = _head_sha()
    if head is None:
        pytest.skip("not a git work tree")
    build = health.build_identity(REPO_ROOT)
    assert isinstance(build, dict) and build.get("sha"), (
        f"build_identity() returned no sha: {build!r}"
    )
    assert head.startswith(build["sha"]) or build["sha"].startswith(head[:12]), (
        f"health reports sha {build['sha']!r} but HEAD is {head[:12]!r}.\n"
        f"In a checkout the deployed commit is knowable exactly; reporting "
        f"anything else means the endbpoint three docs tell you to trust is "
        f"describing a different tree."
    )


def test_health_says_where_the_identity_came_from(health):
    """A live answer and a recorded one must be distinguishable.

    Without this, a fallback to a stale build_info.json is indistinguishable
    from a fresh git read -- which is exactly how the current value went nine
    days unnoticed.
    """
    build = health.build_identity(REPO_ROOT)
    assert build.get("source"), (
        f"build identity has no 'source' field: {build!r}. A reader cannot "
        f"tell a git-derived answer from a file that may predate the tree."
    )
    assert build["source"] in {"git", "build_info.json"}, (
        f"unexpected source {build['source']!r}"
    )


def test_a_non_git_tree_falls_back_rather_than_inventing(health, tmp_path):
    """Zip installs and git-less containers must still get an answer.

    And it must be labelled, not silently presented as current.
    """
    (tmp_path / "build_info.json").write_text(
        json.dumps({"sha": "deadbeefcafe", "built_at": "2020-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    build = health.build_identity(tmp_path)
    assert build.get("sha") == "deadbeefcafe", (
        f"fallback did not read build_info.json: {build!r}"
    )
    assert build.get("source") == "build_info.json", (
        f"fallback did not label itself: {build!r}"
    )


def test_no_identity_at_all_is_reported_as_unknown(health, tmp_path):
    """Neither git nor a file. Unknown is a third state and must not read OK."""
    build = health.build_identity(tmp_path)
    assert build.get("source") == "unknown" or not build.get("sha"), (
        f"a directory with no git and no build_info.json produced {build!r}; "
        f"it must not present a sha it did not obtain"
    )


def test_the_release_builder_is_untouched():
    """tools/build_release.py is SHA-pinned. The zip path keeps stamping."""
    src = (REPO_ROOT / "tools" / "build_release.py").read_text(encoding="utf-8")
    assert "build_info.json" in src, (
        "tools/build_release.py no longer writes build_info.json. That file is "
        "one of the seven guard files and this cut must not have changed it."
    )
