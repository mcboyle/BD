"""Ownership proof must BIND the destructive act, not precede it.

MEASURED at 3d5f1bb8 on test5. v3.66.1149-1152 moved the identity check closer
and closer to the deletion and never joined them, so every cut left the same
shape one seam further along:

    1. prove the pathname currently holds our object
    2. ...the proof ends...
    3. act on the PATHNAME

Between 2 and 3 the name can be given to something else. v3.66.1152 covers a
swap before or during the FIRST removal attempt; it does not cover a swap after
a SUCCESSFUL relaxation and before the retry, and `_relax_owned_dir` returns --
closing its descriptor -- precisely there.

  BLOCKER 1  bd-cut: `_same_object(d, ident)` then `shutil.rmtree(d)`, and
             `_relax_owned_dir()` then a second `shutil.rmtree(d)`. Both are
             prove-then-act on a name.

  BLOCKER 2  tests/_tmproot: `install()` keeps the root PATHNAME and no
             identity at all, so `_force_rmtree` removes whatever directory
             happens to occupy it -- deleting an object it never created,
             leaking the one it did, and leaving pytest green.

  BLOCKER 3  bd-footguns: `_discard()` returns a bool and all three callers
             throw it away (`_run_tool` and `_run_insync` in `finally`,
             `selftest` never folds it into `ok`), so a sandbox that could not
             be removed still yields exit 0 -- which bd-cut's step 0 reads as
             authorization.

  BLOCKER 4  bd-footguns: one PASS plus one timeout returns 0. `_run_tool`
             turns exception/timeout into rc=None, `_check_one` turns that into
             "skip", and `cmd_check` only refuses when NOTHING decided. A
             successful unrelated detector cannot authorize skipping a blocking
             one: UNKNOWN is a third state and it fails.

WHY A PATHNAME RE-CHECK IS NOT THE FIX. Another lstat/realpath/hash before the
call only shortens the window; the act still resolves the name afresh. The
deletion has to be issued THROUGH the descriptor whose identity was proven --
`os.scandir(fd)`, `os.unlink(name, dir_fd=fd)`, `os.rmdir(name, dir_fd=fd)`,
`os.open(name, dir_fd=fd)` -- so no path is resolved for any child at all.

THE ONE IRREDUCIBLE STEP, stated rather than hidden: removing the top directory
itself is `rmdir` of a NAME in its parent, and Linux has no funlinkat. It is
issued parent-descriptor-relative, guarded by a dir_fd lstat identity check
immediately before, and PROVEN afterwards by `os.fstat(held_fd).st_nlink == 0`
-- which says the object unlinked was the one we were holding. A swap in that
last window can therefore be detected and reported, and an rmdir can only ever
remove an EMPTY directory. Refusal is preferred to an unsafe attempt at every
earlier point.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

import pytest

BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
BIN = REPO / "toolchain" / "bin"
BDCUT = BIN / "bd-cut"
FOOTGUNS = BIN / "bd-footguns"

_IS_ROOT = (os.geteuid() == 0)


@pytest.fixture(autouse=True)
def _reclaim_footgun_sandboxes():
    """Several tests here deliberately make `_discard` fail or swap a sandbox
    away, so bd-footguns correctly refuses to remove it and the residue is
    THIS FILE'S to collect. No assertion in this file reads bdfg_* residue, so
    sweeping it cannot mask a production leak."""
    t = pathlib.Path(tempfile.gettempdir())

    def snap():
        return set(t.glob("bdfg_*"))

    before = snap()
    yield
    for leftover in snap() - before:
        _force_rm(leftover)


def _load(path, name):
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def _bdcut():
    return _load(BDCUT, "bd_cut_uut_1153")


def _fg():
    return _load(FOOTGUNS, "bd_footguns_uut_1153")


def _tmproot():
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import _tmproot
        return _tmproot
    finally:
        sys.path.pop(0)


def _force_rm(p):
    """Teardown only. Never used to establish a production assertion."""
    p = str(p)
    if not os.path.lexists(p):
        return
    if os.path.islink(p) or os.path.isfile(p):
        os.unlink(p)
        return
    for root, dirs, _files in os.walk(p):
        for dn in dirs:
            try:
                os.chmod(os.path.join(root, dn), 0o700)
            except OSError:
                pass
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    shutil.rmtree(p, ignore_errors=True)


def _purge(m):
    for fd in list(getattr(m, "_OPEN_FDS", [])):
        try:
            os.close(fd)
        except OSError:
            pass
        m._OPEN_FDS.remove(fd)
    for d in list(getattr(m, "_TEMPDIRS", [])):
        _force_rm(d)
        if d in m._TEMPDIRS:
            m._TEMPDIRS.remove(d)


def _owned(m, marker="original"):
    d = m._owned_tempdir("bdcut_probe_")
    (pathlib.Path(d) / marker).write_text(marker)
    return d


def _stand_up_imposter(d, marker="imposter"):
    """Rename the real object away and put a different directory at its name."""
    stashed = d + ".stashed"
    os.rename(d, stashed)
    os.mkdir(d)
    (pathlib.Path(d) / marker).write_text(marker)
    return stashed


# =========================================================================
# BLOCKER 1 | bd-cut -- both destructive seams
# =========================================================================

def test_seam_1_a_swap_before_the_first_removal_spares_the_imposter(tmp_path):
    """SEAM 1: the identity check passes, then the name is given to another
    directory before the first destructive action."""
    m = _bdcut()
    d = _owned(m)
    nonlocal_stash = []
    real_walk = m._rmtree_fd

    # THE SEAM, AT ITS NEW LOCATION. The identity check is now `os.open` +
    # `os.fstat`, and the destructive walk runs against that descriptor -- so
    # the window to attack is between the proof and the walk. Inject the
    # rename+recreate exactly there. (The previous spelling patched
    # shutil.rmtree, which this path no longer calls at all.)
    def swap_then_walk(fd, dev=None):
        # (fd, dev) since v3.66.1154: the walk carries the parent's
        # st_dev so every entry can be bound to the inode readdir
        # reported. A one-arg spy raises TypeError before its own body
        # runs, so the fixture silently builds nothing.
        if not nonlocal_stash:
            nonlocal_stash.append(_stand_up_imposter(d))
        return real_walk(fd, dev)

    m._rmtree_fd = swap_then_walk
    try:
        got = m._discard_tempdir(d)
    finally:
        m._rmtree_fd = real_walk
    stashed = nonlocal_stash[0] if nonlocal_stash else None

    obs = {
        "returned": got,
        "imposter_dir": os.path.isdir(d),
        "imposter_marker": (pathlib.Path(d) / "imposter").exists() if os.path.isdir(d) else False,
        "original_exists": bool(stashed) and os.path.isdir(stashed),
        "still_registered": d in m._TEMPDIRS,
    }
    try:
        assert obs["imposter_dir"] and obs["imposter_marker"], (
            f"a replacement directory was deleted or damaged: {obs}")
        assert obs["returned"] is False, f"cleanup reported success: {obs}"
        assert obs["still_registered"], (
            f"the unaccounted owned directory was unregistered: {obs}")
        why = str(m._LAST_DISCARD_ERROR.get(d, ""))
        assert why, "no reason was recorded for the refusal"
    finally:
        if d in m._TEMPDIRS:
            m._TEMPDIRS.remove(d)
        _force_rm(d)
        if stashed:
            _force_rm(stashed)


@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_seam_2_a_swap_AFTER_successful_relaxation_spares_the_imposter(tmp_path):
    """SEAM 2 -- THE ONE v3.66.1152 DOES NOT COVER.

    The first removal raises, relaxation verifies and fchmods the CORRECT
    inode and RETURNS (closing its descriptor), and only then is the name given
    to another directory. Reproduced at 3d5f1bb8 as:

        returned True, imposter_deleted True, original_still_exists True,
        still_registered False, rmtree_calls 2

    which is every forbidden outcome at once: the created object survived
    under its new name, a directory bd-cut never made was deleted, cleanup
    reported success, and the path was unregistered so nothing could recover it.
    """
    m = _bdcut()
    d = _owned(m)
    (pathlib.Path(d) / "blocker").write_text("x")
    os.chmod(d, 0o500)                      # sealed: the first walk will raise
    stash = []
    calls = {"n": 0}
    real_walk = m._rmtree_fd

    # SEAM 2: the first walk raises PermissionError, the mode is relaxed
    # THROUGH the descriptor, and the retry begins. Inject the rename+recreate
    # at exactly that instant -- the point where v3.66.1152's
    # `_relax_owned_dir` returned, closed its descriptor, and handed the
    # pathname back to a second shutil.rmtree.
    def swap_at_retry(fd, dev=None):
        # (fd, dev) since v3.66.1154 -- see seam 1.
        calls["n"] += 1
        if calls["n"] == 2 and not stash:
            stash.append(_stand_up_imposter(d))
        return real_walk(fd, dev)

    m._rmtree_fd = swap_at_retry
    try:
        got = m._discard_tempdir(d)
    finally:
        m._rmtree_fd = real_walk

    stashed = stash[0] if stash else None
    obs = {
        "returned": got,
        "imposter_dir": os.path.isdir(d),
        "imposter_marker": (pathlib.Path(d) / "imposter").exists() if os.path.isdir(d) else False,
        "original_exists": bool(stashed) and os.path.isdir(stashed),
        "still_registered": d in m._TEMPDIRS,
        "walk_calls": calls["n"],
    }
    try:
        assert stashed, "the fixture never reached the relaxation seam"
        assert obs["imposter_dir"] and obs["imposter_marker"], (
            f"a replacement directory was deleted after relaxation: {obs}")
        assert obs["returned"] is False, f"cleanup reported success: {obs}"
        assert obs["still_registered"], (
            f"the unaccounted owned directory was unregistered: {obs}")
    finally:
        if d in m._TEMPDIRS:
            m._TEMPDIRS.remove(d)
        _force_rm(d)
        if stashed:
            _force_rm(stashed)


def test_the_child_binding_is_more_than_shutil_already_did(tmp_path):
    """THIS TEST PROVES IT DISCRIMINATES, because its predecessor did not.

    v3.66.1153 shipped `test_no_child_is_removed_by_a_resolved_pathname`, which
    wrapped os.unlink/os.rmdir and required every destructive call to carry
    `dir_fd` and no absolute path. That reads like the mechanism and asserts
    nothing: CPython's `shutil.rmtree` uses `_rmtree_safe_fd` wherever
    `os.supports_dir_fd` allows, so a plain rmtree SATISFIES it -- while doing
    exactly the thing the cut exists to stop. A test a naive implementation
    passes is not evidence about the careful one.

    So the assertion is now an OUTCOME, and the control is run in the same
    test: the same swap is played against `shutil.rmtree`, which must destroy
    the foreign object. If the control ever stops destroying it, the scenario
    has decayed and the real assertion below is worth nothing -- which is the
    failure mode that produced the test this replaces.
    """
    m = _bdcut()

    def _stage():
        """A tree with one child, plus a foreign directory holding payload."""
        top = tempfile.mkdtemp(prefix="bdcut_disc_", dir=str(tmp_path))
        os.mkdir(os.path.join(top, "c"))
        (pathlib.Path(top) / "c" / "ours").write_text("x")
        alien = tempfile.mkdtemp(prefix="ALIEN_", dir=str(tmp_path))
        (pathlib.Path(alien) / "victim").write_text("PRECIOUS")
        return top, alien

    class _Entries:
        """os.scandir's result is an ITERATOR AND a context manager -- shutil's
        `_rmtree_safe_fd` opens it with `with`, so a bare iterator raises
        TypeError and the control would 'pass' by crashing."""

        def __init__(self, items):
            self._it = iter(items)

        def __iter__(self):
            return self._it

        def __next__(self):
            return next(self._it)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

    def _swapper(top, alien, fired):
        real_scandir = os.scandir

        def scandir(fd):
            ents = list(real_scandir(fd))
            if not fired and any(e.name == "c" for e in ents):
                fired.append(1)
                os.rename(os.path.join(top, "c"), os.path.join(top, "c.gone"))
                os.rename(alien, os.path.join(top, "c"))
            return _Entries(ents)
        return scandir, real_scandir

    # --- the CONTROL: a pathname remover destroys the foreign object ---------
    ctop, calien = _stage()
    cfired = []
    spy, real_scandir = _swapper(ctop, calien, cfired)
    os.scandir = spy
    try:
        shutil.rmtree(ctop, ignore_errors=True)
    finally:
        os.scandir = real_scandir
    assert cfired, "the control's swap never fired -- the scenario is broken"
    assert not os.path.exists(os.path.join(ctop, "c", "victim")) \
        and not os.path.exists(os.path.join(calien, "victim")), (
        "the CONTROL did not destroy the foreign payload, so this scenario no "
        "longer distinguishes a bound remover from an unbound one and the "
        "assertion below proves nothing")

    # --- the SUBJECT: the production remover must refuse --------------------
    top, alien = _stage()
    st = os.lstat(top)
    m._TEMPDIRS.append(top)
    m._TEMPDIR_IDENT[top] = (st.st_dev, st.st_ino)
    if hasattr(m, "_TEMPDIR_FD"):
        m._TEMPDIR_FD[top] = os.open(
            top, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fired = []
    spy, real_scandir = _swapper(top, alien, fired)
    os.scandir = spy
    try:
        got = m._discard_tempdir(top)
    finally:
        os.scandir = real_scandir
    # OBSERVE BEFORE TEARING DOWN. The first draft removed the tree in a
    # `finally` and then asserted the victim still existed, so it failed
    # against a CORRECT implementation -- a fixture that destroys its own
    # evidence is the same defect as one that never builds it.
    survived = os.path.exists(os.path.join(top, "c", "victim"))
    try:
        assert fired, "the subject's swap never fired -- nothing was tested"
        assert survived, (
            "the production remover destroyed a foreign directory's contents "
            "because it opened the child by name")
        assert got is False
    finally:
        if top in m._TEMPDIRS:
            m._TEMPDIRS.remove(top)
        m._TEMPDIR_IDENT.pop(top, None)
        if hasattr(m, "_TEMPDIR_FD"):
            _fd = m._TEMPDIR_FD.pop(top, None)
            if _fd is not None:
                os.close(_fd)
        _force_rm(top)


def test_a_clean_owned_directory_is_still_removed(tmp_path):
    """OVER-SENSITIVITY. A cleanup that refused everything would satisfy every
    assertion above and leak on every run."""
    m = _bdcut()
    d = _owned(m)
    os.makedirs(os.path.join(d, "sub", "deep"))
    (pathlib.Path(d) / "sub" / "deep" / "f").write_text("x")
    os.chmod(os.path.join(d, "sub", "deep", "f"), 0o444)
    os.chmod(os.path.join(d, "sub"), 0o500)          # a sealed NESTED directory
    os.chmod(d, 0o500)                               # and a sealed top
    got = m._discard_tempdir(d)
    try:
        assert got is True, (
            f"a sealed but genuinely-owned tree was not reclaimed: "
            f"{m._LAST_DISCARD_ERROR.get(d)}")
        assert not os.path.lexists(d)
        assert d not in m._TEMPDIRS
    finally:
        if d in m._TEMPDIRS:
            m._TEMPDIRS.remove(d)
        _force_rm(d)


@pytest.mark.parametrize("case", ["missing_identity", "already_absent",
                                  "unremoved_under_a_dangling_name"])
def test_the_preserved_v1152_semantics_survive(tmp_path, case):
    """These were earned in v3.66.1152 and must not regress while seam-binding
    is added. Kept in ONE parametrised test so a future edit cannot quietly
    drop one of the three."""
    m = _bdcut()
    if case == "missing_identity":
        d = tempfile.mkdtemp(prefix="bdcut_noident_")
        (pathlib.Path(d) / "theirs").write_text("x")
        m._TEMPDIRS.append(d)
        assert d not in m._TEMPDIR_IDENT
        try:
            assert m._discard_tempdir(d) is False
            assert os.path.isdir(d)
            why = str(m._LAST_DISCARD_ERROR.get(d, ""))
            assert "identity" in why and "renamed" not in why, why
        finally:
            if d in m._TEMPDIRS:
                m._TEMPDIRS.remove(d)
            _force_rm(d)
    elif case == "already_absent":
        d = _owned(m)
        _force_rm(d)
        assert m._discard_tempdir(d) is True
        assert d not in m._TEMPDIRS
    else:
        # THIS ARM WAS A DUPLICATE OF `missing_identity` UNTIL v3.66.1154, and
        # it took two shims to become one. It patched `m.shutil.rmtree`, which
        # the remover stopped calling at v3.66.1153, so the first discard
        # SUCCEEDED and popped the identity; the branch then built the symlink
        # itself and called the helper a second time, on a path that no longer
        # had a recorded identity -- so what it measured was the no-identity
        # refusal, under a different name, one arm along.
        #
        # The defect v3.66.1152 actually closed was `os.path.exists` FOLLOWING
        # a dangling symlink, so a removal that did not happen read as absent
        # and was laundered into success. That is stated here directly: the
        # tree is intact under another name, a dangling link stands where it
        # used to be, and `exists()` says False about both.
        d = _owned(m)
        moved = d + ".still-here"
        os.rename(d, moved)
        os.symlink(str(tmp_path / "no-such-target"), d)
        try:
            assert not os.path.exists(d), "fixture: exists() must read absent"
            assert os.path.lexists(d), "fixture: a NAME must still be here"
            assert os.path.isdir(moved), "fixture: the tree must be intact"
            got = m._discard_tempdir(d)
            assert got is False, (
                "a directory that was never removed -- it is intact under "
                "another name -- was reported as cleaned up, because the "
                "question asked was about a pathname that a dangling symlink "
                "answers False to")
            assert d in m._TEMPDIRS, (
                "and it was unregistered, so nothing will ever look for it "
                "again")
        finally:
            if os.path.lexists(d):
                os.unlink(d)
            _force_rm(moved)
            if d in m._TEMPDIRS:
                m._TEMPDIRS.remove(d)
            m._TEMPDIR_IDENT.pop(d, None)


@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_main_turns_a_seam_2_swap_into_EXIT_CLEANUP_FAILED(tmp_path, monkeypatch, capsys):
    """END TO END. A successful cut whose snapshot directory became
    unaccounted-for must exit 4 and name the failure; a step-0 refusal must
    still exit 3 while ALSO reporting the residue."""
    m = _bdcut()
    z = tmp_path / "r.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("pkg/a.py", "x = 1\n")
        zf.writestr("run_tests.py", "print(1)\n")
    w = tmp_path / "work"
    (w / "bulk_downloader").mkdir(parents=True)
    (w / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')
    (w / "anything.py").write_text("x = 1\n")
    stash = []

    def swap_the_archive(*a, **k):
        for d in list(m._TEMPDIRS):
            if os.path.basename(d).startswith("bdcut_archive_"):
                stash.append(_stand_up_imposter(d))
                return

    monkeypatch.setattr(m, "step0_gate", lambda s, **k: [])
    monkeypatch.setattr(m, "band", swap_the_archive)
    monkeypatch.setattr(m, "verify", lambda *a, **k: None)
    monkeypatch.setattr(m, "max_summary", lambda *a, **k: None)
    try:
        rc = m.main(["--work", str(w), "--out", str(tmp_path / "o"),
                     "--resume-zip", str(z)])
        err = capsys.readouterr().err
        obs = {"imposter_alive": all(os.path.isdir(s.replace(".stashed", "")) for s in stash),
               "original_alive": all(os.path.isdir(s) for s in stash)}
    finally:
        monkeypatch.undo()
        for s in stash:
            _force_rm(s)
            _force_rm(s.replace(".stashed", ""))
        _purge(m)

    assert stash, "the fixture never swapped an archive directory"
    assert obs["imposter_alive"], "production deleted the replacement"
    assert obs["original_alive"], "production deleted the renamed original"
    assert rc == m.EXIT_CLEANUP_FAILED, f"expected exit 4, got {rc}: {err[-400:]}"
    assert "NOT REMOVED" in err.upper(), err[-400:]


# =========================================================================
# BLOCKER 2 | _tmproot -- the root is not identified at creation
# =========================================================================

def _install_root(t):
    """Drive install() without destroying the LIVE session's root state.

    EVERY piece of module state install()/finish() touch must be saved and put
    back -- including _ROOT_IDENT, which v3.66.1153 adds. Missing it pairs the
    session's real root with a test's identity, so the real reclamation refuses
    at session end and turns a green run red. Measured: 35 passed, exit 1.
    """
    saved = (t._ROOT, tempfile.tempdir,
             getattr(t, "_LAST_FAILURE", None),
             getattr(t, "_ROOT_IDENT", None))
    t._ROOT = None
    if hasattr(t, "_ROOT_IDENT"):
        t._ROOT_IDENT = None
    # DRIVE THE MECHANISM IN BOTH MODES. install() returns None under
    # KEEP_TEST_TMPDIRS=1 by design, and that flag is exactly what leak
    # verification sets -- so a test that merely skipped there would be absent
    # from the one run that measures residue. Pop it for the call instead.
    _keep = os.environ.pop("KEEP_TEST_TMPDIRS", None)
    try:
        root = t.install()
    finally:
        if _keep is not None:
            os.environ["KEEP_TEST_TMPDIRS"] = _keep
    return root, saved


def _restore(t, saved):
    t._ROOT, tempfile.tempdir, lf, ident = saved
    if hasattr(t, "_LAST_FAILURE"):
        t._LAST_FAILURE = lf
    if hasattr(t, "_ROOT_IDENT"):
        t._ROOT_IDENT = ident


class _Session:
    def __init__(self, exitstatus=0):
        self.exitstatus = exitstatus


def test_tmproot_records_a_creation_identity(tmp_path):
    """install() kept the root PATHNAME and nothing else, so reclamation could
    only ever be about whatever occupied that name later."""
    t = _tmproot()
    root, saved = _install_root(t)
    try:
        assert root, "install() did not create a root"
        ident = getattr(t, "_ROOT_IDENT", None)
        assert ident is not None, (
            "install() records no creation identity, so _force_rmtree cannot "
            "tell the root it made from a replacement")
        st = os.lstat(root)
        assert ident == (st.st_dev, st.st_ino)
    finally:
        _force_rm(root)
        _restore(t, saved)


@pytest.mark.parametrize("kind", ["directory", "symlink", "dangling"])
def test_tmproot_refuses_a_replacement_at_the_root_pathname(tmp_path, kind):
    """MEASURED at 3d5f1bb8 for the directory case:

        TMPROOT_SESSION_IMPOSTER { session_exitstatus: 0, imposter_deleted:
        True, original_still_exists: True, failed_root: None }

    -- deletes an object it never created, leaks the one it did, stays green.
    """
    t = _tmproot()
    root, saved = _install_root(t)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside-marker").write_text("outside")
    os.chmod(outside, 0o755)
    stashed = root + ".stashed"
    try:
        (pathlib.Path(root) / "original").write_text("original")
        os.rename(root, stashed)
        if kind == "directory":
            os.mkdir(root)
            (pathlib.Path(root) / "imposter").write_text("imposter")
        elif kind == "symlink":
            os.symlink(str(outside), root)
        else:
            os.symlink(str(tmp_path / "no-such-target"), root)

        before_mode = stat.S_IMODE(os.stat(outside).st_mode)
        sess = _Session(0)
        t.finish_session(sess, 0)
        obs = {
            "session_exitstatus": sess.exitstatus,
            "name_still_there": os.path.lexists(root),
            "original_alive": os.path.isdir(stashed),
            "failed_root": t.failed_root(),
            "outside_mode": stat.S_IMODE(os.stat(outside).st_mode),
            "outside_marker": (outside / "outside-marker").exists(),
        }
        if kind == "directory":
            obs["imposter_marker"] = (pathlib.Path(root) / "imposter").exists()
    finally:
        _force_rm(root)
        _force_rm(stashed)
        _restore(t, saved)

    assert obs["original_alive"], "the root install() created was destroyed"
    assert obs["name_still_there"], (
        f"a replacement at the root pathname was removed: {obs}")
    if kind == "directory":
        assert obs["imposter_marker"], f"the replacement's contents were deleted: {obs}"
    assert obs["outside_mode"] == before_mode, (
        f"an object outside the root was chmodded: {obs}")
    assert obs["outside_marker"], "an object outside the root was emptied"
    assert obs["failed_root"], f"the failed reclamation was not recorded: {obs}"
    assert obs["session_exitstatus"] != 0, (
        f"an unreclaimed root left the session green: {obs}")


def test_tmproot_a_genuinely_vanished_root_is_success(tmp_path):
    """OVER-SENSITIVITY: nothing to remove is not a failure."""
    t = _tmproot()
    root, saved = _install_root(t)
    try:
        _force_rm(root)
        sess = _Session(0)
        t.finish_session(sess, 0)
        assert t.failed_root() is None, t.failed_root()
        assert sess.exitstatus == 0
    finally:
        _force_rm(root)
        _restore(t, saved)


def test_tmproot_a_clean_root_is_reclaimed_and_stays_green(tmp_path):
    """OVER-SENSITIVITY: the ordinary case must still work."""
    t = _tmproot()
    root, saved = _install_root(t)
    try:
        (pathlib.Path(root) / "sub").mkdir()
        (pathlib.Path(root) / "sub" / "f").write_text("x")
        os.chmod(pathlib.Path(root) / "sub", 0o500)
        sess = _Session(0)
        t.finish_session(sess, 0)
        assert not os.path.lexists(root), "the root was not reclaimed"
        assert t.failed_root() is None
        assert sess.exitstatus == 0
    finally:
        _force_rm(root)
        _restore(t, saved)


def test_tmproot_a_failing_run_retains_artifacts_without_claiming_failure(tmp_path):
    """The deliberate retention policy must stay distinguishable from a failed
    reclamation, or every red run gains a false cleanup complaint."""
    t = _tmproot()
    root, saved = _install_root(t)
    try:
        sess = _Session(1)
        t.finish_session(sess, 1)
        assert os.path.isdir(root), "a failing run's artifacts were deleted"
        assert t.failed_root() is None, "retention was reported as a failure"
        assert sess.exitstatus == 1
    finally:
        _force_rm(root)
        _restore(t, saved)


def test_tmproot_missing_identity_with_a_name_present_is_unknown(tmp_path):
    """If the creation identity is absent but a name exists, that is UNKNOWN --
    it must fail rather than remove whatever is there."""
    t = _tmproot()
    root, saved = _install_root(t)
    try:
        if hasattr(t, "_ROOT_IDENT"):
            t._ROOT_IDENT = None
        (pathlib.Path(root) / "marker").write_text("m")
        sess = _Session(0)
        t.finish_session(sess, 0)
        assert os.path.isdir(root), "removed a root it could not identify"
        assert t.failed_root(), "no failure recorded for an unidentifiable root"
        assert sess.exitstatus != 0
    finally:
        _force_rm(root)
        _restore(t, saved)


def test_tmproot_does_not_blindly_invoke_a_callback_function(tmp_path):
    """`shutil.rmtree(onexc=...)` hands the handler a FUNCTION whose calling
    convention is not always func(path) -- and the v3.66.1151 handler called
    `func(p)` regardless. Worse, the wrapper caught TypeError around the whole
    rmtree call and retried with the legacy `onerror=` kwarg, so a TypeError
    raised INSIDE the callback was misread as an old shutil signature and the
    entire tree was walked a second time.

    Asserted structurally because it is a property of the source: the module
    must not both pass a callback and treat TypeError as an API-version signal.
    """
    import ast as _ast
    t = _tmproot()
    tree = _ast.parse(pathlib.Path(t.__file__).read_text(encoding="utf-8"))

    # AST, NOT TEXT. This module's own docstrings now EXPLAIN the retired
    # callback, so a grep for "onexc=" or "except TypeError" matches the prose
    # that documents their removal -- CLAUDE.md section 0's rule that a comment
    # is inside the denominator of anything reading source text.
    callbacks = [n for n in _ast.walk(tree)
                 if isinstance(n, _ast.Call)
                 for kw in n.keywords or []
                 if kw.arg in ("onexc", "onerror")]
    typeerror_handlers = [
        h for h in _ast.walk(tree) if isinstance(h, _ast.ExceptHandler)
        and h.type is not None
        and "TypeError" in _ast.dump(h.type)]
    assert not (callbacks and typeerror_handlers), (
        "a TypeError raised inside the callback is indistinguishable from an "
        "older shutil.rmtree signature; do not use TypeError as an API probe")
    for n in _ast.walk(tree):
        if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute):
            assert not (n.func.attr == "rmtree"
                        and getattr(n.func.value, "id", "") == "shutil"), (
                "shutil.rmtree acts on a pathname; reclamation must be bound "
                "to the descriptor whose identity was proven")


# =========================================================================
# BLOCKER 3 | bd-footguns -- cleanup failure must not become success
# =========================================================================

_RECORDER = """#!/usr/bin/env python3
import os, sys
sys.exit(int(os.environ.get("FG_RC", "0")))
"""


def _fg_tree(tmp_path, m, entries, run_tests_body=None):
    """A subject tree whose registry is exactly `entries`: every SEED id is
    retired, so the denominator is ours and nothing else runs."""
    tree = tmp_path / "subject"
    (tree / "bulk_downloader").mkdir(parents=True, exist_ok=True)
    (tree / "bulk_downloader" / "__init__.py").write_text("x = 1\n")
    (tree / "tests").mkdir(exist_ok=True)
    (tree / "tests" / "test_probe.py").write_text("def test_x():\n    pass\n")
    if run_tests_body is not None:
        (tree / "run_tests.py").write_text(run_tests_body)
    reg = [{"id": f["id"], "status": "retired"} for f in m.SEED] + entries
    (tree / "FOOTGUNS.json").write_text(json.dumps(
        {"version": 999999, "footguns": reg}))
    return tree


def _tool_entry(fid, argv, block_on=(3,)):
    return {"id": fid, "severity": "blocking", "status": "active",
            "rule": "synthetic", "fix": "synthetic",
            "detector": {"kind": "tool", "cmd": list(argv),
                         "block_on_exit": list(block_on)}}


def _insync_entry(fid, test="tests/test_probe.py"):
    return {"id": fid, "severity": "blocking", "status": "active",
            "rule": "synthetic", "fix": "synthetic",
            "detector": {"kind": "insync", "test": test}}


class _Args:
    def __init__(self, tree):
        self.tree = str(tree)
        self.json = False


def _passing_tool(tmp_path):
    p = tmp_path / "ok-tool"
    p.write_text(_RECORDER)
    p.chmod(0o755)
    return [sys.executable, str(p)]


@pytest.mark.parametrize("site", ["run_tool", "run_insync"])
def test_a_failed_sandbox_cleanup_is_not_a_pass(tmp_path, monkeypatch, capsys, site):
    """MEASURED at 3d5f1bb8:
       BDFOOTGUNS_CLEANUP_FAILURE { discard_returned_false: True,
       sandbox_still_exists: True, command_return: 0 }
    -- a PASS was printed, OK was printed, and the tool returned 0 while its
    own sandbox remained. bd-cut's step 0 reads that 0 as authorization."""
    m = _fg()
    if site == "run_tool":
        entries = [_tool_entry("FG-PROBE", _passing_tool(tmp_path))]
        tree = _fg_tree(tmp_path, m, entries)
    else:
        entries = [_insync_entry("FG-PROBE")]
        tree = _fg_tree(tmp_path, m, entries,
                        run_tests_body="print('Total: 1  Passed: 1  Failed: 0')\n")
    monkeypatch.setattr(m, "_discard", lambda d: False)
    rc = m.cmd_check(_Args(tree))
    out = capsys.readouterr()
    blob = out.out + out.err
    assert rc != 0, (
        f"a failed sandbox cleanup returned {rc}; step 0 would read that as "
        f"authorization:\n{blob[-600:]}")
    assert rc == m.sec.EXIT_CANNOT_EVALUATE, rc
    assert "OK -- no active footgun violated" not in blob, (
        "the authorization line was printed despite a failed cleanup")
    assert "cleanup" in blob.lower(), blob[-600:]


