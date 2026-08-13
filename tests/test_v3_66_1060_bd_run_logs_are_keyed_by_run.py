"""bd-run must not overwrite one run's evidence with the next one's.

@1060, backlog row 5 (the bd-run half). `bd-run` named its log `<label>.log`,
so two runs with the same label -- which is the NORMAL case, since a label
describes what is being run and not when -- silently destroyed the first run's
output. The tool whose docstring opens "keep EVERYTHING on disk" kept only the
most recent.

WHY THAT IS WORSE THAN IT SOUNDS. bd-run exists because a band is long and its
tail is where the summary, the verdict and the failure names live. The A/B
comparison it was built to support -- run the same label twice and diff -- was
the exact thing it could not do. And the loss is silent: the second run prints
the same path the first one did.

THE SHAPE OF THE FIX. `<label>-<runid>.log` for the real artifact, with
`<label>.log` kept as a symlink to the newest so `tail -f <label>.log` and every
existing caller still work. Retention stays bounded by --keep; a bound that
grows without limit is the leak CLAUDE.md section 0 records at 744 directories.

CLOSED AT v3.66.1099: item 5's capture.sh half. `/tmp/bd_capture` was a fixed
path referenced by five test files, so consecutive captures overwrote each
other; it is now `/tmp/bd_capture-<runid>/`, pruned to the newest few at run
START so a crashed run's evidence survives. See
tests/test_v3_66_1099_capture_dirs_are_keyed_by_run.py.
"""

import os
import pathlib
import subprocess
import sys
import time

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_BIN = _REPO / "toolchain" / "bin"
_RUN = _BIN / "bd-run"


def _run(tmp_path, label, script, keep=None):
    cmd = [sys.executable, str(_RUN), "--label", label, "--dir", str(tmp_path)]
    if keep is not None:
        cmd += ["--keep", str(keep)]
    cmd += ["--", "sh", "-c", script]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def _real_logs(d: pathlib.Path):
    """Regular files only -- a symlink is a pointer, not an artifact."""
    return sorted(p for p in d.glob("*.log") if p.is_file() and not p.is_symlink())


def test_the_tool_is_runnable_at_all(tmp_path):
    """Denominator before verdict. If bd-run cannot execute here, every
    assertion below would be measuring an empty directory."""
    r = _run(tmp_path, "probe", "echo hello")
    assert r.returncode == 0, r.stdout + r.stderr
    assert list(tmp_path.glob("*.log")), "no log produced at all"


def test_two_runs_of_the_same_label_both_survive(tmp_path):
    """THE DEFECT."""
    _run(tmp_path, "band", "echo FIRST-RUN-SENTINEL")
    time.sleep(1.1)          # distinct run id even at one-second granularity
    _run(tmp_path, "band", "echo SECOND-RUN-SENTINEL")

    logs = _real_logs(tmp_path)
    assert len(logs) == 2, (
        f"two runs of label 'band' left {len(logs)} log(s): "
        f"{[p.name for p in logs]}. The second run overwrote the first, so the "
        f"comparison bd-run exists to support is impossible."
    )
    blob = "\n".join(p.read_text() for p in logs)
    for sentinel in ("FIRST-RUN-SENTINEL", "SECOND-RUN-SENTINEL"):
        assert sentinel in blob, f"{sentinel} is gone from the evidence on disk"


def test_the_label_path_still_resolves_to_the_newest(tmp_path):
    """Compatibility is load-bearing: existing callers and `tail -f` name
    `<label>.log`, and a fix that breaks them trades one defect for another."""
    _run(tmp_path, "band", "echo OLD-ONE")
    time.sleep(1.1)
    _run(tmp_path, "band", "echo NEW-ONE")

    alias = tmp_path / "band.log"
    assert alias.exists(), "the plain <label>.log path no longer resolves"
    text = alias.read_text()
    assert "NEW-ONE" in text, "the alias does not point at the newest run"
    assert "OLD-ONE" not in text, "the alias is a concatenation, not a pointer"


