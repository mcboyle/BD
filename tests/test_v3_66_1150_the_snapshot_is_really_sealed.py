"""Four defects the v3.66.1149 fixes left behind, and four claims nothing pinned.

MEASURED at 4f48fc95 (v3.66.1149) on test5. Every one of these is the SAME
shape as the cut that introduced it: a guarantee stated in a docstring, and a
mechanism that does not quite deliver it.

  1. THE SNAPSHOT WAS NOT SEALED. `snapshot_archive` chmods the FILE to 0444
     and calls it immutable. On POSIX, permission to unlink or rename a path
     comes from the WRITE BIT ON THE PARENT DIRECTORY, not from the file's own
     mode, and the parent was left 0700. So any downstream code -- or anything
     else running as this user -- could `os.unlink` the snapshot and drop a
     different archive at the same pathname, which is precisely the swap the
     snapshot exists to make impossible. 0444 stops a rewrite THROUGH the
     path; it does nothing about replacing the path.

  2. BOTH DISCARD HELPERS TREATED A RAISED OSError AS POSSIBLE SUCCESS. They
     caught OSError, fell through, and then consulted `os.path.exists(d)` --
     so a removal that FAILED but happened to leave nothing at that path
     returned True and unregistered the directory. `rmtree` is not atomic: it
     can delete most of a tree and raise on one entry, and a caller that reads
     "gone" from a failed call is trusting a measurement it did not take.
     CLAUDE.md section 0: unknown is a third state, and it fails.

  3. THE SWAP-DURING-VERIFY TEST ASSERTED THE WRONG HALF. It proved the
     summary read the snapshot's bytes and never asserted the run REFUSED.
     Deleting the post-summary `_source_moved` check entirely left it green --
     a test that cannot fail for the reason it was written.

  4. TWO TESTS IN 1145 LEAK, and the suite's own harness hid it. `_tmproot`
     points `tempfile.tempdir` at a per-run root and `finish()` rmtree's that
     root when exitstatus == 0, so on a GREEN run every leak inside it is
     erased before anything can look. Measured with KEEP_TEST_TMPDIRS=1, which
     disables the redirection: 1145 alone leaks 3 directories, 1149 alone leaks
     0, and the combined suite inherits the same 3. The review named
     test_cleanup_failures_are_reported_not_swallowed; the second,
     test_the_real_band_extracts_exactly_once, was found by measuring rather
     than by reading, and it leaks because it calls band() DIRECTLY, so
     main()'s finally -- the only thing that removes the band's BD_HOME --
     never runs.

     This is also why the "zero leaks" line in the v3.66.1149 report was
     worthless: it was produced by `ls -d /tmp/bdcut_*` from a shell, and
     inside a pytest process that family lives under /tmp/bd-testrun-<rand>.
     The instrument's denominator structurally excluded its subject and it
     reported clean. Section 0, in the verification rather than in the code.

AND FOUR CLAIMS v3.66.1149 MADE THAT NOTHING TESTED: the short-read refusal,
that the identity comes from the SAME descriptor the bytes were read through
(os.fstat, not os.stat on the path), that extraction consumes the snapshot
rather than the external path, and that a snapshot which fails mid-copy cleans
up after itself. A docstring is a claim, not a measurement.
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

# Its subject is one tool's archive handling and two discard helpers.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
BIN = REPO / "toolchain" / "bin"
BDCUT = BIN / "bd-cut"
FOOTGUNS = BIN / "bd-footguns"

# Running as root bypasses the permission bits these tests are about, so the
# seal assertions would pass on a tree with no seal at all. Refuse rather than
# certify: an assertion that cannot fail is not evidence.
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
    return _load(BDCUT, "bd_cut_uut_1150")


def _load_footguns():
    return _load(FOOTGUNS, "bd_footguns_uut_1150")


def _zip_with(path, marker):
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(4):
            zf.writestr(f"pkg/mod{i}.py", f"MARKER = {marker!r}\nVALUE = {i}\n" * 10)
        zf.writestr("run_tests.py", f"print({marker!r})\n")
    return path


def _real(p):
    """The snapshot's REAL path.

    snapshot_archive returns a descriptor-backed /proc/self/fd/N path as of
    v3.66.1151, so consumers bind to an inode rather than to a directory entry.
    Every assertion in THIS file is about the directory entry -- unlink,
    os.replace, the parent's mode -- so it has to resolve first. Without it the
    parent of the snapshot resolves to "/proc/self/fd" and these tests would
    quietly be measuring procfs instead of the snapshot directory.
    """
    return os.path.realpath(p)


def _purge(m):
    """Remove every directory the module still owns, seal or no seal."""
    for d in list(getattr(m, "_TEMPDIRS", [])):
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        shutil.rmtree(d, ignore_errors=True)
        if d in m._TEMPDIRS:
            m._TEMPDIRS.remove(d)


# =========================================================================
# 1 | THE SNAPSHOT DIRECTORY MUST BE SEALED, NOT JUST THE FILE
# =========================================================================

@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_the_snapshot_pathname_cannot_be_unlinked(tmp_path):
    """0444 stops a rewrite THROUGH the path. It does not stop unlink.

    On POSIX the right to remove a name comes from the write bit on the
    DIRECTORY that holds it. With the parent at 0700 the snapshot could be
    deleted and a different archive dropped at the same pathname -- the exact
    swap the snapshot was introduced to make impossible.
    """
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    snap, _, _fd = m.snapshot_archive(str(src))
    try:
        assert os.path.isfile(_real(snap))
        with pytest.raises(PermissionError):
            os.unlink(_real(snap))
        assert os.path.isfile(_real(snap)), "the snapshot was unlinked"
    finally:
        _purge(m)


@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_the_snapshot_pathname_cannot_be_replaced(tmp_path):
    """os.replace is the ABA primitive: it swaps the CONTENT AT A PATHNAME
    atomically, and it never opens the target for writing, so a 0444 file is no
    obstacle at all."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    snap, ident, _fd = m.snapshot_archive(str(src))
    imposter = _zip_with(tmp_path / "b.zip", "B-THE-REPLACEMENT")
    try:
        with pytest.raises(PermissionError):
            os.replace(str(imposter), _real(snap))
        assert hashlib.sha256(pathlib.Path(snap).read_bytes()).hexdigest() \
            == ident["sha256"], "the snapshot's bytes changed"
    finally:
        _purge(m)