def test_selftest_folds_its_own_cleanup_result_into_the_verdict(tmp_path, monkeypatch, capsys):
    """selftest() calls _discard(d) and never looks at the answer."""
    m = _fg()
    monkeypatch.setattr(m, "_discard", lambda d: False)
    rc = m.selftest()
    blob = capsys.readouterr().out
    assert rc != 0, f"selftest returned {rc} with its own cleanup failing"
    assert "SELFTEST FAIL" in blob, blob[-400:]
    assert "SELFTEST PASS" not in blob


def test_the_sandbox_cleanup_is_bound_to_the_sandbox_it_created(tmp_path, monkeypatch):
    """A rename-and-recreate must not let _discard delete a replacement."""
    m = _fg()
    made = {}
    real_sandbox = m._sandbox

    def spy():
        d, env = real_sandbox()
        made["d"] = d
        (pathlib.Path(d) / "original").write_text("original")
        return d, env

    monkeypatch.setattr(m, "_sandbox", spy)
    entries = [_tool_entry("FG-PROBE", _passing_tool(tmp_path))]
    tree = _fg_tree(tmp_path, m, entries)
    real_run = subprocess.run

    def swapping_run(*a, **k):
        r = real_run(*a, **k)
        d = made.get("d")
        if d and os.path.isdir(d):
            made["stashed"] = _stand_up_imposter(d)
        return r

    monkeypatch.setattr(m.subprocess, "run", swapping_run)
    try:
        m.cmd_check(_Args(tree))
        d, stashed = made.get("d"), made.get("stashed")
        obs = {"imposter_alive": bool(d) and os.path.isdir(d),
               "imposter_marker": bool(d) and (pathlib.Path(d) / "imposter").exists(),
               "original_alive": bool(stashed) and os.path.isdir(stashed)}
    finally:
        monkeypatch.undo()
        for p in (made.get("d"), made.get("stashed")):
            if p:
                _force_rm(p)
    assert made.get("stashed"), "the fixture never swapped the sandbox"
    assert obs["imposter_alive"] and obs["imposter_marker"], (
        f"_discard deleted a replacement sandbox: {obs}")


