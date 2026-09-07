"""Row 746: empty CLI targets are not a safe reference to the worktree."""
from __future__ import annotations
import subprocess, sys
from pathlib import Path
BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "toolchain/bin/bd-bandcheck"
def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args], cwd=ROOT, text=True, capture_output=True, timeout=30)
def test_empty_target_is_refused_before_tree_resolution():
    assert TOOL.is_file(); assert ROOT.is_dir()
    result = run("", "--tree", str(ROOT)); out = result.stdout + result.stderr
    assert result.returncode == 2 and "EMPTY TARGET" in out and "NO TARGETS" in out and "  ok " not in out, out
def test_missing_arguments_are_usage_error_not_unmeasurable():
    assert TOOL.is_file()
    result = run(); out = result.stdout + result.stderr
    assert result.returncode == 3 and result.returncode != 2, out
