"""Cleanup failures were REPORTED and then returned success, in both tools.

MEASURED at dcf34528 on test5. v3.66.1149-1151 spent three cuts making cleanup
say so honestly, and never made saying so cost anything:

  * `bd-cut.main()` returns whatever `_main_inner` returned. Its finally PRINTS
    "TEMPORARY DIRECTORIES NOT REMOVED" and then hands back the inner value, so
    a run that leaked two directories exits 0. Measured: rc=0 with both the
    subject and the archive snapshot reported unremoved.

  * `_tmproot.finish()` writes to stderr and returns False. BOTH session-finish
    hooks -- this repo's conftest and _tmproot's own -- discard that value, and
    pytest computes its exit status from the test outcomes alone, so the run is
    green. A leak that "is never silent" is still not a leak anything ACTS on.

An exit code is the only part of a tool's output that a caller cannot ignore by
accident. CLAUDE.md section 0's rule is that a check which cannot verify must
say so -- and section 10 adds the half that matters here: the last line a tool
prints is exercised by nobody. A report with an exit 0 beside it is a report
that will be read once and then automated over.

TWO MORE ESCAPES IN THE RECLAIMERS:

  * `_force_rmtree` skips symlinks and chmods everything else. A HARD LINK is
    not a symlink and shares the target's inode, so an in-tree hard link to an
    outside file made the retry handler change that outside file's mode.
    Reproduced: 0644 -> 0700 on a file outside the tree, same inode, file
    surviving. Only a DIRECTORY's mode can block the removal of its entries,
    so relaxation belongs to directories and nothing else.

  * `_discard_tempdir` had three ownership holes. A creation-time lstat failure
    left no recorded identity, and the missing entry PERMITTED deletion while
    the comment beside it said it would refuse. Its retry re-stated and
    chmod'd through following calls AFTER the identity check, so a swap to a
    symlink in that window escaped. And the final `os.path.exists` reads a
    DANGLING symlink as absent, so a leak of that shape reported success.

AND FOUR PROOFS THAT CLAIMED MORE THAN THEY MEASURED:

  * the imposter test deleted the renamed original by hand and never drove
    main(), so it proved nothing about what production does with the leak;
  * the subprocess-binding test mocked verify(), so verify -> run ->
    subprocess.run(pass_fds=...) was never executed;
  * the fstat test asserted a NON-EMPTY INTERSECTION of descriptor sets, which
    a second open to the same file also satisfies;
  * the partial-copy test never observed bytes on disk, so deleting the
    destination write escaped it (it was caught by a different test in the
    file, which is not the same thing as being covered).
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
SEC = BIN / "bdtools_sec.py"

_IS_ROOT = (os.geteuid() == 0)


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


def _load_bdcut():
    return _load(BDCUT, "bd_cut_uut_1152")


def _tmproot():
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import _tmproot
        return _tmproot
    finally:
        sys.path.pop(0)


def _zip_with(path, marker):
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(4):
            zf.writestr(f"pkg/mod{i}.py", f"MARKER = {marker!r}\nVALUE = {i}\n" * 10)
        zf.writestr("run_tests.py", f"print({marker!r})\n")
    return path


def _purge(m):
    for fd in list(getattr(m, "_OPEN_FDS", [])):
        try:
            os.close(fd)
        except OSError:
            pass
        m._OPEN_FDS.remove(fd)
    for d in list(getattr(m, "_TEMPDIRS", [])):
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        shutil.rmtree(d, ignore_errors=True)
        if d in m._TEMPDIRS:
            m._TEMPDIRS.remove(d)


def _work(tmp_path):
    w = tmp_path / "work"
    (w / "bulk_downloader").mkdir(parents=True, exist_ok=True)
    (w / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')
    (w / "anything.py").write_text("x = 1\n")
    return w


def _drive(m, monkeypatch, tmp_path, src, gate=None):
    monkeypatch.setattr(m, "step0_gate", gate or (lambda s, **k: []))
    monkeypatch.setattr(m, "band", lambda *a, **k: None)
    monkeypatch.setattr(m, "verify", lambda *a, **k: None)
    monkeypatch.setattr(m, "max_summary", lambda *a, **k: None)
    return m.main(["--work", str(_work(tmp_path)), "--out", str(tmp_path / "o"),
                   "--resume-zip", str(src)])


# =========================================================================
# 1 | A FAILED CLEANUP MUST COST AN EXIT CODE -- bd-cut
# =========================================================================

def test_the_driver_exits_nonzero_when_cleanup_fails(tmp_path, monkeypatch, capsys):
    """MEASURED at dcf34528: rc=0 with two directories reported unremoved.

    The finally printed and then returned the inner value. A report nobody is
    forced to read is a report that gets automated over.
    """
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    monkeypatch.setattr(
        m.shutil, "rmtree",
        lambda *a, **k: (_ for _ in ()).throw(OSError(13, "Permission denied")))
    try:
        rc = _drive(m, monkeypatch, tmp_path, src)
    finally:
        monkeypatch.undo()
        _purge(m)
    err = capsys.readouterr().err
    assert "NOT REMOVED" in err.upper(), err[-400:]
    assert rc != 0, (
        "bd-cut reported unremoved directories and still exited 0; the only "
        "part of its output a caller cannot ignore by accident is the code")


def test_the_cleanup_exit_code_is_distinguishable_and_named(tmp_path, monkeypatch, capsys):
    """bd-cut answers 1 for every die() and 3 for every step-0 refusal, so a
    test asserting merely "nonzero" passes when any of them fires (CLAUDE.md
    section 10: four mutants escaped exactly that way). The code has to be its
    own, and the run has to SAY which one it is."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    monkeypatch.setattr(
        m.shutil, "rmtree",
        lambda *a, **k: (_ for _ in ()).throw(OSError(13, "Permission denied")))
    try:
        rc = _drive(m, monkeypatch, tmp_path, src)
    finally:
        monkeypatch.undo()
        _purge(m)
    err = capsys.readouterr().err
    assert rc == m.EXIT_CLEANUP_FAILED
    assert rc not in (0, 1, 3), (
        f"the cleanup exit code {rc} collides with die() or a step-0 refusal")
    assert "cleanup" in err.lower(), (
        "the nonzero exit does not say it is about cleanup:\n" + err[-400:])