# =========================================================================
# BLOCKER 4 | bd-footguns -- partial UNKNOWN must not return 0
# =========================================================================

def _unknown_entry(kind, tmp_path):
    """One active BLOCKING detector per UNKNOWN flavour."""
    if kind == "missing_tool":
        return _tool_entry("FG-UNK", [sys.executable, str(tmp_path / "nope")]), None
    if kind == "missing_harness":
        return _insync_entry("FG-UNK", "tests/does_not_exist.py"), None
    if kind in ("timeout", "exception"):
        # A DISTINCTIVE REAL FILE, matched by EXACT ARGV ELEMENT below. The
        # first draft used [python, "-c", "pass"] and matched the substring
        # "pass" in the joined argv -- which also matches the PASSING tool,
        # because pytest names tmp_path after the test
        # ("test_one_pass_cannot_authoriz0"). Both detectors were then
        # intercepted, decided was empty, cmd_check returned EXIT_CANNOT_EVALUATE
        # for the wrong reason, and the test passed on a defective tree.
        u = tmp_path / "unk-tool-sentinel"
        u.write_text(_RECORDER)
        u.chmod(0o755)
        return _tool_entry("FG-UNK", [sys.executable, str(u)]), kind
    if kind == "nonzero_unclassified":
        return _tool_entry("FG-UNK", [sys.executable, "-c", "import sys;sys.exit(7)"]), None
    if kind == "malformed_summary":
        return _insync_entry("FG-UNK"), "malformed"
    if kind == "zero_collection":
        return _insync_entry("FG-UNK"), "zero"
    raise AssertionError(kind)


