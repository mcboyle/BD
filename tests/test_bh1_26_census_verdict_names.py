"""Exercise the complete census against isolated cuts, verdicts and git responses."""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH1_26_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
TREE = "a" * 40


def _census(tmp_path, verdict_name):
    source = Path(CANDIDATE)
    assert source.is_file(), "CENSUS_CANDIDATE_MISSING"
    cuts = tmp_path / "cuts"
    verdicts = tmp_path / "verdicts"
    verdicts.mkdir()
    for name in ("row123", "rows972-973-closed", "row456a-closed"):
        cut = cuts / name
        cut.mkdir(parents=True)
        (cut / "DONE.md").write_text("VERDICT: PATCH\nBASE: " + "b" * 40 + "\n")
    (verdicts / verdict_name).write_text(
        f"VERDICT: BOARD\nINDEX-TREE: {TREE}\nSEAT: bd-review-correctness-fixture\n"
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    git = binary / "git"
    git.write_text(
        '#!/bin/bash\ncase "$*" in\n'
        "*'show origin/main:project-knowledge/IMPROVEMENT_BACKLOG.md')\n"
        "printf '| 123 | OPEN |\\n| 972 | CLOSED |\\n| 456a | CLOSED |\\n';;\n"
        f"*write-tree) echo {TREE};;\n"
        "*diff*) echo fixture-patch;;\n*) exit 91;;\nesac\n"
    )
    git.chmod(0o755)
    text = source.read_text()
    tree = ast.parse(text)
    assignments = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    verdict_pattern = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "/verdicts/" in node.value
    )
    verdict_root = verdict_pattern.split("/verdicts/", 1)[0] + "/verdicts"
    replacements = (
        (assignments["REPO"], str(tmp_path / "repo")),
        (assignments["CUTS"], str(cuts)),
        (verdict_root, str(verdicts)),
    )
    for old, new in replacements:
        assert old in text
        text = text.replace(old, new)
    assert all(old not in text for old, _ in replacements), "LIVE_PATH_REMAINS"
    script = tmp_path / "census.py"
    script.write_text(text)
    result = subprocess.run(
        [sys.executable, str(script)],
        env={**os.environ, "PATH": f"{binary}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rows = [line for line in result.stdout.splitlines() if line.startswith("row")]
    assert len(rows) == 1, f"CLOSED_ROW_LEAK: {result.stdout}"
    assert rows[0].split()[0] == "row123"
    return rows[0].split()[-1]


@pytest.mark.parametrize(
    "verdict_name",
    ["row1234-VERDICT-correctness.md", "row123-extra-VERDICT-correctness.md"],
)
def test_neighbor_cut_verdict_cannot_decide_cut(tmp_path, verdict_name):
    assert _census(tmp_path, verdict_name) == "no", "VERDICT_NAME_BLEED"


@pytest.mark.parametrize(
    "verdict_name",
    ["row123-VERDICT-correctness.md", "VERDICT-correctness-row123.md"],
)
def test_exact_cut_verdict_decides_cut(tmp_path, verdict_name):
    assert _census(tmp_path, verdict_name) == "YES", "EXACT_VERDICT_NOT_COUNTED"
