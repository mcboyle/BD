"""v3.66.799 -- shipped audit wrappers must carry an honest --selftest.

tools/bd-triage.py and tools/bd-audit-gate.py had drifted from their bdsuite
bin/ twins: same names, different behaviour depending on which copy was
invoked. The bin copies grew a delegation selftest (a wrapper that cannot
find what it wraps is broken, and a selftest that cannot verify must not
report success); the tree copies never did -- and for bd-audit-gate the
TREE copy is the one carrying the newer @533 in-tree-first path logic, so
neither side was wholly canonical. This cut converges them: tree body (the
@533 logic where it exists) + a context-aware selftest whose delegation
candidates resolve in BOTH contexts and which FAILS honestly where the
target genuinely does not exist.

The contract under test (environment-independent by design -- the same
suite must be honest on stash, where the sandbox-only delegation targets
are absent):
  1. `--selftest` is HANDLED: the tool emits a SELFTEST verdict line
     instead of falling into its normal main with an unknown argument.
  2. The verdict is HONEST: exit 0 if and only if the output says
     SELFTEST PASS. No third state where the exit code and the verdict
     line disagree.

Deliberately NOT asserted here: parity with the bdsuite bin/ copies.
/home/claude/bin does not exist on stash, so a tests/ suite comparing
against it would assert over a denominator that structurally excludes its
subject on the machine where the full suite runs. Cross-copy parity is the
toolchain's job (bd-pk-mirror).
"""
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = [os.path.join(REPO, "tools", "bd-triage.py"),
         os.path.join(REPO, "tools", "bd-audit-gate.py")]


def _run_selftest(tool):
    return subprocess.run(
        [sys.executable, tool, "--selftest"],
        cwd=REPO, capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("tool", TOOLS, ids=[os.path.basename(t) for t in TOOLS])
def test_selftest_is_handled(tool):
    r = _run_selftest(tool)
    out = r.stdout + r.stderr
    assert "SELFTEST" in out, (
        "%s does not handle --selftest (no verdict line; the flag fell "
        "through to normal main). Output tail: %r"
        % (os.path.basename(tool), out[-400:]))


@pytest.mark.parametrize("tool", TOOLS, ids=[os.path.basename(t) for t in TOOLS])
def test_selftest_verdict_is_honest(tool):
    """exit 0 <=> SELFTEST PASS. On a host where the delegation target is
    genuinely absent the tool must say SELFTEST FAIL and exit non-zero --
    honest failure, never a green report on an empty probe."""
    r = _run_selftest(tool)
    out = r.stdout + r.stderr
    passed = "SELFTEST PASS" in out
    assert (r.returncode == 0) == passed, (
        "%s: exit code (%s) disagrees with its own verdict line (%s)"
        % (os.path.basename(tool), r.returncode,
           "PASS" if passed else "FAIL/none"))
