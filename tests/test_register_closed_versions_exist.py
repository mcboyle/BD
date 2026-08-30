"""The integrator owns register close tags, and every such tag resolves.

A worker can edit its row before integration, but it cannot know which release
will win the serialized cut lane.  The close command therefore treats an
existing CLOSED tag as untrusted input and stamps the integrator's version.
The repository-wide half reconciles every CLOSED row with CHANGELOG headings.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import ModuleType

import pytest


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
BACKLOG = ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
CHANGELOG = ROOT / "CHANGELOG.md"
CLOSE_TOOL = ROOT / "toolchain" / "bin" / "bd-register-close"
PARSER = ROOT / "project-knowledge" / "build_current_overlay.py"

_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|", re.MULTILINE)
_PHYSICAL_CLOSED_ROW = re.compile(r"^\|\s*\d+\s*\|\s*CLOSED\b", re.MULTILINE)
_CLOSED_TAG = re.compile(r"^CLOSED\s+@(\d+)\b")
_CHANGELOG_HEADING = re.compile(r"^## v\d+\.\d+\.(\d+)\b", re.MULTILINE)


@dataclass(frozen=True)
class ClosedVersionAudit:
    closed_rows: int
    tagged_rows: int
    checked_rows: int
    missing: tuple[tuple[int, int], ...]


def _audit_closed_versions(register: str, changelog: str) -> ClosedVersionAudit:
    versions = {
        int(match.group(1)) for match in _CHANGELOG_HEADING.finditer(changelog)
    }
    parsed_closed = [
        (int(match.group(1)), match.group(2).strip())
        for match in _ROW.finditer(register)
        if match.group(2).strip().startswith("CLOSED")
    ]
    tagged = []
    for row_id, status in parsed_closed:
        if tag := _CLOSED_TAG.match(status):
            tagged.append((row_id, int(tag.group(1))))
    missing = tuple(
        (row_id, release) for row_id, release in tagged if release not in versions
    )
    physical_closed = len(_PHYSICAL_CLOSED_ROW.findall(register))
    return ClosedVersionAudit(physical_closed, len(tagged), len(tagged), missing)


def _assert_closed_versions(register: str, changelog: str) -> ClosedVersionAudit:
    audit = _audit_closed_versions(register, changelog)
    assert audit.closed_rows > 0, "register gate examined zero CLOSED rows"
    assert audit.tagged_rows == audit.closed_rows, audit
    assert audit.checked_rows == audit.closed_rows, audit
    assert not audit.missing, (
        f"CLOSED rows cite absent CHANGELOG versions: {audit.missing}"
    )
    return audit


def _synthetic_repo(tmp_path: Path, status: str) -> Path:
    repo = tmp_path / "repo"
    knowledge = repo / "project-knowledge"
    knowledge.mkdir(parents=True)
    shutil.copyfile(PARSER, knowledge / "build_current_overlay.py")
    digest = hashlib.sha256(b"263").hexdigest()
    opened = int(status == "OPEN")
    (knowledge / "IMPROVEMENT_BACKLOG.md").write_text(
        "# fixture\n\n"
        f"<!-- canonical-task-register schema=1 rows=1 open={opened} "
        f"ids-sha256={digest} -->\n\n"
        f"| 263 | {status} | fixture row |\n",
        encoding="ascii",
    )
    return repo


def _run_close(
    repo: Path, version: str = "3.66.4321"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CLOSE_TOOL),
            "--repo",
            str(repo),
            "--row",
            "263",
            "--version",
            version,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def _load_close_module() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader(
        "test_bd_register_close", str(CLOSE_TOOL)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_already_closed_worker_row_is_restamped_with_the_integrators_version(
    tmp_path: Path,
) -> None:
    repo = _synthetic_repo(tmp_path, "CLOSED @1111")
    result = _run_close(repo)
    assert result.returncode == 0, result.stderr
    register = (repo / "project-knowledge" / "IMPROVEMENT_BACKLOG.md").read_text(
        encoding="ascii"
    )
    assert "| 263 | CLOSED @4321 | fixture row |" in register
    assert "CLOSED @1111" not in register
    assert "rows=1 open=0" in register


def test_an_open_row_is_closed_with_the_integrators_version(tmp_path: Path) -> None:
    repo = _synthetic_repo(tmp_path, "OPEN")
    result = _run_close(repo)
    assert result.returncode == 0, result.stderr
    register = (repo / "project-knowledge" / "IMPROVEMENT_BACKLOG.md").read_text(
        encoding="ascii"
    )
    assert "| 263 | CLOSED @4321 | fixture row |" in register
    assert "rows=1 open=0" in register


def test_post_replace_directory_fsync_failure_reports_commit_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _synthetic_repo(tmp_path, "OPEN")
    register = repo / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
    module = _load_close_module()
    real_open, real_fsync = os.open, os.fsync
    directory_opens = 0
    sync_descriptor: int | None = None

    def injected_open(path: object, flags: int, *args: object) -> int:
        nonlocal directory_opens, sync_descriptor
        descriptor = real_open(path, flags, *args)
        if Path(path) == register.parent and flags & getattr(os, "O_DIRECTORY", 0):
            directory_opens += 1
            if directory_opens == 2:
                sync_descriptor = descriptor
        return descriptor

    def injected_fsync(descriptor: int) -> None:
        if descriptor == sync_descriptor:
            raise OSError("injected directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(module.os, "open", injected_open)
    monkeypatch.setattr(module.os, "fsync", injected_fsync)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(CLOSE_TOOL),
            "--repo",
            str(repo),
            "--row",
            "263",
            "--version",
            "3.66.4321",
        ],
    )

    assert module.main() == 3
    captured = capsys.readouterr()
    assert "COMMIT UNCERTAIN" in captured.err
    assert "directory fsync" in captured.err
    assert "| 263 | CLOSED @4321 | fixture row |" in register.read_text(
        encoding="ascii"
    )


def test_restamping_preserves_a_closed_rows_status_annotation(tmp_path: Path) -> None:
    repo = _synthetic_repo(
        tmp_path, "CLOSED @1111 -- PARTIAL, and remainder is named"
    )
    result = _run_close(repo)
    assert result.returncode == 0, result.stderr
    register = (repo / "project-knowledge" / "IMPROVEMENT_BACKLOG.md").read_text(
        encoding="ascii"
    )
    assert "CLOSED @4321 -- PARTIAL, and remainder is named" in register
    assert "CLOSED @1111" not in register


def test_every_closed_row_names_a_version_that_exists_in_the_changelog() -> None:
    audit = _assert_closed_versions(
        BACKLOG.read_text(encoding="ascii"), CHANGELOG.read_text(encoding="utf-8")
    )
    assert audit.checked_rows == audit.closed_rows > 0


def test_a_closed_tag_for_an_absent_version_fails_the_gate() -> None:
    register = "| 1 | CLOSED @9999 | impossible release |\n"
    changelog = "## v3.66.4321 - real release\n"
    audit = _audit_closed_versions(register, changelog)
    assert audit == ClosedVersionAudit(1, 1, 1, ((1, 9999),))
    with pytest.raises(AssertionError, match="9999"):
        _assert_closed_versions(register, changelog)


def test_a_closed_tag_for_an_existing_version_is_a_negative_control() -> None:
    audit = _assert_closed_versions(
        "| 1 | CLOSED @4321 | real release |\n",
        "## v3.66.4321 - real release\n",
    )
    assert audit == ClosedVersionAudit(1, 1, 1, ())


def test_row_250_points_to_the_changelog_release_that_names_its_change() -> None:
    register = BACKLOG.read_text(encoding="ascii")
    row = next(match for match in _ROW.finditer(register) if match.group(1) == "250")
    release = int(_CLOSED_TAG.match(row.group(2).strip()).group(1))
    changelog = CHANGELOG.read_text(encoding="utf-8")
    headings = list(_CHANGELOG_HEADING.finditer(changelog))
    heading = next(match for match in headings if int(match.group(1)) == release)
    following = next(
        (match.start() for match in headings if match.start() > heading.start()),
        len(changelog),
    )
    section = changelog[heading.start() : following].casefold()
    assert "socket recorder" in section


def test_transform_control_only_exercises_the_close_commands_help() -> None:
    result = subprocess.run(
        [sys.executable, str(CLOSE_TOOL), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--version" in result.stdout
