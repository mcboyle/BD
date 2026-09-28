from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH2_01_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _menu_program() -> str:
    candidate = Path(CANDIDATE)
    assert candidate.is_file() and os.access(candidate, os.X_OK), "candidate unavailable"
    source = candidate.read_text(encoding="utf-8")
    assignments = []
    for name in ("NOPT", "CURSORED", "CONFIRM"):
        matches = re.findall(rf"^{name}=.*$", source, re.MULTILINE)
        assert len(matches) == 1, f"ambiguous production assignment: {name}"
        assignments.append(matches[0])
    guards = re.findall(
        r'^if \[ "\$\{NOPT:-0\}"[^\n]*\n.*?^fi$',
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert len(guards) == 1, "production menu refusal branch missing or ambiguous"
    # Execute production expressions and branch; stub only refusal side effects.
    return "\n".join(
        [
            'TAIL15=$1; BD_SAY_MENU_STRICT=$2; S=fixture',
            'refuse6() { printf "%s\\n" "$1"; exit 6; }',
            *assignments,
            guards[0],
            'printf "MENU_ALLOWED nopt=%s cursored=%s\\n" "$NOPT" "$CURSORED"',
        ]
    )


@pytest.mark.parametrize(
    ("pane", "strict", "expected_rc", "diagnostic"),
    [
        ("❯ 1. Accept\n  2. Cancel", "0", 6, "2-option SELECTION MENU (cursored=1 confirm=0)"),
        ("> 1. Accept\n  2. Cancel", "0", 6, "2-option SELECTION MENU (cursored=1 confirm=0)"),
        ("  1. Complete\n  2. Complete", "0", 0, "MENU_ALLOWED nopt=2 cursored=0"),
        ("❯ Send a message", "0", 0, "MENU_ALLOWED nopt=0 cursored=0"),
        ("  1. Accept\n  2. Cancel", "1", 6, "2-option SELECTION MENU (cursored=0 confirm=0)"),
        ("Ready to submit?\n  1. Accept\n  2. Cancel", "0", 6, "2-option SELECTION MENU (cursored=0 confirm=1)"),
    ],
    ids=["unicode-menu", "ascii-menu", "ordinary-list", "input-prompt", "strict-menu", "confirm-menu"],
)
def test_menu_cursor_contract(pane: str, strict: str, expected_rc: int, diagnostic: str) -> None:
    assert pane.strip(), "empty pane fixture"
    result = subprocess.run(
        ["bash", "-c", _menu_program(), "menu-probe", pane, strict],
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == expected_rc, (
        f"MENU_CURSOR_CONTRACT expected rc={expected_rc}, got rc={result.returncode}: "
        f"{result.stdout} {result.stderr}"
    )
    assert diagnostic in result.stdout, f"MENU_DIAGNOSTIC_MISMATCH: {result.stdout!r}"
    assert not result.stderr
