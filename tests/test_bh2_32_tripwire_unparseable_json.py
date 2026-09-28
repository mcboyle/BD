"""Tests for BH2-32: bd-tripwire-hook.py fails closed (exit 2) on unparseable stdin JSON."""

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH2_32_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def invoke():
    candidate = Path(CANDIDATE)
    assert candidate.is_file() and os.access(candidate, os.X_OK)

    def _invoke(payload: str):
        return subprocess.run(
            ["python3", str(candidate)],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
        )

    return _invoke


def test_unparseable_json_fails_closed(invoke):
    result = invoke("INVALID{")
    assert result.returncode == 2, f"expected exit 2 on invalid JSON, got {result.returncode}"
    assert "REFUSED" in result.stderr
    assert "unparseable stdin JSON" in result.stderr


def test_non_dict_json_fails_closed(invoke):
    result = invoke('["array", "not", "object"]')
    assert result.returncode == 2, f"expected exit 2 on non-object JSON, got {result.returncode}"
    assert "REFUSED" in result.stderr
    assert "must be an object" in result.stderr


def test_valid_json_allowed_command_exits_zero(invoke):
    result = invoke('{"tool_name": "Bash", "tool_input": {"command": "git status"}}')
    assert result.returncode == 0, f"expected exit 0 on clean command, got {result.returncode}"


def test_valid_json_refused_command_exits_two(invoke):
    result = invoke('{"tool_name": "Bash", "tool_input": {"command": "git add -A"}}')
    assert result.returncode == 2, f"expected exit 2 on forbidden command, got {result.returncode}"
    assert "REFUSED" in result.stderr