@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_no_new_file_can_be_created_beside_the_snapshot(tmp_path):
    """The whole directory is sealed, not just the one name in it."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    snap, _, _fd = m.snapshot_archive(str(src))
    try:
        with pytest.raises(PermissionError):
            open(os.path.join(os.path.dirname(_real(snap)), "intruder"), "w").close()
    finally:
        _purge(m)


def test_the_sealed_snapshot_is_still_readable_and_openable_as_a_zip(tmp_path):
    """THE OVER-SENSITIVE DIRECTION. A seal that also blocked reading would
    pass every assertion above and break the band, verify and the summary --
    CLAUDE.md section 0 counts that as a soundness bug, not a safe default."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    snap, ident, _fd = m.snapshot_archive(str(src))
    try:
        assert pathlib.Path(snap).read_bytes() == src.read_bytes()
        with zipfile.ZipFile(snap) as zf:
            assert zf.namelist(), "the sealed snapshot has no readable members"
            assert zf.read("run_tests.py")
        # and the directory must still be traversable/listable
        assert os.listdir(os.path.dirname(_real(snap)))
    finally:
        _purge(m)


def test_a_sealed_snapshot_is_still_removable_by_its_owner(tmp_path):
    """Sealing must not defeat the cleanup this cut's predecessor added. A
    directory nothing can remove is a leak with better paperwork."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    snap, _, _fd = m.snapshot_archive(str(src))
    d = os.path.dirname(_real(snap))
    assert m._discard_tempdir(d) is True, (
        "the sealed snapshot directory could not be removed by its owner")
    assert not os.path.exists(d)
    assert d not in m._TEMPDIRS


# =========================================================================
# 2 | A RAISED OSError IS NOT A SUCCESSFUL CLEANUP
# =========================================================================

def _rmtree_that_removes_then_raises(real_rmtree):
    """A removal walk that reports failure.

    v3.66.1153 repoint: the subject is now `_rmtree_fd(fd)`, a descriptor-bound
    walk, so this can no longer "remove the path and then raise" -- there is no
    path. What it still models is the half that mattered: rmtree is NOT atomic,
    so a call can report failure, and a helper must not overrule that with a
    second, weaker observation.
    """
    def fake(fd, *a, **k):
        raise OSError(39, "Directory not empty")
    return fake


def test_bdcut_discard_reports_failure_when_rmtree_raises(tmp_path, monkeypatch):
    m = _load_bdcut()
    d = m._owned_tempdir("bdcut_probe_")
    real = shutil.rmtree
    monkeypatch.setattr(m, "_rmtree_fd", _rmtree_that_removes_then_raises(real))
    got = m._discard_tempdir(d)
    monkeypatch.undo()
    assert got is False, (
        "rmtree RAISED and _discard_tempdir returned True because the path "
        "happened to be gone -- that is a conclusion drawn from a failed "
        "measurement")
    assert d in m._TEMPDIRS, (
        "a directory whose removal raised was unregistered, so main()'s "
        "finally can no longer report it")
    if d in m._TEMPDIRS:
        m._TEMPDIRS.remove(d)
    shutil.rmtree(d, ignore_errors=True)


def test_footguns_discard_reports_failure_when_rmtree_raises(tmp_path, monkeypatch, capsys):
    m = _load_footguns()
    d = tempfile.mkdtemp(prefix="bdfg_probe_")
    real = shutil.rmtree
    monkeypatch.setattr(m, "_rmtree_fd", _rmtree_that_removes_then_raises(real))
    got = m._discard(d)
    err = capsys.readouterr().err
    monkeypatch.undo()
    shutil.rmtree(d, ignore_errors=True)
    assert got is False, (
        "rmtree RAISED and _discard returned True because the path happened "
        "to be gone")
    assert "NOT REMOVED" in err.upper(), (
        "a failed removal was silent:\n" + err[-400:])


def test_a_genuinely_absent_directory_is_still_a_success(tmp_path):
    """THE OVER-SENSITIVE DIRECTION, both tools. FileNotFoundError means there
    was nothing to remove, which IS success -- a fix that returned False for
    every OSError subclass would report a leak on every clean teardown."""
    mc = _load_bdcut()
    mf = _load_footguns()
    # For bd-cut the subject must be a directory it CREATED which has since
    # vanished. An arbitrary path it never made is REFUSED as of v3.66.1152 --
    # deliberately, because "no recorded identity" is unknown, not permission.
    d = mc._owned_tempdir("bdcut_vanished_")
    shutil.rmtree(d)
    assert mc._discard_tempdir(d) is True
    assert d not in mc._TEMPDIRS
    # bd-footguns keeps no identity register, so for it an absent path is
    # simply nothing to do.
    assert mf._discard(str(tmp_path / "never-existed")) is True


def test_a_normal_removal_still_succeeds_and_unregisters(tmp_path):
    mc = _load_bdcut()
    d = mc._owned_tempdir("bdcut_probe_")
    open(os.path.join(d, "f"), "w").close()
    assert mc._discard_tempdir(d) is True
    assert not os.path.exists(d)
    assert d not in mc._TEMPDIRS


# =========================================================================
# 3 | THE REFUSAL ITSELF, NOT ONLY THE BYTES
# =========================================================================

def _drive_resume(m, monkeypatch, tmp_path, src, on_band=None, on_verify=None):
    seen = {}
    work = tmp_path / "work"
    (work / "bulk_downloader").mkdir(parents=True, exist_ok=True)
    (work / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')
    (work / "anything.py").write_text("x = 1\n")

    def _band(zippath, suites, w, extracted=None):
        seen["band"] = zippath
        seen["extracted"] = extracted
        if on_band:
            on_band()
        seen["band_bytes"] = pathlib.Path(zippath).read_bytes()

    def _verify(w, z, pass_fds=()):
        seen["verify"] = z
        if on_verify:
            on_verify()
        seen["verify_bytes"] = pathlib.Path(z).read_bytes()

    monkeypatch.setattr(m, "step0_gate", lambda s, **k: [])
    monkeypatch.setattr(m, "band", _band)
    monkeypatch.setattr(m, "verify", _verify)
    monkeypatch.setattr(m, "max_summary", lambda z, b: seen.update(
        summary=z, summary_bytes=pathlib.Path(z).read_bytes()))
    rc = m.main(["--work", str(work), "--out", str(tmp_path / "o"),
                 "--resume-zip", str(src)])
    return rc, seen


def test_a_swap_during_verify_is_REFUSED_and_says_why(tmp_path, monkeypatch, capsys):
    """The half v3.66.1149 forgot to assert.

    Its swap-during-verify test proved only that the summary read the
    snapshot's bytes -- which the snapshot guarantees structurally -- and never
    asserted the run refused. Deleting the post-summary check left it GREEN.
    Assert the REASON as well as the code: bd-cut answers 3 for every step-0
    refusal, so `rc == 3` alone passes when any other guard fires.
    """
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    rc, seen = _drive_resume(m, monkeypatch, tmp_path, src,
                             on_verify=lambda: _zip_with(src, "B-DURING-VERIFY"))
    err = capsys.readouterr().err
    assert "verify" in seen and "summary" in seen, (
        "verify/summary did not run, so the post-summary window was never "
        "opened and this proves nothing")
    assert rc == 3, (
        f"the archive changed during verify and the run reported rc={rc}. The "
        "verdict describes a snapshot; the operator ships the external file.")
    assert "during verify/summary" in err, (
        "the refusal does not name WHICH window the archive moved in:\n"
        + err[-500:])
    assert "changed during this run" in err, err[-500:]


def test_a_swap_after_the_band_is_refused_before_verify_runs(tmp_path, monkeypatch, capsys):
    """The other window, asserted the same way, so the two refusals are
    distinguishable from each other rather than only from success."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    rc, seen = _drive_resume(m, monkeypatch, tmp_path, src,
                             on_band=lambda: _zip_with(src, "B-AFTER-BAND"))
    err = capsys.readouterr().err
    assert rc == 3, rc
    assert "after the band" in err, err[-500:]
    assert "verify" not in seen, "verify ran after the archive had moved"


