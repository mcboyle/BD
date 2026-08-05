"""bd-freshcheck reported a FALSE STALE in a shallow clone (open item A).

THE DEFECT. `check_session_close_tip` asks
`git merge-base --is-ancestor <claimed> HEAD` and treats every nonzero exit
alike. In a shallow clone the two nonzero codes mean opposite things:

    1    the commit is genuinely not in this history  -- a real STALE
    128  the commit is not in this repository at all  -- "I cannot see it"

Measured 2026-08-05 in a `git clone --depth 1` of this repo:
`bd-freshcheck --repo-only` exits 1 and says section 15.30 "names a commit
this branch does not contain" about `5e87c68`, a genuine ancestor of `main`.
The section is innocent and the sentence is false -- CLAUDE.md section 0's
gate-firing-on-its-own-blindness, stated with confident specifics.

WHY THE OBVIOUS REPAIRS ARE BOTH WRONG. Two drafts of the fix are recorded in
SESSION_CARRY 15.33 because the second was written by someone who had just
fixed the first:

  draft 1  "only an object still unreachable becomes UNKNOWN" -- keys on
           OBJECT PRESENCE. Measured below and in 15.33: a by-sha fetch
           delivers the object WITHOUT its connecting history, after which
           `is-ancestor` answers 1 rather than 128. Presence is exactly the
           state where the answer is most wrong, so a presence test ships the
           false STALE unchanged.
  draft 2  "any nonzero in a shallow clone is UNKNOWN" -- destroys the gate.
           A typo, an abandoned-branch commit and a sha invented from memory
           are all nonzero in a shallow clone too, so every real STALE becomes
           UNKNOWN in the environment sessions actually run in. Removing a
           false clean by removing the verdict is section 0's over-sensitivity
           flip, one draft after the false clean.

THE FIX, and the third measurement that shaped it. 15.33's spec ends with a
by-sha existence probe to split the still-shallow case. MEASURED HERE, that
probe cannot be applied to this register's own data: the close sections name
SHORT shas (15.30 writes `5e87c68`), git reads a short sha as a REF NAME, and
`git fetch --depth=1 origin 5e87c68` therefore exits 128 `couldn't find remote
ref` for a commit that is a genuine reachable ancestor. Feeding that into a
verdict reproduces the false STALE the fix exists to remove. So the probe is
NOT implemented, `--deepen` carries the whole repair, and the residual case
degrades to UNKNOWN rather than guessing. `test_no_verdict_is_computed_from_a_
by_sha_fetch` pins that structurally.

RED IN BOTH DIRECTIONS, which is the point of the file. Two cases fail on
pristine source (the false STALE, and the offline-shallow case that must be
UNKNOWN); two more pass before and after and exist to prove the fix did not
buy them by laundering real staleness (an invented sha stays STALE, in a
shallow clone and in a full one).
"""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import os
import pathlib
import subprocess

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TOOL = _REPO_ROOT / "toolchain" / "bin" / "bd-freshcheck"

# A sha-shaped string that is certainly not a commit: 15 hex characters, so it
# satisfies the register's own `[0-9a-f]{7,40}` shape and cannot collide.
_INVENTED = "deadbeefcafe123"