@pytest.mark.parametrize("kind", [
    "missing_tool", "missing_harness", "timeout", "exception",
    "nonzero_unclassified", "malformed_summary", "zero_collection",
])
def test_one_pass_cannot_authorize_another_detectors_UNKNOWN(tmp_path, monkeypatch, capsys, kind):
    """MEASURED at 3d5f1bb8 for the timeout flavour:

        [PASS] A-PASS     exit 0
        [skip] B-TIMEOUT  tool unavailable
        OK -- no active footgun violated (1 detector(s) ran).
        BDFOOTGUNS_PARTIAL_UNKNOWN { one_passed: True, one_timed_out: True,
        command_return: 0 }

    A successful unrelated detector cannot authorize omitting a blocking one.
    """
    m = _fg()
    unk, mode = _unknown_entry(kind, tmp_path)
    entries = [_tool_entry("FG-PASS", _passing_tool(tmp_path)), unk]
    body = "print('Total: 1  Passed: 1  Failed: 0')\n"
    if mode == "malformed":
        body = "print('no recognisable summary here')\n"
    elif mode == "zero":
        body = "print('Total: 0  Passed: 0  Failed: 0')\n"
    tree = _fg_tree(tmp_path, m, entries, run_tests_body=body)

    sentinel = str(tmp_path / "unk-tool-sentinel")
    hits = {"n": 0}
    if mode in ("timeout", "exception"):
        real_run = subprocess.run

        def flaky(cmd, *a, **k):
            argv = [str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else [cmd])]
            if sentinel in argv:              # EXACT element, never a substring
                hits["n"] += 1
                if mode == "timeout":
                    raise subprocess.TimeoutExpired(cmd, 150)
                raise OSError(12, "Cannot allocate memory")
            return real_run(cmd, *a, **k)

        monkeypatch.setattr(m.subprocess, "run", flaky)

    rc = m.cmd_check(_Args(tree))
    cap = capsys.readouterr()                 # ONCE: a second call returns empty
    blob = cap.out + cap.err
    if mode in ("timeout", "exception"):
        assert hits["n"] == 1, (
            f"{kind}: the injector fired {hits['n']} times; it must intercept "
            "the UNKNOWN detector and nothing else, or an empty decided-set "
            "makes this pass for the wrong reason")
    assert "[PASS" in blob, (
        f"{kind}: the passing detector did not pass, so there is no "
        f"one-PASS-plus-one-UNKNOWN shape to judge:\n{blob[-500:]}")
    assert rc != 0, (
        f"{kind}: one PASS plus one UNKNOWN blocking detector returned {rc}\n"
        f"{blob[-500:]}")
    assert rc == m.sec.EXIT_CANNOT_EVALUATE, f"{kind}: rc={rc}"