def test_an_unchanged_archive_still_completes(tmp_path, monkeypatch):
    """THE OVER-SENSITIVE DIRECTION for both refusals above."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    rc, seen = _drive_resume(m, monkeypatch, tmp_path, src)
    assert rc == 0, rc
    assert seen["band"] == seen["verify"] == seen["summary"]


# =========================================================================
# 4 | THE FOUR UNPINNED CLAIMS
# =========================================================================

def test_the_identity_comes_from_the_open_descriptor_not_the_path(tmp_path, monkeypatch):
    """CLAIMED in the docstring: identity via os.fstat on the same descriptor
    the bytes were read through, 'so it cannot end up describing a different
    inode than the one that was copied'. Nothing tested it.

    Driven by making os.stat on that path FATAL: if the implementation reaches
    for the path a second time, it dies here instead of quietly describing
    whatever is at the name now.
    """
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    real_stat = os.stat
    target = os.path.realpath(str(src))

    def guarded(path, *a, **k):
        try:
            same = os.path.realpath(path) == target
        except TypeError:                      # an int fd, which is fstat-like
            same = False
        if same:
            raise AssertionError(
                "snapshot_archive called os.stat on the archive PATH; the "
                "identity must come from fstat on the descriptor it read")
        return real_stat(path, *a, **k)

    monkeypatch.setattr(os, "stat", guarded)
    try:
        snap, ident, _fd = m.snapshot_archive(str(src))
    finally:
        monkeypatch.undo()
    try:
        assert ident["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
        assert ident["size"] == src.stat().st_size
    finally:
        _purge(m)


def test_a_short_read_is_refused_rather_than_snapshotted(tmp_path, monkeypatch):
    """CLAIMED: 'a short read against the stat size is a refusal rather than a
    truncated snapshot'. Nothing exercised it, so the branch was unreachable
    evidence -- CLAUDE.md section 10: a branch nothing can reach is dead code
    that reads as a safety feature."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    real_open = open
    state = {"n": 0}

    def truncating_open(file, mode="r", *a, **k):
        fh = real_open(file, mode, *a, **k)
        if "b" in mode and "r" in mode and os.path.realpath(str(file)) == \
                os.path.realpath(str(src)):
            real_read = fh.read

            def short(n=-1):
                state["n"] += 1
                return b"" if state["n"] > 1 else real_read(64)
            fh.read = short
        return fh

    monkeypatch.setattr("builtins.open", truncating_open)
    try:
        with pytest.raises(Exception) as ei:
            m.snapshot_archive(str(src))
    finally:
        monkeypatch.undo()
    assert "changed size" in str(ei.value) or "read" in str(ei.value).lower(), (
        f"the short read was not refused by name: {ei.value!r}")
    _purge(m)