def test_the_announced_path_is_the_one_that_gets_written(tmp_path):
    """bd-run prints its log path up front so an operator can tail it from the
    first second. A path that is announced and then not written is worse than
    no announcement -- the operator tails a file that never appears."""
    r = _run(tmp_path, "announce", "echo WROTE-HERE")
    printed = [l for l in r.stdout.splitlines() if "->" in l]
    assert printed, f"bd-run announced no path:\n{r.stdout}"
    target = pathlib.Path(printed[0].split("->")[-1].strip())
    assert target.exists(), f"announced {target} but nothing was written there"
    assert "WROTE-HERE" in target.read_text()


def test_retention_stays_bounded_and_leaves_no_dangling_alias(tmp_path):
    """Keeping every run forever is the 744-directory leak in slow motion.

    The alias makes this sharper than plain pruning: deleting the newest real
    log without repointing leaves a symlink to nothing, and a dangling pointer
    reads to the next reader as a missing run rather than a pruned one.
    """
    for i in range(5):
        _run(tmp_path, "rot", "echo run-%d" % i, keep=2)
        time.sleep(1.1)

    logs = _real_logs(tmp_path)
    # EXACTLY two, not "at most". A mutant that let prune count the symlink
    # alias as a log kept one real log and one pointer while --keep said 2 --
    # fewer artifacts than promised -- and sailed through a `<= 2` assertion.
    # An upper bound alone cannot see under-retention.
    assert len(logs) == 2, (
        f"--keep 2 left {len(logs)} real logs, expected exactly 2: "
        f"{[p.name for p in logs]}. Too few means the alias is being counted "
        f"against the retention budget; too many means prune did not run."
    )
    alias = tmp_path / "rot.log"
    assert alias.exists(), "the alias to the newest run is missing"
    assert "run-4" in alias.read_text(), "alias does not name the newest run"


def test_an_alias_whose_target_was_pruned_is_collected(tmp_path):
    """The dangling case, which needs a SECOND label to construct at all.

    THIS TEST EXISTS BECAUSE A MUTANT ESCAPED. The first version of the
    retention test above could never produce a dangling alias: the alias always
    points at the NEWEST log, and the newest is exactly what --keep retains. So
    a mutant deleting the dangling-alias cleanup changed nothing observable and
    the band stayed green over a behaviour with no coverage at all.

    The shape only occurs across labels: label A's log ages out while A.log
    still points at it. Section 6 -- assert the harness built the shape before
    asserting the verdict.
    """
    _run(tmp_path, "old", "echo the-old-one", keep=2)
    time.sleep(1.1)
    for i in range(4):
        _run(tmp_path, "new", "echo new-%d" % i, keep=2)
        time.sleep(1.1)

    # PRECONDITION: old's real log must actually be gone, or this test is
    # asserting over a condition that never arose.
    survivors = [p.name for p in _real_logs(tmp_path)]
    assert not any(n.startswith("old-") for n in survivors), (
        f"harness did not build the shape -- old's log was never pruned, so "
        f"nothing here tests the dangling case. Survivors: {survivors}"
    )

    stale_alias = tmp_path / "old.log"
    assert not (stale_alias.is_symlink() and not stale_alias.exists()), (
        "old.log points at a log that was pruned. A pointer that outlives its "
        "target reads to the next reader as a MISSING run rather than a pruned "
        "one, which is the more alarming of the two and the wrong one."
    )


def test_run_ids_are_distinct_across_rapid_runs(tmp_path):
    """A run id that collides silently recreates the overwrite it fixes."""
    for i in range(3):
        _run(tmp_path, "rapid", "echo r%d" % i)
    names = {p.name for p in _real_logs(tmp_path)}
    assert len(names) == 3, (
        f"three back-to-back runs produced {len(names)} distinct log name(s): "
        f"{sorted(names)} -- the run id is not unique enough to be one"
    )
