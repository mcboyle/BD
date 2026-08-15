"""Four escapes v3.66.1150 left open, and two claims it asserted without proving.

MEASURED at 55ae94f8 on test5. The pattern is the same one CLAUDE.md section 0
calls the highest-yield rule on the page: every defect here is in the FIX, and
three of them are in a fix written to close the previous round's fix.

  1. THE SNAPSHOT ABA STILL ESCAPED. v3.66.1150 answered the rename+recreate
     swap with `_snapshot_moved`, a hash taken AFTER the band and again AFTER
     the summary. An after-the-fact hash cannot see a swap that is undone: a
     consumer renames A away, does its work against an imposter B at the same
     pathname, and restores A before returning, and both post-stage hashes see
     A and report clean. The verdict is then about bytes nobody judged.

     A hash samples a pathname at an instant. Only an OPEN DESCRIPTOR names an
     inode for as long as it is held, so the consumers are now bound to one.

  2. `_force_rmtree` CHMOD'D OUTSIDE ITS TREE THROUGH A SYMLINK. The guard
     added in v3.66.1150 is LEXICAL -- it compares strings -- while os.chmod
     FOLLOWS symlinks. A link inside the tree pointing anywhere at all is
     lexically "inside", so the retry handler chmod'd its target. Reproduced:
     a directory outside the tree went 0755 -> 0700, and survived.

  3. `_discard_tempdir` HAD THE SAME FOLLOWING BEHAVIOUR, and worse, it could
     not tell its own directory from a stranger's. After a rename+recreate the
     recorded path holds someone else's directory: the cleanup deleted THAT --
     an imposter it never created -- while the real sealed snapshot, now living
     under a different name, leaked silently. v3.66.1150's own test hid this by
     removing the renamed original by hand.

  4. NOTHING LOOKED AT `finish()`'s RETURN VALUE. Both session-finish call
     sites -- tests/_tmproot.pytest_sessionfinish and tests/conftest.py --
     discard it, and `_ROOT` is cleared before the removal is attempted, so a
     failed reclamation is unrecoverable AND unreported and pytest stays green.
     A cleanup that did not happen is never silent: the report belongs INSIDE
     finish(), where no call site can drop it.

AND TWO PROOFS THAT WERE NOT PROOFS:

  * The fstat test rejected os.stat on the archive path, which shows the
    identity did not come from a second lookup. It does NOT show that fstat was
    called on the SAME DESCRIPTOR the bytes were read through -- an
    implementation that opened the file twice would pass it. The claim is about
    one descriptor; the test has to name it.

  * The failed-snapshot test made the DESTINATION open raise, so the copy died
    before a single byte was written and the interesting case -- a partial file
    already on disk -- was never exercised. Cleanup after a mid-copy failure
    was asserted by a test that could not reach it.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import zipfile

import pytest

# Its subject is one tool's archive binding and two cleanup helpers.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
BIN = REPO / "toolchain" / "bin"
BDCUT = BIN / "bd-cut"

# Root bypasses the mode bits several of these rest on, and would make the
# assertions pass against a tree with no guard at all.
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
    return _load(BDCUT, "bd_cut_uut_1151")


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
    for d in list(getattr(m, "_TEMPDIRS", [])):
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        shutil.rmtree(d, ignore_errors=True)
        if d in m._TEMPDIRS:
            m._TEMPDIRS.remove(d)


def _close_snapshot_fds(m):
    for fd in list(getattr(m, "_OPEN_FDS", [])):
        try:
            os.close(fd)
        except OSError:
            pass
        m._OPEN_FDS.remove(fd)


# =========================================================================
# 1 | THE CONSUMERS ARE BOUND TO ONE DESCRIPTOR
# =========================================================================

def _drive_resume(m, monkeypatch, tmp_path, src, on_band=None, on_verify=None):
    seen = {}
    work = tmp_path / "work"
    (work / "bulk_downloader").mkdir(parents=True, exist_ok=True)
    (work / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')
    (work / "anything.py").write_text("x = 1\n")

    def _band(zippath, suites, w, extracted=None):
        seen["band"] = zippath
        if on_band:
            on_band(zippath, seen)
        seen.setdefault("band_bytes", pathlib.Path(zippath).read_bytes())

    def _verify(w, z, pass_fds=()):
        seen["verify"] = z
        seen["verify_pass_fds"] = tuple(pass_fds)
        if on_verify:
            on_verify(z, seen)
        seen.setdefault("verify_bytes", pathlib.Path(z).read_bytes())

    monkeypatch.setattr(m, "step0_gate", lambda s, **k: [])
    monkeypatch.setattr(m, "band", _band)
    monkeypatch.setattr(m, "verify", _verify)
    monkeypatch.setattr(m, "max_summary", lambda z, b: seen.update(
        summary=z, summary_bytes=pathlib.Path(z).read_bytes()))
    rc = m.main(["--work", str(work), "--out", str(tmp_path / "o"),
                 "--resume-zip", str(src)])
    return rc, seen


def test_an_ABA_swap_that_is_UNDONE_cannot_change_what_a_consumer_reads(tmp_path, monkeypatch):
    """THE ESCAPE v3.66.1150's HASH COULD NOT SEE.

    The consumer renames the real snapshot directory away, stands an imposter
    at the same pathname, does its reading, and puts the original back before
    returning. Every hash taken after the stage sees the original and reports
    clean -- which is exactly what a post-stage check is for and exactly what
    it cannot detect.

    The assertion is therefore on what the consumer READ DURING the window,
    not on any hash taken afterwards. With the consumer bound to an open
    descriptor the pathname is irrelevant; with it bound to a name, this reads
    the imposter.
    """
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A-THE-REAL-ARCHIVE")
    imposter = _zip_with(tmp_path / "imposter.zip", "B-THE-IMPOSTER")
    original = src.read_bytes()

    def aba(zippath, seen):
        real_dir = None
        for d in list(m._TEMPDIRS):
            if os.path.basename(d).startswith("bdcut_archive_"):
                real_dir = d
                break
        assert real_dir, "no owned archive directory to swap"
        stashed = real_dir + ".stashed"
        os.rename(real_dir, stashed)                 # A out of the way
        os.mkdir(real_dir)
        shutil.copyfile(str(imposter), os.path.join(
            real_dir, os.listdir(stashed)[0]))       # B at the same pathname
        # THE CONSUMER READS HERE, mid-swap. This is the whole test.
        seen["band_bytes"] = pathlib.Path(zippath).read_bytes()
        shutil.rmtree(real_dir)
        os.rename(stashed, real_dir)                 # A restored: hash sees A

    try:
        rc, seen = _drive_resume(m, monkeypatch, tmp_path, src, on_band=aba)
    finally:
        _close_snapshot_fds(m)
        _purge(m)
        for leftover in pathlib.Path(tempfile.gettempdir()).glob("bdcut_archive_*.stashed"):
            try:
                os.chmod(leftover, 0o700)
            except OSError:
                pass
            shutil.rmtree(leftover, ignore_errors=True)

    assert seen["band_bytes"] == original, (
        "the band read the IMPOSTER during an ABA window that was undone "
        "before any hash could sample it -- a post-stage hash cannot see this, "
        "only an open descriptor can")


def test_the_consumers_receive_a_descriptor_backed_path(tmp_path, monkeypatch):
    """The mechanism, asserted directly: every consumer is handed a path that
    names an INODE rather than a directory entry."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    try:
        rc, seen = _drive_resume(m, monkeypatch, tmp_path, src)
    finally:
        _close_snapshot_fds(m)
        _purge(m)
    assert rc == 0, rc
    for stage in ("band", "verify", "summary"):
        assert stage in seen, f"{stage} never ran"
        assert seen[stage].startswith("/proc/self/fd/"), (
            f"{stage} was handed {seen[stage]!r}, a pathname that another "
            "process can rename out from under it")
    assert seen["band"] == seen["verify"] == seen["summary"]
    # The SUBPROCESS consumer needs the descriptor carried into the child, or
    # /proc/self/fd/N resolves there to whatever the child happens to have at
    # that number -- which is not a binding, it is a coincidence.
    fd = int(seen["verify"].rsplit("/", 1)[1])
    assert seen["verify_pass_fds"] == (fd,), (
        f"verify was handed {seen['verify']} but pass_fds={seen['verify_pass_fds']}; "
        "the child cannot resolve that path to this inode")