def test_a_real_failure_is_not_masked_by_the_cleanup_code(tmp_path, monkeypatch, capsys):
    """THE OVER-SENSITIVE DIRECTION, and the one that would do damage. A step-0
    refusal is exit 3 and means "this cut is not authorized". If a cleanup
    failure overwrote it with its own code, the caller would be told the wrong
    thing about the more important event."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    monkeypatch.setattr(
        m.shutil, "rmtree",
        lambda *a, **k: (_ for _ in ()).throw(OSError(13, "Permission denied")))
    try:
        rc = _drive(m, monkeypatch, tmp_path, src,
                    gate=lambda s, **k: ["NO-CUT: step-0 synthetic refusal"])
    finally:
        monkeypatch.undo()
        _purge(m)
    assert rc == 3, (
        f"a step-0 refusal returned {rc}; the cleanup failure overwrote the "
        "verdict that actually matters")


def test_a_clean_run_still_exits_zero(tmp_path, monkeypatch):
    """THE OTHER OVER-SENSITIVE DIRECTION: a tool that failed every run would
    pass every assertion above."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    try:
        rc = _drive(m, monkeypatch, tmp_path, src)
    finally:
        _purge(m)
    assert rc == 0, rc


def test_the_REAL_CLI_exits_nonzero_when_cleanup_fails(tmp_path):
    """END TO END through `sys.exit(main())`, not through main()'s return value.

    The in-process tests above cannot see a wrapper that computes the right
    number and then fails to hand it to the shell. This copies bd-cut, makes
    its discard helper fail (applied-check: unique anchor plus length
    arithmetic, CLAUDE.md section 6), and reads the process's real exit status.
    """
    m = _load_bdcut()
    src_text = BDCUT.read_text(encoding="utf-8")

    # TWO injections, each with a unique anchor and length arithmetic
    # (CLAUDE.md section 6). The second is what stops this test passing for the
    # wrong reason: without it the run reaches the REAL band, dies there, and
    # exits 1 -- so a bare `returncode != 0` assertion is satisfied by a guard
    # that has nothing to do with cleanup. It passed that way on first run.
    a1 = "def _discard_tempdir(d):\n"
    n1 = "def _discard_tempdir(d):\n    return False    # INJECTED: cleanup always fails\n"
    a2 = 'if __name__ == "__main__":\n'
    n2 = ("step0_gate = lambda *a, **k: []          # INJECTED\n"
          "band = lambda *a, **k: None              # INJECTED\n"
          "verify = lambda *a, **k: None            # INJECTED\n"
          "max_summary = lambda *a, **k: None       # INJECTED\n"
          'if __name__ == "__main__":\n')
    after = src_text
    for old_s, new_s in ((a1, n1), (a2, n2)):
        assert after.count(old_s) == 1, f"anchor {old_s!r} count {after.count(old_s)}"
        prev = len(after)
        after = after.replace(old_s, new_s, 1)
        assert len(after) == prev - len(old_s) + len(new_s)

    b = tmp_path / "bin"
    b.mkdir()
    (b / "bd-cut").write_text(after, encoding="utf-8")
    (b / "bd-cut").chmod(0o755)
    shutil.copy(SEC, b / "bdtools_sec.py")

    z = _zip_with(tmp_path / "r.zip", "A")
    w = _work(tmp_path)

    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    # THIS TEST DELIBERATELY BREAKS CLEANUP IN A CHILD PROCESS, so it owns the
    # residue that produces -- the child cannot remove anything by
    # construction, and nothing else will. The same rule v3.66.1150 wrote for
    # the in-process case; measured here as 2 directories per run under
    # KEEP_TEST_TMPDIRS=1, which is the mode leak measurement uses.
    _tmp = pathlib.Path(tempfile.gettempdir())

    def _sweep():
        return set(_tmp.glob("bdcut_*")) | set(_tmp.glob("bdfg_*"))

    _before = _sweep()
    try:
        r = subprocess.run(
            [sys.executable, str(b / "bd-cut"), "--work", str(w),
             "--out", str(tmp_path / "o"), "--resume-zip", str(z)],
            capture_output=True, text=True, timeout=300, env=env, cwd=str(tmp_path))
    finally:
        for _d in _sweep() - _before:
            try:
                os.chmod(_d, 0o700)
            except OSError:
                pass
            shutil.rmtree(_d, ignore_errors=True)
    blob = r.stdout + r.stderr

    # PRECONDITION: the run must have got all the way through, or "nonzero"
    # says nothing about cleanup.
    assert "resume complete" in blob.lower(), (
        "the run did not reach the end, so its exit code is about something "
        "else:\n" + blob[-900:])
    assert "NOT REMOVED" in blob.upper(), blob[-900:]
    assert r.returncode == m.EXIT_CLEANUP_FAILED, (
        f"the real CLI exited {r.returncode} after completing a cut and "
        f"reporting unremoved directories; expected "
        f"{m.EXIT_CLEANUP_FAILED}:\n{blob[-900:]}")