def test_a_failed_snapshot_leaves_no_directory_behind(tmp_path, monkeypatch):
    """CLAIMED by the BaseException handler. Nothing drove it."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    before_reg = list(m._TEMPDIRS)
    before_disk = set(pathlib.Path(tempfile.gettempdir()).glob("bdcut_archive_*"))

    real_open = open

    def exploding_open(file, mode="r", *a, **k):
        if "w" in mode:
            raise OSError(28, "No space left on device")
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr("builtins.open", exploding_open)
    try:
        with pytest.raises(OSError):
            m.snapshot_archive(str(src))
    finally:
        monkeypatch.undo()

    leaked = set(pathlib.Path(tempfile.gettempdir()).glob("bdcut_archive_*")) - before_disk
    assert not leaked, f"a failed snapshot leaked: {sorted(str(p) for p in leaked)}"
    assert list(m._TEMPDIRS) == before_reg, (
        "a failed snapshot left its directory registered even though it was "
        "removed")


def test_extraction_consumes_the_snapshot_not_the_external_path(tmp_path, monkeypatch):
    """CLAIMED: 'the only extract' comes from the owned snapshot. The handoff
    itself was never asserted -- 1149 checked what band/verify/summary got, and
    extraction happens BEFORE all three."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    seen = {}
    real_extract = m.extract_and_attest

    def spy(zippath):
        seen["path"] = zippath
        # RESOLVED HERE, WHILE THE DESCRIPTOR IS STILL OPEN. main() closes it
        # on the way out, and once it is closed /proc/self/fd/N no longer
        # resolves -- realpath then returns the path unchanged and the
        # assertion below silently measures the wrong string.
        seen["real"] = os.path.realpath(zippath)
        return real_extract(zippath)

    monkeypatch.setattr(m, "extract_and_attest", spy)
    rc, driven = _drive_resume(m, monkeypatch, tmp_path, src)
    assert rc == 0, rc
    assert "path" in seen, "extract_and_attest never ran"
    assert os.path.realpath(seen["path"]) != os.path.realpath(str(src)), (
        "extraction read the MUTABLE external archive; the gate would then "
        "certify a tree built from a file the band never saw")
    assert "bdcut_archive_" in seen["real"], seen["real"]
    assert os.path.realpath(seen["path"]) == os.path.realpath(driven["band"]), (
        "extraction and the band consumed different archives")


# =========================================================================
# 5 | THE LEAK INSTRUMENT ITSELF
# =========================================================================

