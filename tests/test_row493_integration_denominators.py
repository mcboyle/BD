"""Row 493: integration evidence cannot pass over an empty denominator."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bd_integration_verdict.py"
REAL_GIT = shutil.which("git")
assert REAL_GIT is not None
ROW = 493
REQUIRED_PATH = "tests/test_row493_contract.py"
BD_GATE_SCOPE = "module"


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


def _git(cwd: Path, *args: str) -> str:
    return _run([REAL_GIT, *args], cwd=cwd).stdout


def _write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def _register(status: str, *, copies: int = 1) -> str:
    header = (
        "# Improvement Backlog\n\n"
        "| ID | Status | Evidence |\n"
        "|---:|---|---|\n"
    )
    row = f"| {ROW} | {status} | row 493 evidence |\n"
    return header + row * copies


def _commit(cwd: Path, message: str) -> str:
    _git(cwd, "add", "--all")
    _git(cwd, "commit", "-m", message)
    return _git(cwd, "rev-parse", "HEAD").strip()


class IntegrationCase:
    def __init__(self, tmp_path: Path) -> None:
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "main")
        _git(self.repo, "config", "user.name", "Row 493 Test")
        _git(self.repo, "config", "user.email", "row493@example.invalid")
        _write(
            self.repo / "bulk_downloader" / "__init__.py",
            '__version__ = "3.66.493"\n',
        )
        _write(
            self.repo / "project-knowledge" / "IMPROVEMENT_BACKLOG.md",
            _register("OPEN"),
        )
        self.open_main = _commit(self.repo, "open row without required path")
        self.candidate = self.open_main

        _write(
            self.repo / "project-knowledge" / "IMPROVEMENT_BACKLOG.md",
            _register("CLOSED @1411"),
        )
        _write(self.repo / REQUIRED_PATH, "def test_row493_contract():\n    assert True\n")
        self.integrated_main = _commit(self.repo, "close row with required path")

        _write(
            self.repo / "project-knowledge" / "IMPROVEMENT_BACKLOG.md",
            _register("CLOSED @1411", copies=2),
        )
        self.duplicate_main = _commit(self.repo, "duplicate closed row")
        self.verdict_calls = 0

    def run_verdict(
        self,
        *,
        main_ref: str,
        row: int | None,
        required_paths: tuple[str, ...],
        as_json: bool,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(self.repo),
            "--candidate",
            self.candidate,
            "--main-ref",
            main_ref,
            "--expected-version",
            "3.66.493",
        ]
        if row is not None:
            command.extend(("--row", str(row)))
        for path in required_paths:
            command.extend(("--require-path", path))
        if as_json:
            command.append("--json")
        self.verdict_calls += 1
        return _run(command, cwd=ROOT, check=False)

    def assert_open_main_preconditions(self) -> None:
        path_bytes = subprocess.run(
            [
                REAL_GIT,
                "-C",
                str(self.repo),
                "--literal-pathspecs",
                "ls-tree",
                "-z",
                self.open_main,
                "--",
                REQUIRED_PATH,
            ],
            capture_output=True,
            check=True,
        ).stdout
        register = _git(
            self.repo,
            "show",
            f"{self.open_main}:project-knowledge/IMPROVEMENT_BACKLOG.md",
        )
        matches = re.findall(
            rf"^\|\s*{ROW}\s*\|\s*([^|]+?)\s*\|",
            register,
            flags=re.MULTILINE,
        )
        assert len(path_bytes) == 0, (
            "precondition: the open main tree must contain zero bytes of ls-tree "
            "evidence for the required path"
        )
        assert matches == ["OPEN"], (
            "precondition: the register must contain exactly one row 493 and it "
            "must be OPEN"
        )

    def assert_integrated_main_preconditions(self) -> None:
        register = _git(
            self.repo,
            "show",
            f"{self.integrated_main}:project-knowledge/IMPROVEMENT_BACKLOG.md",
        )
        matches = re.findall(
            rf"^\|\s*{ROW}\s*\|\s*([^|]+?)\s*\|",
            register,
            flags=re.MULTILINE,
        )
        path_bytes = subprocess.run(
            [
                REAL_GIT,
                "-C",
                str(self.repo),
                "--literal-pathspecs",
                "ls-tree",
                "-z",
                self.integrated_main,
                "--",
                REQUIRED_PATH,
            ],
            capture_output=True,
            check=True,
        ).stdout
        assert matches == ["CLOSED @1411"]
        assert path_bytes.count(b"\0") == 1


@pytest.fixture
def integration_case(tmp_path: Path) -> IntegrationCase:
    return IntegrationCase(tmp_path)


def test_omitted_denominators_are_unknown_before_any_evidence_is_scored(
    integration_case: IntegrationCase,
) -> None:
    integration_case.assert_open_main_preconditions()

    omitted_json = integration_case.run_verdict(
        main_ref=integration_case.open_main,
        row=None,
        required_paths=(),
        as_json=True,
    )
    omitted_text = integration_case.run_verdict(
        main_ref=integration_case.open_main,
        row=None,
        required_paths=(),
        as_json=False,
    )
    supplied = integration_case.run_verdict(
        main_ref=integration_case.open_main,
        row=ROW,
        required_paths=(REQUIRED_PATH,),
        as_json=True,
    )

    omitted_body = json.loads(omitted_json.stdout)
    supplied_body = json.loads(supplied.stdout)
    assert integration_case.verdict_calls == 3
    assert (
        omitted_json.returncode,
        omitted_body["verdict"],
        omitted_body.get("reason_code"),
        omitted_text.returncode,
        omitted_text.stdout.strip(),
    ) == (
        2,
        "UNKNOWN",
        "ROW_AND_REQUIRED_PATH_DENOMINATORS_EMPTY",
        2,
        "UNKNOWN ROW_AND_REQUIRED_PATH_DENOMINATORS_EMPTY: evidence denominators "
        "must be nonzero: row_denominator=0 required_path_denominator=0",
    )
    assert "evidence" not in omitted_body
    assert supplied.returncode == 1
    assert supplied_body["verdict"] == "NOT_INTEGRATED"
    assert supplied_body["row"] == ROW
    assert supplied_body["required_paths"] == {REQUIRED_PATH: False}
    assert {
        name for name, value in supplied_body["evidence"].items() if not value
    } == {"required_paths_present", "row_closed_exactly_once"}


def test_one_measured_closed_row_and_present_path_remains_integrated(
    integration_case: IntegrationCase,
) -> None:
    integration_case.assert_integrated_main_preconditions()

    result = integration_case.run_verdict(
        main_ref=integration_case.integrated_main,
        row=ROW,
        required_paths=(REQUIRED_PATH,),
        as_json=True,
    )
    text_result = integration_case.run_verdict(
        main_ref=integration_case.integrated_main,
        row=ROW,
        required_paths=(REQUIRED_PATH,),
        as_json=False,
    )

    body = json.loads(result.stdout)
    assert integration_case.verdict_calls == 2
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert body["verdict"] == "INTEGRATED"
    assert len(body["required_paths"]) == 1
    assert sum(value is True for value in body["evidence"].values()) == 5
    assert text_result.returncode == 0
    assert text_result.stdout.strip().endswith(
        "row_denominator=1 required_path_denominator=1"
    )


@pytest.mark.parametrize(
    ("row", "required_paths", "reason_code", "row_count", "path_count"),
    (
        (None, (REQUIRED_PATH,), "ROW_DENOMINATOR_EMPTY", 0, 1),
        (ROW, (), "REQUIRED_PATH_DENOMINATOR_EMPTY", 1, 0),
    ),
)
def test_each_missing_denominator_is_independently_unknown(
    integration_case: IntegrationCase,
    row: int | None,
    required_paths: tuple[str, ...],
    reason_code: str,
    row_count: int,
    path_count: int,
) -> None:
    integration_case.assert_integrated_main_preconditions()

    result = integration_case.run_verdict(
        main_ref=integration_case.integrated_main,
        row=row,
        required_paths=required_paths,
        as_json=True,
    )

    body = json.loads(result.stdout)
    assert integration_case.verdict_calls == 1
    assert result.returncode == 2
    assert body["verdict"] == "UNKNOWN"
    assert body["reason_code"] == reason_code
    assert body["message"].endswith(
        f"row_denominator={row_count} required_path_denominator={path_count}"
    )
    assert "evidence" not in body


def test_duplicate_measured_row_stays_distinct_from_an_empty_denominator(
    integration_case: IntegrationCase,
) -> None:
    register = _git(
        integration_case.repo,
        "show",
        f"{integration_case.duplicate_main}:project-knowledge/IMPROVEMENT_BACKLOG.md",
    )
    matches = re.findall(
        rf"^\|\s*{ROW}\s*\|\s*([^|]+?)\s*\|",
        register,
        flags=re.MULTILINE,
    )
    assert matches == ["CLOSED @1411", "CLOSED @1411"]
    assert _git(
        integration_case.repo,
        "ls-tree",
        "--name-only",
        integration_case.duplicate_main,
        "--",
        REQUIRED_PATH,
    ).strip() == REQUIRED_PATH

    result = integration_case.run_verdict(
        main_ref=integration_case.duplicate_main,
        row=ROW,
        required_paths=(REQUIRED_PATH,),
        as_json=True,
    )

    body = json.loads(result.stdout)
    assert integration_case.verdict_calls == 1
    assert result.returncode == 1
    assert body["verdict"] == "NOT_INTEGRATED"
    assert body.get("reason_code") != "ROW_AND_REQUIRED_PATH_DENOMINATORS_EMPTY"
    assert body["evidence"]["required_paths_present"] is True
    assert body["evidence"]["row_closed_exactly_once"] is False
    assert sum(value is False for value in body["evidence"].values()) == 1
