"""Tombstone the zip-era release shell wrapper after its retirement.

The supported packaging implementation is ``tools/build_release.py``.  The
shell wrapper froze v3.66.137/v3.66.148 zip paths and had no execution callers;
restoring it would revive a second, contradictory release path.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import warnings
from pathlib import Path

import pytest

from python_source import contains_assembled
from tracked_source import tracked_source_files


REPO_ROOT = Path(__file__).resolve().parents[1]
RETIRED = "scripts/" + "build_release.sh"
CURRENT = "tools/build_release.py"
_GATE_RELATIVE = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()
_CD_THEN_RELATIVE_WRAPPER = re.compile(
    r"(?:^|[;&|])\s*cd\s+(?:--\s+)?(?:\./)?(?:scripts/?|['\"]scripts/?['\"])\s*"
    r"(?:&&|;)\s*\./build_release\.sh(?:\s|$)"
)


def _tracked_paths() -> set[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    paths = {path for path in proc.stdout.split("\0") if path}
    assert len(paths) > 1_000, "tracked-file denominator unexpectedly collapsed"
    return paths


def test_zip_era_release_wrapper_is_not_tracked():
    assert RETIRED not in _tracked_paths()


def test_current_release_documentation_names_the_python_builder():
    readme = (REPO_ROOT / "project-knowledge" / "README.md").read_text(
        encoding="utf-8"
    )
    assert CURRENT in readme
    assert RETIRED not in readme


def _execution_references(entries, read_text) -> list[str]:
    offenders: list[str] = []
    for relative, kind in entries:
        source = read_text(relative)
        if kind == "python":
            direct_reference = False
            if RETIRED in source:
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.Constant)
                        and isinstance(node.value, str)
                        and RETIRED in node.value
                    ):
                        offenders.append(f"{relative}:{node.lineno}")
                        direct_reference = True
            if not direct_reference and relative != _GATE_RELATIVE:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    assembled_reference = contains_assembled(
                        REPO_ROOT / relative, RETIRED
                    )
                if assembled_reference:
                    offenders.append(f"{relative}:assembled")
        else:
            for lineno, line in enumerate(source.splitlines(), 1):
                executable = line.split("#", 1)[0]
                # This source floor catches a literal same-line cd into scripts
                # followed by a relative invocation. Variables, aliases, eval,
                # and line continuations remain outside its declared surface.
                if RETIRED in executable or _CD_THEN_RELATIVE_WRAPPER.search(
                    executable
                ):
                    offenders.append(f"{relative}:{lineno}")
    return offenders


def test_no_tracked_source_executes_the_retired_wrapper():
    entries = tracked_source_files(REPO_ROOT)
    assert len(entries) > 100, "tracked-source denominator collapsed"
    offenders = _execution_references(
        entries,
        lambda relative: (REPO_ROOT / relative).read_text(
            encoding="utf-8", errors="replace"
        ),
    )
    assert not offenders, f"retired release wrapper is still executable: {offenders}"


def test_source_denominator_failure_cannot_report_the_tombstone_clean(monkeypatch):
    monkeypatch.setattr("test_v3_66_1192_build_release_sh_stays_retired.tracked_source_files", lambda _root: [])
    with pytest.raises(AssertionError, match="denominator"):
        test_no_tracked_source_executes_the_retired_wrapper()


def test_execution_reference_detector_fires_for_python_and_shell_subjects():
    entries = [("subject.py", "python"), ("subject.sh", "shell")]
    sources = {
        "subject.py": f'x = "{RETIRED}"\n',
        "subject.sh": f'run {RETIRED}\n# ignored {RETIRED}\n',
    }
    assert _execution_references(entries, sources.__getitem__) == [
        "subject.py:1", "subject.sh:1"
    ]
    assert _execution_references(
        [("comment.sh", "shell")], lambda _path: f"# {RETIRED}\n"
    ) == []


def test_execution_reference_detector_catches_assembled_python_evasion(
    tmp_path, monkeypatch
):
    source = 'target = "scripts/" + "build_release.sh"\n'
    subject = tmp_path / "subject.py"
    subject.write_text(source, encoding="utf-8")
    assert RETIRED not in source
    assert contains_assembled(subject, RETIRED)
    monkeypatch.setattr(
        "test_v3_66_1192_build_release_sh_stays_retired.REPO_ROOT", tmp_path
    )
    assert _execution_references(
        [("subject.py", "python")], lambda _path: source
    ) == ["subject.py:assembled"]


def test_execution_reference_detector_catches_cd_then_relative_shell_evasion():
    source = "cd scripts && ./build_release.sh --dist\n"
    assert RETIRED not in source
    assert _execution_references(
        [("subject.sh", "shell")], lambda _path: source
    ) == ["subject.sh:1"]


def test_surviving_python_builder_is_tracked_and_guard_pinned():
    assert CURRENT in _tracked_paths()
    builder = REPO_ROOT / CURRENT
    guards = json.loads((REPO_ROOT / "guards.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(builder.read_bytes()).hexdigest() == guards["guards"][CURRENT]


def test_the_retirement_is_documented_not_just_done():
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    backlog = (REPO_ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG.md").read_text(encoding="ascii")
    assert RETIRED in changelog and "retire" in changelog.lower()
    assert "| 115 | CLOSED" in backlog and RETIRED in backlog


BD_GATE_SCOPE = "repo-wide"