def test_all_blocking_detectors_passing_still_returns_zero(tmp_path):
    """OVER-SENSITIVITY. A checker that refused whenever anything was odd would
    satisfy every assertion above and block every cut."""
    m = _fg()
    entries = [_tool_entry("FG-A", _passing_tool(tmp_path)),
               _tool_entry("FG-B", _passing_tool(tmp_path)),
               _insync_entry("FG-C")]
    tree = _fg_tree(tmp_path, m, entries,
                    run_tests_body="print('Total: 1  Passed: 1  Failed: 0')\n")
    assert m.cmd_check(_Args(tree)) == 0


def test_a_declared_violation_still_blocks_with_its_own_code(tmp_path):
    """The violation path must not be swallowed by the new UNKNOWN path."""
    m = _fg()
    bad = tmp_path / "bad-tool"
    bad.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(3)\n")
    bad.chmod(0o755)
    entries = [_tool_entry("FG-A", _passing_tool(tmp_path)),
               _tool_entry("FG-BAD", [sys.executable, str(bad)], block_on=(3,))]
    tree = _fg_tree(tmp_path, m, entries)
    assert m.cmd_check(_Args(tree)) == 3


def test_inactive_and_advisory_entries_do_not_become_failures(tmp_path):
    """Deliberate inactive/advisory semantics must survive: they are not
    UNKNOWN blocking detectors."""
    m = _fg()
    entries = [
        _tool_entry("FG-A", _passing_tool(tmp_path)),
        {"id": "FG-OFF", "severity": "blocking", "status": "retired",
         "rule": "r", "fix": "f", "detector": {"kind": "tool", "cmd": ["nope"]}},
        {"id": "FG-NOTE", "severity": "advisory", "status": "active",
         "rule": "r", "fix": "f", "detector": {"kind": "none"}},
    ]
    tree = _fg_tree(tmp_path, m, entries)
    assert m.cmd_check(_Args(tree)) == 0


