"""O1698 M27: the tripwire's T8 worktree-deletion rule must cover every worktree root.

bd-tripwire-hook.py's WT constant named bd-cuts, .worktrees, the codex/claude trees,
bd-floor-private and bd-persist, but not bd-local-wt (builder cuts), bd-review-wt (lens
trees) or bd-seats (seat dirs), so `rm -rf` on any of those passed. The fixed hook is
the live one outside the repo (bd-persist/harness/bd-tripwire-hook.py); point
BD_O1698_MOD_M27_WORKTREE_GUARD_CANDIDATE at it. Only command STRINGS go through
check(); nothing is deleted, reset or executed.
"""
import importlib.util
import os
import subprocess
import sys

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_MOD_M27_WORKTREE_GUARD_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

ENV = {"PWD": "/tmp/bd-m27-test"}
DENIED = [
    "rm -rf /home/mboyle/bd-local-wt/x",
    "rm -rf /home/mboyle/bd-review-wt/x",
    "rm -rf /var/tmp/bd-seats/worker/x",
]


@pytest.fixture(scope="module")
def hook():
    assert os.path.isfile(CANDIDATE), f"M27_CANDIDATE_MISSING: {CANDIDATE}"
    spec = importlib.util.spec_from_file_location("m27_tripwire_candidate", CANDIDATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rules(hook, cmd):
    return [h.split()[0] for h in hook.check(cmd, ENV)]


@pytest.mark.parametrize("cmd", DENIED)
def test_rm_rf_of_a_worktree_root_is_denied_by_t8(hook, cmd):
    assert "T8" in _rules(hook, cmd), f"M27_WORKTREE_DELETE_ADMITTED: {cmd!r}"


def test_positive_control_existing_roots_still_denied(hook):
    assert "T8" in _rules(hook, "rm -rf /home/mboyle/bd-cuts/x")
    assert "T8" in _rules(hook, "git worktree remove /home/mboyle/bd-local-wt/x")
    assert "T2" in _rules(hook, "git -C /home/mboyle/bd-local-wt/x reset --hard")


@pytest.mark.parametrize("cmd", [
    "git -C bd-cuts/x status",
    "git -C /home/mboyle/bd-local-wt/x status",
    "ls /home/mboyle/bd-review-wt/x",
    "rm -rf /tmp/scratch",
])
def test_negative_control_reads_and_unrelated_deletes_pass(hook, cmd):
    assert _rules(hook, cmd) == [], cmd


def test_candidate_selftest_passes(tmp_path):
    env = dict(os.environ, BD_TRIPWIRE_LOG=str(tmp_path / "tripwire.log"))
    r = subprocess.run([sys.executable, CANDIDATE, "--selftest"],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "selftest OK" in r.stdout, r.stdout
