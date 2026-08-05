"""The provision trigger was a gate that could see one fifth of the damage.

@879. The operator reported the same three failures recurring across sessions:
the checkout rolls back to a stale commit, the venv loses packages, and
.claude-env-report.md asserts about a tree 60 versions old. @873 fixed the first
and the other two kept happening, which is the tell that the diagnosis was
incomplete.

They share ONE cause. `.claude/hooks/session-start.sh` decides whether to
provision by asking `tools/check_requirements.py` whether every requirement NAME
resolves. That instrument's denominator is names. An image reversion breaks five
things -- the checkout, venv package VERSIONS, frontend/dist, __pycache__, and
the env report -- and four of them are structurally invisible to it. So the
question "is a repair needed?" was answered by a check that could not see most of
what was broken, and it answered "no" while the session ran on a reverted image.
That is CLAUDE.md section 0, sitting in the fix written for section 0.

The trigger is now the reverted-image SIGNATURE itself, which the hook already
detects at section 3 and already repairs. When that fires on startup/resume it
hands over to scripts/cloud-setup.sh, which is idempotent and converges all five.

Three more defects in the same path, each found by an adversarial probe of @873:

  * a FAILED fetch was swallowed (`2>/dev/null || true`). On a faithful rollback
    the image reverts refs/remotes/origin/main TOGETHER with HEAD, so with no
    fetch the is-ancestor check sees HEAD == origin/main and the hook exits 0
    silent -- a real rollback rendered invisible by the failure of the one step
    that would have revealed it. Unknown reported as OK.
  * the repair was gated on losslessness alone, never on WHICH ref is checked
    out. A clean topic branch or detached HEAD parked at an ancestor of
    origin/main satisfies the predicate and got reset onto main. No commit
    becomes unreachable -- so the hook's byte-losslessness claim stayed true --
    but the operator's POSITION is destroyed, and CLAUDE.md section 2b tells
    agents to `git checkout --detach FETCH_HEAD` before measuring.
  * tests/test_v3_66_873_rollback_repairs_itself.py's fixture reverts HEAD but
    NOT the tracking ref, so it does not reproduce the rollback it documents.
    With the fetch deleted the suite still passed: the fetch was unconstrained.

And one in the delegate: `scripts/cloud-setup.sh` runs check_requirements.py with
NO argument, so it grades `requirements.txt` (the tool's DEFAULT_REQUIREMENTS)
and nothing else. `pyyaml` and `pyflakes` are declared only in
requirements-test.txt and sit outside the denominator, while the hook's own
comment justifies delegating repair to that script on the ground that it
"verifies each step rather than trusting an exit code".
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / ".claude" / "hooks" / "session-start.sh"
SETUP = REPO / "scripts" / "cloud-setup.sh"

# A fake provisioner. The real one is a 33-step apt/npm/playwright run, so the
# test cannot invoke it -- but it CAN assert that the hook reached for it, which
# is the property under test. Writing argv proves the hand-over, not just a call.
_FAKE_SETUP = """#!/bin/bash
echo "provisioned pwd=$PWD repo=${BD_REPO:-unset}" >> "$(dirname "$0")/../.provision_marker"
exit 0
"""


def _git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=check, timeout=120)


def _origin_and_clone(tmp_path: Path, behind_by: int = 3, faithful: bool = False):
    """A bare 'origin' with N commits and a clone parked at the FIRST one.

    `faithful=True` also rewinds refs/remotes/origin/main, which is what an image
    reversion actually does -- the whole .git directory goes back, not just the
    work tree. Without it the fetch in the hook is unconstrained: delete the
    fetch and the clone still knows the true tip, so the test passes anyway.
    """
    origin = tmp_path / "origin"
    work = tmp_path / "seed"
    work.mkdir(parents=True)
    _git("init", "-q", "-b", "main", ".", cwd=work)
    _git("config", "user.email", "a@b.c", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    (work / "src.py").write_text("VERSION = 0\n")
    scripts = work / "scripts"
    scripts.mkdir()
    (scripts / "cloud-setup.sh").write_text(_FAKE_SETUP)
    (scripts / "cloud-setup.sh").chmod(0o755)
    _git("add", "-A", cwd=work)
    _git("commit", "-qm", "c0", cwd=work)
    first = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    for i in range(1, behind_by + 1):
        (work / "src.py").write_text(f"VERSION = {i}\n")
        _git("commit", "-qam", f"c{i}", cwd=work)
    _git("clone", "-q", "--bare", str(work), str(origin), cwd=tmp_path)

    clone = tmp_path / "clone"
    _git("clone", "-q", str(origin), str(clone), cwd=tmp_path)
    _git("config", "user.email", "a@b.c", cwd=clone)
    _git("config", "user.name", "t", cwd=clone)
    _git("reset", "--hard", "-q", first, cwd=clone)
    if faithful:
        _git("update-ref", "refs/remotes/origin/main", first, cwd=clone)
    return origin, clone


def _run_hook(clone: Path, source: str = "startup"):
    env = dict(os.environ)
    env["CLAUDE_CODE_REMOTE"] = "true"
    env["CLAUDE_PROJECT_DIR"] = str(clone)
    env.pop("CLAUDE_ENV_FILE", None)
    return subprocess.run(["bash", str(HOOK)], cwd=str(clone),
                          input='{"source":"%s"}' % source,
                          capture_output=True, text=True, timeout=300, env=env)


def _marker(clone: Path) -> str:
    p = clone / ".provision_marker"
    return p.read_text() if p.exists() else ""


def _head(clone: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=clone).stdout.strip()


def _tip(clone: Path) -> str:
    return _git("rev-parse", "origin/main", cwd=clone).stdout.strip()


# --------------------------------------------------------------------------- #
# 1. the trigger: the signature, not the name-resolution check                 #
# --------------------------------------------------------------------------- #

def test_a_repaired_rollback_also_reprovisions(tmp_path):
    """THE DEFECT. Repairing the tree converges the checkout and nothing derived
    from it -- not the venv's package versions, not frontend/dist, not
    __pycache__, not the env report. The reverted-image signature is the one
    event that breaks all five, and the hook already detects it."""
    _origin, clone = _origin_and_clone(tmp_path)
    r = _run_hook(clone, source="startup")
    assert _head(clone) == _tip(clone), "precondition: the rollback was not repaired"
    assert "provisioned" in _marker(clone), (
        "a rollback was repaired at the tree level and the environment was left "
        "on the reverted image. stderr=%r" % r.stderr)


def test_a_current_checkout_does_not_reprovision(tmp_path):
    """The over-sensitive direction, and the one that decides whether this fix is
    usable at all. A 33-step provision on every session start would make the
    session unusable, and a hook that is always expensive gets disabled."""
    _origin, clone = _origin_and_clone(tmp_path)
    _git("reset", "--hard", "-q", "origin/main", cwd=clone)
    r = _run_hook(clone, source="startup")
    assert _marker(clone) == "", (
        "the hook reprovisioned a checkout that was already current -- this "
        "fires on every session. stderr=%r" % r.stderr)


def test_compact_reports_rather_than_stalling_the_session(tmp_path):
    """compact/clear fire MID-SESSION. The tree repair is instant and safe there,
    but a 33-step provision would stall a running session for minutes, so the
    hook says what is wrong and lets the operator choose -- the same split the
    dependency-floor branch already makes."""
    _origin, clone = _origin_and_clone(tmp_path)
    r = _run_hook(clone, source="compact")
    assert _head(clone) == _tip(clone), "the tree repair must still happen on compact"
    assert _marker(clone) == "", (
        "a 33-step provision ran mid-session on source=compact. stderr=%r" % r.stderr)
    # NOT a bare "cloud-setup.sh" substring. The dependency-floor branch already
    # prints "no venv at ... -- run scripts/cloud-setup.sh for a full provision"
    # in this fixture, so that assertion passed on PRISTINE source, for a line
    # that has nothing to do with a rollback. Caught by reading the RED run
    # instead of counting it: 6 failed, 3 passed, and this was one of the three.
    assert "ENVIRONMENT NOT RECONVERGED" in r.stderr, (
        "the operator was not told the environment still needs reprovisioning "
        "after a mid-session rollback. stderr=%r" % r.stderr)


# --------------------------------------------------------------------------- #
# 2. a failed fetch is UNKNOWN, and unknown is a third state that fails        #
# --------------------------------------------------------------------------- #

def test_an_unreachable_origin_is_reported_not_swallowed(tmp_path):
    """On a faithful rollback the tracking ref reverts with HEAD, so without a
    successful fetch the hook's own comparison says 'current'. Swallowing the
    fetch failure therefore does not degrade the check -- it INVERTS it, turning
    the one condition that reveals a rollback into silence."""
    _origin, clone = _origin_and_clone(tmp_path, faithful=True)
    _git("remote", "set-url", "origin", str(tmp_path / "does_not_exist"), cwd=clone)
    stale = _head(clone)
    r = _run_hook(clone, source="startup")
    assert _head(clone) == stale, "nothing should move when origin is unreachable"
    assert "UNVERIFIED" in r.stderr, (
        "an unreachable origin produced no warning at all; a real rollback is "
        "indistinguishable from a healthy tree here. stderr=%r" % r.stderr)


def test_a_faithful_rollback_is_still_repaired(tmp_path):
    """Constrains the fetch. The @873 fixture reverted HEAD but left the tracking
    ref at the true tip, so the clone already knew where main was and deleting
    the fetch left the suite green. With the tracking ref reverted too, the fetch
    is the only way the hook can learn the tip."""
    _origin, clone = _origin_and_clone(tmp_path, faithful=True)
    assert _head(clone) == _tip(clone), (
        "fixture precondition: a faithful rollback starts with HEAD and the "
        "tracking ref agreeing, which is exactly why it looks healthy")
    r = _run_hook(clone, source="startup")
    assert "REPAIRED" in r.stderr, (
        "a faithfully-reproduced rollback was not detected. stderr=%r" % r.stderr)
    assert (clone / "src.py").read_text().strip() == "VERSION = 3"


# --------------------------------------------------------------------------- #
# 3. losslessness is not the only question -- WHICH ref is checked out matters #
# --------------------------------------------------------------------------- #

def test_a_topic_branch_at_an_ancestor_is_not_reset_onto_main(tmp_path):
    """Byte-lossless and still wrong. A clean topic branch with no unique commits
    satisfies the old predicate, so it was reset onto main. Nothing becomes
    unreachable, which is why the hook's losslessness claim stayed literally
    true -- but the operator's POSITION is destroyed, and that is the thing they
    chose."""
    _origin, clone = _origin_and_clone(tmp_path)
    _git("checkout", "-q", "-b", "my-topic", cwd=clone)
    parked = _head(clone)
    r = _run_hook(clone, source="startup")
    assert _head(clone) == parked, (
        "a deliberately-parked topic branch was reset onto main. stderr=%r" % r.stderr)
    assert "NOT repairing" in r.stderr, r.stderr
    assert "my-topic" in r.stderr or "branch" in r.stderr, (
        "the refusal does not say WHY, so the operator cannot tell it from a "
        "rollback. stderr=%r" % r.stderr)


def test_a_detached_head_is_not_reset(tmp_path):
    """CLAUDE.md section 2b tells agents to `git checkout --detach FETCH_HEAD`
    before measuring anything, so detached-at-an-older-commit is a ROUTINE
    deliberate state in this repo, not a symptom."""
    _origin, clone = _origin_and_clone(tmp_path)
    _git("checkout", "-q", "--detach", cwd=clone)
    parked = _head(clone)
    r = _run_hook(clone, source="startup")
    assert _head(clone) == parked, (
        "a detached HEAD -- the state this project's own contract instructs "
        "agents to create -- was reset onto main. stderr=%r" % r.stderr)
    assert "NOT repairing" in r.stderr, r.stderr


# --------------------------------------------------------------------------- #
# 4. the delegate's own denominator                                            #
# --------------------------------------------------------------------------- #

def _shell_code_only(path: Path) -> str:
    """The script with whole-line `#` comments removed.

    Load-bearing, and learned the hard way FOUR times in one session: an
    assertion over raw source cannot tell prose from code. The first version of
    the two tests below searched a window of raw text, and the comment written
    to EXPLAIN the fix names `requirements-test.txt` -- so a mutant reducing the
    loop to `for REQ_FILE in requirements.txt` left both green. bd-mutate caught
    it; review had not.

    Whole-line comments only. Stripping trailing `# ...` would have to know
    whether the `#` is inside a quoted string, and a wrong strip corrupts the
    subject rather than narrowing it.
    """
    return "\n".join(l for l in path.read_text().splitlines()
                     if not l.lstrip().startswith("#"))


def _enclosing_loop_block(lines, idx):
    """The `for ... do` / `done` construct containing line `idx`, or that line.

    STRUCTURE, not a fixed width. The first draft sliced src[call-900:call+900],
    which is the shape CLAUDE.md section 2a forbids -- a harness that cut a shell
    branch on a fixed width swallowed its closing `fi` and produced bash syntax
    errors presenting as subject failures. It is also counted by
    tests/test_source_windows_do_not_shift.py, whose ratchet is one-directional:
    it went 115 -> 117 on this file and the remedy is to remove the window, not
    to raise the baseline.

    Falls back to the single line when the call is not inside a loop, so an
    implementation using two explicit calls is judged on its own text rather
    than failing for its form.
    """
    start = None
    for i in range(idx, -1, -1):
        s = lines[i].strip()
        if re.match(r"^(for|while)\b.*\bdo\b", s) or re.match(r"^(for|while)\b", s):
            start = i
            break
        if s == "done":            # a sibling construct closed above us
            break
    if start is None:
        return lines[idx]
    depth, end = 0, len(lines) - 1
    for j in range(start, len(lines)):
        s = lines[j].strip()
        if re.search(r"\bdo\b", s):
            depth += 1
        if s == "done" or s.startswith("done"):
            depth -= 1
            if depth <= 0:
                end = j
                break
    return "\n".join(lines[start:end + 1])


def test_cloud_setup_resolution_checks_every_core_manifest():
    """`check_requirements.py` with no argument grades DEFAULT_REQUIREMENTS,
    which is requirements.txt alone. pyyaml and pyflakes are declared only in
    requirements-test.txt, so they sat outside the denominator of the check the
    hook delegates to precisely because it 'verifies each step'."""
    # CODE ONLY -- see _shell_code_only. Deliberately NOT "the literal appears on
    # the call line" either: the first draft required that, and the fix passes
    # the manifest through a loop variable, so it failed a correct
    # implementation for its FORM. The property is that both core manifests are
    # inside the denominator the call iterates.
    lines = _shell_code_only(SETUP).splitlines()
    hits = [i for i, l in enumerate(lines) if "check_requirements.py" in l]
    assert hits, "cloud-setup.sh no longer calls check_requirements.py at all"
    graded = "\n".join(_enclosing_loop_block(lines, i) for i in hits)
    for manifest in ("requirements.txt", "requirements-test.txt"):
        assert manifest in graded, (
            "%s is not inside any construct that calls check_requirements.py, so "
            "the provisioner's own verification cannot see it. pyyaml and "
            "pyflakes are declared only in requirements-test.txt." % manifest)


def test_the_test_manifest_is_actually_graded_not_merely_mentioned():
    """The weaker sibling of the assertion above: naming the manifest in a
    comment would satisfy a substring check while grading nothing. Require the
    result to reach a row and the failure counter, the way the core check does."""
    lines = _shell_code_only(SETUP).splitlines()
    for i, l in enumerate(lines):
        if "check_requirements.py" not in l:
            continue
        block = _enclosing_loop_block(lines, i)
        if "requirements-test.txt" in block and "row " in block:
            return
    raise AssertionError(
        "requirements-test.txt is mentioned in cloud-setup.sh but no mention "
        "sits near both a check_requirements.py call and a report row, so "
        "nothing grades it")
