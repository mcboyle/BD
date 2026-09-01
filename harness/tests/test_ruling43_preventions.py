"""Ruling 43: the four preventions ruled after v3.66.1381 cost 20 minutes.

One broken mutant anchor surfaced 13 minutes into a verify and again in CI, and
bd-anchorcheck already answered that question in 0.30 seconds. Each test here is
a refusal or a positive control, because the failure being prevented is a check
that returns clean.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HOME = Path(os.environ.get("BD_HARNESS_HOME", str(Path.home())))
ANCHORCHECK = HOME / "bd-anchorcheck.py"
NEXT_ROW = HOME / "bd-next-row"
INSERT = HOME / "bd-register-insert.py"
VERIFY = HOME / "bd-verify-cut.sh"
PREFLIGHT = HOME / "bd-denom-preflight"
REPO = Path("/home/mboyle/BulkDownloader")
REGISTER = REPO / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"


def sh(*argv, **kw):
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True, **kw)


# --------------------------------------------------------------------------
# bd-anchorcheck --catchers
# --------------------------------------------------------------------------
@pytest.fixture
def spec_tree(tmp_path):
    """A minimal tree with one mutant spec and one subject, both real files."""
    (tmp_path / "tests" / "mutants").mkdir(parents=True)
    subject = tmp_path / "tests" / "test_subject.py"
    subject.write_text(
        "MARKER = 1\n\n\ndef test_catcher():\n    assert MARKER == 1\n\n\n"
        "class TestGroup:\n    def test_nested_catcher(self):\n        assert True\n",
        encoding="utf-8")
    return tmp_path


def write_spec(tree: Path, mutants: list[dict]) -> Path:
    p = tree / "tests" / "mutants" / "spec.json"
    p.write_text(json.dumps({"subject": "control", "mutants": mutants}, indent=1),
                 encoding="utf-8")
    return p


def test_a_missing_catcher_is_refused(spec_tree):
    write_spec(spec_tree, [{
        "label": "M1", "file": "tests/test_subject.py",
        "old": "MARKER = 1", "new": "MARKER = 2", "direction": "regression",
        "catcher": "tests/test_subject.py::test_that_does_not_exist"}])
    r = sh("python3", ANCHORCHECK, "--work", spec_tree, "--catchers")
    assert r.returncode == 1, r.stdout
    assert "BROKEN" in r.stdout and "test_that_does_not_exist" in r.stdout
    assert "can never fail" in r.stdout


def test_a_class_qualified_catcher_is_not_a_false_positive(spec_tree):
    """The first version looked for `def TestGroup::test_nested_catcher(` and
    reported five perfectly good catchers as broken."""
    write_spec(spec_tree, [{
        "label": "M1", "file": "tests/test_subject.py",
        "old": "MARKER = 1", "new": "MARKER = 2", "direction": "regression",
        "catcher": "tests/test_subject.py::TestGroup::test_nested_catcher"}])
    r = sh("python3", ANCHORCHECK, "--work", spec_tree, "--catchers")
    assert r.returncode == 0, r.stdout
    assert "BROKEN" not in r.stdout


def test_a_catcher_file_that_vanished_is_refused(spec_tree):
    write_spec(spec_tree, [{
        "label": "M1", "file": "tests/test_subject.py",
        "old": "MARKER = 1", "new": "MARKER = 2", "direction": "regression",
        "catcher": "tests/test_gone.py::test_catcher"}])
    r = sh("python3", ANCHORCHECK, "--work", spec_tree, "--catchers")
    assert r.returncode == 1
    assert "catcher file does not exist" in r.stdout


def test_a_stale_anchor_is_still_refused_with_catchers_on(spec_tree):
    """The v3.66.1381 break itself: the anchored line is gone."""
    write_spec(spec_tree, [{
        "label": "M1", "file": "tests/test_subject.py",
        "old": "MARKER = 99999", "new": "MARKER = 2", "direction": "regression",
        "catcher": "tests/test_subject.py::test_catcher"}])
    r = sh("python3", ANCHORCHECK, "--work", spec_tree, "--catchers")
    assert r.returncode == 1
    assert "occurs 0 times, expected 1" in r.stdout


def test_the_live_repository_passes_both_halves():
    """A positive control: the tool must be able to say OK about a real tree, or
    the refusals above prove nothing."""
    r = sh("python3", ANCHORCHECK, "--work", REPO, "--catchers")
    assert r.returncode == 0, r.stdout[-3000:]
    anchors = int(re.search(r"anchors: (\d+) checked", r.stdout).group(1))
    catchers = int(re.search(r"catchers: (\d+) checked", r.stdout).group(1))
    assert anchors > 1000 and catchers > 1000, (anchors, catchers)


# --------------------------------------------------------------------------
# bd-next-row
# --------------------------------------------------------------------------
def test_next_row_is_not_the_row_count():
    """The whole trap: 474 rows, ids to 531. Naming a file from the count
    collides with a live row."""
    r = sh(NEXT_ROW, "--json")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["next"] == d["max"] + 1
    assert d["next"] != d["rows"], "the count and the next id coincided; pick another tree"
    assert d["gap_count"] > 0


def test_next_row_refuses_a_register_with_no_rows(tmp_path):
    empty = tmp_path / "empty.md"
    empty.write_text("# nothing here\n", encoding="utf-8")
    r = sh(NEXT_ROW, empty)
    assert r.returncode == 2
    assert "zero rows" in r.stderr


def test_next_row_refuses_duplicate_ids(tmp_path):
    dup = tmp_path / "dup.md"
    dup.write_text("| 1 | OPEN | a |\n| 2 | OPEN | b |\n| 2 | OPEN | c |\n", encoding="utf-8")
    r = sh(NEXT_ROW, dup)
    assert r.returncode == 2
    assert "duplicate row id" in r.stderr


def test_next_row_refuses_a_missing_register(tmp_path):
    r = sh(NEXT_ROW, tmp_path / "nope.md")
    assert r.returncode == 2
    assert "UNKNOWN" in r.stderr


# --------------------------------------------------------------------------
# bd-register-insert.py repairs the header it used to leave stale
# --------------------------------------------------------------------------
@pytest.fixture
def register_copy(tmp_path):
    """A copy positioned so the tool's repo-relative parser lookup still works."""
    pk = tmp_path / "project-knowledge"
    pk.mkdir()
    shutil.copy2(REGISTER, pk / REGISTER.name)
    shutil.copy2(REPO / "project-knowledge" / "build_current_overlay.py",
                 pk / "build_current_overlay.py")
    return pk / REGISTER.name