def test_the_descriptor_path_survives_deleting_the_original_name(tmp_path):
    """The property that makes the binding real, isolated from the driver."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    try:
        bound, ident, fd = m.snapshot_archive(str(src))
        assert bound.startswith("/proc/self/fd/"), bound
        before = pathlib.Path(bound).read_bytes()
        real = os.path.realpath(bound)
        os.chmod(os.path.dirname(real), 0o700)
        os.unlink(real)                       # the NAME is gone
        assert not os.path.exists(real)
        assert pathlib.Path(bound).read_bytes() == before, (
            "the descriptor-backed path stopped resolving once the directory "
            "entry was removed; it is not bound to the inode")
    finally:
        _close_snapshot_fds(m)
        _purge(m)


def test_the_snapshot_descriptor_is_closed_on_every_exit_path(tmp_path, monkeypatch):
    """An fd held for the life of the process is a leak with a different name.
    CLAUDE.md section 0: creating a path -- or a descriptor -- is a promise to
    remove it."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    # PRECONDITION, not getattr-with-a-default: `getattr(m, "_OPEN_FDS", [])`
    # returns an empty list on a tree that has no such register at all, so the
    # emptiness assertion below would pass vacuously -- which is how the first
    # draft of this test passed at 55ae94f8, before the mechanism existed.
    assert hasattr(m, "_OPEN_FDS"), (
        "bd-cut has no descriptor register, so nothing tracks the snapshot fd "
        "and nothing can close it")
    before = len(os.listdir("/proc/self/fd"))
    try:
        _drive_resume(m, monkeypatch, tmp_path, src)
    finally:
        _purge(m)
    after = len(os.listdir("/proc/self/fd"))
    assert not m._OPEN_FDS, (
        f"the run left descriptors registered: {m._OPEN_FDS}")
    assert after <= before + 1, (
        f"descriptor count grew {before} -> {after} across one resume run")


