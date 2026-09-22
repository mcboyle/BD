"""H640: a re-prep must ARCHIVE completed lens verdicts, never delete them.

WHAT HAPPENED, and it is the reason this file exists. On 2026-09-22 at ~03:xx
the offer sweep re-prepped 29 review worktrees -- a routine H526 refresh,
triggered because each cut's DONE.md was newer than its review copy -- and every
`VERDICT-*.md` in all 29 was deleted. The mechanism was two lines far apart:
`bd-offer-sweep.sh` passed `BD_REVIEW_PREP_FORCE=1`, and the `FORCE=1` branch of
the H195 guard in `bd-review-prep.sh` only *echoed* "discarding", then fell
through to `git worktree remove --force`. The guard was doing exactly what it
said; nobody had decided that a SCHEDULED refresh should be allowed to say it.

THE TWO HALVES OF THE FIX, asserted separately below because either alone still
loses verdicts: the sweep stops passing the flag, and the prep archives rather
than discards when the flag IS passed by a human at the CLI.

WHY ARCHIVE AND NOT REFUSE. A verdict names a TREE. Once the cut is re-prepped
the tree has moved, so the old verdict is not a judgement on the new object and
refusing the re-prep forever would wedge the lane. But it is still the record of
a lens round that happened, and FLEET_RULE 14 wants that written when taken, not
reconstructed. So it moves to `.review/superseded/<ts>/` beside the cut it
judged, and to the router copy in bd-persist/verdicts/ which outlives the
worktree entirely.

O1045/O1066: this file stages in the product repo; the candidate scripts live at
an absolute bd-persist path and the INTEGRATOR deploys them. Nothing here writes
to bd-persist/harness or to ~/.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_H640_CANDIDATE", "")

pytestmark = pytest.mark.skipif(
    not CANDIDATE,
    reason="BD_H640_CANDIDATE opt-in required: this gate judges a harness "
           "candidate at an absolute bd-persist path, and FLEET_RULE 46 forbids "
           "guessing one. A SUPPLIED but absent path FAILS below rather than "
           "skipping, so the opt-in cannot hide a missing candidate.")


def _candidate(name: str) -> Path:
    """The candidate script, or a FAILURE -- never a skip."""
    root = Path(CANDIDATE)
    assert root.is_dir(), (
        f"BD_H640_CANDIDATE={CANDIDATE!r} is not a directory; a supplied "
        "candidate path that does not exist is a failure, not a skip")
    path = root / name
    assert path.is_file(), f"candidate {name} missing from {root}"
    assert os.access(path, os.X_OK), f"candidate {name} is not executable"
    return path


def _text(name: str) -> str:
    return _candidate(name).read_text(encoding="utf-8", errors="replace")


# ── half one: the sweep stops handing an unattended job a destructive flag ────

def test_the_sweep_never_passes_force_to_the_prep():
    """The scheduled caller must not be able to authorise a discard.

    Asserted on the CALL, not on the file: the string may legitimately appear in
    the comment explaining why it is gone, and a test that only grepped the file
    would fail on its own explanation.
    """
    body = _text("bd-offer-sweep.sh")
    calls = [ln for ln in body.splitlines()
             if "bd-review-prep.sh" in ln and not ln.lstrip().startswith("#")]
    assert calls, (
        "no bd-review-prep.sh invocation found in the sweep candidate; the "
        "probe cannot say no about a call it cannot see")
    offenders = [ln.strip() for ln in calls if "BD_REVIEW_PREP_FORCE=1" in ln]
    assert not offenders, (
        "the sweep still authorises a verdict discard on an unattended "
        f"refresh: {offenders}")


def test_the_sweep_control_can_see_a_force_call():
    """Positive control for the assertion above: the same probe, pointed at the
    pre-fix twin, MUST find the offending call. Without this, a probe that
    silently stopped matching would report a clean sweep forever."""
    pre = Path(CANDIDATE) / "bd-offer-sweep.sh.pre"
    assert pre.is_file(), (
        "the pre-fix twin is missing, so the positive control cannot run and "
        "the zero above is unproven")
    calls = [ln for ln in pre.read_text(encoding="utf-8").splitlines()
             if "bd-review-prep.sh" in ln and not ln.lstrip().startswith("#")]
    assert any("BD_REVIEW_PREP_FORCE=1" in ln for ln in calls), (
        "the probe found no FORCE=1 call in the PRE-fix sweep, so it cannot "
        "discriminate and the clean result above means nothing")


# ── half two: the prep archives instead of discarding ────────────────────────

def _fake_prep_env(tmp_path: Path) -> tuple[Path, list[str]]:
    """A worktree carrying two completed verdicts, plus the guard's variables.

    The candidate is executed for real, but only the H195/H640 block is driven:
    the surrounding prep needs a full git repo and a collected cut, which is a
    different subject. The block is extracted by line range and run under bash
    with the same variable names, so a rename in the candidate breaks this test
    rather than silently passing.
    """
    work = tmp_path / "row999-local"
    (work / ".review").mkdir(parents=True)
    names = ["VERDICT-correctness-seat-a.md", "VERDICT-correctness-seat-b.md"]
    for i, name in enumerate(names):
        (work / ".review" / name).write_text(f"VERDICT: BOARD\nbody {i}\n",
                                             encoding="utf-8")
    return work, names


def _run_guard(tmp_path: Path, script: Path, work: Path, names: list[str],
               router: Path) -> subprocess.CompletedProcess:
    """Run the candidate's FORCE branch with a stubbed `die` and no worktree removal."""
    body = script.read_text(encoding="utf-8")
    start = body.index('  if [ "${BD_REVIEW_PREP_FORCE:-0}" = 1 ]; then')
    end = body.index('[ -d "$W" ] && { git -C "$R" worktree remove', start)
    block = body[start:end]
    harness = tmp_path / "guard.sh"
    harness.write_text(
        "set -u\n"
        "die() { echo \"DIE: $*\" >&2; exit 9; }\n"
        f"W={work!s}\n"
        f"_H195='{' '.join(names)}'\n"
        f"_H195N={len(names)}\n"
        "BD_REVIEW_PREP_FORCE=1\n"
        f"BD_VERDICT_ROUTER_DIR={router!s}\n"
        # The extracted range ends with the `fi` that closes the enclosing
        # `if [ -n "$_H195" ]`, so the opener is re-supplied here rather than
        # widening the slice to swallow the unrelated SYN-70 guard above it.
        "if [ -n \"$_H195\" ]; then\n"
        + block,
        encoding="utf-8")
    return subprocess.run(["bash", str(harness)], capture_output=True, text=True)