def _header(path: Path) -> str:
    m = re.search(r"<!-- canonical-task-register schema=1 rows=(\d+) open=(\d+) ", 
                  path.read_text(encoding="utf-8"))
    assert m, "no canonical header"
    return m.group(0)


def test_insert_repairs_the_header_it_used_to_leave_stale(register_copy):
    before = _header(register_copy)
    rows_before = int(re.search(r"rows=(\d+)", before).group(1))
    r = sh("python3", INSERT, register_copy, "9001",
           "| 9001 | OPEN | control row inserted by the harness |")
    assert r.returncode == 0, r.stdout + r.stderr
    after = _header(register_copy)
    rows_after = int(re.search(r"rows=(\d+)", after).group(1))
    assert rows_after == rows_before + 1, (
        "the header still disagrees with the table; bd-register-append will "
        "refuse the whole cut three steps away from this cause")
    assert "header repaired" in r.stdout


def test_insert_reports_stale_rather_than_lying_when_it_cannot_repair(tmp_path):
    """Missing parser: the row is written and the staleness is LOUD, exit 4.
    A silent stale header is the failure this exists to end."""
    pk = tmp_path / "project-knowledge"
    pk.mkdir()
    shutil.copy2(REGISTER, pk / REGISTER.name)     # no build_current_overlay.py
    target = pk / REGISTER.name
    r = sh("python3", INSERT, target, "9002",
           "| 9002 | OPEN | control row with no parser available |")
    assert r.returncode == 4, r.stdout + r.stderr
    assert "STALE" in r.stderr
    assert "| 9002 |" in target.read_text(encoding="utf-8"), (
        "the insertion was silently dropped; reporting a problem must not also "
        "discard the work")