@pytest.mark.parametrize("body,label", [
    ("import sys\nprint('Total: 1  Passed: 0  Failed: 0')\nsys.exit(4)\n", "nonzero_exit"),
    ("print('Total: 0  Passed: 0  Failed: 0')\n", "zero_collection"),
    ("print('nothing parseable')\n", "no_summary"),
])
def test_insync_does_not_trust_a_parsed_summary_over_the_child(tmp_path, body, label):
    """`Failed: 0` in the output is not a verdict when the process exited
    nonzero, collected nothing, or printed no summary at all."""
    m = _fg()
    tree = _fg_tree(tmp_path, m, [_insync_entry("FG-SYNC")], run_tests_body=body)
    rc = m.cmd_check(_Args(tree))
    assert rc != 0, f"{label}: became a PASS (rc={rc})"
    assert rc == m.sec.EXIT_CANNOT_EVALUATE, f"{label}: rc={rc}"


# =========================================================================
# SOURCE ACCURACY
# =========================================================================

def test_no_tracked_file_claims_test_799_runs_bd_footguns():
    """The PR body was corrected; the source comment inside bd-footguns still
    said it. Every remaining occurrence is a false claim about coverage."""
    r = subprocess.run(["git", "grep", "-n", "-l", "test_v3_66_799"],
                       cwd=str(REPO), capture_output=True, text=True)
    # A DENIAL IS NOT A CLAIM. The corrections written when this was found are
    # themselves lines pairing "test_v3_66_799" with "bd-footguns", so a naive
    # grep flags the fix as the defect -- CLAUDE.md section 0's rule that
    # explaining a removal by naming the removed thing recreates it.
    DENIES = ("false", "not ", "never", "does not", "corrected", "it does",
              "was not")
    offenders = []
    for path in [p for p in r.stdout.splitlines() if p.strip()]:
        try:
            lines = (REPO / path).read_text(encoding="utf-8",
                                            errors="ignore").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            low = line.lower()
            if "test_v3_66_799" not in line:
                continue
            # look at the sentence, not the line: the claim and the word
            # "footguns" are routinely split by the wrap.
            window = " ".join(lines[max(0, n - 4):n + 3]).lower()
            if "footgun" not in window:
                continue
            if any(d in window for d in DENIES):
                continue
            offenders.append(f"{path}:{n}: {line.strip()}")
    assert not offenders, (
        "these claim test_v3_66_799 covers bd-footguns; its TOOLS list is "
        "exactly tools/bd-triage.py and tools/bd-audit-gate.py:\n"
        + "\n".join(offenders))

    # And the positive half: 799's own subject is unchanged.
    t799 = (REPO / "tests" / "test_v3_66_799_audit_tool_selftests.py").read_text()
    assert "bd-triage.py" in t799 and "bd-audit-gate.py" in t799
    assert "footgun" not in t799.lower(), (
        "799's TOOLS list was widened to make the old comment true; the direct "
        "v1152/v1153 test is the clearer authority")
