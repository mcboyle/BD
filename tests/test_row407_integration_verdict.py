"""Row 407: only ancestry-backed evidence may emit INTEGRATED."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bd_integration_verdict.py"
REAL_GIT = shutil.which("git")
assert REAL_GIT is not None
ROW = 407
REQUIRED_TEST = "tests/test_row407_behavior.py"


def _run(
    argv: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
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
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        command = [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(self.repo),
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
        result = _run(command, cwd=ROOT, check=False)
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
