"""tests/test_bh2_03_say_prefix.py -- Regression tests for BH2-say-003.

Defect:
In harness/bd-say.sh, when sending to PM or Integrator ($IS_PM -eq 1 || $IS_INT -eq 1),
line 201 filter hand-maintained an incomplete list of bypass prefixes that drifted from
ESC_RE. Specifically, WAKE, LANDED, DONE, CLAIM, DISPATCH, REFUTE, STOP, QUESTION,
FABLE, HANG, and DOWN were either omitted or incomplete, causing RELAY=1 to buffer them
in the batch relay queue (>=120s flush) instead of bypassing immediately.

Fix:
Hoist WAKE, PM_WAKE_RE, and ESC_RE above the batch bypass decision, remove duplicate
definitions later in the file, and evaluate printf '%s' "$T0" | grep -qiE "$ESC_RE" directly.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH2_03_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def candidate_path() -> Path:
    p = Path(CANDIDATE)
    assert p.is_file(), f"Candidate file does not exist: {p}"
    assert os.access(p, os.X_OK), f"Candidate is not executable: {p}"
    return p


def _eval_prefix_condition(script: Path, message: str) -> str:
    """Evaluate the line 201 batch bypass condition using the script's own definitions."""
    lines = script.read_text(encoding="utf-8").splitlines()
    target_line = None
    defs: list[str] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("WAKE=", "PM_WAKE_RE=", "ESC_RE=")):
            defs.append(stripped)
        if "bypassing batch queue" in line and idx > 0:
            target_line = lines[idx - 1]
            break
    assert target_line is not None, "Could not find batch bypass guard in script"

    defs_str = "\n".join(defs)
    cmd = f"""
    {defs_str}
    T="$1"
    T0="$1"
    _SND="worker-test"
    BD_SAY_NO_RELAY=0
    cond=$(printf '%s' "$2" | sed -e 's/^[[:space:]]*if //' -e 's/; then.*//')
    eval "if $cond; then echo BYPASS; else echo RELAY; fi"
    """
    res = subprocess.run(
        ["bash", "-c", cmd, "_", message, target_line],
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def _get_esc_re_words() -> list[str]:
    """The canonical vocabulary of ESC_RE escalation words."""
    wake_words = [
        "WAKE",
        "FABLE",
        "HIGH",
        "BLOCKED",
        "REFUSED",
        "QUESTION",
        "ESCALATION",
        "OUTAGE",
        "STALL",
        "LIMIT",
        "CRASH",
        "HANG",
        "DOWN",
        "ORDER",
        "POLL",
    ]
    esc_words = ["ESC", "STOP", "DONE", "LANDED", "REFUTE", "CLAIM", "DISPATCH"]
    return sorted(set(wake_words + esc_words))


@pytest.mark.parametrize("word", _get_esc_re_words())
def test_all_esc_re_words_bypass_batch(candidate_path: Path, word: str) -> None:
    """Every word in ESC_RE must bypass the batch relay queue when sent to PM/Integrator."""
    msg = f"{word} target-seat notice"
    result = _eval_prefix_condition(candidate_path, msg)
    assert result == "BYPASS", (
        f"Expected BYPASS for '{msg}' (word '{word}'), got {result}"
    )


@pytest.mark.parametrize(
    "routine_msg",
    [
        "routine status check",
        "working on candidate cut",
        "note: review progress updated",
        "hello world",
    ],
)
def test_routine_messages_not_bypassed(candidate_path: Path, routine_msg: str) -> None:
    """Routine messages must not bypass batch queue (negative control)."""
    result = _eval_prefix_condition(candidate_path, routine_msg)
    assert result == "RELAY", f"Expected RELAY for '{routine_msg}', got {result}"


def test_syntax_clean(candidate_path: Path) -> None:
    """Candidate must pass bash -n syntax validation."""
    res = subprocess.run(
        ["bash", "-n", str(candidate_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"bash -n failed: {res.stderr}"


def test_no_duplicate_vocabulary_definitions(candidate_path: Path) -> None:
    """Vocabulary regex definitions must appear exactly once in the script (no drift)."""
    text = candidate_path.read_text(encoding="utf-8")
    lines = [
        line.strip() for line in text.splitlines() if not line.strip().startswith("#")
    ]
    esc_defs = [line for line in lines if line.startswith("ESC_RE=")]
    pm_wake_defs = [line for line in lines if line.startswith("PM_WAKE_RE=")]
    wake_defs = [line for line in lines if line.startswith("WAKE=")]

    # Candidate has single hoisted definition block (lines 201-203), duplicate deleted per B4-B F2
    if 'grep -qiE "$ESC_RE"' in text:
        assert len(esc_defs) == 1, (
            f"Expected exactly 1 ESC_RE definition, found {len(esc_defs)}"
        )
        assert len(pm_wake_defs) == 1, (
            f"Expected exactly 1 PM_WAKE_RE definition, found {len(pm_wake_defs)}"
        )
        assert len(wake_defs) == 1, (
            f"Expected exactly 1 WAKE definition, found {len(wake_defs)}"
        )