# =========================================================================
# 2 | NO CHMOD THROUGH A SYMLINK, IN EITHER TOOL
# =========================================================================

def _symlink_trap(tmp_path):
    """A tree whose removal is FORCED to touch a symlink pointing outside it.

    `sub` is 0500, so unlinking anything inside it fails and the retry handler
    fires with the symlink as its path. Returns (tree, outside).
    """
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    os.symlink(str(outside), str(tree / "sub" / "link"))
    os.chmod(tree / "sub", 0o500)
    return tree, outside


@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_force_rmtree_never_chmods_through_a_symlink(tmp_path):
    """MEASURED at 55ae94f8: 0755 -> 0700 on a directory outside the tree.

    v3.66.1150's guard compares STRINGS -- `link` is lexically inside the tree
    -- while os.chmod FOLLOWS the link to its target. A lexical containment
    check cannot answer a question about what a path resolves to.
    """
    t = _tmproot()
    tree, outside = _symlink_trap(tmp_path)
    before = stat.S_IMODE(os.stat(outside).st_mode)
    assert before == 0o755, oct(before)
    try:
        t._force_rmtree(str(tree))
        after = stat.S_IMODE(os.stat(outside).st_mode)
        assert after == before, (
            f"the reclaimer chmod'd THROUGH a symlink: the target outside the "
            f"tree went {oct(before)} -> {oct(after)}")
    finally:
        for p in (tree / "sub", tree, outside):
            try:
                os.chmod(p, 0o700)
            except OSError:
                pass
        shutil.rmtree(str(tree), ignore_errors=True)


