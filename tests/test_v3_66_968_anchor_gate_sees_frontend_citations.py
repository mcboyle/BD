"""bd-freshcheck's anchor gate must be able to SEE a frontend citation.

CLAUDE.md section 0's opening example is a band tool that "didn't count
`.tsx`/`.ts` as source, so it reported 'changed source (0)' on a real frontend
cut". The same blind spot lived in this gate: its anchor regex alternated over
`py|sh|json|md|txt|yml`, so a `file:line` citation into a `.tsx` or `.ts` file
was never PARSED -- and the gate then reported every anchor resolving over a
denominator that structurally excluded them.

The load-bearing assertion here is the BROKEN one, not the OK one. A gate that
cannot see a subject reports clean truthfully and uselessly, so proving it
counts a VALID frontend anchor is weak evidence -- an unparsed anchor and a
resolving anchor produce the same verdict. Only a DELIBERATELY out-of-range
frontend anchor discriminates: invisible means status OK, visible means STALE.
Both directions are asserted, because a "fix" that reported every frontend
anchor broken would pass a one-sided test while destroying the gate.
"""

import importlib.machinery
import importlib.util
import os
import pathlib
import subprocess

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The gate returns UNKNOWN below this many tracked files, on the grounds that a
# collapsed denominator is not a pass. A fixture under it would prove nothing
# about the predicate, so the ballast is load-bearing, not padding.
_MIN_TRACKED = 100
_PANEL_LINES = 40


def _freshcheck_mod():
    p = _REPO_ROOT / "toolchain" / "bin" / "bd-freshcheck"
    assert p.is_file(), f"{p} absent -- this test would prove nothing"
    spec = importlib.util.spec_from_loader(
        "bdfresh_anchor", importlib.machinery.SourceFileLoader("bdfresh_anchor", str(p)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixture_repo(tmp_path, claude_body):
    """A real git repo carrying a tracked .tsx file and both gating documents."""
    root = pathlib.Path(tmp_path)
    (root / "project-knowledge").mkdir(parents=True, exist_ok=True)
    panel = root / "frontend" / "src" / "routes"
    panel.mkdir(parents=True, exist_ok=True)
    (panel / "Panel.tsx").write_text(
        "".join("const line%d = %d;\n" % (i, i) for i in range(1, _PANEL_LINES + 1)),
        encoding="utf-8")
    for i in range(_MIN_TRACKED + 10):
        (root / ("ballast_%03d.py" % i)).write_text("x = 1\n", encoding="utf-8")

    (root / "CLAUDE.md").write_text(claude_body, encoding="utf-8")
    (root / "project-knowledge" / "IMPROVEMENT_BACKLOG.md").write_text(
        "# fixture backlog\n", encoding="utf-8")

    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "commit", "-qm", "fixture"]):
        subprocess.run(cmd, cwd=str(root), env=env, check=True,
                       capture_output=True)
    n = len([f for f in subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(root), capture_output=True, text=True
    ).stdout.split("\0") if f])
    assert n >= _MIN_TRACKED, (
        "BD-GATE-UNRUNNABLE: fixture tracked only %d files; the gate returns "
        "UNKNOWN below %d, so this fixture could not exercise the predicate"
        % (n, _MIN_TRACKED))
    return root


def test_an_out_of_range_frontend_anchor_is_REPORTED(tmp_path):
    """The discriminating direction: invisible reads OK, visible reads STALE."""
    past_end = _PANEL_LINES + 500
    root = _fixture_repo(
        tmp_path,
        "# fixture\n\nSee frontend/src/routes/Panel.tsx:%d for the thing.\n" % past_end)
    res = _freshcheck_mod().check_anchors(root)
    # STALE specifically, not merely "not OK": UNKNOWN also satisfies != OK, and
    # UNKNOWN is what a gate returns when it saw NOTHING -- which is the exact
    # state being tested for. A != comparison here would pass on the blindness
    # it is supposed to detect.
    assert res["status"] == "STALE", (
        "an anchor %d lines past the end of a %d-line .tsx file was reported "
        "%r -- the gate cannot SEE frontend citations, so it certifies them by "
        "excluding them from its denominator (detail: %s)"
        % (past_end - _PANEL_LINES, _PANEL_LINES, res["status"], res["detail"]))
    assert "Panel.tsx" in res["detail"], (
        "the gate flagged something, but not the frontend anchor: %s" % res["detail"])


def test_a_valid_frontend_anchor_is_NOT_reported(tmp_path):
    """The over-sensitive direction, asserted in the same cut.

    A 'fix' that simply reported every frontend anchor broken would satisfy the
    test above and destroy the gate. This is the half that forbids it.
    """
    root = _fixture_repo(
        tmp_path,
        "# fixture\n\nSee frontend/src/routes/Panel.tsx:%d for the thing.\n"
        % (_PANEL_LINES // 2))
    res = _freshcheck_mod().check_anchors(root)
    assert res["status"] == "OK", (
        "an IN-RANGE frontend anchor was reported %r -- widening the gate must "
        "not make it fire on correct citations, which section 0 counts as a "
        "soundness bug equal to a false clean (detail: %s)"
        % (res["status"], res["detail"]))


def test_a_bare_basename_frontend_anchor_still_resolves(tmp_path):
    """Bare basenames resolve against the tracked tree -- .tsx is no exception.

    Recorded because the register predicted the OPPOSITE: that widening the
    extension alone would fail a correct citation for its FORM. Measured false
    -- check_anchors has resolved bare basenames since it was written, and
    reports AMBIGUOUS rather than guessing when one matches several files.
    """
    root = _fixture_repo(
        tmp_path, "# fixture\n\nSee Panel.tsx:%d for the thing.\n" % (_PANEL_LINES // 2))
    res = _freshcheck_mod().check_anchors(root)
    assert res["status"] == "OK", (
        "a bare-basename .tsx anchor was reported %r: %s"
        % (res["status"], res["detail"]))
