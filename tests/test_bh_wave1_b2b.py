"""BH-007 (bughunt wave 1, bd-worker-B2-B): regression for
FINDING-BH-bd-agy-audit-1-007 (classify_toolchain TREE must be the repo root
even when a parent directory name contains "toolchain").
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import importlib.util
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_classify_toolchain(repo: Path):
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "classify_toolchain.py", repo / "scripts" / "classify_toolchain.py")
    spec = importlib.util.spec_from_file_location("classify_toolchain_bh007", repo / "scripts" / "classify_toolchain.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_classify_toolchain_tree_survives_toolchain_in_parent_path(tmp_path):
    repo = tmp_path / "toolchain_repo" / "BulkDownloader"
    mod = _load_classify_toolchain(repo)
    assert mod.BIN == str(repo / "toolchain" / "bin")
    assert mod.TREE == str(repo), f"BH-007 TREE corrupted: {mod.TREE!r} != {str(repo)!r}"


def test_classify_toolchain_tree_plain_path(tmp_path):
    repo = tmp_path / "plain" / "BulkDownloader"
    mod = _load_classify_toolchain(repo)
    assert mod.TREE == str(repo)
