"""Unit tests for toolchain/bin/bd-axtree-distill.

Verifies HTML noise stripping, accessibility node extraction, action mapping, and stats.
Fleet Rule 21 compliant: zero site interactions touched.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BIN_PATH = Path(__file__).resolve().parent.parent / "toolchain" / "bin" / "bd-axtree-distill"

SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head><style>body { background: #000; }</style><script>alert('test');</script></head>
<body>
    <nav><a href="/home">Home Page</a></nav>
    <main>
        <button id="submit-btn" aria-label="Confirm Action">Submit</button>
        <input type="text" id="query-input" placeholder="Search..." />
    </main>
</body>
</html>
"""


def test_bd_axtree_distill_compact():
    p = subprocess.run(
        [sys.executable, str(BIN_PATH)],
        input=SAMPLE_HTML,
        text=True,
        capture_output=True,
        check=False,
    )
    assert p.returncode == 0, f"Failed with stderr: {p.stderr}"
    assert "Confirm Action" in p.stdout
    assert "Search..." in p.stdout
    assert "Home Page" in p.stdout
    # Ensure noise was pruned
    assert "alert" not in p.stdout
    assert "background" not in p.stdout


def test_bd_axtree_distill_json():
    p = subprocess.run(
        [sys.executable, str(BIN_PATH), "--format", "json"],
        input=SAMPLE_HTML,
        text=True,
        capture_output=True,
        check=False,
    )
    assert p.returncode == 0, f"Failed with stderr: {p.stderr}"
    nodes = json.loads(p.stdout)
    assert isinstance(nodes, list)
    assert len(nodes) >= 3


def test_bd_axtree_distill_stats():
    p = subprocess.run(
        [sys.executable, str(BIN_PATH), "--stats"],
        input=SAMPLE_HTML,
        text=True,
        capture_output=True,
        check=False,
    )
    assert p.returncode == 0
    assert "[AXTree Stats]" in p.stderr
    assert "Reduction:" in p.stderr


def test_bd_axtree_distill_empty():
    p = subprocess.run(
        [sys.executable, str(BIN_PATH)],
        input="",
        text=True,
        capture_output=True,
        check=False,
    )
    assert p.returncode == 1
    assert "Empty HTML input" in p.stderr
