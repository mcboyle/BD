"""Whole-tree human-review censuses expire before a row spends its band.

The queue's QA command invokes pytest over every test module changed by a row.
A census-bearing module therefore declares the review version as module data;
the collection plugin reads it before any test body can run.  The nested
projects below exercise that real pytest boundary rather than grepping source.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "tests" / "_row_census_pin.py"
REGEN = ROOT / "toolchain" / "bin" / "bd-regen-order"


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Row 323 fixture",
            "-c",
            "user.email=row323@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _write_version(repo: Path, version: str) -> None:
    package = repo / "bulk_downloader"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="ascii"
    )


def _literal_pin(pin_version: str) -> str:
    return (
            "BD_WHOLE_TREE_CENSUS_PIN = {\n"
            "    'row': 292,\n"
            f"    'taken_at': 'v{pin_version}',\n"
            "}\n\n"
        )


def _write_subject(repo: Path, declaration: str = "") -> None:
    (repo / "test_census_subject.py").write_text(
        "from pathlib import Path\n"
        "import os\n\n"
        + declaration
        + "def test_band_body():\n"
        "    Path(os.environ['ROW323_SENTINEL']).write_text('ran', encoding='ascii')\n",
        encoding="ascii",
    )


def _candidate_repo(
    tmp_path: Path, declaration: str, *, delete_census_file: bool = False
) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")

    _write_version(repo, "3.66.1274")
    if delete_census_file:
        (repo / "reviewed_then_deleted.py").write_text("REVIEWED = True\n", encoding="ascii")
    _git(repo, "add", "bulk_downloader/__init__.py")
    if delete_census_file:
        _git(repo, "add", "reviewed_then_deleted.py")
    _git(repo, "commit", "-qm", "census release")

    _write_version(repo, "3.66.1297")
    if delete_census_file:
        (repo / "reviewed_then_deleted.py").unlink()
        _git(repo, "add", "-u", "reviewed_then_deleted.py")
    else:
        reviewed = repo / "reviewed_population"
        reviewed.mkdir()
        (reviewed / "new_a.py").write_text("A = 1\n", encoding="ascii")
        (reviewed / "new_b.py").write_text("B = 2\n", encoding="ascii")
        _git(repo, "add", "reviewed_population")
    _git(repo, "add", "bulk_downloader/__init__.py")
    _git(repo, "commit", "-qm", "main moved after census")

    _write_version(repo, "3.66.1298")
    (repo / "conftest.py").write_text(
        'pytest_plugins = ("_row_census_pin",)\n', encoding="ascii"
    )
    _write_subject(repo, declaration)
    _git(repo, "add", "bulk_downloader/__init__.py", "conftest.py", "test_census_subject.py")
    _git(repo, "commit", "-qm", "candidate carrying census declaration")
    return repo, repo / "body-ran"


def _run_nested_pytest(repo: Path, sentinel: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    env["ROW323_SENTINEL"] = str(sentinel)
    old_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(PLUGIN.parent) + (
        os.pathsep + old_pythonpath if old_pythonpath else ""
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "test_census_subject.py",
            "-p",
            "no:randomly",
            "-q",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _load_regen_tool():
    loader = importlib.machinery.SourceFileLoader("row323_regen", str(REGEN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _configure_minimal_regen(regen, repo: Path, sentinel: Path, monkeypatch) -> None:
    generator = repo / "row323_generator.py"
    generator.write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('ran', encoding='ascii')\n",
        encoding="ascii",
    )
    monkeypatch.setattr(sys, "argv", ["bd-regen-order", "--work", str(repo)])
    monkeypatch.setattr(
        regen,
        "CHAIN",
        [("row323 sentinel", [generator.name], "must run only after census check")],
    )
    monkeypatch.setattr(regen, "VERIFY", [])
    monkeypatch.setattr(regen, "check_reach", lambda _work: (True, "in sync"))


def test_an_expired_census_refuses_during_collection_before_the_band_body(
    tmp_path: Path,
) -> None:
    assert PLUGIN.is_file(), "row-census collection checker is missing"
    repo, sentinel = _candidate_repo(tmp_path, _literal_pin("3.66.1274"))

    result = _run_nested_pytest(repo, sentinel)

    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert (
        "census taken at v3.66.1274, tree is now v3.66.1297, "
        "3 files unreviewed"
    ) in output
    assert not sentinel.exists(), "the band body ran after the census had expired"


def test_the_declared_version_is_compared_with_the_candidate_base(
    tmp_path: Path,
) -> None:
    assert PLUGIN.is_file(), "row-census collection checker is missing"
    repo, sentinel = _candidate_repo(tmp_path, _literal_pin("3.66.1297"))

    result = _run_nested_pytest(repo, sentinel)

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert sentinel.read_text(encoding="ascii") == "ran"


def test_a_row_without_a_census_pin_does_no_git_work_and_is_not_delayed(
    tmp_path: Path,
) -> None:
    assert PLUGIN.is_file(), "row-census collection checker is missing"
    repo = tmp_path / "not-a-git-repository"
    repo.mkdir()
    (repo / "conftest.py").write_text(
        'pytest_plugins = ("_row_census_pin",)\n', encoding="ascii"
    )
    _write_subject(repo)
    sentinel = repo / "body-ran"

    result = _run_nested_pytest(repo, sentinel)

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert sentinel.read_text(encoding="ascii") == "ran"


def test_the_repository_loads_the_census_checker_as_a_pytest_plugin(
    pytestconfig,
) -> None:
    assert pytestconfig.pluginmanager.hasplugin("_row_census_pin")


def test_transform_control_exercises_only_a_row_without_a_pin(tmp_path: Path) -> None:
    """The comparison-removal mutant must escape this unrelated control."""
    assert PLUGIN.is_file(), "row-census collection checker is missing"
    repo = tmp_path / "transform-control"
    repo.mkdir()
    (repo / "conftest.py").write_text(
        'pytest_plugins = ("_row_census_pin",)\n', encoding="ascii"
    )
    _write_subject(repo)
    sentinel = repo / "body-ran"

    result = _run_nested_pytest(repo, sentinel)

    assert result.returncode == 0, result.stdout + result.stderr
    assert sentinel.is_file()


def test_a_dirty_worker_compares_its_census_with_the_integration_target(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "dirty-worker"
    repo.mkdir()
    _git(repo, "init", "-q")

    _write_version(repo, "3.66.1274")
    _git(repo, "add", "bulk_downloader/__init__.py")
    _git(repo, "commit", "-qm", "worker dispatch point")
    census_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    _write_version(repo, "3.66.1297")
    (repo / "new_a.py").write_text("A = 1\n", encoding="ascii")
    (repo / "new_b.py").write_text("B = 2\n", encoding="ascii")
    _git(repo, "add", "bulk_downloader/__init__.py", "new_a.py", "new_b.py")
    _git(repo, "commit", "-qm", "integration target moved")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "--detach", census_commit)

    (repo / "conftest.py").write_text(
        'pytest_plugins = ("_row_census_pin",)\n', encoding="ascii"
    )
    _write_subject(repo, _literal_pin("3.66.1274"))
    sentinel = repo / "body-ran"

    result = _run_nested_pytest(repo, sentinel)

    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert (
        "census taken at v3.66.1274, tree is now v3.66.1297, "
        "3 files unreviewed"
    ) in output
    assert not sentinel.exists()


def test_a_clean_pin_without_parent_history_fails_closed(tmp_path: Path) -> None:
    repo = tmp_path / "root-candidate"
    repo.mkdir()
    _git(repo, "init", "-q")
    _write_version(repo, "3.66.1274")
    (repo / "conftest.py").write_text(
        'pytest_plugins = ("_row_census_pin",)\n', encoding="ascii"
    )
    _write_subject(repo, _literal_pin("3.66.1274"))
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "root candidate carrying census declaration")
    sentinel = repo / "body-ran"

    result = _run_nested_pytest(repo, sentinel)

    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert "UNKNOWN census expiry:" in output
    assert "HEAD^1" in output
    assert not sentinel.exists()


def test_a_computed_version_cannot_pose_as_review_evidence(tmp_path: Path) -> None:
    computed = (
        "BD_WHOLE_TREE_CENSUS_PIN = {\n"
        "    'row': 292,\n"
        "    'taken_at': 'v' + '3.66.1297',\n"
        "}\n\n"
    )
    repo, sentinel = _candidate_repo(tmp_path, computed)

    result = _run_nested_pytest(repo, sentinel)

    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert "UNKNOWN census expiry:" in output
    assert "literal declaration" in output
    assert not sentinel.exists()


def test_a_malformed_declaration_is_reported_as_unknown_not_a_traceback(
    tmp_path: Path,
) -> None:
    malformed = "BD_WHOLE_TREE_CENSUS_PIN = {'row': 292}\n\n"
    repo, sentinel = _candidate_repo(tmp_path, malformed)

    result = _run_nested_pytest(repo, sentinel)

    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert "UNKNOWN census expiry:" in output
    assert "must contain exactly row and taken_at" in output
    assert "INTERNALERROR" not in output
    assert not sentinel.exists()


def test_deleted_files_are_included_in_the_unreviewed_population(
    tmp_path: Path,
) -> None:
    repo, sentinel = _candidate_repo(
        tmp_path,
        _literal_pin("3.66.1274"),
        delete_census_file=True,
    )

    result = _run_nested_pytest(repo, sentinel)

    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert (
        "census taken at v3.66.1274, tree is now v3.66.1297, "
        "2 files unreviewed"
    ) in output
    assert not sentinel.exists()


def test_integration_regen_rechecks_the_fetched_target_before_any_generator(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = tmp_path / "assembled-cut"
    repo.mkdir()
    _git(repo, "init", "-q")
    _write_version(repo, "3.66.1274")
    _git(repo, "add", "bulk_downloader/__init__.py")
    _git(repo, "commit", "-qm", "census release")

    _write_version(repo, "3.66.1297")
    (repo / "new_a.py").write_text("A = 1\n", encoding="ascii")
    (repo / "new_b.py").write_text("B = 2\n", encoding="ascii")
    _git(repo, "add", "bulk_downloader/__init__.py", "new_a.py", "new_b.py")
    _git(repo, "commit", "-qm", "fetched integration target")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    tests = repo / "tests"
    tests.mkdir()
    shutil.copy2(PLUGIN, tests / PLUGIN.name)
    _write_subject(repo, _literal_pin("3.66.1274"))
    (repo / "test_census_subject.py").replace(tests / "test_census_subject.py")
    _git(repo, "add", "tests")
    sentinel = repo / "generator-ran"
    regen = _load_regen_tool()
    _configure_minimal_regen(regen, repo, sentinel, monkeypatch)

    result = regen.main()

    output = capsys.readouterr().out
    assert result == 1, output
    assert (
        "census taken at v3.66.1274, tree is now v3.66.1297, "
        "3 files unreviewed"
    ) in output
    assert not sentinel.exists(), "regen mutated the cut before refusing its stale census"


def test_integration_regen_does_not_resolve_a_remote_for_a_no_pin_row(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = tmp_path / "no-pin-cut"
    repo.mkdir()
    _git(repo, "init", "-q")
    _write_version(repo, "3.66.1297")
    tests = repo / "tests"
    tests.mkdir()
    shutil.copy2(PLUGIN, tests / PLUGIN.name)
    _git(repo, "add", "bulk_downloader/__init__.py", "tests/_row_census_pin.py")
    _git(repo, "commit", "-qm", "integration base without any remote")
    _write_subject(repo)
    (repo / "test_census_subject.py").replace(tests / "test_census_subject.py")
    _git(repo, "add", "tests/test_census_subject.py")
    sentinel = repo / "generator-ran"
    regen = _load_regen_tool()
    _configure_minimal_regen(regen, repo, sentinel, monkeypatch)

    result = regen.main()

    assert result == 0, capsys.readouterr().out
    assert sentinel.read_text(encoding="ascii") == "ran"