# =========================================================================
# 2 | A FAILED RECLAMATION MUST FAIL THE PYTEST RUN -- _tmproot
# =========================================================================

_NESTED_CONFTEST = """
import sys
sys.path.insert(0, {tests!r})
import _tmproot

{override}

def pytest_configure(config):
    _tmproot.install()

def pytest_sessionfinish(session, exitstatus):
    _tmproot.finish_session(session, exitstatus)
"""

_FAIL_OVERRIDE = """
_tmproot._force_rmtree = lambda path: False      # the reclamation cannot finish
"""


def _nested(tmp_path, override, body="def test_passes():\n    assert True\n"):
    d = tmp_path / "nested"
    d.mkdir()
    (d / "conftest.py").write_text(
        _NESTED_CONFTEST.format(tests=str(REPO / "tests"), override=override))
    (d / "test_it.py").write_text(body)
    env = dict(os.environ)
    env.pop("KEEP_TEST_TMPDIRS", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(d)],
        cwd=str(d), capture_output=True, text=True, timeout=300, env=env)


def test_a_nested_pytest_exits_nonzero_when_the_temp_root_cannot_be_reclaimed(tmp_path):
    """Both hooks discard finish()'s return value and pytest computes its exit
    status from test outcomes alone, so a leaked per-run root left the run
    GREEN. Driven as a real nested pytest, because the thing under test is the
    process's exit status."""
    r = _nested(tmp_path, _FAIL_OVERRIDE)
    blob = r.stdout + r.stderr
    assert "1 passed" in blob, blob[-600:]
    assert r.returncode != 0, (
        "the per-run temp root could not be reclaimed and pytest still exited "
        f"0:\n{blob[-600:]}")
    assert "NOT REMOVED" in blob.upper(), blob[-600:]