# --------------------------------------------------------------------------
# bd-verify-cut publishes, and refuses to do anything reckless about it
# --------------------------------------------------------------------------
def test_publish_never_forces_and_is_opt_out_able():
    body = VERIFY.read_text(encoding="utf-8")
    start = body.index("publish_candidate() {")
    end = body.index("publish_candidate\n", start)
    fn = body[start:end]
    assert "--force" not in fn, "the publisher may not force a remote ref (A4)"
    assert "BD_VERIFY_CUT_NO_PUBLISH" in fn
    assert 'if [ "$head" != "$CANDIDATE_SHA" ]' in fn, (
        "the publisher must refuse to push a branch that is not at the exact "
        "candidate; otherwise CI proves something about a different tree")


def test_precut_leaving_no_exit_status_is_unknown_not_a_pass():
    body = VERIFY.read_text(encoding="utf-8")
    start = body.index("collect_precut() {")
    fn = body[start:body.index("\n}\n", start)]
    assert "PRECUT_RC=97" in fn and "UNOBSERVED" in fn, (
        "a concurrent gate that leaves no status must be UNKNOWN; an absent "
        "measurement is never permission (A2)"
    )


# --------------------------------------------------------------------------
# Remote dispatch: the modes, and the fallbacks that must not become passes
# --------------------------------------------------------------------------
BAND_REMOTE = HOME / "bd-band-remote.sh"


def test_the_remote_executor_has_one_host_selection_mechanism():
    """precut and prepush were the last two gates pinned to the integrator.
    They go through the SAME mirror/slot/worktree machinery as the band; a
    second implementation of it is what A8 forbids."""
    body = BAND_REMOTE.read_text(encoding="utf-8")
    assert body.count("worktree add -q --detach") == 1, (
        "the host-side worktree is created in more than one place")
    for mode in ("band", "precut", "prepush"):
        assert f"  {mode})" in body, f"mode {mode} is not dispatched"


def test_a_selector_free_mode_is_not_refused_for_having_no_selectors():
    """precut and prepush judge the whole tree. The first attempt refused them
    with 'no tests given' from the band's own argument check."""
    body = BAND_REMOTE.read_text(encoding="utf-8")
    i = body.index('no tests given')
    guard = body[max(0, i - 200):i]
    assert "BD_REMOTE_MODE" in guard, (
        "the selector precondition is unconditional, so every selector-free "
        "mode is refused before it starts")


def test_the_shipped_prepush_script_is_written_outside_the_worktree():
    """It was written INSIDE it, and prepush's own untracked-drift check
    counted it: the first remote prepush failed with
    'untracked: .bd-prepush.shipped' -- a gate reporting the harness that
    invoked it."""
    body = BAND_REMOTE.read_text(encoding="utf-8")
    assert '"$wt/.bd-prepush.shipped"' not in body
    assert 'mktemp "${TMPDIR:-/tmp}/bd-prepush-shipped' in body


def test_no_capacity_host_is_never_a_prepush_pass():
    """rc 64 means REMOTE-UNAVAILABLE. Read as a gate result it is a pass, and
    a pass is exactly what an unrun gate must never produce (A2)."""
    body = VERIFY.read_text(encoding="utf-8")
    i = body.index("PREPUSH_RAN_REMOTE=0")
    block = body[i:i + 1400]
    assert 'PREPUSH_RC" -eq 64' in block, "rc 64 is not distinguished at all"
    assert "PREPUSH_RC=98" in block, (
        "an unavailable capacity host leaves prepush's rc at 64, which the "
        "classifier below does not treat as a failure")
    assert 'if [ "$PREPUSH_RAN_REMOTE" -eq 0 ]' in block, (
        "there is no local fallback, so an unavailable fleet blocks every cut")


def test_every_remote_refusal_names_itself():
    """One exit code for ten causes cost an afternoon: 'unavailable, busy, or
    subject proof failed' hid a broken fetch while every band silently ran on
    the integrator."""
    body = BAND_REMOTE.read_text(encoding="utf-8")
    for code in (75, 76, 77, 78, 79, 80, 81, 82, 83, 84):
        assert f"    {code})" in body, f"exit {code} has no named diagnosis"
    assert "unavailable, busy, or subject proof failed" not in body, (
        "the collapsed diagnostic is back")
