"""Hermetic regression test for BH2-say-004 (bh2-28).

Verifies that plugins/bd-fleet-mcp/hooks/pre-tool-use-say.sh passes "$payload"
(single arg) to cli.py hook instead of "$@", allowing multi-argument command
line invocations without triggering ValueError('one hook payload expected').
"""

import os
import subprocess

import pytest

BD_GATE_SCOPE = "module"

# The hook lives in bd-persist (host-local, outside git): opt in with the candidate path, e.g.
# /home/mboyle/bd-persist/harness-cuts/bh2-28-bd-agy-trainer-1/plugins/bd-fleet-mcp/hooks/pre-tool-use-say.sh
CANDIDATE = os.environ.get("BD_BH2_28_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

_ENV = {}


@pytest.fixture(autouse=True)
def _private_say_cache(tmp_path):
    # Hermetic (rule 41): every hook-mode validation rewrites the say cache (record_send=True) and
    # a refused say counts as a strike for its sender; keep both off the live cache and live seats.
    _ENV.clear()
    _ENV.update(
        BD_SAY_CACHE=str(tmp_path / "say-recent.json"), BD_SEAT="fixture-bh2-28"
    )
    yield
    _ENV.clear()


def run_hook(*args, input_data=None):
    cmd = ["bash", CANDIDATE, *args]
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    env.update(_ENV)
    env["LC_ALL"] = "C"
    res = subprocess.run(
        cmd,
        input=input_data,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    return res


def test_multi_arg_invocation_passes():
    """Multi-arg invocation like 'echo hello' must pass as a single payload to cli.py hook."""
    res = run_hook("echo", "hello")
    assert res.returncode == 0, f"Expected 0, got {res.returncode}: {res.stderr}"


def test_single_arg_invocation_passes():
    """Single-arg quoted invocation must pass."""
    res = run_hook("echo hello")
    assert res.returncode == 0, f"Expected 0, got {res.returncode}: {res.stderr}"


def test_piped_stdin_passes():
    """Piped stdin invocation must pass."""
    res = run_hook(input_data="echo hello\n")
    assert res.returncode == 0, f"Expected 0, got {res.returncode}: {res.stderr}"


def test_unapproved_say_refused():
    """An actual invalid say call must still be refused (exit 2)."""
    res = run_hook("bd-say", "nonexistent-target-9999", "hello")
    assert res.returncode == 2, (
        f"Expected 2 for invalid say, got {res.returncode}: {res.stdout} {res.stderr}"
    )
    # the refusal came from the validator (not a gate-DOWN fallback), and the validator's cache
    # transaction wrote the private cache, not the live one
    assert "target_not_claimed" in res.stderr + res.stdout, res.stderr
    assert os.path.exists(_ENV["BD_SAY_CACHE"])
