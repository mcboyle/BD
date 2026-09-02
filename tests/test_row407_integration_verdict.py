"""Row 407: only ancestry-backed evidence may emit INTEGRATED."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bd_integration_verdict.py"
REAL_GIT = shutil.which("git")
assert REAL_GIT is not None
ROW = 407
REQUIRED_TEST = "tests/test_row407_behavior.py"
BD_GATE_SCOPE = "module"


def _run(
    argv: list[str],
    *,
    cwd: Path,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {argv!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run([REAL_GIT, *args], cwd=cwd, check=check)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _version(version: str) -> str:
    return f'__version__ = "{version}"\n'


def _register(status: str = "CLOSED @11", *, copies: int = 1) -> str:
    header = (
        "# Improvement Backlog\n\n"
        "| ID | Status | Evidence | Improvement |\n"
        "|---:|---|---|---|\n"
    )
    row = f"| {ROW} | {status} | test evidence | row407 safety |\n"
    return header + row * copies


def _commit(cwd: Path, message: str) -> str:
    _git(cwd, "add", "--all")
    _git(cwd, "commit", "-m", message)
    return _git(cwd, "rev-parse", "HEAD").stdout.strip()


class VerdictRepo:
    def __init__(self, tmp_path: Path) -> None:
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "main")
        _git(self.repo, "config", "user.name", "Row 407 Test")
        _git(self.repo, "config", "user.email", "row407@example.invalid")

        _write(self.repo / "bulk_downloader" / "__init__.py", _version("3.66.10"))
        _write(self.repo / "project-knowledge" / "IMPROVEMENT_BACKLOG.md", _register("OPEN"))
        self.base_head = _commit(self.repo, "base")

        _git(self.repo, "switch", "-c", "candidate")
        _write(self.repo / "bulk_downloader" / "__init__.py", _version("3.66.11"))
        _write(self.repo / "project-knowledge" / "IMPROVEMENT_BACKLOG.md", _register())
        _write(self.repo / REQUIRED_TEST, "def test_row407_behavior():\n    assert True\n")
        self.merged_candidate = _commit(self.repo, "row407 candidate")

        _git(self.repo, "switch", "main")
        _git(self.repo, "merge", "--ff-only", "candidate")
        _write(self.repo / "bulk_downloader" / "__init__.py", _version("3.66.12"))
        _write(self.repo / "after.txt", "later main\n")
        self.main_head = _commit(self.repo, "later main")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", self.main_head)

        self.divergent_candidate = self._case_commit(
            self.base_head,
            "divergent candidate",
            version="3.66.11",
            register=_register(),
            required_test=True,
        )
        self.open_row_main = self._case_commit(
            self.merged_candidate,
            "open row main",
            register=_register("OPEN"),
        )
        self.missing_row_main = self._case_commit(
            self.merged_candidate,
            "missing row main",
            register=_register(copies=0),
        )
        self.duplicate_row_main = self._case_commit(
            self.merged_candidate,
            "duplicate row main",
            register=_register(copies=2),
        )
        self.missing_test_main = self._case_commit(
            self.merged_candidate,
            "missing test main",
            remove_required_test=True,
        )
        self.future_candidate = self._case_commit(
            self.merged_candidate,
            "future candidate",
            version="3.66.12",
        )
        self.version_downgrade_main = self._case_commit(
            self.future_candidate,
            "version downgrade main",
            version="3.66.11",
        )
        _git(self.repo, "switch", "main")

    def _case_commit(
        self,
        start: str,
        message: str,
        *,
        version: str | None = None,
        register: str | None = None,
        required_test: bool = False,
        remove_required_test: bool = False,
    ) -> str:
        _git(self.repo, "switch", "--detach", start)
        if version is not None:
            _write(self.repo / "bulk_downloader" / "__init__.py", _version(version))
        if register is not None:
            _write(
                self.repo / "project-knowledge" / "IMPROVEMENT_BACKLOG.md",
                register,
            )
        test_path = self.repo / REQUIRED_TEST
        if required_test:
            _write(test_path, "def test_row407_behavior():\n    assert True\n")
        if remove_required_test:
            test_path.unlink()
        return _commit(self.repo, message)

    def run_verdict(
        self,
        *,
        candidate: str | None = None,
        main_ref: str = "refs/remotes/origin/main",
        expected_version: str = "3.66.11",
        row: int = ROW,
        required_paths: tuple[str, ...] = (REQUIRED_TEST,),
        env: dict[str, str] | None = None,
        repo: Path | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        command = [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repo or self.repo),
            "--candidate",
            candidate or self.merged_candidate,
            "--main-ref",
            main_ref,
            "--expected-version",
            expected_version,
            "--row",
            str(row),
            "--json",
        ]
        for path in required_paths:
            command.extend(("--require-path", path))
        result = _run(command, cwd=ROOT, check=False, env=env)
        return result, json.loads(result.stdout)


@pytest.fixture
def verdict_repo(tmp_path: Path) -> VerdictRepo:
    return VerdictRepo(tmp_path)


def test_integrated_requires_candidate_ancestry_version_closed_row_and_required_test(
    verdict_repo: VerdictRepo,
) -> None:
    result, body = verdict_repo.run_verdict(candidate=verdict_repo.merged_candidate)

    assert result.returncode == 0, result.stderr
    assert body["verdict"] == "INTEGRATED"
    assert body["candidate_sha"] == verdict_repo.merged_candidate
    assert body["main_sha"] == verdict_repo.main_head
    assert body["candidate_version"] == "3.66.11"
    assert body["main_version"] == "3.66.12"
    assert body["evidence"] == {
        "candidate_is_ancestor": True,
        "candidate_version_matches": True,
        "main_version_at_least_expected": True,
        "required_paths_present": True,
        "row_closed_exactly_once": True,
    }


def test_verdict_records_checkout_and_tree_identity_without_gating_on_dirt(
    verdict_repo: VerdictRepo,
    tmp_path: Path,
) -> None:
    origin_url = "https://example.invalid/evidence.git"
    _git(verdict_repo.repo, "remote", "add", "origin", origin_url)
    clean = tmp_path / "clean-checkout"
    dirty = tmp_path / "dirty-checkout"
    _git(verdict_repo.repo, "worktree", "add", "--detach", str(clean), verdict_repo.main_head)
    _git(verdict_repo.repo, "worktree", "add", "--detach", str(dirty), verdict_repo.main_head)
    _write(dirty / "untracked.txt", "deliberately dirty\n")
    clean_status = _git(clean, "status", "--porcelain=v2").stdout
    dirty_status = _git(dirty, "status", "--porcelain=v2").stdout
    assert clean.resolve() != dirty.resolve(), "precondition: checkout paths differ"
    assert clean_status == "", "precondition: clean checkout is clean"
    assert dirty_status != "", "precondition: dirty checkout is dirty"

    clean_result, clean_body = verdict_repo.run_verdict(
        repo=clean,
        candidate=verdict_repo.merged_candidate,
        main_ref=verdict_repo.main_head,
    )
    dirty_result, dirty_body = verdict_repo.run_verdict(
        repo=dirty,
        candidate=verdict_repo.merged_candidate,
        main_ref=verdict_repo.main_head,
    )

    identity_fields = {
        "hostname",
        "repository_path",
        "origin_url",
        "candidate_tree_sha",
        "main_tree_sha",
        "working_tree_cleanliness",
    }
    assert clean_result.returncode == dirty_result.returncode == 0
    assert clean_body["verdict"] == dirty_body["verdict"] == "INTEGRATED"
    assert identity_fields <= clean_body.keys()
    assert identity_fields <= dirty_body.keys()
    differing = {
        field for field in identity_fields if clean_body[field] != dirty_body[field]
    }
    assert differing == {"repository_path", "working_tree_cleanliness"}
    assert clean_body["hostname"] == dirty_body["hostname"] == socket.gethostname()
    assert clean_body["origin_url"] == dirty_body["origin_url"] == origin_url
    assert clean_body["repository_path"] == str(clean.resolve())
    assert dirty_body["repository_path"] == str(dirty.resolve())
    assert clean_body["working_tree_cleanliness"] == "clean"
    assert dirty_body["working_tree_cleanliness"] == "dirty"
    candidate_tree = _git(
        clean, "rev-parse", f"{verdict_repo.merged_candidate}^{{tree}}"
    ).stdout.strip()
    main_tree = _git(
        clean, "rev-parse", f"{verdict_repo.main_head}^{{tree}}"
    ).stdout.strip()
    assert clean_body["candidate_tree_sha"] == dirty_body["candidate_tree_sha"] == candidate_tree
    assert clean_body["main_tree_sha"] == dirty_body["main_tree_sha"] == main_tree
    assert clean_body["evidence"] == dirty_body["evidence"] == {
        "candidate_is_ancestor": True,
        "candidate_version_matches": True,
        "main_version_at_least_expected": True,
        "required_paths_present": True,
        "row_closed_exactly_once": True,
    }

    repeat_result, repeat_body = verdict_repo.run_verdict(
        repo=clean,
        candidate=verdict_repo.merged_candidate,
        main_ref=verdict_repo.main_head,
    )
    assert repeat_result.returncode == 0
    assert repeat_body == clean_body


def test_text_verdict_names_host_and_repository(verdict_repo: VerdictRepo) -> None:
    command = [
        sys.executable,
        str(SCRIPT),
        "--repo",
        str(verdict_repo.repo),
        "--candidate",
        verdict_repo.merged_candidate,
        "--main-ref",
        verdict_repo.main_head,
        "--expected-version",
        "3.66.11",
        "--row",
        str(ROW),
        "--require-path",
        REQUIRED_TEST,
    ]

    result = _run(command, cwd=ROOT, check=False)

    assert result.returncode == 0, result.stderr
    assert f"host={socket.gethostname()}" in result.stdout
    assert f"repo={verdict_repo.repo.resolve()}" in result.stdout


def test_missing_origin_is_recorded_as_unknown(verdict_repo: VerdictRepo) -> None:
    assert _git(verdict_repo.repo, "remote").stdout == ""

    result, body = verdict_repo.run_verdict(
        candidate=verdict_repo.merged_candidate,
        main_ref=verdict_repo.main_head,
    )

    assert result.returncode == 0
    assert body["origin_url"] == "UNKNOWN"


def test_transform_control_imports_verdict_without_judging_identity() -> None:
    result = _run(
        [
            sys.executable,
            "-c",
            "import runpy,sys; runpy.run_path(sys.argv[1], run_name='identity_import')",
            str(SCRIPT),
        ],
        cwd=ROOT,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_non_ancestor_candidate_is_not_integrated_even_when_other_evidence_matches(
    verdict_repo: VerdictRepo,
) -> None:
    result, body = verdict_repo.run_verdict(candidate=verdict_repo.divergent_candidate)

    assert result.returncode == 1
    assert body["verdict"] == "NOT_INTEGRATED"
    assert body["evidence"]["candidate_is_ancestor"] is False
    assert body["evidence"]["candidate_version_matches"] is True
    assert body["evidence"]["row_closed_exactly_once"] is True
    assert body["evidence"]["required_paths_present"] is True


def test_wrong_candidate_version_is_not_integrated(verdict_repo: VerdictRepo) -> None:
    result, body = verdict_repo.run_verdict(candidate=verdict_repo.base_head)

    assert result.returncode == 1
    assert body["verdict"] == "NOT_INTEGRATED"
    assert body["evidence"]["candidate_is_ancestor"] is True
    assert body["evidence"]["candidate_version_matches"] is False


def test_main_older_than_expected_version_is_not_integrated(
    verdict_repo: VerdictRepo,
) -> None:
    result, body = verdict_repo.run_verdict(
        candidate=verdict_repo.future_candidate,
        main_ref=verdict_repo.version_downgrade_main,
        expected_version="3.66.12",
    )

    assert result.returncode == 1
    assert body["verdict"] == "NOT_INTEGRATED"
    assert body["evidence"]["candidate_is_ancestor"] is True
    assert body["evidence"]["candidate_version_matches"] is True
    assert body["evidence"]["main_version_at_least_expected"] is False


@pytest.mark.parametrize(
    "main_attribute",
    ("open_row_main", "missing_row_main", "duplicate_row_main"),
)
def test_missing_non_closed_or_duplicate_row_is_not_integrated(
    verdict_repo: VerdictRepo,
    main_attribute: str,
) -> None:
    result, body = verdict_repo.run_verdict(
        main_ref=getattr(verdict_repo, main_attribute)
    )

    assert result.returncode == 1
    assert body["verdict"] == "NOT_INTEGRATED"
    assert body["evidence"]["candidate_is_ancestor"] is True
    assert body["evidence"]["row_closed_exactly_once"] is False


def test_missing_required_test_path_is_not_integrated(
    verdict_repo: VerdictRepo,
) -> None:
    result, body = verdict_repo.run_verdict(main_ref=verdict_repo.missing_test_main)

    assert result.returncode == 1
    assert body["verdict"] == "NOT_INTEGRATED"
    assert body["evidence"]["candidate_is_ancestor"] is True
    assert body["evidence"]["required_paths_present"] is False
    assert body["required_paths"] == {REQUIRED_TEST: False}


@pytest.mark.parametrize(
    ("candidate", "main_ref"),
    (("does-not-exist", "refs/remotes/origin/main"), (None, "does-not-exist")),
)
def test_unreadable_candidate_or_main_is_unknown_not_integrated(
    verdict_repo: VerdictRepo,
    candidate: str | None,
    main_ref: str,
) -> None:
    result, body = verdict_repo.run_verdict(candidate=candidate, main_ref=main_ref)

    assert result.returncode == 2
    assert body["verdict"] == "UNKNOWN"
    assert body["reason_code"] in {"CANDIDATE_UNREADABLE", "MAIN_REF_UNREADABLE"}
    assert {
        "hostname",
        "repository_path",
        "origin_url",
        "candidate_tree_sha",
        "main_tree_sha",
        "working_tree_cleanliness",
    } <= body.keys()


def test_poisoned_git_environment_cannot_manufacture_an_integrated_verdict(
    verdict_repo: VerdictRepo,
    tmp_path: Path,
) -> None:
    """Inheriting GIT_DIR can make ancestry/version evidence describe another repo."""

    poison = tmp_path / "poison"
    poison.mkdir()
    _git(poison, "init", "-b", "main")
    _git(poison, "config", "user.name", "Poison")
    _git(poison, "config", "user.email", "poison@example.invalid")
    _write(poison / "bulk_downloader" / "__init__.py", _version("3.66.99"))
    _write(
        poison / "project-knowledge" / "IMPROVEMENT_BACKLOG.md",
        _register("OPEN"),
    )
    poison_head = _commit(poison, "poison")
    env = dict(os.environ)
    env.update(
        GIT_DIR=str(poison / ".git"),
        GIT_WORK_TREE=str(poison),
        GIT_INDEX_FILE=str(tmp_path / "poison.index"),
        GIT_OBJECT_DIRECTORY=str(poison / ".git" / "objects"),
    )

    result, body = verdict_repo.run_verdict(env=env)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert body["verdict"] == "INTEGRATED"
    assert body["candidate_sha"] == verdict_repo.merged_candidate
    assert body["main_sha"] == verdict_repo.main_head
    assert body["candidate_sha"] != poison_head


def test_fatal_required_path_measurement_is_unknown_not_missing(
    verdict_repo: VerdictRepo,
    tmp_path: Path,
) -> None:
    """A Git evidence failure cannot be reported as a proven-absent path."""

    bin_dir = tmp_path / "fatal-git-bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "git"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "if 'cat-file' in args or 'ls-tree' in args:\n"
        "    os.write(2, b'fatal: injected object read failure\\n')\n"
        "    raise SystemExit(70)\n"
        "os.execv(os.environ['BD_REAL_GIT'], "
        "[os.environ['BD_REAL_GIT'], *args])\n"
    )
    wrapper.chmod(0o755)
    env = dict(os.environ)
    env.update(
        BD_REAL_GIT=REAL_GIT,
        PATH=str(bin_dir) + os.pathsep + env.get("PATH", ""),
    )

    result, body = verdict_repo.run_verdict(env=env)

    assert result.returncode == 2
    assert body["verdict"] == "UNKNOWN"
    assert body["reason_code"] == "REQUIRED_PATH_UNREADABLE"


def test_every_integration_git_step_uses_the_stable_c_locale(
    verdict_repo: VerdictRepo,
    tmp_path: Path,
) -> None:
    """578. A caller locale must not change Git evidence or diagnostics."""

    bin_dir = tmp_path / "row578-verdict-bin"
    bin_dir.mkdir()
    marker = tmp_path / "row578-verdict-calls.jsonl"
    wrapper = bin_dir / "git"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "marker = pathlib.Path(os.environ['BD_ROW578_VERDICT_MARKER'])\n"
        "with marker.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps({'lc_all': os.environ.get('LC_ALL')}) + '\\n')\n"
        "real = os.environ['BD_REAL_GIT']\n"
        "real_env = dict(os.environ)\n"
        "real_env['LC_ALL'] = 'C'\n"
        "os.execve(real, [real, *args], real_env)\n"
    )
    wrapper.chmod(0o755)
    env = dict(os.environ)
    env.update(
        BD_REAL_GIT=REAL_GIT,
        BD_ROW578_VERDICT_MARKER=str(marker),
        LC_ALL="row578-host-locale",
        PATH=str(bin_dir) + os.pathsep + env.get("PATH", ""),
    )

    result, body = verdict_repo.run_verdict(env=env)

    calls = [json.loads(line) for line in marker.read_text().splitlines()]
    assert len(calls) == 11, (
        "precondition: candidate/main resolution, both version reads, required "
        "path, register, ancestry, origin, both trees, and cleanliness must each "
        "invoke Git exactly once")
    assert calls == [{"lc_all": "C"}] * 11
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert body["verdict"] == "INTEGRATED"
