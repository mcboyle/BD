"""Regression test for BH2-31 / MED-1: bd-verdict-write-hook.sh relative file_path hang.

Defect: In bd-verdict-write-hook.sh, the *review*) branch's directory walk
`while [ "$D" != "/" ] && [ -n "$D" ]` lacked a `[ "$D" != "." ]` guard. For relative
file paths with no .git ancestor, `dirname .` evaluates to "." indefinitely, causing
the hook to hang until killed by timeout.
Fix: Absolutize relative file_path with realpath and add `[ "$D" != "." ]` guard
to the *review*) directory walk.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE_SCRIPT = os.environ.get("BD_BH2_31_CANDIDATE", "")

pytestmark = pytest.mark.skipif(
    not CANDIDATE_SCRIPT,
    reason="candidate opt-in required (BD_BH2_31_CANDIDATE)",
)


def test_static_script_review_guard():
    """Candidate script *review*) branch must have '.' guard and realpath."""
    assert os.path.isfile(CANDIDATE_SCRIPT), (
        f"candidate script {CANDIDATE_SCRIPT} missing"
    )
    with open(CANDIDATE_SCRIPT, encoding="utf-8") as f:
        content = f.read()

    review_block = content.split("*review*)", 1)[1].split("*worker*)", 1)[0]
    assert '[ "$D" != "." ]' in review_block, (
        'candidate script *review*) branch must contain \'[ "$D" != "." ]\' guard'
    )
    assert "realpath" in review_block, (
        "candidate script *review*) branch must absolutize path with realpath"
    )


def test_behavioral_relative_file_path_does_not_hang(tmp_path: Path):
    """Writing a relative path with no .git ancestor must not hang."""
    assert os.path.isfile(CANDIDATE_SCRIPT), (
        f"candidate script {CANDIDATE_SCRIPT} missing"
    )

    payload = json.dumps({"tool_input": {"file_path": "scratch-note.md"}})
    env = os.environ.copy()
    env["BD_SEAT"] = "bd-review-shape-1"
    env["BD_VERDICT_LINT_LOG"] = str(tmp_path / "lint.log")

    try:
        proc = subprocess.run(
            ["bash", CANDIDATE_SCRIPT],
            input=payload,
            env=env,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
            cwd=str(tmp_path),
        )
        assert proc.returncode == 0, f"hook failed: {proc.stderr}\n{proc.stdout}"
    except subprocess.TimeoutExpired:
        pytest.fail("hook hung on relative file_path (infinite loop in directory walk)")


def test_behavioral_absolute_path_runs_normally(tmp_path: Path):
    """Writing an absolute path must run normally without hang."""
    assert os.path.isfile(CANDIDATE_SCRIPT), (
        f"candidate script {CANDIDATE_SCRIPT} missing"
    )

    abs_path = str(tmp_path / "notes" / "note.txt")
    payload = json.dumps({"tool_input": {"file_path": abs_path}})
    env = os.environ.copy()
    env["BD_SEAT"] = "bd-review-shape-1"
    env["BD_VERDICT_LINT_LOG"] = str(tmp_path / "lint.log")

    proc = subprocess.run(
        ["bash", CANDIDATE_SCRIPT],
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        timeout=2.0,
        check=False,
    )
    assert proc.returncode == 0, f"hook failed: {proc.stderr}\n{proc.stdout}"
