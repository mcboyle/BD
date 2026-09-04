"""Row 678: every bd-bandcheck exclusion still names a tracked test file."""

from __future__ import annotations

import copy
import importlib.machinery
import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_TOOL = _REPO / "toolchain" / "bin" / "bd-bandcheck"
_EXPECTED_COUNTS = {"HANG": 1, "NOT_A_FILE": 1, "LEAK_PAIRS": 2}


@dataclass(frozen=True)
class AuditResult:
    code: int
    counts: dict[str, int]
    resolved: tuple[str, ...]
    unresolved: tuple[str, ...]
    diagnostic: str


def _load_bandcheck(path: Path = _TOOL):
    loader = importlib.machinery.SourceFileLoader("bd_bandcheck_row678", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _table_names(module) -> dict[str, list[str]]:
    redirects = []
    for text in module.NOT_A_FILE.values():
        redirects.extend(re.findall(r"tests/(test[^ )]+\.py)", text))
    leak_names = [name for left, right, _why in module.LEAK_PAIRS for name in left | right]
    return {
        "HANG": list(module.HANG),
        "NOT_A_FILE": redirects,
        "LEAK_PAIRS": leak_names,
    }


def _audit_exclusions(
    repo: Path, tables: dict[str, list[str]] | None = None
) -> AuditResult:
    tool = repo / "toolchain" / "bin" / "bd-bandcheck"
    if tables is None:
        if not tool.is_file():
            return AuditResult(2, {}, (), (), f"UNKNOWN: cannot locate {tool}")
        try:
            tables = _table_names(_load_bandcheck(tool))
        except (ImportError, OSError, SyntaxError) as exc:
            return AuditResult(2, {}, (), (), f"UNKNOWN: cannot load {tool}: {exc}")

    counts = {name: len(entries) for name, entries in tables.items()}
    try:
        tracked_run = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "tests"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return AuditResult(2, counts, (), (), f"UNKNOWN: cannot list tracked tests: {exc}")
    if tracked_run.returncode != 0:
        diagnostic = (tracked_run.stderr or tracked_run.stdout).strip()
        return AuditResult(2, counts, (), (), f"UNKNOWN: cannot list tracked tests: {diagnostic}")

    tracked = set(tracked_run.stdout.splitlines())
    resolved = []
    unresolved = []
    for table_name, entries in tables.items():
        for name in entries:
            relative = f"tests/{name}"
            label = f"{table_name}:{relative}"
            path = repo / relative
            if not path.is_file() or relative not in tracked:
                unresolved.append(label)
            else:
                resolved.append(label)
    if counts != _EXPECTED_COUNTS:
        detail = f"FAIL: exclusion denominator changed: {counts}"
        if unresolved:
            detail += "; unresolved exclusions: " + ", ".join(unresolved)
        return AuditResult(
            1,
            counts,
            tuple(resolved),
            tuple(unresolved),
            detail,
        )
    if unresolved:
        return AuditResult(
            1,
            counts,
            tuple(resolved),
            tuple(unresolved),
            "FAIL: unresolved exclusions: " + ", ".join(unresolved),
        )
    return AuditResult(
        0,
        counts,
        tuple(resolved),
        (),
        f"PASS: all {len(resolved)} exclusion paths are tracked files",
    )


def test_current_exclusion_population_is_complete_and_resolves():
    result = _audit_exclusions(_REPO)
    if result.code == 2:
        pytest.exit(result.diagnostic, returncode=2)
    assert result.code == 0, result.diagnostic
    assert result.counts == _EXPECTED_COUNTS
    assert len(result.resolved) == 4
    assert result.unresolved == ()
    assert "NOT_A_FILE:tests/test_route_index_in_sync.py" in result.resolved


def test_a_nonexistent_exclusion_is_named_as_unresolved():
    module = _load_bandcheck()
    tables = _table_names(module)
    assert {name: len(entries) for name, entries in tables.items()} == _EXPECTED_COUNTS
    broken = copy.deepcopy(tables)
    broken["HANG"].append("test_row678_does_not_exist.py")
    result = _audit_exclusions(_REPO, broken)
    assert result.code == 1
    assert result.unresolved == (
        "HANG:tests/test_row678_does_not_exist.py",
    )
    assert result.diagnostic == (
        "FAIL: exclusion denominator changed: "
        "{'HANG': 2, 'NOT_A_FILE': 1, 'LEAK_PAIRS': 2}; unresolved exclusions: "
        "HANG:tests/test_row678_does_not_exist.py"
    )


def test_missing_bandcheck_is_unknown_not_pass(tmp_path):
    result = _audit_exclusions(tmp_path)
    assert result.code == 2
    assert result.resolved == ()
    assert result.unresolved == ()
    assert result.diagnostic == (
        f"UNKNOWN: cannot locate {tmp_path / 'toolchain' / 'bin' / 'bd-bandcheck'}"
    )


def test_row678_transform_control_only_loads_bandcheck():
    module = _load_bandcheck()
    assert module.__name__ == "bd_bandcheck_row678"
