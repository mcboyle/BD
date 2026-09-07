"""Row 745: lint receipts count readable tools and refuse unreadable corpus input."""
from __future__ import annotations
import os, subprocess, sys
from pathlib import Path
import pytest
BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]; TOOL = ROOT / "toolchain/bin/bd-tool-lint"
def stub(path): path.write_text("#!/usr/bin/env python3\nif __name__ == '__main__': pass\n"); path.chmod(0o755)
def test_unreadable_tool_is_cannot_evaluate_and_receipt_counts_reads(tmp_path):
    if os.geteuid() == 0: pytest.skip("root can read mode-000 fixtures")
    for name in ("bd-one", "bd-two", "bd-three"): stub(tmp_path / name)
    bad = tmp_path / "bd-two"; bad.chmod(0)
    with pytest.raises(PermissionError): bad.open("rb").read()
    r = subprocess.run([sys.executable, str(TOOL), "--bin", str(tmp_path), "--no-runtime"], text=True, capture_output=True, timeout=30); out = r.stdout + r.stderr
    assert r.returncode == 2 and "CANNOT-EVALUATE" in r.stderr and "reason=UNREADABLE" in r.stderr and "bd-two" in r.stderr, out
    assert "linted 2 of 3 discovered tools (1 unreadable: bd-two)" in out, out