def test_the_temp_root_erases_leaks_on_a_green_run(tmp_path):
    """WHY THE v3.66.1149 'zero leaks' PROOF WAS WORTHLESS, pinned so nobody
    repeats it.

    `tests/_tmproot.install()` points tempfile.tempdir at a per-run root and
    `finish()` rmtree's that root when exitstatus == 0. Two consequences, and
    both bit: a shell `ls -d /tmp/bdcut_*` cannot see anything a test leaks,
    because the family lives under the root; and after a GREEN run the root is
    gone, so the residue is unobservable even in the right place. Leak
    measurement therefore has to happen INSIDE the session, or with
    KEEP_TEST_TMPDIRS=1.
    """
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import _tmproot
    finally:
        sys.path.pop(0)
    src = pathlib.Path(_tmproot.__file__).read_text(encoding="utf-8")
    assert "tempfile.tempdir = _ROOT" in src, (
        "the temp root no longer redirects tempfile.tempdir; the reasoning "
        "below needs re-deriving")
    assert "KEEP_TEST_TMPDIRS" in src, (
        "the escape hatch that makes leaks observable is gone")
    # The live consequence, measured rather than read: inside this process the
    # temp dir is NOT the system one.
    #
    # ...UNLESS the escape hatch is set, which is the whole point of it. This
    # assertion is only meaningful under the DEFAULT configuration, and it
    # failed under KEEP_TEST_TMPDIRS=1 on its first outing -- an assertion
    # about a redirection, run in the one mode that deliberately disables the
    # redirection. Skip rather than weaken: a test that quietly passes in both
    # modes would no longer be measuring anything.
    if os.environ.get("KEEP_TEST_TMPDIRS") == "1":
        pytest.skip("KEEP_TEST_TMPDIRS=1 disables the redirection under test")
    assert pathlib.Path(tempfile.gettempdir()) != _tmproot.SYSTEM_TMP, (
        "tempfile.gettempdir() is the system temp dir, so a /tmp-based leak "
        "sweep would be measuring the right place -- re-check this reasoning")
    assert "bd-testrun-" in tempfile.gettempdir()