def test_a_nested_pytest_that_reclaims_cleanly_still_exits_zero(tmp_path):
    """THE OVER-SENSITIVE DIRECTION: a hook that always failed the session
    would pass the test above and make every green run red."""
    r = _nested(tmp_path, "")
    blob = r.stdout + r.stderr
    assert "1 passed" in blob, blob[-600:]
    assert r.returncode == 0, blob[-600:]


def test_a_root_KEPT_after_a_failing_run_is_not_reported_as_a_cleanup_failure(tmp_path):
    """`finish()` deliberately KEEPS the root when the run failed, so a
    debugging directory is never deleted on the one run that needed it. That
    False means "kept on purpose" and must not be confused with "tried and
    could not" -- otherwise every failing run gains a second, false complaint
    about cleanup."""
    r = _nested(tmp_path, "", body="def test_fails():\n    assert False\n")
    blob = r.stdout + r.stderr
    assert "1 failed" in blob, blob[-600:]
    assert "NOT REMOVED" not in blob.upper(), (
        "a root kept deliberately after a failing run was reported as a "
        "cleanup failure:\n" + blob[-600:])


# =========================================================================
# 3 | ONLY A DIRECTORY'S MODE BLOCKS REMOVAL
# =========================================================================

@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_the_reclaimer_never_chmods_a_hard_link_to_an_outside_file(tmp_path):
    """A HARD LINK IS NOT A SYMLINK, and it shares the target's inode.

    v3.66.1151 skipped symlinks and chmod'd everything else, and containment
    was decided on realpath -- which for a hard link is the IN-TREE path,
    because a hard link has no target to resolve. So the entry looked local and
    the chmod landed on an inode outside the tree. Reproduced at dcf34528:
    0644 -> 0700 on a file outside the tree, same inode, file surviving.

    Only a DIRECTORY's mode can block the removal of its entries, so that is
    the whole population worth relaxing.
    """
    t = _tmproot()
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("data")
    os.chmod(outside, 0o644)
    os.link(str(outside), str(tree / "sub" / "hardlink"))
    os.chmod(tree / "sub", 0o500)          # force the retry handler to fire

    before = stat.S_IMODE(os.stat(outside).st_mode)
    assert os.stat(outside).st_ino == os.stat(tree / "sub" / "hardlink").st_ino, (
        "the fixture did not create a hard link")
    try:
        t._force_rmtree(str(tree))
        after = stat.S_IMODE(os.stat(outside).st_mode)
        assert after == before, (
            f"the reclaimer chmod'd an inode outside its tree through a hard "
            f"link: {oct(before)} -> {oct(after)}")
        assert outside.read_text() == "data"
    finally:
        for p in (tree / "sub", tree):
            try:
                os.chmod(p, 0o700)
            except OSError:
                pass
        shutil.rmtree(str(tree), ignore_errors=True)


# =========================================================================
# 4 | THE THREE OWNERSHIP HOLES IN _discard_tempdir
# =========================================================================

