"""tests/_run_context.py's prune() crashed pytest_unconfigure on a TOCTOU race.

CAPTURED 2026-09-01 in /home/mboyle/bd-persist/precut_1410_diag.log, with every
concurrent verify lane on this host sharing the same /tmp/bd-runctx/: a
concurrent pytest process removed a run directory between prune()'s directory
listing and the stat() that ranks it, so `_newest_touch`'s
`run_dir.stat().st_mtime` raised FileNotFoundError. That exception escaped
`pytest_unconfigure` (tests/conftest.py), so the whole pytest PROCESS exited
NONZERO after every one of its own tests had already passed -- observed making
tests/test_v3_66_1184_mutation_specs_are_tracked.py FAIL inside a subprocess
assertion on the child's exit code, while the exact same test passes cleanly
in isolation (`1 passed in 88.98s`, serial). `bd-gc` independently confirms
individual run subdirectories (not the shared parent) are its own removal
candidates too (toolchain/bin/bd-gc: `NEVER = ("/tmp/bd-jobs", "/tmp/bd-runctx")`
protects only the parent), so this race has more than one real trigger on a
busy host.

A run directory that vanishes mid-scan is, by definition, not a pruning
candidate any more -- something else already reclaimed it. `prune()` touches a
candidate's on-disk identity at four points: the generator's `p.is_dir()`
filter, `_newest_touch`'s `stat()` (the exact crash site), the `sorted()` call
that invokes it as a key function, and the removal loop's own
`iterdir/unlink/rmdir`. Every test below drives the actual race with a REAL
removal (or a real permission failure) at the exact moment the code under test
touches the path -- no sleep, no mocked exception object -- and checks the
pruner survives a genuine disappearance while still letting an unrelated
error (e.g. PermissionError) surface rather than being silently absorbed.
"""
import os
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _run_context as rc                                    # noqa: E402


BD_GATE_SCOPE = "module"


def _seed_run(base, name, mtime):
    run = base / name
    run.mkdir()
    chain = run / "gw0.chain"
    chain.write_text("x\n")
    os.utime(chain, (mtime, mtime))
    os.utime(run, (mtime, mtime))
    return run


# ── vanish point 1: is_dir() in the generator filter, already tolerant ──────

def test_pathlib_is_dir_already_tolerates_a_directory_gone_before_the_check(tmp_path):
    """Documents why this vanish point needs no new guard in this module: a
    path removed before `.is_dir()` runs is reported False (ENOENT/ENOTDIR are
    in pathlib's own `_IGNORED_ERRNOS`), not raised. If a future Python
    stopped doing this, the crash below would reappear one call earlier and
    every other test in this file would need a matching second guard."""
    ghost = tmp_path / "ghost"
    ghost.mkdir()
    ghost.rmdir()
    assert not ghost.exists(), "fixture bug: ghost must be genuinely absent"
    assert ghost.is_dir() is False


# ── vanish point 2/3: _newest_touch's stat(), and the sort that calls it ────

def test_newest_touch_tolerates_the_directory_vanishing_at_its_own_stat(tmp_path):
    """Direct unit case, no race needed: the function must not raise when
    handed a path that is already gone, which is exactly what a concurrent
    remover leaves behind."""
    run = tmp_path / "run_gone"
    run.mkdir()
    (run / "gw0.chain").write_text("x\n")
    shutil.rmtree(run)
    assert not run.exists(), "fixture bug: run must be genuinely gone"
    result = rc._newest_touch(run)
    assert result < 0, (
        "a vanished run must rank as the OLDEST possible, not raise and not "
        "look newer than a real run: got %r" % (result,))


def test_newest_touch_propagates_a_permission_error_not_a_disappearance(tmp_path, monkeypatch):
    """NEGATIVE CONTROL. A PermissionError from the same stat() call is not a
    disappearance and must still surface -- a fix that widens the guard to
    catch OSError generally (or Exception) would swallow this too."""
    run = tmp_path / "run_denied"
    run.mkdir()
    real_stat = pathlib.Path.stat

    def denying_stat(self, *a, **kw):
        if self == run:
            raise PermissionError(13, "Permission denied", str(self))
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "stat", denying_stat)

    with pytest.raises(PermissionError):
        rc._newest_touch(run)