@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_force_rmtree_never_chmods_a_symlink_even_inside_its_own_tree(tmp_path):
    """The guard the containment check CANNOT cover, so it needs its own test.

    A realpath-based containment check already refuses a link pointing OUTSIDE
    the tree, which is why deleting the explicit symlink guard left the
    outside-escape test green -- bd-mutate found that, and an escape is a
    behaviour asserted but not constrained.

    An in-tree link is the case the two guards disagree on: its target is
    genuinely inside, so containment permits the chmod and only the symlink
    check refuses it. Harmless here, because the whole tree is going -- but
    `os.chmod` on a path the caller believes is a directory, which silently
    lands on a file somewhere else, is exactly the behaviour that produced the
    /tmp escape one round earlier. Assert the CALL, not a resulting mode: the
    tree is removed, so there is nothing left to inspect afterwards.
    """
    t = _tmproot()
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    victim = tree / "victim"
    victim.write_text("x")
    os.chmod(victim, 0o644)
    link = tree / "sub" / "link"
    os.symlink(str(victim), str(link))
    os.chmod(tree / "sub", 0o500)          # force the retry handler to fire

    attempted, real_chmod = [], os.chmod

    def spy(path, mode, *a, **k):
        attempted.append(str(path))
        return real_chmod(path, mode, *a, **k)

    os.chmod = spy
    try:
        t._force_rmtree(str(tree))
    finally:
        os.chmod = real_chmod
        for p in (tree / "sub", tree):
            try:
                real_chmod(p, 0o700)
            except OSError:
                pass
        shutil.rmtree(str(tree), ignore_errors=True)

    assert str(link) not in attempted, (
        f"the reclaimer chmod'd a SYMLINK ({link}); os.chmod follows it, so "
        "the mode change lands on the target, not on the name it was given. "
        f"calls: {attempted}")


@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_discard_tempdir_never_chmods_through_a_symlink(tmp_path):
    """The same following behaviour in bd-cut's helper: it chmods the directory
    it was handed, and a symlink handed to it resolves elsewhere."""
    m = _load_bdcut()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    link = tmp_path / "link"
    os.symlink(str(outside), str(link))
    before = stat.S_IMODE(os.stat(outside).st_mode)

    # SPY ON THE CALL, not only on the resulting mode. The pre-fix code chmods
    # THROUGH the link to 0700 and then restores 0755 on its way out, so a
    # before/after comparison of the final mode reports clean while the target
    # really was modified -- and any crash in between would leave it changed.
    attempted, real_chmod = [], os.chmod

    def spy(path, mode, *a, **k):
        attempted.append(os.path.realpath(str(path)))
        return real_chmod(path, mode, *a, **k)

    os.chmod = spy
    try:
        got = m._discard_tempdir(str(link))
    finally:
        os.chmod = real_chmod
    after = stat.S_IMODE(os.stat(outside).st_mode)

    assert os.path.realpath(str(outside)) not in attempted, (
        f"_discard_tempdir chmod'd THROUGH a symlink to {outside} "
        f"(calls: {attempted})")
    assert after == before, f"{oct(before)} -> {oct(after)}"
    assert outside.is_dir(), "it removed the symlink's TARGET"
    assert got is False, (
        "a symlink is not a directory this tool created; removing it must not "
        "report success")