def test_a_forced_reprep_archives_every_verdict(tmp_path):
    """The row. Two verdicts in, two verdicts archived, byte-identical."""
    work, names = _fake_prep_env(tmp_path)
    router = tmp_path / "router"
    proc = _run_guard(tmp_path, _candidate("bd-review-prep.sh"), work, names, router)
    assert proc.returncode == 0, (
        f"the candidate's FORCE branch exited {proc.returncode}: {proc.stderr}")

    superseded = sorted((work / ".review" / "superseded").glob("*/"))
    assert len(superseded) == 1, (
        f"expected one timestamped archive dir, found {superseded}")
    archived = sorted(p.name for p in superseded[0].iterdir())
    assert archived == sorted(names), (
        f"archive holds {archived}, not the {len(names)} verdicts that existed")
    for name in names:
        assert (superseded[0] / name).read_bytes() == \
               (work / ".review" / name).read_bytes(), (
            f"{name} was archived as a DIFFERENT byteset; a mangled record is "
            "not a record")

    routed = sorted(p.name for p in router.iterdir()) if router.is_dir() else []
    assert len(routed) == len(names), (
        f"the router copy holds {routed}; it must survive the worktree removal")


def test_the_archive_is_carried_across_the_rebuild(tmp_path):
    """The worktree is removed right after this block, so an archive written only
    INSIDE it would be deleted by the very next line. It must also land in
    SUPERSEDED_TMP, which the prep copies back into the rebuilt worktree."""
    work, names = _fake_prep_env(tmp_path)
    proc = _run_guard(tmp_path, _candidate("bd-review-prep.sh"), work, names,
                      tmp_path / "router")
    assert proc.returncode == 0, proc.stderr
    carried = list(Path("/tmp").glob("bd-prep-superseded.*/*/VERDICT-*.md"))
    mine = [p for p in carried if p.name in names]
    assert mine, (
        "nothing was carried into SUPERSEDED_TMP, so the archive would die with "
        "the worktree that `git worktree remove --force` deletes on the next line")
    for p in {q.parent.parent for q in mine}:
        shutil.rmtree(p, ignore_errors=True)


def test_the_guard_refuses_rather_than_half_archiving(tmp_path):
    """Negative control with an exact exit code. If the archive cannot be
    written, the candidate must DIE before the removal rather than report a
    partial success -- a half-archived round loses the other half silently."""
    work, names = _fake_prep_env(tmp_path)
    # make the archive location unwritable by occupying it with a file
    (work / ".review" / "superseded").write_text("not a directory\n",
                                                 encoding="utf-8")
    proc = _run_guard(tmp_path, _candidate("bd-review-prep.sh"), work, names,
                      tmp_path / "router")
    assert proc.returncode == 9, (
        f"expected the stubbed die (exit 9), got {proc.returncode}: "
        f"{proc.stdout}{proc.stderr}")
    assert "H640" in proc.stderr, (
        f"the refusal does not name its reason: {proc.stderr!r}")


def test_the_pre_fix_twin_discards_and_this_probe_sees_it(tmp_path):
    """Positive control for the archive assertions: the PRE-fix prep, driven by
    the identical harness, archives NOTHING. That is what makes a clean result
    from the candidate a measurement instead of a tautology."""
    pre = Path(CANDIDATE) / "bd-review-prep.sh.pre"
    assert pre.is_file(), "the pre-fix twin is missing; the control cannot run"
    work, names = _fake_prep_env(tmp_path)
    proc = _run_guard(tmp_path, pre, work, names, tmp_path / "router")
    assert proc.returncode == 0, proc.stderr
    assert not (work / ".review" / "superseded").exists(), (
        "the PRE-fix prep archived something, so this probe does not "
        "discriminate and the candidate's pass proves nothing")
