"""Cut 1431: freeze feature bytes; prove the release trio at land time."""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent
PRECUT = REPO / "toolchain" / "bin" / "bd-precut"
LAND_RELEASE = REPO / "toolchain" / "bin" / "bd-land-release"


def _load_precut():
    spec = importlib.util.spec_from_loader(
        "row1431_precut", SourceFileLoader("row1431_precut", str(PRECUT))
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _release_tree(tmp_path: Path, *, version: str, pin: str, changelog: str) -> Path:
    root = tmp_path / "tree"
    (root / "bulk_downloader").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "bulk_downloader" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (root / "tests" / "test_settings_center_slice4.py").write_text(
        "from bulk_downloader import __version__\n\n"
        "def test_version():\n"
        f'    assert __version__ == "{pin}"\n',
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    return root


def _baseline_zip(tmp_path: Path, *, version: str = "3.66.700") -> Path:
    target = tmp_path / "baseline.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("bulk_downloader/__init__.py", f'__version__ = "{version}"\n')
        archive.writestr(
            "tests/test_settings_center_slice4.py",
            f'assert __version__ == "{version}"\n',
        )
        archive.writestr("CHANGELOG.md", f"# Changelog\n\n## v{version} - prior\n\nold\n")
    return target


@pytest.mark.parametrize(
    ("version", "pin", "changelog", "diagnostic"),
    [
        (
            "3.66.701",
            "3.66.701",
            "# Changelog\n\n## v3.66.700 - prior\n\nold\n",
            "top entry is v3.66.700, not v3.66.701",
        ),
        (
            "3.66.700",
            "3.66.700",
            "# Changelog\n\n## v3.66.701 - feature\n\nbody\n\n## v3.66.700 - prior\n",
            "top entry is v3.66.701, not v3.66.700",
        ),
        (
            "3.66.701",
            "3.66.701",
            "# Changelog\n\n## v3.66.701 - feature\n\nnon-ASCII — body\n\n"
            "## v3.66.700 - prior\n",
            "contains 1 non-ASCII",
        ),
        (
            "3.66.701",
            "3.66.701",
            "# Changelog\n\n## v3.66.701 - feature\n\nbody\n\n## v3.66.699 - wrong\n",
            "not anchored on the previous release header",
        ),
    ],
)
def test_precut_release_trio_refuses_each_named_contract_failure(
    tmp_path, version, pin, changelog, diagnostic
):
    root = _release_tree(tmp_path, version=version, pin=pin, changelog=changelog)
    baseline = _baseline_zip(tmp_path)

    assert (root / "bulk_downloader" / "__init__.py").read_text().count(version) == 1
    assert (root / "tests" / "test_settings_center_slice4.py").read_text().count(pin) == 1
    headers = [line for line in changelog.splitlines() if line.startswith("## v")]
    assert headers
    if "non-ASCII" in diagnostic:
        assert sum(ord(char) > 127 for char in changelog) == 1
    if "not anchored" in diagnostic:
        assert headers[:2] == ["## v3.66.701 - feature", "## v3.66.699 - wrong"]

    problem = _load_precut().check_release_trio(root, baseline)

    assert problem is not None
    assert diagnostic in problem


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "--", *sorted(p.name for p in repo.iterdir() if p.is_file()))
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _transfer_fixture(tmp_path: Path, *, drift: bool = False, overlap: bool = False):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Cut Test")
    _git(repo, "config", "user.email", "cut@example.invalid")
    (repo / "candidate.txt").write_text("base\n")
    (repo / "stable.txt").write_text("stable\n")
    old_base = _commit(repo, "base")

    _git(repo, "checkout", "-b", "candidate-old")
    (repo / "candidate.txt").write_text("candidate\n")
    old_head = _commit(repo, "candidate old")

    _git(repo, "checkout", "main")
    if overlap:
        (repo / "candidate.txt").write_text("main touched this path\n")
    else:
        (repo / "main.txt").write_text("main gained\n")
    new_base = _commit(repo, "main gained")

    _git(repo, "checkout", "-b", "candidate-new")
    (repo / "candidate.txt").write_text("drifted\n" if drift else "candidate\n")
    new_head = _commit(repo, "candidate rebased")
    return repo, old_base, old_head, new_base, new_head


def _run_transfer(fixture):
    repo, old_base, old_head, new_base, new_head = fixture
    return subprocess.run(
        [
            str(LAND_RELEASE),
            "transfer-check",
            "--repo",
            str(repo),
            "--old-base",
            old_base,
            "--old-head",
            old_head,
            "--new-base",
            new_base,
            "--new-head",
            new_head,
            "--json",
        ],
        capture_output=True,
        text=True,
    )


def test_freeze_requires_all_three_release_paths_to_match_the_base(tmp_path):
    repo = tmp_path / "freeze"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Cut Test")
    _git(repo, "config", "user.email", "cut@example.invalid")
    (repo / "bulk_downloader").mkdir()
    (repo / "tests").mkdir()
    (repo / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.700"\n')
    (repo / "tests" / "test_settings_center_slice4.py").write_text(
        'assert __version__ == "3.66.700"\n'
    )
    (repo / "CHANGELOG.md").write_text("## v3.66.700 - prior\n")
    _git(repo, "add", "--", "bulk_downloader/__init__.py",
         "tests/test_settings_center_slice4.py", "CHANGELOG.md")
    base = _commit(repo, "base trio")
    (repo / "feature.txt").write_text("feature\n")
    feature_head = _commit(repo, "feature only")

    clean = subprocess.run(
        [str(LAND_RELEASE), "freeze-check", "--repo", str(repo),
         "--base", base, "--head", feature_head, "--json"],
        capture_output=True, text=True,
    )
    assert clean.returncode == 0, clean.stderr
    assert json.loads(clean.stdout)["identical_trio_blobs"] == 3

    (repo / "CHANGELOG.md").write_text("## v3.66.701 - early\n## v3.66.700 - prior\n")
    early_head = _commit(repo, "early release edit")
    dirty = subprocess.run(
        [str(LAND_RELEASE), "freeze-check", "--repo", str(repo),
         "--base", base, "--head", early_head],
        capture_output=True, text=True,
    )
    assert dirty.returncode == 2
    assert "freeze candidate changes release trio path: CHANGELOG.md" in dirty.stderr


def test_transfer_refuses_when_a_candidate_path_blob_changes(tmp_path):
    fixture = _transfer_fixture(tmp_path, drift=True)
    repo, old_base, old_head, new_base, new_head = fixture
    assert _git(repo, "diff", "--name-only", old_base, old_head) == "candidate.txt"
    assert _git(repo, "diff", "--name-only", old_base, new_base) == "main.txt"
    assert _git(repo, "rev-parse", f"{old_head}:candidate.txt") != _git(
        repo, "rev-parse", f"{new_head}:candidate.txt"
    )

    result = _run_transfer(fixture)

    assert result.returncode == 2
    assert "candidate blob changed across rebase: candidate.txt" in result.stderr


def test_transfer_refuses_when_main_gained_commit_touches_candidate_path(tmp_path):
    fixture = _transfer_fixture(tmp_path, overlap=True)
    repo, old_base, old_head, new_base, new_head = fixture
    assert _git(repo, "diff", "--name-only", old_base, old_head) == "candidate.txt"
    assert _git(repo, "rev-parse", f"{old_head}:candidate.txt") == _git(
        repo, "rev-parse", f"{new_head}:candidate.txt"
    )

    result = _run_transfer(fixture)

    assert result.returncode == 2
    assert "main gained commit(s) overlap candidate path(s): candidate.txt" in result.stderr


def test_transfer_accepts_identical_candidate_blobs_and_disjoint_main_paths(tmp_path):
    result = _run_transfer(_transfer_fixture(tmp_path))

    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["candidate_paths"] == 1
    assert evidence["identical_blobs"] == 1
    assert evidence["main_gained_commits"] == 1
    assert evidence["main_touched_paths"] == 1
    assert evidence["overlap_paths"] == []


def test_stamp_writes_all_three_tree_facts_above_exact_previous_header(tmp_path):
    root = _release_tree(
        tmp_path,
        version="3.66.700",
        pin="3.66.700",
        changelog="# Changelog\n\n## v3.66.700 - prior\n\nold\n",
    )
    before_header = "## v3.66.700 - prior"

    result = subprocess.run(
        [
            str(LAND_RELEASE),
            "stamp",
            "--work",
            str(root),
            "--version",
            "3.66.701",
            "--title",
            "feature proven at land",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '__version__ = "3.66.701"' in (
        root / "bulk_downloader" / "__init__.py"
    ).read_text()
    assert 'assert __version__ == "3.66.701"' in (
        root / "tests" / "test_settings_center_slice4.py"
    ).read_text()
    changelog = (root / "CHANGELOG.md").read_text()
    headers = [line for line in changelog.splitlines() if line.startswith("## v")]
    assert headers == ["## v3.66.701 - feature proven at land", before_header]
    assert all(ord(char) < 128 for char in headers[0])
