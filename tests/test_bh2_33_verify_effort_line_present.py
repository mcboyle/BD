"""Regression test for BH2-33: bd-launch-codex-role.sh verify effort line present after sed.

Defect: The script replaces the `model_reasoning_effort` line using `sed` but does not
verify if the original profile actually had that line. If missing, it silently proceeds,
resulting in a generated config that lacks the effort setting.
Fix: Add a `grep -qxF` check after the `sed` to verify the line exists, and die 4 if not.
"""

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE_SCRIPT = os.environ.get("BD_BH2_33_CANDIDATE", "")

pytestmark = pytest.mark.skipif(
    not CANDIDATE_SCRIPT,
    reason="candidate opt-in required (BD_BH2_33_CANDIDATE)",
)

def test_static_script_effort_grep_guard():
    """Candidate script must have grep check after sed for model_reasoning_effort."""
    assert os.path.isfile(CANDIDATE_SCRIPT), f"candidate script {CANDIDATE_SCRIPT} missing"
    with open(CANDIDATE_SCRIPT, encoding="utf-8") as f:
        content = f.read()

    assert 'grep -qxF "model_reasoning_effort = \\"$BD_CX_EFFORT\\""' in content, (
        'candidate script must contain grep check for model_reasoning_effort'
    )
    assert 'die 4 "effort derivation failed: $PROF has no model_reasoning_effort line to set"' in content, (
        'candidate script must die 4 if effort line is missing'
    )

def test_behavioral_missing_effort_line_dies_4(tmp_path: Path):
    """Running the script on a profile without model_reasoning_effort should exit 4."""
    assert os.path.isfile(CANDIDATE_SCRIPT), f"candidate script {CANDIDATE_SCRIPT} missing"
    
    # We will simulate the environment for bd-launch-codex-role.sh
    # It requires BD_CX_EFFORT and a valid role.
    home_d = tmp_path / "home_d"
    home_d.mkdir()
    
    agents_dir = home_d / "agents"
    agents_dir.mkdir()
    (agents_dir / "bd-worker.toml").write_text("")
    
    # Create a fake bd-worker.config.toml (missing model_reasoning_effort)
    prof_file = home_d / "bd-worker.config.toml"
    prof_file.write_text("model = \"claude-3-opus-20240229\"\n")
    
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home_d)
    env["BD_CX_EFFORT"] = "high"
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}" # In case it calls other tools
    
    cmd = [CANDIDATE_SCRIPT, "worker", "mytask_bh2_33_test_abc123", "--dry-run"]
    
    result = subprocess.run(
        cmd,
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    
    # We expect it to die 4 because model_reasoning_effort is missing.
    assert result.returncode == 4, (
        f"Expected exit code 4, got {result.returncode}.\nStdout: {result.stdout}\nStderr: {result.stderr}"
    )
    assert "has no model_reasoning_effort line to set" in result.stderr or "has no model_reasoning_effort line to set" in result.stdout
