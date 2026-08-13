"""A tree that changes DURING a capture invalidates the run's verdict.

BACKLOG 100. The @1079 guard checks PREFLIGHT ONLY, so it cannot see the
failure it was written for. `capture.sh` refuses a tree that is dirty when the
run STARTS; a tree that goes dirty while the run is in flight passes that check
untouched. That is exactly what invalidated test5 at the 1082 capture round --
nine files edited mid-run, step [2b]'s graph pin drifted against a tree that no
longer matched the one collection had read, and the whole capture was graded
FAIL over a suite that was entirely green.

WHY A THIRD STATE RATHER THAN `FAIL`. The 1082 round is the argument: a
tree-caused red reads as a code defect, and it was read that way. FAIL is the
word this grader uses for "the software is broken", so spending it on "the
measurement is void" destroys the distinction the operator needs most. INVALID
says the run measured a tree that no longer exists -- not a pass, and not a
defect report. CLAUDE.md section 0's rule, in the verdict grammar: unknown is a
third state, and it fails.

WHY `CAPTURE_ALLOW_DIRTY` DOES NOT SUPPRESS IT. The override means "I know this
tree is dirty and I meant it" -- consent to a KNOWN state at t=0. A tree that
shifts underneath a run in progress is a different event, which nobody consented
to and which drifts the graph pin mid-flight. An override that covered both
would re-open the hole this row exists to close, for anyone who sets the flag
out of habit.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Its subject is one script's postflight and one tool's verdict grammar, not an
# invariant over the tree. Same scope as @1079, which gates the preflight half.
BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_LIB = _REPO / "scripts" / "lib" / "tree_state.sh"
_CAPTURE = _REPO / "capture.sh"

sys.path.insert(0, str(_REPO / "tests"))

# Imported rather than re-implemented: these build the unit/live artifacts the
# grader parses, and a second copy here would drift from the schema the real
# tool reads the moment `schema_version` moves.
from test_capture_verdict import _write_live, _write_unit  # noqa: E402

from tools.capture_verdict import assess_capture, main  # noqa: E402


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _repo(tmp_path: Path, name: str = "r") -> Path:
    """A real git repo with one commit. The predicate reads git, so a fake
    directory would prove nothing about it."""
    r = tmp_path / name
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(r, "add", "tracked.txt")
    _git(r, "commit", "-qm", "init")
    return r


def _snapshot(directory: Path) -> subprocess.CompletedProcess:
    """Run the library's snapshot helper against `directory`."""
    script = (
        f". {_LIB}\n"
        f'bd_tree_state_snapshot "{directory}"\n'
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


# ---------------------------------------------------------------- snapshot --

def test_the_snapshot_helper_exists_and_is_stable_across_calls(tmp_path):
    """Two readings of an unchanged tree must be identical.

    An unstable snapshot is the over-sensitivity failure CLAUDE.md section 0
    names alongside the blind one: a drift check that fires on every run gets
    switched off, and then it protects nothing. This is the reason the snapshot
    must not carry a timestamp -- the manifest-pin defect that hashed a
    wall-clock `generated` field is the recorded worked example.
    """
    r = _repo(tmp_path)

    first = _snapshot(r)
    second = _snapshot(r)

    assert first.returncode == 0, (
        f"snapshot of a clean repo failed: {first.stderr}")
    assert first.stdout, "snapshot produced no output for a real repo"
    assert first.stdout == second.stdout, (
        "the snapshot is not stable across two readings of an unchanged tree; "
        "a drift check built on it would fire on every capture")


def test_the_snapshot_moves_when_a_tracked_file_changes(tmp_path):
    r = _repo(tmp_path)
    before = _snapshot(r).stdout

    (r / "tracked.txt").write_text("two\n", encoding="utf-8")
    after = _snapshot(r).stdout

    assert before != after, (
        "editing a tracked file did not move the snapshot -- this is the exact "
        "mid-run edit that invalidated the 1082 round")


def test_the_snapshot_moves_when_an_untracked_file_appears(tmp_path):
    """@1079 already counts an untracked file as dirty; the postflight half
    must agree, or a file dropped into the tree mid-run is invisible."""
    r = _repo(tmp_path)
    before = _snapshot(r).stdout

    (r / "scratch.py").write_text("x = 1\n", encoding="utf-8")
    after = _snapshot(r).stdout

    assert before != after, (
        "an untracked file appearing mid-run did not move the snapshot")


def test_the_snapshot_moves_when_head_moves(tmp_path):
    """Committing mid-run leaves the tree CLEAN, so a porcelain-only snapshot
    reads identical before and after -- and the graph pin has still drifted.

    This is the case a dirty-only predicate cannot see: `git status` is empty on
    both sides while the source the capture measured is no longer the source
    HEAD names.
    """
    r = _repo(tmp_path)
    before = _snapshot(r).stdout

    (r / "tracked.txt").write_text("two\n", encoding="utf-8")
    _git(r, "add", "tracked.txt")
    _git(r, "commit", "-qm", "mid-run commit")
    after = _snapshot(r).stdout

    assert before != after, (
        "a mid-run COMMIT left the snapshot unchanged; the tree is clean on "
        "both sides, so only HEAD distinguishes them")


def test_a_non_repo_snapshot_is_unknown_and_says_so(tmp_path):
    """UNKNOWN is a third state here too, and the failure it prevents is
    specific: two unreadable snapshots compare EQUAL as strings.

    If the helper answered with silence outside a repository, `before == after`
    would be trivially true and the comparison would certify "no drift" over a
    subject it could not see -- CLAUDE.md section 0's entire subject, reproduced
    inside the check written to close a section 0 gap.
    """
    plain = tmp_path / "not_a_repo"
    plain.mkdir()

    result = _snapshot(plain)

    assert result.returncode != 0, (
        "a non-repository produced a SUCCESSFUL snapshot; callers cannot "
        "distinguish 'no drift' from 'could not look'")
    combined = (result.stdout + result.stderr).upper()
    assert "UNKNOWN" in combined, (
        "the helper does not say it could not answer; a caller comparing two "
        "silent snapshots would find them equal and report no drift")


def _drift(earlier: str, directory: Path) -> subprocess.CompletedProcess:
    """Run the library's comparison helper, exactly as capture.sh calls it."""
    script = (
        f". {_LIB}\n"
        f'bd_tree_state_drift "$1" "{directory}"\n'
    )
    return subprocess.run(["bash", "-c", script, "_", earlier],
                          capture_output=True, text=True)


def test_the_comparison_reports_no_drift_on_an_untouched_tree(tmp_path):
    """The over-sensitivity control at the seam. If this fires, every capture
    on every host reports INVALID and the check gets switched off."""
    r = _repo(tmp_path)
    before = _snapshot(r).stdout

    result = _drift(before, r)

    assert result.returncode == 0, (
        f"an untouched tree reported drift: {result.stdout}{result.stderr}")
    assert result.stdout.strip() == "", (
        f"no drift, but the helper printed: {result.stdout!r}")


def test_the_comparison_names_a_file_that_appeared(tmp_path):
    r = _repo(tmp_path)
    before = _snapshot(r).stdout
    (r / "scratch.py").write_text("x = 1\n", encoding="utf-8")

    result = _drift(before, r)

    assert result.returncode == 1, (
        f"a new file did not register as drift (rc={result.returncode})")
    assert "scratch.py" in result.stdout, (
        f"drift detected but the path was not named: {result.stdout!r}")


def test_the_comparison_names_a_file_that_vanished(tmp_path):
    """A mid-run `git stash` removes files, and CLAUDE.md section 9 records
    that reflex explicitly -- collection has already happened, so removing
    files leaves a collected-but-inconsistent state. A one-sided diff would
    miss it entirely."""
    r = _repo(tmp_path)
    (r / "scratch.py").write_text("x = 1\n", encoding="utf-8")
    before = _snapshot(r).stdout
    (r / "scratch.py").unlink()

    result = _drift(before, r)

    assert result.returncode == 1, (
        "a file REMOVED mid-run did not register as drift")
    assert "scratch.py" in result.stdout


def test_the_comparison_refuses_when_the_tree_became_unreadable(tmp_path):
    """Third state at the seam as well: if the tree cannot be read at the end,
    the honest answer is 'unanswerable', not 'no drift'."""
    r = _repo(tmp_path)
    before = _snapshot(r).stdout

    plain = tmp_path / "gone"
    plain.mkdir()
    result = _drift(before, plain)

    assert result.returncode == 2, (
        f"an unreadable tree did not report UNKNOWN (rc={result.returncode}); "
        "reporting 'no drift' here would certify over a subject it cannot see")


# ----------------------------------------------------------------- verdict --

def _artifacts(tmp_path, **unit_kwargs):
    unit = tmp_path / "unit.json"
    live = tmp_path / "live.log"
    _write_unit(unit, **unit_kwargs)
    _write_live(live)
    return unit, live


def test_recorded_drift_makes_the_verdict_invalid(tmp_path):
    unit, live = _artifacts(tmp_path)
    drift = tmp_path / "00_tree_drift.txt"
    drift.write_text("M bulk_downloader/app.py\n", encoding="utf-8")

    result = assess_capture(unit, live, suite_exit=0, live_exit=0,
                            tree_drift_file=drift)

    assert "INVALID" in result.summary, (
        f"a run whose tree moved was not graded INVALID: {result.summary}")
    assert result.ok is False, "an INVALID run must not report ok"
    assert result.exit_code == 3, (
        f"INVALID must carry its own exit code, got {result.exit_code}; "
        "sharing FAIL's code throws away the distinction the verdict just "
        "gained (CLAUDE.md section 10: assert the reason, not the code)")


def test_the_invalid_summary_names_the_paths_that_moved(tmp_path):
    """A verdict that says only "the tree changed" sends the reader back to a
    machine that has since been reset. Name the paths while they are known."""
    unit, live = _artifacts(tmp_path)
    drift = tmp_path / "00_tree_drift.txt"
    drift.write_text("M bulk_downloader/app.py\n?? tests/scratch.py\n",
                     encoding="utf-8")

    result = assess_capture(unit, live, suite_exit=0, live_exit=0,
                            tree_drift_file=drift)

    assert "bulk_downloader/app.py" in result.summary, (
        f"the INVALID verdict does not name what moved: {result.summary}")


def test_invalid_takes_precedence_over_fail(tmp_path):
    """A run whose tree shifted cannot ATTRIBUTE its own failures.

    The counts stay in the line -- nothing is hidden -- but the grade must be
    INVALID, because "these tests failed" is a claim about a tree that was not
    the tree under test.
    """
    unit, live = _artifacts(tmp_path, passed=1, failed=1, skipped=0, ok=False)
    drift = tmp_path / "00_tree_drift.txt"
    drift.write_text("M bulk_downloader/app.py\n", encoding="utf-8")

    result = assess_capture(unit, live, suite_exit=1, live_exit=0,
                            tree_drift_file=drift)

    assert "INVALID" in result.summary, (
        "drift plus failures was graded as an ordinary FAIL, so a void "
        "measurement is indistinguishable from a broken product")
    assert "unit failures=1" in result.summary, (
        "the INVALID verdict discarded the counts; they are still the only "
        "record of what the run saw")


def test_an_empty_drift_file_leaves_a_clean_run_passing(tmp_path):
    """The over-sensitivity control. A capture that touched nothing still
    writes the file; an empty one must mean exactly 'no drift'."""
    unit, live = _artifacts(tmp_path)
    drift = tmp_path / "00_tree_drift.txt"
    drift.write_text("", encoding="utf-8")

    result = assess_capture(unit, live, suite_exit=0, live_exit=0,
                            tree_drift_file=drift)

    assert result.ok is True and result.exit_code == 0, (
        f"an empty drift file invalidated a clean run: {result.summary}")
    assert "PASS" in result.summary


def test_a_missing_drift_file_leaves_the_verdict_alone(tmp_path):
    """Every capture bundle recorded before this cut has no drift file, and
    replaying one must not turn it INVALID. Absence is 'not recorded', which is
    the state every historical archive is in."""
    unit, live = _artifacts(tmp_path)

    absent = assess_capture(unit, live, suite_exit=0, live_exit=0,
                            tree_drift_file=tmp_path / "nope.txt")
    unpassed = assess_capture(unit, live, suite_exit=0, live_exit=0)

    assert absent.ok is True and absent.exit_code == 0
    assert unpassed.ok is True and unpassed.exit_code == 0


def test_the_cli_exposes_the_drift_file_and_returns_three(tmp_path, capsys):
    """The seam, per CLAUDE.md section 10: capture.sh calls this through
    argparse, not through `assess_capture`, and a flag the parser does not
    accept is a flag the run never passes."""
    unit, live = _artifacts(tmp_path)
    drift = tmp_path / "00_tree_drift.txt"
    drift.write_text("M bulk_downloader/app.py\n", encoding="utf-8")

    code = main([
        "--tests-json", str(unit),
        "--live-log", str(live),
        "--suite-exit", "0",
        "--live-exit", "0",
        "--expected-live-tests", "3",
        "--tree-drift-file", str(drift),
    ])

    assert code == 3, f"CLI did not return the INVALID code, got {code}"
    assert "INVALID" in capsys.readouterr().out


# ------------------------------------------------------------------- shell --

def _capture_code() -> str:
    from shell_source import shell_code_only
    return shell_code_only(_CAPTURE)


def test_capture_sh_snapshots_before_and_compares_after():
    """Asserted over comment-stripped shell, so the paragraph explaining the
    comparison cannot stand in for the comparison."""
    code = _capture_code()

    assert "bd_tree_state_snapshot" in code, (
        "capture.sh never takes a tree snapshot, so it has nothing to compare "
        "the post-run state against")
    assert "bd_tree_state_drift" in code, (
        "capture.sh snapshots but never compares; the postflight half is the "
        "whole point of backlog 100")
    assert "--tree-drift-file" in code, (
        "capture.sh takes a snapshot but never hands the result to the grader, "
        "so the verdict cannot report it")


def test_the_postflight_comparison_is_not_gated_on_allow_dirty():
    """CAPTURE_ALLOW_DIRTY consents to a state, not to a moving target.

    Asserted structurally rather than by proximity: the override appears in the
    library's preflight branch, so a bare 'both strings are present' check would
    pass on any file containing either.

    `if_blocks_containing`, NOT `blocks_containing` -- the latter walks only
    `for`/`while` and falls back to the bare line for anything else, so an
    assertion about an `if` branch written against it gets back the header line
    and can never see the body. Its own docstring records that escaping a
    mutant at v3.66.1037, and this test was written against the wrong one first.
    """
    from shell_source import if_blocks_containing

    code = _capture_code()
    drift_blocks = if_blocks_containing(code, "bd_tree_state_snapshot")
    assert drift_blocks, (
        "no shell block contains the snapshot call; cannot establish whether "
        "the override reaches it")
    for block in drift_blocks:
        assert "CAPTURE_ALLOW_DIRTY" not in block, (
            "the mid-run drift check sits inside a CAPTURE_ALLOW_DIRTY branch, "
            "so setting the override out of habit re-opens backlog 100")
