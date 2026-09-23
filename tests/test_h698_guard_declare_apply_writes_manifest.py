"""H698: `bd-guard-declare --apply` writes the declared pin into guards.json, with the reason, and bd-guardcheck agrees.

The STATE.json flow it was written for is retired, so --apply had become CANNOT-EVALUATE (rc 2) on every tree while the
onboarding briefs still name it as THE way to bump a pin. Every case runs on a scratch copy of the real manifest and the
real guard files; nothing in the repository is written.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "toolchain/bin/bd-guard-declare"
CHECK = ROOT / "toolchain/bin/bd-guardcheck"


def _tree(tmp_path: Path) -> tuple[Path, str]:
    shutil.copy2(ROOT / "guards.json", tmp_path / "guards.json")
    manifest = json.loads((tmp_path / "guards.json").read_text())
    for rel in manifest["guards"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, tmp_path / rel)
    guard = next(iter(manifest["guards"]))
    (tmp_path / guard).write_bytes((tmp_path / guard).read_bytes() + b"\n# declared change\n")
    return tmp_path, guard


def _run(tool: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(tool), *args], text=True, capture_output=True, timeout=60)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_apply_rewrites_the_pin_records_the_reason_and_guardcheck_passes(tmp_path):
    tree, guard = _tree(tmp_path)
    before = json.loads((tree / "guards.json").read_text())
    drifted = _run(CHECK, "--tree", str(tree))
    assert drifted.returncode != 0, "control: the drifted scratch tree must fail bd-guardcheck before the declaration"
    r = _run(TOOL, "--file", guard, "--root", str(tree), "--apply", "--reason", "h698 fixture change")
    assert r.returncode == 0, r.stdout + r.stderr
    after = json.loads((tree / "guards.json").read_text())
    assert after["guards"][guard] == _sha(tree / guard)
    assert {k: v for k, v in after["guards"].items() if k != guard} == {
        k: v for k, v in before["guards"].items() if k != guard}, "the other six pins must not move"
    rec = after["guards_changelog"][-1]
    assert (rec["file"], rec["old_sha256"], rec["new_sha256"], rec["reason"]) == (
        guard, before["guards"][guard], _sha(tree / guard), "h698 fixture change")
    ok = _run(CHECK, "--tree", str(tree))
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_apply_without_reason_or_for_a_non_guard_writes_nothing(tmp_path):
    tree, guard = _tree(tmp_path)
    pinned = _sha(tree / "guards.json")
    r = _run(TOOL, "--file", guard, "--root", str(tree), "--apply")
    assert r.returncode == 1 and "--reason" in r.stdout + r.stderr, r.stdout + r.stderr
    n = _run(TOOL, "--file", "bulk_downloader/not_a_guard.py", "--root", str(tree), "--apply", "--reason", "x")
    assert n.returncode == 1 and "REFUSED" in n.stdout + n.stderr
    assert _sha(tree / "guards.json") == pinned


def test_apply_with_no_drift_is_a_no_op(tmp_path):
    tree, guard = _tree(tmp_path)
    shutil.copy2(ROOT / guard, tree / guard)
    pinned = _sha(tree / "guards.json")
    r = _run(TOOL, "--file", guard, "--root", str(tree), "--apply", "--reason", "nothing changed")
    assert r.returncode == 0 and "no change" in r.stdout
    assert _sha(tree / "guards.json") == pinned
