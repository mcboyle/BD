"""Row 677: bd-bandcheck distinguishes missing evidence from unsafe bands."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
from pathlib import Path


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_TOOL = _REPO / "toolchain" / "bin" / "bd-bandcheck"
_MISSING = "tests/test_row677_does_not_exist_xyz.py"


def _run(*targets: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_TOOL), "--work", str(_REPO), *targets],
        cwd=_REPO,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_missing_target_is_unknown_and_names_unmeasurable_input():
    assert _TOOL.is_file()
    assert not os.path.lexists(_REPO / _MISSING)

    result = _run(_MISSING)
    output = result.stdout + result.stderr

    assert result.returncode == 2, output
    assert f"MISSING '{_MISSING}'" in output
    assert "could not be measured" in output


def test_unsafe_directory_remains_a_measured_finding():
    assert (_REPO / "tests").is_dir()

    result = _run("tests/")
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "UNSAFE" in output
    assert "whole tests/ dir" in output


def test_present_safe_file_remains_safe():
    target = "tests/test_toolchain_534.py"
    assert (_REPO / target).is_file()

    result = _run(target)
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "all targets safe to band" in output


def test_row677_transform_control_imports_without_judging_exit_code():
    loader = importlib.machinery.SourceFileLoader("row677_bandcheck", str(_TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.check)
