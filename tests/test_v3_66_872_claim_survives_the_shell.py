"""A claim keyed on the pid of the process that MAKES it is stillborn.

@872. `bd-claim add` keyed the record on os.getpid() -- the pid of the bd-claim
CLI, which exits the instant the command returns. So it printed success, wrote
the file, and the very next invocation reaped it as dead:

    $ bd-claim add victim.py --label agentA
    claimed 1 path(s) as pid 16401 (agentA)
    $ bd-claim list
    no live claims

The consumer then failed OPEN. `.githooks/pre-commit` had nothing to enforce, so
a blanket `git add -A` swept the claimed file onto the branch and the hook said
nothing -- the v3.66.848 failure the whole mechanism exists to prevent, with the
guard installed and armed. CLAUDE.md section 0: the gate could not see its
subject, so it reported OK.

A COUPLED SECOND DEFECT MADE THIS ONE CUT, NOT TWO. The hook called
`conflicts(..., pid=os.getppid())`, and os.getppid() there is the per-commit
bash process, spawned fresh every time. It can never equal any stored claim, so
the self-exclusion branch was unreachable from the only consumer. Fixing the
claim key ALONE would have converted a silently-inert guard into one that
refuses the claimant permission to commit its own claimed file.

EVERY TEST HERE DRIVES REAL SUBPROCESSES AND A REAL `git commit`. The subprocess
boundary is load-bearing, not ceremony: an in-process test keeps the claimant
alive and passes for the wrong reason, which is exactly how bd-claim's own
selftest stayed green through all of this.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLAIM = REPO / "toolchain" / "bin" / "bd-claim"
HOOK = REPO / ".githooks" / "pre-commit"
PY = str(REPO / "venv" / "bin" / "python")


def _mkrepo(tmp_path: Path) -> Path:
    """A throwaway git repo carrying byte-copies of the two subject files.

    NEVER the real checkout: core.hooksPath is armed here and a broken hook
    would block commits for every concurrent agent on this tree.
    """
    r = tmp_path / "r"
    (r / "toolchain" / "bin").mkdir(parents=True)
    (r / ".githooks").mkdir(parents=True)
    (r / "venv" / "bin").mkdir(parents=True)
    (r / "toolchain" / "bin" / "bd-claim").write_bytes(CLAIM.read_bytes())
    (r / ".githooks" / "pre-commit").write_bytes(HOOK.read_bytes())
    os.chmod(r / "toolchain" / "bin" / "bd-claim", 0o755)
    os.chmod(r / ".githooks" / "pre-commit", 0o755)
    if os.path.exists(PY):
        os.symlink(PY, r / "venv" / "bin" / "python")
    for c in (["git", "init", "-q", "."],
              ["git", "config", "user.email", "a@b.c"],
              ["git", "config", "user.name", "t"],
              ["git", "config", "core.hooksPath", ".githooks"]):
        subprocess.run(c, cwd=r, check=True)
    (r / "victim.py").write_text("orig\n")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=r, check=True,
                   env={**os.environ, "BD_SKIP_CLAIM_CHECK": "1"})
    return r


def _claim(r: Path, *args: str, owner: str | None = None):
    env = dict(os.environ)
    env.pop("BD_CLAIM_OWNER", None)
    if owner is not None:
        env["BD_CLAIM_OWNER"] = owner
    return subprocess.run(
        [sys.executable, str(r / "toolchain" / "bin" / "bd-claim"), *args],
        cwd=r, capture_output=True, text=True, timeout=120, env=env)


def _commit(r: Path, msg: str, owner: str | None = None):
    env = dict(os.environ)
    env.pop("BD_CLAIM_OWNER", None)
    if owner is not None:
        env["BD_CLAIM_OWNER"] = owner
    return subprocess.run(["git", "commit", "-m", msg], cwd=r,
                          capture_output=True, text=True, timeout=120, env=env)


# --------------------------------------------------------------------------- #
# RED on pristine                                                             #
# --------------------------------------------------------------------------- #

def test_a_shell_claim_survives_the_process_that_made_it(tmp_path):
    """The literal defect. A claim made by one CLI invocation must still be
    live when a LATER, SEPARATE process asks who holds what.

    RED on pristine: keyed on the first subprocess's now-dead pid, so
    live_claims() reaps it and `list` prints "no live claims".
    """
    r = _mkrepo(tmp_path)
    add = _claim(r, "add", "victim.py", "--label", "agentA", owner="agentA")
    assert add.returncode == 0, add.stderr
    lst = _claim(r, "list", owner="agentA")
    assert "victim.py" in lst.stdout, (
        "a claim made from a shell was gone by the next invocation. "
        "bd-claim list said: %r" % lst.stdout)


def test_the_hook_refuses_a_sweep_of_a_shell_made_claim(tmp_path):
    """The consumer, and the v3.66.848 failure verbatim: agent B's blanket
    `git add -A` sweeps agent A's half-written work onto the branch.

    RED on pristine: exit 0, no refusal, the work committed.
    """
    r = _mkrepo(tmp_path)
    assert _claim(r, "add", "victim.py", "--label", "agentA",
                  owner="agentA").returncode == 0
    (r / "victim.py").write_text("orig\nhalf-written work by agent A\n")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    c = _commit(r, "agent B regen sweep", owner="agentB")
    assert c.returncode != 0, (
        "agent B swept agent A's claimed file onto the branch and the hook "
        "allowed it (exit 0). stdout=%r" % c.stdout)
    assert "REFUSING" in c.stderr, c.stderr
    assert "agentA" in c.stderr, (
        "the refusal does not name who holds the path: %r" % c.stderr)


def test_the_claimant_may_commit_its_own_claimed_path(tmp_path):
    """THE OVER-SENSITIVE DIRECTION, and the reason both halves ship together.

    A durable claim must not lock its own owner out. RED on pristine because
    the hook compared against os.getppid() -- a fresh per-commit bash pid that
    matches nothing -- so the self-exclusion branch was unreachable and the
    claimant was refused its own file.

    Without this assertion, half A alone ships a guard that refuses every
    commit by the agent that made the claim, which is strictly worse than the
    inert guard it replaced.
    """
    r = _mkrepo(tmp_path)
    assert _claim(r, "add", "victim.py", "--label", "me",
                  owner="agentA").returncode == 0
    (r / "victim.py").write_text("orig\nmine\n")
    subprocess.run(["git", "add", "victim.py"], cwd=r, check=True)
    c = _commit(r, "claimant commits its own file", owner="agentA")
    assert c.returncode == 0, (
        "the hook refused the CLAIMANT's own commit. stderr=%r" % c.stderr)


# --------------------------------------------------------------------------- #
# green on pristine -- regression guards, constrained by bd-mutate not by      #
# their pristine result. A test that passes before and after proves nothing on #
# its own.                                                                     #
# --------------------------------------------------------------------------- #

def test_a_dead_claimant_never_wedges_the_repo(tmp_path):
    """A crashed agent must not block the repo forever. Bound one of two."""
    r = _mkrepo(tmp_path)
    claims = r / ".git" / "bd-claims"
    claims.mkdir(parents=True, exist_ok=True)
    dead = 2 ** 22        # far above pid_max on Linux
    (claims / "crashed.json").write_text(json.dumps(
        {"owner": "crashed", "owner_pid": dead, "owner_start": "1",
         "expires_at": None, "label": "crashed", "paths": ["victim.py"]}))
    (r / "victim.py").write_text("orig\nrecovery\n")
    subprocess.run(["git", "add", "victim.py"], cwd=r, check=True)
    c = _commit(r, "after a crash", owner="someone-else")
    assert c.returncode == 0, (
        "a CRASHED agent's claim wedged the repo. stderr=%r" % c.stderr)


def test_an_expired_claim_is_reaped_even_when_its_owner_is_alive(tmp_path):
    """Bound two, and INDEPENDENT of the first -- this is the one that stops a
    claim keyed on a session-long harness pid outliving the edit that justified
    it. The owner here is this very test process, so the liveness bound cannot
    be what reaps it.
    """
    r = _mkrepo(tmp_path)
    claims = r / ".git" / "bd-claims"
    claims.mkdir(parents=True, exist_ok=True)
    (claims / "stale.json").write_text(json.dumps(
        {"owner": "stale", "owner_pid": os.getpid(), "owner_start": None,
         "expires_at": time.time() - 1, "label": "stale",
         "paths": ["victim.py"]}))
    (r / "victim.py").write_text("orig\nlater\n")
    subprocess.run(["git", "add", "victim.py"], cwd=r, check=True)
    c = _commit(r, "after the ttl", owner="someone-else")
    assert c.returncode == 0, (
        "an EXPIRED claim still blocked a commit while its owner was alive. "
        "stderr=%r" % c.stderr)


def test_no_claims_means_no_opinion_and_no_output(tmp_path):
    """A single-operator session must not notice this exists. A guard that
    speaks when nothing is wrong gets switched off, taking the real check with
    it."""
    r = _mkrepo(tmp_path)
    (r / "victim.py").write_text("orig\nsolo\n")
    subprocess.run(["git", "add", "victim.py"], cwd=r, check=True)
    c = _commit(r, "solo operator")
    assert c.returncode == 0, c.stderr
    assert "REFUSING" not in c.stderr and "pre-commit" not in c.stderr, (
        "the hook spoke when no claims were in flight: %r" % c.stderr)


# --------------------------------------------------------------------------- #
# the new design's own failure modes                                          #
# --------------------------------------------------------------------------- #

def test_release_actually_releases_a_claim_made_by_an_earlier_process(tmp_path):
    """release() keyed on os.getpid() too, so it could never match a claim made
    by any earlier process: "nothing to release for this pid", every time."""
    r = _mkrepo(tmp_path)
    assert _claim(r, "add", "victim.py", owner="agentA").returncode == 0
    rel = _claim(r, "release", owner="agentA")
    assert rel.returncode == 0 and "released" in rel.stdout, rel.stdout + rel.stderr
    lst = _claim(r, "list", owner="agentA")
    assert "victim.py" not in lst.stdout, lst.stdout


def test_add_refuses_rather_than_writing_a_claim_it_cannot_key(tmp_path):
    """UNKNOWN IS A THIRD STATE AND IT FAILS.

    If no durable owner can be resolved, writing a claim anyway is worse than
    writing none: it reports success and protects nothing, which is the defect
    this cut removed. Exercised through --pid pointing at a dead process.
    """
    r = _mkrepo(tmp_path)
    add = _claim(r, "add", "victim.py", "--pid", str(2 ** 22))
    assert add.returncode == 2, (
        "bd-claim accepted a claim it could not key to anything live "
        "(exit %d)" % add.returncode)
    assert "UNEVALUABLE" in add.stderr, add.stderr
    assert not list((r / ".git" / "bd-claims").glob("*.json")), (
        "a claim file was written despite the refusal")


def test_add_refuses_a_derived_owner_that_another_agent_already_holds(tmp_path):
    """RISK 1, ENFORCED RATHER THAN DOCUMENTED.

    Two agents under one harness share their durable ancestor, so the DERIVED
    owner token can be identical for both. The hook's self-exclusion would then
    match agent B against agent A's claim and the guard would go silently inert
    for exactly the concurrent case it exists for -- the original defect wearing
    the fix's clothes.

    So a derived owner already held under a different label is refused, and the
    message names the fix (set BD_CLAIM_OWNER). Note this is scoped to DERIVED
    owners: an explicit token is the operator's business and is never refused,
    which the next test pins.
    """
    r = _mkrepo(tmp_path)
    a = _claim(r, "add", "victim.py", "--label", "agentA")   # no owner -> derived
    assert a.returncode == 0, a.stderr
    b = _claim(r, "add", "other.py", "--label", "agentB")    # same derived owner
    assert b.returncode == 2, (
        "a second agent sharing the derived owner was accepted; both would then "
        "exclude each other and nothing would be guarded. stdout=%r stderr=%r"
        % (b.stdout, b.stderr))
    assert "BD_CLAIM_OWNER" in b.stderr, b.stderr


def test_an_explicit_owner_is_never_refused(tmp_path):
    """THE NARROWNESS OF THE REFUSAL, which is what keeps the tool usable.

    The check above must fire ONLY on a derived collision. Two agents with
    distinct explicit tokens are the supported configuration; refusing them
    would make the fix unusable, which is section 0's inverse applied to this
    cut's own guard.
    """
    r = _mkrepo(tmp_path)
    assert _claim(r, "add", "victim.py", "--label", "agentA",
                  owner="agent-A").returncode == 0
    b = _claim(r, "add", "other.py", "--label", "agentB", owner="agent-B")
    assert b.returncode == 0, (
        "two agents with DISTINCT explicit owners were refused: %r" % b.stderr)
    lst = _claim(r, "list", owner="agent-A")
    assert "victim.py" in lst.stdout and "other.py" in lst.stdout, lst.stdout


def test_the_skip_override_still_works(tmp_path):
    """BD_SKIP_CLAIM_CHECK=1 is the documented single escape hatch. A hook edit
    that breaks it strands every concurrent agent on the tree."""
    r = _mkrepo(tmp_path)
    assert _claim(r, "add", "victim.py", "--label", "agentA",
                  owner="agentA").returncode == 0
    (r / "victim.py").write_text("orig\nforced\n")
    subprocess.run(["git", "add", "victim.py"], cwd=r, check=True)
    c = subprocess.run(["git", "commit", "-m", "forced"], cwd=r,
                       capture_output=True, text=True, timeout=120,
                       env={**os.environ, "BD_CLAIM_OWNER": "agentB",
                            "BD_SKIP_CLAIM_CHECK": "1"})
    assert c.returncode == 0, (
        "the documented override no longer works: %r" % c.stderr)


def test_the_same_explicit_owner_may_reclaim_under_a_different_label(tmp_path):
    """The refusal must be scoped to DERIVED owners, and this is what pins that.

    A mutation that widened the collision check to explicit owners too escaped
    the sibling test above, because that test uses two DIFFERENT tokens and so
    never reaches the check at all -- its denominator excluded the mutant's
    subject. One agent re-claiming under its own token with a new label is
    ordinary use; refusing it would make an explicit owner harder to use than
    no owner, which is the inverse of what this cut is for.
    """
    r = _mkrepo(tmp_path)
    first = _claim(r, "add", "victim.py", "--label", "phase-one", owner="agent-A")
    assert first.returncode == 0, first.stderr
    second = _claim(r, "add", "other.py", "--label", "phase-two", owner="agent-A")
    assert second.returncode == 0, (
        "an agent was refused permission to re-claim under its OWN explicit "
        "token with a new label: %r" % second.stderr)


def test_a_recycled_pid_is_not_mistaken_for_the_original_claimant(tmp_path):
    """PID REUSE, and pid_max is 32768 on this container so it is not exotic.

    A claim whose owner_pid is alive but whose start time does NOT match was
    made by a process that has since exited and had its pid handed to someone
    else. Treating it as live would let a stale claim block commits forever --
    the precise wedge the liveness check exists to prevent, reintroduced by the
    liveness check itself.

    os.getpid() here is genuinely alive, so _alive() alone cannot reap this;
    only the start-time comparison can. That is what makes this test able to
    see the mutant that deletes it.
    """
    r = _mkrepo(tmp_path)
    claims = r / ".git" / "bd-claims"
    claims.mkdir(parents=True, exist_ok=True)
    (claims / "recycled.json").write_text(json.dumps(
        {"owner": "recycled", "owner_pid": os.getpid(),
         "owner_start": "definitely-not-the-real-start-time",
         "expires_at": None, "label": "recycled", "paths": ["victim.py"]}))
    (r / "victim.py").write_text("orig\nafter recycling\n")
    subprocess.run(["git", "add", "victim.py"], cwd=r, check=True)
    c = _commit(r, "after the pid was recycled", owner="someone-else")
    assert c.returncode == 0, (
        "a claim held by a RECYCLED pid blocked the commit. Its owner_pid is "
        "alive but is a different process; only the start-time check can tell "
        "them apart. stderr=%r" % c.stderr)
