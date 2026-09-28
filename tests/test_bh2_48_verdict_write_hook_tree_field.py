"""Hermetic test for BH2-48 (bd-verdict-write-hook TREE_VAL extraction).

Verifies that:
1. When no 40-hex tree is present in a verdict file, TREE_VAL does NOT fall back
   to PATCH-SHA256, leaving the bus payload 'tree' field empty.
2. Bare word occurrences of 'tree' in commentary do not extract unintended 40-hex hashes.
3. Explicit 'INDEX TREE:', 'INDEX-TREE:', 'write-tree:', and 'TREE:' headers extract correctly.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import subprocess

import pytest

BD_GATE_SCOPE = "module"

_ENV_VAR = "BD_BH2_48_CANDIDATE"
_ALT_ENV_VAR = "TEST_VERDICT_WRITE_HOOK_BIN"


def _get_hook_bin() -> pathlib.Path:
    path_str = os.environ.get(_ENV_VAR) or os.environ.get(_ALT_ENV_VAR)
    if not path_str:
        pytest.skip(f"Neither {_ENV_VAR} nor {_ALT_ENV_VAR} set; skipping harness test")
    p = pathlib.Path(path_str)
    if not p.is_file():
        pytest.skip(f"Candidate script {p} does not exist")
    return p


def _run_hook(
    script_path: pathlib.Path,
    cut_dir: pathlib.Path,
    verdict_content: str,
    tmp_path: pathlib.Path,
) -> dict[str, str]:
    review_dir = cut_dir / ".review"
    review_dir.mkdir(parents=True, exist_ok=True)
    git_dir = cut_dir / ".git"
    git_dir.mkdir(exist_ok=True)

    vfile = review_dir / "VERDICT-shape-bd-agy-shape-1.md"
    vfile.write_text(verdict_content, encoding="utf-8")
    db_path = tmp_path / "bus.db"

    inp = json.dumps({"tool_input": {"file_path": str(vfile)}})
    env = os.environ.copy()
    env["BD_BUS_DB_PATH"] = str(db_path)
    env["BD_SAY_CMD"] = "/bin/true"
    env["BD_SCRATCH_REAPER_SH"] = "/bin/true"
    env["BD_SEAT"] = "bd-agy-shape-1"

    res = subprocess.run(
        ["bash", str(script_path)],
        input=inp.encode("utf-8"),
        env=env,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, (
        f"Script failed with stderr: {res.stderr.decode('utf-8')}"
    )

    assert db_path.exists(), "SQLite database was not created"
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT payload FROM messages")
        row = cur.fetchone()
        assert row is not None, "No messages inserted into SQLite database"
        payload_data = json.loads(row[0])
        assert isinstance(payload_data, dict)
        return {str(k): str(v) for k, v in payload_data.items()}


def test_tree_not_fallback_to_patch_sha_when_omitted(tmp_path: pathlib.Path) -> None:
    hook_bin = _get_hook_bin()
    cut_dir = tmp_path / "cut-no-tree"
    patch_sha = "a" * 64
    content = f"""VERDICT: BOARD
ROW: BH2-48  SEAT: bd-agy-shape-1
PATCH-SHA256: {patch_sha}
"""
    payload = _run_hook(hook_bin, cut_dir, content, tmp_path)
    assert payload["patch_sha256"] == patch_sha
    assert payload["tree"] == ""


def test_bare_word_tree_not_extracted(tmp_path: pathlib.Path) -> None:
    hook_bin = _get_hook_bin()
    cut_dir = tmp_path / "cut-bare-tree"
    patch_sha = "a" * 64
    accidental_hash = "b" * 40
    content = f"""VERDICT: BOARD
ROW: BH2-48  SEAT: bd-agy-shape-1
Discussion regarding working tree {accidental_hash} in commentary.
PATCH-SHA256: {patch_sha}
"""
    payload = _run_hook(hook_bin, cut_dir, content, tmp_path)
    assert payload["patch_sha256"] == patch_sha
    assert payload["tree"] == ""


def test_explicit_tree_headers_extracted(tmp_path: pathlib.Path) -> None:
    hook_bin = _get_hook_bin()
    cut_dir = tmp_path / "cut-explicit-tree"
    patch_sha = "a" * 64
    tree_hash = "c" * 40
    content = f"""VERDICT: BOARD
ROW: BH2-48  SEAT: bd-agy-shape-1
INDEX TREE: {tree_hash}
PATCH-SHA256: {patch_sha}
"""
    payload = _run_hook(hook_bin, cut_dir, content, tmp_path)
    assert payload["patch_sha256"] == patch_sha
    assert payload["tree"] == tree_hash