def test_a_directory_with_no_recorded_identity_is_refused(tmp_path, capsys):
    """The comment said cleanup refuses what it cannot identify. It did not:
    a creation-time lstat failure leaves no entry, and the lookup treated a
    MISSING identity as permission rather than as the unknown it is. Measured
    at dcf34528: returns True and deletes."""
    m = _load_bdcut()
    d = tempfile.mkdtemp(prefix="bdcut_noident_")
    (pathlib.Path(d) / "someone-elses-file").write_text("x")
    m._TEMPDIRS.append(d)
    assert d not in m._TEMPDIR_IDENT, "the fixture recorded an identity"
    try:
        got = m._discard_tempdir(d)
        assert got is False, (
            "a directory with NO recorded identity was deleted; unknown is a "
            "third state and it fails")
        assert os.path.isdir(d), "it deleted a directory it could not identify"
        # ASSERT THE REASON, NOT ONLY THE REFUSAL. Deleting the explicit
        # missing-identity branch still refuses -- _same_object(d, None) is
        # False for any real directory -- so the outcome alone does not
        # constrain it, and bd-mutate proved that by escaping. What changes is
        # the REPORT: the fallback says the path was "renamed or replaced",
        # which is a specific and FALSE account of a directory whose identity
        # was simply never recorded. An operator acts on that sentence.
        why = str(m._LAST_DISCARD_ERROR.get(d, ""))
        assert "identity" in why and "renamed" not in why, (
            "the refusal misreports WHY: a directory with no recorded identity "
            f"is not one that was renamed or replaced. Got: {why!r}")
    finally:
        if d in m._TEMPDIRS:
            m._TEMPDIRS.remove(d)
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_the_retry_cannot_be_swapped_to_a_symlink(tmp_path):
    """A TOCTOU IN THE RETRY. The identity check ran once, and the retry then
    re-stated and chmod'd through FOLLOWING calls -- so a swap in that window
    reached whatever the new path pointed at. The swap is injected exactly
    there: rmtree replaces the directory with a symlink and raises, which is
    the moment the retry begins."""
    m = _load_bdcut()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    before = stat.S_IMODE(os.stat(outside).st_mode)

    d = m._owned_tempdir("bdcut_toctou_")
    calls = {"n": 0}
    real_rmtree = shutil.rmtree
    attempted = []
    real_chmod = os.chmod

    def swapping_rmtree(path, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            real_rmtree(path, ignore_errors=True)
            os.symlink(str(outside), path)          # d is now a symlink
            raise OSError(13, "Permission denied")
        return real_rmtree(path, *a, **k)

    def spy_chmod(path, mode, *a, **k):
        attempted.append(os.path.realpath(str(path)))
        return real_chmod(path, mode, *a, **k)

    m.shutil.rmtree = swapping_rmtree
    os.chmod = spy_chmod
    try:
        got = m._discard_tempdir(d)
    finally:
        m.shutil.rmtree = real_rmtree
        os.chmod = real_chmod
        if os.path.islink(d):
            os.unlink(d)
        if d in m._TEMPDIRS:
            m._TEMPDIRS.remove(d)
        real_rmtree(d, ignore_errors=True)

    assert os.path.realpath(str(outside)) not in attempted, (
        f"the retry chmod'd through a symlink swapped in after the identity "
        f"check: {attempted}")
    assert stat.S_IMODE(os.stat(outside).st_mode) == before
    assert outside.is_dir(), "the retry removed the symlink's TARGET"
    assert got is False


def test_a_dangling_symlink_is_not_reported_as_removed(tmp_path):
    """`os.path.exists` follows, so a dangling symlink reads as ABSENT and the
    helper reported the path gone while the link was still on disk. lexists is
    the question actually being asked: is there still a NAME here?"""
    m = _load_bdcut()
    d = m._owned_tempdir("bdcut_dangle_")
    real_rmtree = shutil.rmtree

    # The symlink appears AFTER the removal, which is the only way to reach the
    # final existence check -- an identity check up front already refuses a
    # path that is a symlink when it is asked. This is the window where
    # os.path.exists() answers about the TARGET and the caller is asking about
    # the NAME.
    def rmtree_then_dangle(path, *a, **k):
        real_rmtree(path, *a, **k)
        os.symlink(str(tmp_path / "no-such-target"), path)

    m.shutil.rmtree = rmtree_then_dangle
    try:
        got = m._discard_tempdir(d)
    finally:
        m.shutil.rmtree = real_rmtree
    try:
        assert os.path.lexists(d) and not os.path.exists(d), "fixture wrong"
        assert got is False, (
            "a dangling symlink was left at the path and the helper reported "
            "it removed -- os.path.exists follows the link, so it answers "
            "about a target that is not there rather than about the name")
        assert d in m._TEMPDIRS, "it unregistered a path that still has a name"
    finally:
        if os.path.lexists(d):
            os.unlink(d)
        if d in m._TEMPDIRS:
            m._TEMPDIRS.remove(d)
        m._TEMPDIR_IDENT.pop(d, None)


# =========================================================================
# 5 | THE FOUR OVERSTATED PROOFS, RE-STATED AS MEASUREMENTS
# =========================================================================

@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_production_REPORTS_the_imposter_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    """v3.66.1151's imposter test called _discard_tempdir directly and removed
    the renamed original by hand, so it never showed what PRODUCTION does with
    the leak. Drive main(): the imposter must survive, the real directory must
    still be there under its new name, the run must name it, and it must cost
    an exit code."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    stash = {}

    def rename_the_snapshot(*a, **k):
        for d in list(m._TEMPDIRS):
            if os.path.basename(d).startswith("bdcut_archive_"):
                stash["real"] = d + ".stashed"
                stash["path"] = d
                os.rename(d, stash["real"])
                os.mkdir(d)
                (pathlib.Path(d) / "theirs").write_text("imposter")
                return

    monkeypatch.setattr(m, "step0_gate", lambda s, **k: [])
    monkeypatch.setattr(m, "band", rename_the_snapshot)
    monkeypatch.setattr(m, "verify", lambda *a, **k: None)
    monkeypatch.setattr(m, "max_summary", lambda *a, **k: None)
    try:
        rc = m.main(["--work", str(_work(tmp_path)), "--out", str(tmp_path / "o"),
                     "--resume-zip", str(src)])
        err = capsys.readouterr().err
        # OBSERVED BEFORE THE TEARDOWN. _purge() removes everything still
        # registered, and the imposter IS still registered precisely because
        # production refused it -- so reading these after the finally measures
        # this test's own cleanup, not production's behaviour. That is how the
        # first run of this test failed.
        obs = {
            "imposter_dir": os.path.isdir(stash["path"]),
            "imposter_file": (pathlib.Path(stash["path"]) / "theirs").exists(),
            "real_dir": os.path.isdir(stash["real"]),
        }
    finally:
        monkeypatch.undo()
        for p in (stash.get("real"), stash.get("path")):
            if p and os.path.lexists(p):
                try:
                    os.chmod(p, 0o700)
                except OSError:
                    pass
                shutil.rmtree(p, ignore_errors=True)
        _purge(m)

    assert stash.get("path"), "the fixture never renamed a snapshot directory"
    assert obs["imposter_dir"], (
        "production DELETED the imposter -- a directory it never created")
    assert obs["imposter_file"]
    assert obs["real_dir"], (
        "the real sealed snapshot is gone; production removed the wrong one")
    assert "NOT REMOVED" in err.upper(), err[-500:]
    assert rc != 0, (
        f"production leaked the real snapshot, refused the imposter, and "
        f"exited {rc}")


def test_verify_really_passes_the_descriptor_through_subprocess_run(tmp_path, monkeypatch):
    """v3.66.1151 asserted pass_fds on a MOCKED verify. That proves the driver
    computes the argument; it does not prove verify -> run ->
    subprocess.run(pass_fds=...) carries the descriptor into a real child.

    So run the real chain against a real child, and have the child read the
    /proc/self/fd path and report the bytes it saw.
    """
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A-REAL-ARCHIVE")
    bound, ident, fd = m.snapshot_archive(str(src))
    w = _work(tmp_path)
    (w / "tools").mkdir(exist_ok=True)
    marker = tmp_path / "child.json"
    (w / "tools" / "verify_release.py").write_text(
        "import hashlib, json, os, sys\n"
        "p = sys.argv[sys.argv.index('--zip') + 1]\n"
        "try:\n"
        "    b = open(p, 'rb').read()\n"
        "    out = {'ok': True, 'sha': hashlib.sha256(b).hexdigest(), 'n': len(b)}\n"
        "except Exception as e:\n"
        "    out = {'ok': False, 'err': repr(e)}\n"
        f"open({str(marker)!r}, 'w').write(json.dumps(out))\n"
        "sys.exit(0 if out['ok'] else 9)\n")
    monkeypatch.setattr(m, "python_for", lambda work: sys.executable)
    try:
        m.verify(str(w), bound, pass_fds=(fd,))
        got = json.loads(marker.read_text())
    finally:
        monkeypatch.undo()
        _purge(m)

    assert got["ok"], (
        "the child could not open the descriptor-backed path, so the binding "
        f"does not survive subprocess.run: {got}")
    assert got["sha"] == ident["sha256"], (
        "the child read different bytes than the snapshot's recorded identity")
    assert got["n"] == ident["size"]


def test_the_child_CANNOT_read_the_path_without_the_descriptor(tmp_path, monkeypatch):
    """THE CONTROL THAT MAKES THE TEST ABOVE MEAN SOMETHING. If the child could
    open /proc/self/fd/N regardless, the pass_fds argument would be decorative
    and the previous assertion would pass with the mechanism removed."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    bound, ident, fd = m.snapshot_archive(str(src))
    w = _work(tmp_path)
    (w / "tools").mkdir(exist_ok=True)
    marker = tmp_path / "child2.json"
    (w / "tools" / "verify_release.py").write_text(
        "import json, sys\n"
        "p = sys.argv[sys.argv.index('--zip') + 1]\n"
        "try:\n"
        "    b = open(p, 'rb').read(); out = {'ok': True, 'n': len(b)}\n"
        "except Exception as e:\n"
        "    out = {'ok': False, 'err': repr(e)}\n"
        f"open({str(marker)!r}, 'w').write(json.dumps(out))\n"
        "sys.exit(0)\n")
    monkeypatch.setattr(m, "python_for", lambda work: sys.executable)
    try:
        m.verify(str(w), bound, pass_fds=())        # deliberately withheld
        got = json.loads(marker.read_text())
    finally:
        monkeypatch.undo()
        _purge(m)
    assert got["ok"] is False, (
        "the child opened the descriptor-backed path WITHOUT being given the "
        "descriptor, so pass_fds proves nothing")


def test_the_identity_descriptor_is_the_ONLY_one_ever_fstatted(tmp_path, monkeypatch):
    """v3.66.1151 asserted a NON-EMPTY INTERSECTION of descriptor sets, which a
    second open to the same file also satisfies -- the fd that supplied the
    bytes would be in the set, and so would the extra one.

    The claim is that ONE descriptor is authoritative. So: every fstat that
    landed on the SOURCE's inode must have been on the descriptor that supplied
    the bytes, and there must be exactly one such descriptor.
    """
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    src_ino = os.stat(str(src)).st_ino
    target = os.path.realpath(str(src))
    fstatted_on_source, read_fds = [], []
    real_fstat, real_open = os.fstat, open

    def spy_fstat(fd, *a, **k):
        st = real_fstat(fd, *a, **k)
        if st.st_ino == src_ino:
            fstatted_on_source.append(fd)
        return st

    def spy_open(file, mode="r", *a, **k):
        fh = real_open(file, mode, *a, **k)
        try:
            same = os.path.realpath(str(file)) == target
        except TypeError:
            same = False
        if same and "b" in str(mode) and "r" in str(mode):
            inner = fh.read

            def counting(n=-1):
                data = inner(n)
                if data:
                    read_fds.append(fh.fileno())
                return data
            fh.read = counting
        return fh

    monkeypatch.setattr(os, "fstat", spy_fstat)
    monkeypatch.setattr("builtins.open", spy_open)
    try:
        m.snapshot_archive(str(src))
    finally:
        monkeypatch.undo()
        _purge(m)

    supplier = set(read_fds)
    assert len(supplier) == 1, f"the bytes came from several descriptors: {supplier}"
    assert fstatted_on_source, "nothing fstat'd the source at all"
    assert set(fstatted_on_source) == supplier, (
        f"fstat landed on the source inode through descriptor(s) "
        f"{sorted(set(fstatted_on_source))} while the bytes came through "
        f"{sorted(supplier)} -- a SECOND descriptor to the same file was "
        "opened, so no single one is authoritative")


def test_a_partial_copy_is_OBSERVED_on_disk_before_it_is_cleaned_up(tmp_path, monkeypatch):
    """v3.66.1151 asserted the destination was gone afterwards, which is also
    true when nothing was ever written -- so deleting the write escaped that
    test (it was caught by a different test in the file, which is not the same
    as being covered).

    Observe the partial file WHILE it exists: stat the destination at the
    moment of failure and require a non-zero size.
    """
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    target = os.path.realpath(str(src))
    state = {"chunks": 0, "dst": None, "size_at_failure": None}
    real_open = open

    def spy_open(file, mode="r", *a, **k):
        fh = real_open(file, mode, *a, **k)
        if "w" in str(mode):
            state["dst"] = str(file)
        try:
            same = os.path.realpath(str(file)) == target
        except TypeError:
            same = False
        if same and "b" in str(mode) and "r" in str(mode):
            inner = fh.read

            def failing(n=-1):
                state["chunks"] += 1
                if state["chunks"] > 1:
                    # THE OBSERVATION. Flush first: the bytes may still be in
                    # the destination's buffer, and an unflushed write is
                    # indistinguishable from no write at all on disk.
                    try:
                        state["dst_handle"].flush()
                    except Exception:
                        pass
                    state["size_at_failure"] = os.path.getsize(state["dst"])
                    raise OSError(5, "Input/output error")
                return inner(64)
            fh.read = failing
        if "w" in str(mode):
            state["dst_handle"] = fh
        return fh

    before_reg = list(m._TEMPDIRS)
    monkeypatch.setattr("builtins.open", spy_open)
    try:
        with pytest.raises(OSError):
            m.snapshot_archive(str(src))
    finally:
        monkeypatch.undo()
        _purge(m)

    assert state["chunks"] > 1, "the copy did not get past its first chunk"
    assert state["size_at_failure"], (
        "the destination was EMPTY at the moment of failure, so this test "
        "never observed a partial copy and would pass with the write removed")
    assert not os.path.exists(state["dst"]), "the partial file survived"
    assert list(m._TEMPDIRS) == before_reg


# =========================================================================
# 6 | THE CI CLAIM THAT WAS NOT TRUE
# =========================================================================

def test_bd_footguns_selftest_is_wired_into_ci_here(tmp_path):
    """THE PR BODY CLAIMED test_v3_66_799 RAN THIS. IT DOES NOT.

    That file's TOOLS list is exactly tools/bd-triage.py and
    tools/bd-audit-gate.py -- bd-footguns is not in it, and nothing else in the
    suite ran `bd-footguns --selftest`. The claim was corrected AND the gap
    closed here, because the honest repair for "X is covered" turning out false
    is to cover X, and this file is in a CI shard.

    Asserts the verdict LINE as well as the code: a green exit with no verdict
    behind it is the shape this repo keeps finding (test_toolchain_534's rule).
    """
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    before = set(pathlib.Path(tempfile.gettempdir()).glob("*"))
    r = subprocess.run([sys.executable, str(FOOTGUNS), "--selftest"],
                       capture_output=True, text=True, timeout=300,
                       cwd=str(tmp_path), env=env)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-800:]
    assert "SELFTEST PASS" in out, (
        "bd-footguns --selftest exited 0 without printing a verdict:\n"
        + out[-800:])
    leaked = set(pathlib.Path(tempfile.gettempdir()).glob("*")) - before
    for p in leaked:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    assert not leaked, f"--selftest leaked {sorted(p.name for p in leaked)}"