def _load():
    assert _TOOL.is_file(), "bd-freshcheck is missing"
    spec = importlib.util.spec_from_loader(
        "_bd_fresh_shallow",
        importlib.machinery.SourceFileLoader("_bd_fresh_shallow", str(_TOOL)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(args, cwd):
    r = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                       text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


def _make_upstream(tmp_path: pathlib.Path, claim: str | None) -> tuple[pathlib.Path, str]:
    """A real repo with six commits whose register names the FIRST one.

    HERMETIC ON PURPOSE. The fixture clones over `file://`, never the network:
    a freshness gate's own test must not need GitHub to run on the box. The
    `file://` form is required -- a plain local-path clone ignores `--depth`.
    """
    up = tmp_path / "up"
    up.mkdir(parents=True)
    assert _git(["init", "-q", "-b", "main", "."], up)[0] == 0
    for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"),
                 ("commit.gpgsign", "false")):
        assert _git(["config", k, v], up)[0] == 0
    for i in range(5):
        (up / "f.txt").write_text("c%d\n" % i, encoding="utf-8")
        assert _git(["add", "f.txt"], up)[0] == 0
        assert _git(["commit", "-q", "-m", "c%d" % i], up)[0] == 0
    rc, first = _git(["rev-list", "--max-parents=0", "HEAD"], up)
    assert rc == 0
    first = first.strip()
    named = claim if claim is not None else first[:7]
    reg = up / "project-knowledge"
    reg.mkdir()
    (reg / "SESSION_CARRY.md").write_text(
        "### 15.1 | Session close at %s\n\nbody\n" % named, encoding="utf-8")
    assert _git(["add", "-A"], up)[0] == 0
    assert _git(["commit", "-q", "-m", "register"], up)[0] == 0
    return up, named


def _clone(tmp_path: pathlib.Path, up: pathlib.Path, name: str, depth: int | None):
    dst = tmp_path / name
    args = ["clone", "-q"]
    if depth is not None:
        args += ["--depth", str(depth)]
    args += ["file://%s" % up.resolve(), str(dst)]
    rc, out = _git(args, tmp_path)
    assert rc == 0, out
    rc, shallow = _git(["rev-parse", "--is-shallow-repository"], dst)
    assert rc == 0
    assert (shallow.strip() == "true") == (depth is not None), (
        "fixture did not produce the clone shape it asked for: depth=%r "
        "is-shallow=%r" % (depth, shallow.strip()))
    return dst


def _break_origin(repo: pathlib.Path):
    """Point `origin` at nothing, so any network/fetch step must fail.

    A verdict that survives this was computed WITHOUT fetching -- which is how
    the hermetic cases below prove absence of a fetch rather than asserting it.
    """
    assert _git(["remote", "set-url", "origin",
                 str(repo.parent / "no-such-upstream.git")], repo)[0] == 0


# --------------------------------------------------------------------------- #
# RED on pristine source: the two cases the defect gets wrong                  #
# --------------------------------------------------------------------------- #

def test_shallow_clone_does_not_report_a_false_stale(tmp_path):
    """The measured defect: an innocent section reported STALE because the
    commit it names lies beyond the shallow boundary."""
    mod = _load()
    up, named = _make_upstream(tmp_path, None)
    sh = _clone(tmp_path, up, "sh", depth=1)

    # the raw git answer this check consumes -- 128, not 1
    rc, _ = _git(["merge-base", "--is-ancestor", named, "HEAD"], sh)
    assert rc == 128, (
        "fixture precondition: the claimed commit must be INVISIBLE (128), "
        "not merely absent from history (1); got %d" % rc)

    res = mod.check_session_close_tip(sh)
    assert res["status"] != mod.STALE, (
        "a shallow clone reported STALE about a commit that IS an ancestor -- "
        "the gate fired on its own blindness. detail=%r" % res["detail"])
    assert res["status"] == mod.OK, (
        "the boundary was reachable by deepening, so the honest verdict is OK, "
        "not UNKNOWN. detail=%r" % res["detail"])


def test_shallow_and_unreachable_is_unknown_not_stale(tmp_path):
    """Blind AND unable to see further is UNKNOWN -- the third state.

    This is the case draft 1 would still get wrong and the one that keeps the
    fix honest: when the boundary cannot be pushed back, the tool must say it
    does not know rather than accuse the register.
    """
    mod = _load()
    up, named = _make_upstream(tmp_path, None)
    sh = _clone(tmp_path, up, "sh", depth=1)
    _break_origin(sh)

    res = mod.check_session_close_tip(sh)
    assert res["status"] == mod.UNKNOWN, (
        "a clone that is shallow AND cannot be deepened cannot decide "
        "ancestry, so anything but UNKNOWN is a guess. got %s / %r"
        % (res["status"], res["detail"]))


def test_a_deepen_that_falls_short_is_unknown_not_stale(tmp_path):
    """`_DEEPEN` is a floor, and falling short must degrade to UNKNOWN.

    The other UNKNOWN case above is reached by breaking the network. This one
    is the case that will actually occur as the history grows past the
    constant: the fetch SUCCEEDS, the boundary moves, and the commit is still
    on the far side of it. Forced by shrinking `_DEEPEN` rather than by
    building a thousand-commit fixture -- the branch is the subject, not the
    number.
    """
    mod = _load()
    mod._DEEPEN = 1
    up, named = _make_upstream(tmp_path, None)
    sh = _clone(tmp_path, up, "sh", depth=1)

    res = mod.check_session_close_tip(sh)
    assert res["status"] == mod.UNKNOWN, (
        "a deepen that moved the boundary without reaching the claimed commit "
        "produced %s. Still-shallow means still blind, and blind is UNKNOWN. "
        "detail=%r" % (res["status"], res["detail"]))
    rc, shallow = _git(["rev-parse", "--is-shallow-repository"], sh)
    assert shallow.strip() == "true", (
        "fixture precondition: the clone must still be shallow, or this case "
        "is testing the authoritative branch instead")


# --------------------------------------------------------------------------- #
# GREEN before and after: the fix must not buy the above by killing the gate   #
# --------------------------------------------------------------------------- #

def test_an_invented_sha_is_still_stale_in_a_shallow_clone(tmp_path):
    """Draft 2's failure mode, pinned.

    A sha invented from memory is nonzero in a shallow clone for the same
    reason a real ancestor is. Deepening separates them: the history becomes
    complete, so the nonzero is authoritative and STALE survives.
    """
    mod = _load()
    up, named = _make_upstream(tmp_path, _INVENTED)
    sh = _clone(tmp_path, up, "sh", depth=1)

    res = mod.check_session_close_tip(sh)
    assert res["status"] == mod.STALE, (
        "an invented sha was not reported STALE in a shallow clone -- the fix "
        "laundered a real positive into %s. detail=%r"
        % (res["status"], res["detail"]))
    assert _INVENTED in res["detail"]


def test_full_clone_verdicts_need_no_network(tmp_path):
    """On a complete history nothing below the first `is-ancestor` may run.

    This is the box and CI's `gates` job (`fetch-depth: 0`). Both verdicts are
    computed with `origin` pointing at nothing, so a fetch on either path would
    fail the case rather than merely slow it down -- the gate stays hermetic
    and stays read-only where it always was.
    """
    mod = _load()

    up_ok, named = _make_upstream(tmp_path / "a", None)
    full_ok = _clone(tmp_path / "a", up_ok, "full", depth=None)
    _break_origin(full_ok)
    res = mod.check_session_close_tip(full_ok)
    assert res["status"] == mod.OK, (
        "a real ancestor in a full clone must be OK without touching the "
        "network. got %s / %r" % (res["status"], res["detail"]))

    up_bad, _ = _make_upstream(tmp_path / "b", _INVENTED)
    full_bad = _clone(tmp_path / "b", up_bad, "full", depth=None)
    _break_origin(full_bad)
    res = mod.check_session_close_tip(full_bad)
    assert res["status"] == mod.STALE, (
        "a full clone's nonzero is authoritative and must stay STALE without "
        "any fetch. got %s / %r" % (res["status"], res["detail"]))


# --------------------------------------------------------------------------- #
# The structural pin: no verdict may be computed from a by-sha fetch           #
# --------------------------------------------------------------------------- #

def test_no_verdict_is_computed_from_a_by_sha_fetch():
    """`git fetch <sha>` is a sound EXISTENCE probe and a poisonous ANCESTRY
    input, and conflating the two is what made draft 1 wrong.

    MEASURED 2026-08-05, both halves, on a `--depth 1` clone of this repo:
    fetching the full 40-char sha exits 0 and `is-ancestor` then answers 1 --
    not 0, and no longer 128. The object arrived without its connecting
    history, so a DETECTABLE blindness became an UNDETECTABLE false negative.
    Fetching the SHORT sha the register actually writes exits 128 `couldn't
    find remote ref` for that same genuine ancestor, because git reads a short
    sha as a ref name.

    ASSERTED OVER THE AST, NOT THE SOURCE TEXT, deliberately. CLAUDE.md section
    0: a comment is inside the denominator of every gate that reads source
    text, and explaining a removal by naming the removed thing recreates it --
    this file's own docstring spells `git fetch --depth=1 origin <sha>` in
    order to say why it is absent. A structural walk cannot see prose, so the
    explanation and the assertion cannot collide.
    """
    tree = ast.parse(_TOOL.read_text(encoding="utf-8"))

    # every argv list literal in the tool that invokes `git fetch`
    fetches = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        lits = [e.value for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if len(lits) >= 2 and lits[0] == "git" and "fetch" in lits:
            fetches.append(node)

    assert fetches, (
        "no `git fetch` argv literal found in bd-freshcheck. Either the deepen "
        "was removed -- in which case the shallow-clone repair is gone -- or "
        "it is now built by a shape this walk cannot see, and this pin has "
        "become a gate that cannot see its subject.")

    for node in fetches:
        names = {e.id for e in node.elts if isinstance(e, ast.Name)}
        assert "claimed" not in names, (
            "a `git fetch` in bd-freshcheck is passed the claimed sha. That is "
            "an existence probe being wired into an ancestry question, which "
            "is measured to convert 128 into a FALSE 1 -- draft 1's defect.")
        for e in node.elts:
            if isinstance(e, ast.JoinedStr):
                inner = {n.id for n in ast.walk(e) if isinstance(n, ast.Name)}
                assert "claimed" not in inner, (
                    "the claimed sha is interpolated into a `git fetch` "
                    "argument -- same defect, spelled with an f-string.")


def test_deepen_is_bounded_and_declared():
    """The deepen depth is a named constant, so it can be read and changed.

    A literal buried in a call is the shape that goes stale silently as the
    repo grows. It is a FLOOR, not a promise: when it does not reach the root
    the verdict degrades to UNKNOWN (pinned above), never to a guess.
    """
    mod = _load()
    n = getattr(mod, "_DEEPEN", None)
    assert isinstance(n, int) and n > 0, (
        "bd-freshcheck must declare its deepen depth as a positive int "
        "constant `_DEEPEN`; got %r" % (n,))