@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_force_rmtree_still_reclaims_the_sealed_tree_it_is_for(tmp_path):
    """THE OVER-SENSITIVE DIRECTION. A no-follow fix that simply stopped
    chmod'ing would pass both tests above and reinstate the leak v3.66.1150
    closed."""
    t = _tmproot()
    root = tmp_path / "root"
    sealed = root / "bdcut_archive_probe"
    sealed.mkdir(parents=True)
    (sealed / "r.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    os.chmod(sealed / "r.zip", 0o444)
    os.chmod(sealed, 0o500)
    try:
        assert t._force_rmtree(str(root)) is True
        assert not root.exists()
    finally:
        for p in (sealed, root):
            try:
                os.chmod(p, 0o700)
            except OSError:
                pass
        shutil.rmtree(str(root), ignore_errors=True)


# =========================================================================
# 3 | NEVER DELETE A DIRECTORY WE DID NOT CREATE
# =========================================================================

@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_cleanup_refuses_an_imposter_at_a_path_it_owns(tmp_path, capsys):
    """THE HALF v3.66.1150's TEST HID BY TIDYING UP AFTER IT.

    After a rename+recreate, the recorded path holds a directory this tool
    never made. The old cleanup deleted that -- a stranger's directory -- and
    the REAL sealed snapshot, now living under another name, leaked with
    nothing reporting it. v3.66.1150's own regression removed the renamed
    original by hand, so the leak never showed.

    Identity is inode + device recorded at creation, because the PATH is
    exactly the thing that stopped being trustworthy.
    """
    m = _load_bdcut()
    d = m._owned_tempdir("bdcut_probe_")
    (pathlib.Path(d) / "ours").write_text("real")
    stashed = d + ".stashed"
    os.rename(d, stashed)
    os.mkdir(d)
    (pathlib.Path(d) / "theirs").write_text("imposter")

    got = m._discard_tempdir(d)
    err = capsys.readouterr().err
    try:
        assert got is False, (
            "cleanup reported success for a path holding a directory it never "
            "created")
        assert os.path.isdir(d) and (pathlib.Path(d) / "theirs").exists(), (
            "cleanup DELETED an imposter directory it did not create")
        assert os.path.isdir(stashed), "the fixture lost the real directory"
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(stashed, ignore_errors=True)
        if d in m._TEMPDIRS:
            m._TEMPDIRS.remove(d)


def test_cleanup_still_removes_the_directory_it_did_create(tmp_path):
    """THE OVER-SENSITIVE DIRECTION for the identity check."""
    m = _load_bdcut()
    d = m._owned_tempdir("bdcut_probe_")
    (pathlib.Path(d) / "f").write_text("x")
    assert m._discard_tempdir(d) is True
    assert not os.path.exists(d)
    assert d not in m._TEMPDIRS


# =========================================================================
# 4 | A FAILED RECLAMATION IS NEVER SILENT
# =========================================================================

def test_finish_reports_its_own_failure_where_no_call_site_can_drop_it(tmp_path, capsys):
    """Both session-finish call sites discard finish()'s return value, and
    `_ROOT` is cleared before the attempt, so the failure is unrecoverable as
    well as unreported. The report therefore belongs INSIDE finish()."""
    t = _tmproot()
    root = tmp_path / "root"
    root.mkdir()
    (root / "f").write_text("x")

    saved_root, saved_tempdir = t._ROOT, tempfile.tempdir
    saved_ident = getattr(t, '_ROOT_IDENT', None)
    saved_failure = getattr(t, "_LAST_FAILURE", None)
    real = shutil.rmtree
    try:
        t._ROOT = str(root)
        shutil.rmtree = lambda *a, **k: (_ for _ in ()).throw(
            OSError(39, "Directory not empty"))
        got = t.finish(0)
    finally:
        shutil.rmtree = real
        t._ROOT, tempfile.tempdir = saved_root, saved_tempdir
        if hasattr(t, '_ROOT_IDENT'):
            t._ROOT_IDENT = saved_ident
        if hasattr(t, "_LAST_FAILURE"):
            t._LAST_FAILURE = saved_failure
    err = capsys.readouterr().err
    real(str(root), ignore_errors=True)

    assert got is False
    assert "NOT REMOVED" in err.upper() or "not reclaim" in err.lower(), (
        "finish() failed to remove the per-run root and said nothing; both "
        "call sites discard its return value, so this is the only place the "
        "failure can be reported:\n" + err[-400:])
    assert str(root) in err, "the report does not name the path that leaked"


def test_finish_is_silent_on_the_happy_path(tmp_path, capsys):
    """THE OVER-SENSITIVE DIRECTION: a reclaimer that narrated every success
    would be switched off, which CLAUDE.md section 0 counts as a soundness bug
    rather than a safe default."""
    t = _tmproot()
    root = tmp_path / "root"
    root.mkdir()
    saved_root, saved_tempdir = t._ROOT, tempfile.tempdir
    saved_ident = getattr(t, '_ROOT_IDENT', None)
    saved_failure = getattr(t, "_LAST_FAILURE", None)
    try:
        t._ROOT = str(root)
        _st = os.lstat(str(root))
        t._ROOT_IDENT = (_st.st_dev, _st.st_ino)
        assert t.finish(0) is True
    finally:
        t._ROOT, tempfile.tempdir = saved_root, saved_tempdir
        if hasattr(t, '_ROOT_IDENT'):
            t._ROOT_IDENT = saved_ident
        if hasattr(t, "_LAST_FAILURE"):
            t._LAST_FAILURE = saved_failure
    assert capsys.readouterr().err == ""


# =========================================================================
# 5 | THE TWO PROOFS THAT WERE NOT PROOFS
# =========================================================================

def test_the_identity_fstat_names_the_SAME_descriptor_the_bytes_came_from(tmp_path, monkeypatch):
    """The claim is 'os.fstat on the descriptor the bytes were read through'.

    v3.66.1150 proved only that os.stat was not called on the archive PATH,
    which an implementation opening the file TWICE would also satisfy. So
    record which descriptor supplied the bytes and which descriptor fstat was
    asked about, and require them to be the same one.
    """
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    target = os.path.realpath(str(src))
    fstat_fds, read_fds = [], []

    real_fstat, real_open = os.fstat, open

    def spy_fstat(fd, *a, **k):
        fstat_fds.append(fd)
        return real_fstat(fd, *a, **k)

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
        _close_snapshot_fds(m)
        _purge(m)

    assert read_fds, "no bytes were read from the archive; the probe missed it"
    assert fstat_fds, "os.fstat was never called; the identity came from elsewhere"
    supplier = set(read_fds)
    assert len(supplier) == 1, f"the bytes came from several descriptors: {supplier}"
    assert supplier & set(fstat_fds), (
        f"fstat was called on {sorted(set(fstat_fds))} but the bytes were read "
        f"through {sorted(supplier)} -- the identity describes a DIFFERENT "
        "descriptor than the one that supplied the content")


def test_a_failure_PART_WAY_THROUGH_the_copy_still_cleans_up(tmp_path, monkeypatch):
    """v3.66.1150's version made the DESTINATION open raise, so the copy died
    before one byte was written and the partial-file case was never reached.
    Fail after the first chunk instead, with the destination already on disk."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    target = os.path.realpath(str(src))
    state = {"chunks": 0, "dst": None}
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
                    raise OSError(5, "Input/output error")
                return inner(64)          # a real, short first chunk
            fh.read = failing
        return fh

    before_reg = list(m._TEMPDIRS)
    before_disk = set(pathlib.Path(tempfile.gettempdir()).glob("bdcut_archive_*"))
    monkeypatch.setattr("builtins.open", spy_open)
    try:
        with pytest.raises(OSError):
            m.snapshot_archive(str(src))
    finally:
        monkeypatch.undo()

    # PRECONDITION: the failure must have happened AFTER bytes were written,
    # or this is the same test v3.66.1150 already had.
    assert state["chunks"] > 1, "the copy did not get past its first chunk"
    assert state["dst"] is not None, "the destination was never opened for writing"

    leaked = set(pathlib.Path(tempfile.gettempdir()).glob("bdcut_archive_*")) - before_disk
    assert not leaked, (
        f"a mid-copy failure left a partial snapshot: "
        f"{sorted(str(p) for p in leaked)}")
    assert list(m._TEMPDIRS) == before_reg, "the failed snapshot stayed registered"
    assert not os.path.exists(state["dst"]), (
        "the partially-written destination file survived")