def test_prune_survives_a_run_dir_vanishing_at_the_stat_that_ranks_it(tmp_path, monkeypatch):
    """RED on the unfixed base, reproducing the captured crash exactly:
    FileNotFoundError from `_newest_touch`'s `run_dir.stat()`, raised while
    `prune()`'s `sorted(..., key=_newest_touch, ...)` is ranking candidates.

    The removal happens for REAL, and only at the moment `_newest_touch` is
    about to stat the vanishing directory -- i.e. strictly after `p.is_dir()`
    has already accepted it during list materialization (proving this
    exercises the SORT's stat, not the already-tolerant is_dir() check), and
    through the module's real `_newest_touch` so a fix inside that function is
    what actually gets exercised, not a shim's own behaviour.
    """
    monkeypatch.setattr(rc, "sink_dir", lambda: tmp_path)
    kept = [_seed_run(tmp_path, "run%d" % i, 2000 + i) for i in range(3)]
    vanish = _seed_run(tmp_path, "run_vanishes", 1000)

    # Precondition: the fixture really created 4 run directories, all real
    # right now, sitting where prune() will look.
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [p.name for p in kept] + [vanish.name])
    for p in kept + [vanish]:
        assert p.is_dir()

    real_newest_touch = rc._newest_touch
    calls = {"vanish_seen_present": None, "vanish_stat_calls": 0}

    def racing_newest_touch(run_dir):
        if run_dir == vanish:
            calls["vanish_stat_calls"] += 1
            # Precondition this whole test rests on: still here right up
            # until we pull it out from under the pruner, mid-scan.
            calls["vanish_seen_present"] = run_dir.is_dir()
            shutil.rmtree(vanish)
            assert not vanish.exists(), "fixture bug: removal did not happen"
        return real_newest_touch(run_dir)

    monkeypatch.setattr(rc, "_newest_touch", racing_newest_touch)

    removed = rc.prune(keep=2)

    assert calls["vanish_seen_present"] is True, (
        "the race never armed: the vanishing directory must still exist when "
        "_newest_touch is called on it, or this is not testing the sort's "
        "stat at all")
    assert calls["vanish_stat_calls"] == 1, calls

    # 4 candidates, keep=2 -> 2 are stale. One stale candidate (`vanish`) is
    # already gone by the time the removal loop reaches it -- prune() must not
    # count that as something IT removed.
    left = sorted(p.name for p in tmp_path.iterdir())
    assert removed == 1, (
        "prune() should report exactly 1 directory it actually removed (the "
        "vanished one was already gone before the removal loop, not removed "
        "BY prune): got %d, left=%s" % (removed, left))
    assert left == ["run1", "run2"], (
        "prune kept the wrong runs after tolerating the race: %s" % left)


# ── vanish point 4: the removal loop's own iterdir/unlink/rmdir ─────────────

def test_prune_tolerates_a_stale_dir_vanishing_during_its_own_removal(tmp_path, monkeypatch):
    """A directory selected as stale can be reclaimed by someone else (a
    concurrent prune(), a concurrent bd-gc sweep) between being chosen and
    being removed. Simulated at the `rmdir()` call specifically -- the one
    operation in this module used ONLY by the removal loop -- so this cannot
    be confused with `_newest_touch`'s own, separate, iterdir/stat calls
    during ranking."""
    monkeypatch.setattr(rc, "sink_dir", lambda: tmp_path)
    keep = _seed_run(tmp_path, "run_keep", 2000)
    stale = _seed_run(tmp_path, "run_stale", 1000)

    real_rmdir = pathlib.Path.rmdir

    def racing_rmdir(self):
        if self == stale:
            # A concurrent remover finishes first: the directory (already
            # emptied by OUR own unlink loop above) is gone before our rmdir
            # call lands.
            assert not any(self.iterdir()), (
                "fixture invariant broken: our own removal loop should have "
                "already unlinked every file in %s" % self)
            shutil.rmtree(self, ignore_errors=True)
        return real_rmdir(self)

    monkeypatch.setattr(pathlib.Path, "rmdir", racing_rmdir)

    removed = rc.prune(keep=1)

    assert not stale.exists()
    assert removed == 0, (
        "the vanished directory must not be counted as removed BY prune: "
        "got %d" % removed)
    assert [p.name for p in tmp_path.iterdir()] == ["run_keep"]


def test_prune_does_not_swallow_a_permission_error_during_removal(tmp_path, monkeypatch):
    """NEGATIVE CONTROL. A PermissionError while removing is not a
    disappearance and must still surface. RED on the unfixed base: the
    current removal loop's `except OSError: pass` swallows this silently and
    returns a clean-looking count, which is indistinguishable from correctly
    skipping a directory that is still legitimately present."""
    monkeypatch.setattr(rc, "sink_dir", lambda: tmp_path)
    keep = _seed_run(tmp_path, "run_keep", 2000)
    denied = _seed_run(tmp_path, "run_denied", 1000)

    real_rmdir = pathlib.Path.rmdir

    def denying_rmdir(self):
        if self == denied:
            raise PermissionError(13, "Permission denied", str(self))
        return real_rmdir(self)

    monkeypatch.setattr(pathlib.Path, "rmdir", denying_rmdir)

    with pytest.raises(PermissionError):
        rc.prune(keep=1)


# ── the recorder's existing retention contract still holds ──────────────────

def test_prune_still_keeps_the_newest_when_nothing_vanishes(tmp_path, monkeypatch):
    """Regression guard alongside the race tests above: with no injected
    failure, ranking and retention behave exactly as before this fix."""
    monkeypatch.setattr(rc, "sink_dir", lambda: tmp_path)
    for i in range(4):
        _seed_run(tmp_path, "run%d" % i, 3000 + i)
    assert rc.prune(keep=2) == 2
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["run2", "run3"], left