def test_the_temp_root_can_reclaim_a_sealed_directory(tmp_path):
    """THE SEAL'S BLAST RADIUS, and it reaches the suite's own reclamation.

    `shutil.rmtree(root, ignore_errors=True)` CANNOT remove a tree containing a
    read-only directory: it fails trying to unlink inside it, and the flag
    hides the failure. That call is exactly what `tests/_tmproot.finish()` runs
    at session end, so ONE sealed 0500 snapshot left anywhere under the per-run
    root defeats the whole reclamation and the root survives -- which is the
    15392-entries-in-/tmp problem _tmproot was built to fix, reintroduced by a
    hardening change three files away.

    MEASURED before the fix: a root holding one 0500 directory was still
    present after rmtree(ignore_errors=True) returned, and 23 such directories
    had accumulated under /tmp during this cut's own development.

    This is not hypothetical tidiness. bd-cut seals a directory on every
    --resume-zip run, and any abnormal exit -- a crash, a SIGKILL, a test that
    forgets to reclaim -- leaves one behind.
    """
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import _tmproot
    finally:
        sys.path.pop(0)

    root = tmp_path / "root"
    sealed = root / "bdcut_archive_probe"
    sealed.mkdir(parents=True)
    (sealed / "r.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    os.chmod(sealed / "r.zip", 0o444)
    os.chmod(sealed, 0o500)                      # exactly what bd-cut writes
    try:
        # PRECONDITION: the naive call really does fail here, or this test is
        # asserting against a hazard that does not exist on this filesystem.
        if not _IS_ROOT:
            shutil.rmtree(str(root), ignore_errors=True)
            assert root.exists(), (
                "rmtree(ignore_errors=True) removed a sealed tree on this "
                "filesystem, so there is no hazard to guard against here")
        remover = getattr(_tmproot, "_force_rmtree", None)
        assert remover is not None, (
            "_tmproot has no removal helper that can reclaim a read-only "
            "directory; finish() calls rmtree(ignore_errors=True), which "
            "cannot, so a single sealed snapshot leaks the entire per-run root")
        assert remover(str(root)) is True
        assert not root.exists(), "the sealed tree survived reclamation"
    finally:
        for p in (sealed, root):
            try:
                os.chmod(p, 0o700)
            except OSError:
                pass
        shutil.rmtree(str(root), ignore_errors=True)


def _sealed_root(tmp_path):
    root = tmp_path / "root"
    sealed = root / "bdcut_archive_probe"
    sealed.mkdir(parents=True)
    (sealed / "r.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    os.chmod(sealed / "r.zip", 0o444)
    os.chmod(sealed, 0o500)                      # exactly what bd-cut writes
    return root, sealed


def _unseal(root, sealed):
    for p in (sealed, root):
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass
    shutil.rmtree(str(root), ignore_errors=True)


@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_finish_ITSELF_reclaims_a_sealed_root(tmp_path):
    """The escape the first draft left open.

    Asserting that `_force_rmtree` exists and works does NOT assert that
    `finish()` calls it: a mutant restoring the old
    `rmtree(root, ignore_errors=True)` inside finish(), while leaving the
    helper defined and correct, kept the band green. bd-mutate caught that --
    review did not. So drive finish() itself.

    _ROOT and tempfile.tempdir are saved and restored because finish() sets
    tempfile.tempdir = None; leaving that in place would send every later
    mkdtemp in this session to the real /tmp, which is the leak the module
    exists to prevent, caused by the test for it.
    """
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import _tmproot
    finally:
        sys.path.pop(0)

    root, sealed = _sealed_root(tmp_path)
    saved_root = _tmproot._ROOT
    saved_tempdir = tempfile.tempdir
    saved_ident = getattr(_tmproot, '_ROOT_IDENT', None)
    try:
        _tmproot._ROOT = str(root)
        # v3.66.1153: reclamation is bound to the identity install()
        # recorded, so a hand-built root must supply one or it is
        # correctly UNKNOWN and refused.
        _st = os.lstat(str(root))
        _tmproot._ROOT_IDENT = (_st.st_dev, _st.st_ino)
        got = _tmproot.finish(0)
        assert not root.exists(), (
            "finish() left a root holding a sealed directory on disk -- one "
            "0500 snapshot defeats the whole per-run reclamation")
        assert got is True, "finish() reclaimed the root but reported failure"
    finally:
        _tmproot._ROOT = saved_root
        tempfile.tempdir = saved_tempdir
        if hasattr(_tmproot, '_ROOT_IDENT'):
            _tmproot._ROOT_IDENT = saved_ident
        _unseal(root, sealed)


@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_finish_still_keeps_artifacts_when_the_run_failed(tmp_path):
    """THE OVER-SENSITIVE DIRECTION, and it is a deliberate contract:
    `_tmproot.finish` keeps the root on a non-zero exit so a debugging
    directory is never deleted on the one run that needed it. A reclaimer that
    removed unconditionally would pass every assertion above."""
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import _tmproot
    finally:
        sys.path.pop(0)

    root, sealed = _sealed_root(tmp_path)
    saved_root = _tmproot._ROOT
    saved_tempdir = tempfile.tempdir
    saved_ident = getattr(_tmproot, '_ROOT_IDENT', None)
    try:
        _tmproot._ROOT = str(root)
        _st = os.lstat(str(root))
        _tmproot._ROOT_IDENT = (_st.st_dev, _st.st_ino)
        assert _tmproot.finish(1) is False
        assert root.exists(), "a FAILING run had its artifacts deleted"
    finally:
        _tmproot._ROOT = saved_root
        tempfile.tempdir = saved_tempdir
        if hasattr(_tmproot, '_ROOT_IDENT'):
            _tmproot._ROOT_IDENT = saved_ident
        _unseal(root, sealed)


def test_the_reclaimer_still_reports_a_genuine_failure(tmp_path):
    """THE OVER-SENSITIVE DIRECTION. A reclaimer that always returned True
    would pass the test above and re-hide every leak _tmproot exists to
    surface."""
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import _tmproot
    finally:
        sys.path.pop(0)
    remover = getattr(_tmproot, "_force_rmtree", None)
    assert remover is not None
    # A path that cannot be removed because it is not a directory we own the
    # parent of: /proc is kernel-backed and refuses removal outright.
    assert remover("/proc/self") is False, (
        "the reclaimer claimed success for a path it cannot possibly remove")


# ---- defects found by adversarial review of THIS cut's first implementation --

def test_the_reclaimer_never_chmods_outside_the_tree_it_was_given(tmp_path):
    """THE WORST BUG THIS CUT PRODUCED, and it was in the FIX, not the subject.

    `_force_rmtree`'s retry handler chmod'd `os.path.dirname(p)` -- one level
    ABOVE the tree it was asked to remove -- and `finish()` calls it with
    /tmp/bd-testrun-<rand>, so dirname is `/tmp`. On this box it is saved only
    by EPERM (/tmp is root-owned) and the bare `except OSError: pass` swallows
    that. Under CI or any container where pytest runs as root -- the Docker
    default -- the chmod SUCCEEDS and takes /tmp from 1777 to 0700, silently,
    breaking every other user and service on the box.

    Measured before the fix: `_force_rmtree('/proc/self')` attempted
    `chmod('/proc', 0o700)`. A cleanup helper must never touch a path it was
    not handed.
    """
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import _tmproot
    finally:
        sys.path.pop(0)

    attempted = []
    real_chmod = os.chmod

    def spy(path, mode, *a, **k):
        attempted.append(os.path.abspath(str(path)))
        return real_chmod(path, mode, *a, **k)

    # A tree whose ROOT is what fails, which is the case that reached upward.
    root = tmp_path / "root"
    (root / "child").mkdir(parents=True)
    os.chmod(root, 0o300)                     # unlistable: scandir(root) raises
    os.chmod = spy
    try:
        _tmproot._force_rmtree(str(root))
    finally:
        os.chmod = real_chmod
        try:
            real_chmod(root, 0o700)
        except OSError:
            pass
        shutil.rmtree(str(root), ignore_errors=True)

    outside = [p for p in attempted
               if not (p == os.path.abspath(str(root))
                       or p.startswith(os.path.abspath(str(root)) + os.sep))]
    assert not outside, (
        f"the reclaimer chmod'd path(s) outside the tree it was given: "
        f"{outside}. Called on a per-run temp root that is /tmp/bd-testrun-*, "
        "this chmods /tmp itself.")


def test_footguns_discard_reports_a_directory_that_survived_a_FileNotFoundError(tmp_path, monkeypatch, capsys):
    """A REGRESSION THIS CUT INTRODUCED, and its twin does not have it.

    Turning `except FileNotFoundError: pass` into `... return True` skipped the
    os.path.exists() verification entirely. shutil.rmtree raises
    FileNotFoundError with the TOP DIRECTORY STILL PRESENT whenever an entry is
    unlinked concurrently -- reproduced 40/40 by an adversarial pass -- and
    that race is live at both call sites, which run with cwd and BD_HOME inside
    the sandbox after a delegate that may have left grandchildren.

    So the pre-1150 code reported the leak and the "hardened" code returned
    success and printed nothing. bd-cut's _discard_tempdir falls through to the
    exists() check and gets this right; the two must agree.
    """
    m = _load_footguns()
    d = tempfile.mkdtemp(prefix="bdfg_fnf_")
    open(os.path.join(d, "still-here"), "w").close()

    def raiser(*a, **k):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(m, "_rmtree_fd", raiser)
    got = m._discard(d)
    err = capsys.readouterr().err
    monkeypatch.undo()
    survived = os.path.exists(d)
    shutil.rmtree(d, ignore_errors=True)

    assert survived, "the fixture did not build the shape under test"
    assert got is False, (
        "_discard returned success for a directory that is still on disk with "
        "contents -- FileNotFoundError from rmtree does not imply the tree went")
    assert "NOT REMOVED" in err.upper(), err[-400:]


@pytest.mark.skipif(_IS_ROOT, reason="root bypasses the mode bits under test")
def test_a_directory_reported_as_leaked_is_left_SEALED(tmp_path, monkeypatch):
    """v3.66.1150's first implementation unsealed UNCONDITIONALLY and only then
    tried to remove, so a directory it went on to report as leaked was left
    0700 with the snapshot readable inside it -- the leak it reports was also
    an UNPROTECTED leak. Unseal only as a retry, and put the seal back if the
    removal still will not happen."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    snap, _, _fd = m.snapshot_archive(str(src))
    d = os.path.dirname(_real(snap))
    before_mode = stat.S_IMODE(os.stat(d).st_mode)
    assert before_mode == 0o500, oct(before_mode)

    monkeypatch.setattr(
        m, "_rmtree_fd",
        lambda *a, **k: (_ for _ in ()).throw(OSError(39, "Directory not empty")))
    got = m._discard_tempdir(d)
    monkeypatch.undo()
    after_mode = stat.S_IMODE(os.stat(d).st_mode)
    _purge(m)

    assert got is False
    assert after_mode == before_mode, (
        f"a directory reported as NOT REMOVED was left at {oct(after_mode)}; "
        f"it was {oct(before_mode)} and nothing removed it, so the seal must "
        "still be there")


def test_a_renamed_and_recreated_snapshot_cannot_reach_a_consumer(tmp_path, monkeypatch, capsys):
    """THE SEAL DOES NOT BIND THE PATHNAME, and v3.66.1150's comment claimed it
    did. chmod(d, 0500) removes write INSIDE d, but d's own parent is still
    writable by the same uid, so the sealed directory can be renamed away and a
    fresh one created at the same path holding an imposter.

    v3.66.1150 answered that with a post-stage hash. v3.66.1151 replaced the
    answer: the consumers are handed a DESCRIPTOR-backed path, so a swap of the
    directory entry cannot reach them at all -- which is why this test no
    longer asserts a refusal. It asserts the stronger property. A refusal would
    have meant the swap was VISIBLE to a consumer and had to be caught after
    the fact; being unreachable is better than being detected.
    """
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    imposter = _zip_with(tmp_path / "imposter.zip", "B-IMPOSTER")

    def swap_the_snapshot():
        snap = seen_paths["band"]
        d = os.path.dirname(_real(snap))
        base = os.path.basename(_real(snap))
        os.rename(d, d + ".stashed")          # the parent is writable
        os.mkdir(d)
        # The imposter goes at the DIRECTORY ENTRY, not through the descriptor
        # (which is O_RDONLY -- writing through it is what a swap cannot do).
        shutil.copyfile(str(imposter), os.path.join(d, base))
        seen_paths["read_during_swap"] = pathlib.Path(snap).read_bytes()

    seen_paths = {}

    def _band(zippath, suites, w, extracted=None):
        seen_paths["band"] = zippath
        swap_the_snapshot()

    monkeypatch.setattr(m, "step0_gate", lambda s, **k: [])
    monkeypatch.setattr(m, "band", _band)
    monkeypatch.setattr(
        m, "verify",
        lambda w, z, pass_fds=(): seen_paths.setdefault("verify", z))
    monkeypatch.setattr(m, "max_summary", lambda z, b: seen_paths.setdefault("summary", z))
    work = tmp_path / "work"
    (work / "bulk_downloader").mkdir(parents=True)
    (work / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')
    try:
        rc = m.main(["--work", str(work), "--out", str(tmp_path / "o"),
                     "--resume-zip", str(src)])
    finally:
        for p in tmp_path.parent.glob("*"):
            pass
        _purge(m)
        for leftover in pathlib.Path(tempfile.gettempdir()).glob("bdcut_archive_*.stashed"):
            try:
                os.chmod(leftover, 0o700)
            except OSError:
                pass
            shutil.rmtree(leftover, ignore_errors=True)
    err = capsys.readouterr().err

    assert seen_paths.get("read_during_swap") == src.read_bytes(), (
        "a consumer read the IMPOSTER while the directory entry was swapped -- "
        "the descriptor binding is not in place")
    # rc is the CLEANUP code, not a refusal of the cut (v3.66.1152). No
    # consumer was affected -- that is the assertion above -- but the real
    # snapshot directory was renamed away, so cleanup genuinely could not
    # account for it, and since v3.66.1152 that costs an exit code. A step-0
    # refusal (3) here would be wrong: the cut itself was fine.
    assert rc == m.EXIT_CLEANUP_FAILED, (
        f"expected the cleanup code for an unaccountable snapshot directory, "
        f"got rc={rc}: {err[-300:]}")


_LEAKERS_1145 = (
    "tests/test_v3_66_1145_step0_fails_closed.py::"
    "test_cleanup_failures_are_reported_not_swallowed",
    "tests/test_v3_66_1145_step0_fails_closed.py::"
    "test_the_real_band_extracts_exactly_once",
)


@pytest.mark.slow
def test_the_1145_tests_that_deliberately_break_cleanup_remove_their_own_residue():
    """MEASURED BY RUNNING THEM, not by grepping their source.

    The first draft of this test asserted `"rmtree" in body`, and passed on the
    defective tree -- the leaking test contains that word BECAUSE IT PATCHES
    rmtree to fail. A predicate over the wrong part of the syntax is worse than
    a grep, because it looks rigorous (CLAUDE.md section 1).

    So this drives a real pytest subprocess with KEEP_TEST_TMPDIRS=1 and counts
    what lands in the SYSTEM temp dir. Both halves are load-bearing and
    _tmproot's own docstring records what happens without them: the escape
    hatch is what stops tempfile.tempdir being redirected, and the system dir
    is where the residue then appears. Measured at 4f48fc95: 3 directories.

    Both leakers are named because they leak for DIFFERENT reasons and one fix
    would not cover both -- one patches rmtree to fail, so its residue is
    deliberate; the other calls band() DIRECTLY, so main()'s finally, the only
    thing that removes the band's BD_HOME, never runs.
    """
    import subprocess
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import _tmproot
    finally:
        sys.path.pop(0)
    # THE CHILD GETS ITS OWN TMPDIR (v3.66.1153). This swept the SHARED system
    # temp directory, so anything else running on the box during the window was
    # attributed to the two subject tests -- observed live: a concurrent
    # reviewer's `bdcut_probeC2_*` failed this assertion. A private TMPDIR makes
    # the denominator exactly "what the child created", which is what the test
    # claims to measure, and removes the concurrency flake with it.
    import tempfile as _tf
    child_tmp = pathlib.Path(_tf.mkdtemp(prefix="bd1150_childtmp_",
                                         dir=str(_tmproot.SYSTEM_TMP)))

    def sweep():
        return set(child_tmp.glob("bdcut_*")) | set(child_tmp.glob("bdfg_*"))

    env = dict(os.environ)
    env["KEEP_TEST_TMPDIRS"] = "1"       # no redirection: residue is observable
    env["BD_DISABLE_KEEPALIVE"] = "1"
    env["TMPDIR"] = str(child_tmp)
    env.pop("BD_INSTALL_DIR", None)

    before = sweep()
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *_LEAKERS_1145,
         "-p", "no:randomly", "-q", "--timeout=180"],
        cwd=str(REPO), capture_output=True, text=True, timeout=600, env=env)
    leaked = sweep() - before
    for d in leaked:                      # never leave OUR measurement behind
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        shutil.rmtree(d, ignore_errors=True)

    # PRECONDITION: if the two tests did not actually run, "no residue" is a
    # statement about an empty denominator (CLAUDE.md section 6).
    assert "2 passed" in (r.stdout + r.stderr), (
        "the two subject tests did not both run and pass, so the residue count "
        "below means nothing:\n" + (r.stdout + r.stderr)[-1500:])
    shutil.rmtree(child_tmp, ignore_errors=True)
    assert not leaked, (
        f"{len(leaked)} directory(ies) survived the two 1145 tests: "
        f"{sorted(p.name for p in leaked)}. A test that deliberately breaks "
        "cleanup owns the residue it creates.")
