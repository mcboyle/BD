"""Exercise the complete census against isolated cuts, verdicts and git responses.

bh1-26 follow-up: bd-pm-census.py read every global verdict named "*<cut>.md", so another
cut's verdict ("VERDICT-correctness-foo-row123.md", "VERDICT-correctness-xrow123.md") was
counted for cut row123 and, with a matching tree, decided it. Only the two named shapes
bd-boardgate reads count now -- "<cut>-VERDICT-*.md" and "VERDICT-*-<cut>.md" -- and a name
ending in a longer cut's name belongs to that cut. The census honours BD_PM_CENSUS_CUTS and
BD_PM_CENSUS_VERDICTS, so the fixture points it at tmp dirs instead of rewriting its source.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH1_26_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
TREE = "a" * 40
CUT_NAMES = ("row123", "rows972-973-closed", "row456a-closed", "foo-row123", "row123[1]")


def _census(tmp_path, verdict_name):
    source = Path(CANDIDATE)
    assert source.is_file(), "CENSUS_CANDIDATE_MISSING"
    cuts = tmp_path / "cuts"
    verdicts = tmp_path / "verdicts"
    verdicts.mkdir()
    for name in CUT_NAMES:
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
    result = subprocess.run(
        [sys.executable, str(source)],
        env={
            **os.environ,
            "PATH": f"{binary}:{os.environ['PATH']}",
            "BD_PM_CENSUS_CUTS": str(cuts),
            "BD_PM_CENSUS_VERDICTS": str(verdicts),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    decided = {
        line.split()[0]: line.split()[-1]
        for line in result.stdout.splitlines()
        if line.split() and line.split()[0] in CUT_NAMES
    }
    assert set(decided) == {"row123", "foo-row123", "row123[1]"}, f"CLOSED_ROW_LEAK: {result.stdout}"
    return decided


@pytest.mark.parametrize(
    "verdict_name",
    ["row1234-VERDICT-correctness.md", "row123-extra-VERDICT-correctness.md"],
)
def test_neighbor_cut_verdict_cannot_decide_cut(tmp_path, verdict_name):
    assert _census(tmp_path, verdict_name)["row123"] == "no", "VERDICT_NAME_BLEED"


@pytest.mark.parametrize(
    "verdict_name",
    [
        "VERDICT-correctness-xrow123.md",
        "VERDICT-correctness-bd-cx-review-correctness9-xrow123.md",
        "notes-row123.md",
    ],
)
def test_unnamed_or_suffix_neighbor_verdict_cannot_decide_cut(tmp_path, verdict_name):
    assert _census(tmp_path, verdict_name)["row123"] == "no", "BH1-26-FOLLOWUP: SUFFIX_GLOB_BLEED"


def test_longer_cut_owns_its_verdict(tmp_path):
    decided = _census(tmp_path, "VERDICT-correctness-bd-cx-review-correctness9-foo-row123.md")
    assert decided["foo-row123"] == "YES", "OWNER_VERDICT_NOT_COUNTED"
    assert decided["row123"] == "no", "BH1-26-FOLLOWUP: LONGER_CUT_BLEED"


def test_glob_metacharacters_in_cut_name_are_literal(tmp_path):
    decided = _census(tmp_path, "VERDICT-correctness-row1231.md")
    assert decided["row123[1]"] == "no", "BH1-26-FOLLOWUP: UNESCAPED_CUT_NAME"
    (tmp_path / "exact").mkdir()
    assert _census(tmp_path / "exact", "VERDICT-correctness-row123[1].md")["row123[1]"] == "YES"


@pytest.mark.parametrize(
    "verdict_name",
    [
        "row123-VERDICT-correctness.md",
        "VERDICT-correctness-row123.md",
        "VERDICT-correctness-bd-cx-review-correctness9-row123.md",
    ],
)
def test_exact_cut_verdict_decides_cut(tmp_path, verdict_name):
    assert _census(tmp_path, verdict_name)["row123"] == "YES", "EXACT_VERDICT_NOT_COUNTED"
