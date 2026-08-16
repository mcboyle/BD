"""The three removers act on an OBJECT, and say so about the object.

v3.66.1153 bound the top-level directory of each removal to the identity
recorded when it was created, and moved the walk onto a descriptor. That was
necessary and it was not sufficient, and the gap has one shape in both
directions:

  * IDENTITY STOPPED AT THE TOP. Every CHILD was opened and removed by NAME,
    with nothing carried from the moment the name was read. A foreign directory
    renamed onto a child pathname mid-walk had its contents recursively
    destroyed -- measured at 63be0464, victim inode nlink 0.

  * SUCCESS AND FAILURE WERE STILL READ OFF A PATHNAME. `lexists(d)` answered
    "already clean" before the work and "still there" after it, so an owned
    directory RENAMED AWAY reported success -- unregistered, no reason, payload
    intact -- while a correct removal whose freed name an unrelated object
    happened to reuse reported failure.

Both are the same error stated twice: the name is not the object. These tests
are therefore written at the level of OBJECTS -- which inode survived, which
inode died, what the tool said about the one it was given -- and never at the
level of which syscall was issued. A test that asserts call FORM passes for a
remover that resolves the right names for the wrong reasons; v3.66.1153 shipped
exactly such a test and `shutil.rmtree` already satisfied it, because CPython's
`_rmtree_safe_fd` is itself dir_fd-relative.

EVERY INJECTION ASSERTS THAT IT FIRED. CLAUDE.md section 0's rule about empty
denominators applies hardest to a fixture: "not flagged" and "nothing was there
to flag" are the same green. Four of the tests this file replaces passed over a
seam that never executed.

ONE MATRIX, THREE SUBJECTS. `bd-cut`, `tests/_tmproot.py` and `bd-footguns`
carry near-identical removers by necessity -- _tmproot may import nothing from
the repo, and the bd-* tools are standalone scripts -- so the copies can drift.
Running one behavioural matrix against all three is the mechanized answer:
drift becomes red rather than becoming a third implementation nobody compares.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import contextlib
import errno
import importlib.util
import io
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
from importlib.machinery import SourceFileLoader

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
BDCUT = REPO / "toolchain" / "bin" / "bd-cut"
BDFG = REPO / "toolchain" / "bin" / "bd-footguns"

# Refusal CODES, not prose. CLAUDE.md section 10: four bd-jobs mutants escaped
# because every refusal shared an exit code, so a test asserting the code passed
# whichever guard fired. The same is true of a test asserting "not removed":
# these codes name WHICH refusal, and a mutant that deletes one guard cannot
# sail through to the next.
R_RENAMED = "[renamed-away]"
R_FOREIGN = "[foreign-object]"
R_UNPROVEN = "[not-proven]"
R_NO_IDENT = "[no-identity]"
R_TOO_DEEP = "[too-deep]"


def _load(path, name):
    ld = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, ld)
    m = importlib.util.module_from_spec(spec)
    ld.exec_module(m)
    return m


def _force_rm(p):
    """TEARDOWN ONLY -- never the subject. Deliberately the crude pathname
    remover this file exists to replace, so a bug in the subject cannot make a
    test's own cleanup silently skip."""
    p = str(p)
    if os.path.islink(p) or os.path.isfile(p):
        with contextlib.suppress(OSError):
            os.unlink(p)
        return
    if os.path.isdir(p):
        subprocess.run(["chmod", "-R", "u+rwx", p], capture_output=True)
        shutil.rmtree(p, ignore_errors=True)


# --------------------------------------------------------------------------
# ONE ADAPTER PER SUBJECT. Each exposes the same four questions, so the matrix
# below is written once. `make()` registers exactly the way production does --
# through the tool's own creation path, never by hand -- because the identity
# and the descriptor the removal is bound to are established THERE, and a
# hand-built registration would test a shape the tool never produces.
# --------------------------------------------------------------------------
class _Subject:
    def __init__(self, name, module):
        self.name, self.m = name, module

    def make(self):        raise NotImplementedError
    def discard(self, d):  raise NotImplementedError
    def reason(self, d):   raise NotImplementedError
    def failure_is_accounted_for(self, d): raise NotImplementedError
    def reset(self):       pass


class _BdCut(_Subject):
    def make(self):
        return self.m._owned_tempdir("bdcut_1154_")

    def discard(self, d):
        return self.m._discard_tempdir(d)

    def reason(self, d):
        return str(self.m._LAST_DISCARD_ERROR.get(d) or "")

    def failure_is_accounted_for(self, d):
        return d in self.m._TEMPDIRS

    def reset(self):
        self.m._TEMPDIRS[:] = []
        self.m._TEMPDIR_IDENT.clear()
        self.m._LAST_DISCARD_ERROR.clear()
        for fd in list(getattr(self.m, "_TEMPDIR_FD", {}).values()):
            with contextlib.suppress(OSError):
                os.close(fd)
        getattr(self.m, "_TEMPDIR_FD", {}).clear()


class _Footguns(_Subject):
    def make(self):
        d, _env = self.m._sandbox()
        return d

    def discard(self, d):
        self._err = io.StringIO()
        with contextlib.redirect_stderr(self._err):
            return self.m._discard(d)

    def reason(self, d):
        return getattr(self, "_err", io.StringIO()).getvalue()

    def failure_is_accounted_for(self, d):
        return d in self.m._SANDBOX_IDENT

    def reset(self):
        self.m._SANDBOX_IDENT.clear()
        for fd in list(getattr(self.m, "_SANDBOX_FD", {}).values()):
            with contextlib.suppress(OSError):
                os.close(fd)
        getattr(self.m, "_SANDBOX_FD", {}).clear()
        if hasattr(self.m, "_CLEANUP_FAILURES"):
            self.m._CLEANUP_FAILURES[:] = []


class _TmpRoot(_Subject):
    """finish() is the removal, and it takes the run's exit status rather than
    a path -- so `discard` drives the real entry point and the adapter carries
    the saved module state."""

    def make(self):
        self._saved = (self.m._ROOT, self.m._ROOT_IDENT, self.m._LAST_FAILURE,
                       tempfile.tempdir, getattr(self.m, "_ROOT_FD", None),
                       getattr(self.m, "_ROOT_FD_PATH", None))
        self.m._ROOT = None
        keep = os.environ.pop("KEEP_TEST_TMPDIRS", None)
        try:
            root = self.m.install()
        finally:
            if keep is not None:
                os.environ["KEEP_TEST_TMPDIRS"] = keep
        assert root, "install() built no root -- the fixture has nothing to test"
        return root

    def discard(self, d):
        self._err = io.StringIO()
        with contextlib.redirect_stderr(self._err):
            return self.m.finish(0)

    def reason(self, d):
        return getattr(self, "_err", io.StringIO()).getvalue()

    def failure_is_accounted_for(self, d):
        return self.m.failed_root() == d

    def reset(self):
        saved = getattr(self, "_saved", None)
        if saved is None:
            return
        root, ident, failure, td, fd, fdpath = saved
        cur_fd = getattr(self.m, "_ROOT_FD", None)
        if cur_fd is not None and cur_fd != fd:
            with contextlib.suppress(OSError):
                os.close(cur_fd)
        self.m._ROOT, self.m._ROOT_IDENT, self.m._LAST_FAILURE = root, ident, failure
        tempfile.tempdir = td
        # BOTH HALVES, OR THE PAIR IS INCOHERENT. The descriptor is only used
        # when it was opened on the path being asked about, so restoring one
        # and not the other leaves the session's fd claiming a test's path --
        # which fails safe here, and is exactly the kind of half-restore whose
        # cost this file's own _install_root docstring already records.
        if hasattr(self.m, "_ROOT_FD"):
            self.m._ROOT_FD, self.m._ROOT_FD_PATH = fd, fdpath
        self._saved = None


@pytest.fixture(params=["bd-cut", "_tmproot", "bd-footguns"])
def subject(request):
    if request.param == "bd-cut":
        s = _BdCut("bd-cut", _load(BDCUT, "bd_cut_uut_1154"))
    elif request.param == "bd-footguns":
        s = _Footguns("bd-footguns", _load(BDFG, "bd_fg_uut_1154"))
    else:
        sys.path.insert(0, str(REPO / "tests"))
        try:
            import _tmproot
        finally:
            sys.path.pop(0)
        s = _TmpRoot("_tmproot", _tmproot)
    # EVERY PATH THIS FILE CREATES IS TRACKED HERE. `_tmproot`'s root lives in
    # the REAL system temp by construction, so a test that renames it away
    # leaves its sibling outside anything pytest reclaims -- and a battery
    # about leaked directories that leaks directories is CLAUDE.md section 0's
    # "the fix reproduces the shape of the defect", one file from the defect.
    records = []
    renamed_records = []
    s._ledger_fds = set()
    orig_make = s.make
    orig_rename = s.m._rename_noclobber
    ledger_stat = os.stat

    def record_fd(fd):
        st = os.fstat(fd)
        records.append((fd, (st.st_dev, st.st_ino)))
        s._ledger_fds.add(fd)

    def rename_noclobber(old, new, dir_fd, *args, **kwargs):
        result = orig_rename(old, new, dir_fd, *args, **kwargs)
        try:
            st = ledger_stat(new, dir_fd=dir_fd, follow_symlinks=False)
            parent = os.fstat(dir_fd)
            parent_path = os.readlink("/proc/self/fd/%d" % dir_fd)
            if parent_path.endswith(" (deleted)"):
                raise OSError(errno.ENOENT,
                              "renamed-object parent has no linked path")
            renamed_records.append((parent_path,
                                    (parent.st_dev, parent.st_ino),
                                    os.fsdecode(new), (st.st_dev, st.st_ino)))
        except OSError as error:
            raise AssertionError("could not record renamed-object identity: %s"
                                 % error) from error
        return result

    def make():
        d = orig_make()
        record_fd(os.open(d, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW))
        return d
    s.make = make
    s.also = lambda _path: None
    s.m._rename_noclobber = rename_noclobber
    try:
        yield s
    finally:
        s.m._rename_noclobber = orig_rename
        s.reset()
        failures = []
        for observer, expected in reversed(records):
            try:
                st = os.fstat(observer)
                if (st.st_dev, st.st_ino) != expected:
                    failures.append("observer identity changed: %r != %r" %
                                    ((st.st_dev, st.st_ino), expected))
                    continue
                if st.st_nlink == 0:
                    continue
                current = os.readlink("/proc/self/fd/%d" % observer)
                if current.endswith(" (deleted)"):
                    failures.append("linked object has deleted fd path: %r" % current)
                    continue
                now = os.lstat(current)
                if (now.st_dev, now.st_ino) != expected:
                    failures.append("path identity changed at %r" % current)
                    continue
                _force_rm(current)
            except OSError as error:
                failures.append("exact teardown failed: %s" % error)
            finally:
                with contextlib.suppress(OSError):
                    os.close(observer)
        for parent_path, parent_ident, name, expected in reversed(renamed_records):
            child = os.path.join(parent_path, name)
            try:
                parent = os.lstat(parent_path)
            except FileNotFoundError:
                continue
            except OSError as error:
                failures.append("recorded parent could not be checked: %s" % error)
                continue
            if (parent.st_dev, parent.st_ino) != parent_ident:
                failures.append("recorded parent identity changed at %r" % parent_path)
                continue
            try:
                now = os.lstat(child)
            except FileNotFoundError:
                continue
            except OSError as error:
                failures.append("recorded private path could not be checked: %s" % error)
                continue
            if (now.st_dev, now.st_ino) != expected:
                failures.append("recorded private identity changed at %r" % child)
                continue
            _force_rm(child)
        s._ledger_fds.clear()
        assert not failures, "; ".join(failures)


def test_tmproot_failed_path_accounting_is_exact(tmp_path):
    """A failure recorded for one root must not account for another path."""
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import _tmproot as t
    finally:
        sys.path.pop(0)
    recorded = str(tmp_path / "recorded-root")
    unrelated = str(tmp_path / "unrelated-root")
    saved = t._LAST_FAILURE
    try:
        t._LAST_FAILURE = recorded
        adapter = _TmpRoot("_tmproot", t)
        assert t.failed_root() == recorded, (
            "fixture: no exact failed root was recorded, so the adapter "
            "assertions have an empty denominator")
        assert adapter.failure_is_accounted_for(recorded)
        assert not adapter.failure_is_accounted_for(unrelated), (
            "an unrelated path matched merely because some _tmproot failure "
            "was recorded")
    finally:
        t._LAST_FAILURE = saved


def test_a_recycled_descriptor_cannot_authorize_cleanup_success(subject,
                                                                  tmp_path):
    """A saved fd number is not ownership proof after that number is reused."""
    d = subject.make()
    if subject.name == "bd-cut":
        owned_fd = subject.m._TEMPDIR_FD[d]
        recorded = subject.m._TEMPDIR_IDENT[d]
    elif subject.name == "bd-footguns":
        owned_fd = subject.m._SANDBOX_FD[d]
        recorded = subject.m._SANDBOX_IDENT[d]
    else:
        owned_fd = subject.m._ROOT_FD
        recorded = subject.m._ROOT_IDENT

    foreign_path = str(tmp_path / "foreign-unlinked-directory")
    os.mkdir(foreign_path)
    foreign_ident = _ident(foreign_path)
    os.close(owned_fd)
    foreign_fd = os.open(foreign_path,
                         os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    os.rmdir(foreign_path)
    try:
        foreign_before = os.fstat(foreign_fd)
        assert foreign_fd == owned_fd, (
            f"fixture: descriptor {owned_fd} was not reused; got {foreign_fd}")
        assert foreign_ident != recorded, (
            "fixture: the foreign object unexpectedly has the owned root's "
            "identity")
        assert (foreign_before.st_dev, foreign_before.st_ino) == foreign_ident
        assert foreign_before.st_nlink == 0, (
            "fixture: the foreign descriptor is not an unlinked object")
        assert os.path.isdir(d), (
            "fixture: the real owned root is absent before discard")

        got = subject.discard(d)
        root_removed = not os.path.lexists(d)
        try:
            foreign_after = os.fstat(foreign_fd)
            foreign_open = ((foreign_after.st_dev, foreign_after.st_ino)
                            == foreign_ident)
        except OSError:
            foreign_open = False
        accounted = subject.failure_is_accounted_for(d)
        assert got is root_removed, (
            "the cleanup verdict disagrees with the real owned root: "
            f"got={got} root_removed={root_removed} "
            f"foreign_open={foreign_open} accounted={accounted}")
        assert root_removed, "the real owned root was reported removed but remains"
        assert foreign_open, (
            "cleanup closed the unrelated descriptor whose number was recycled")
        assert not accounted, (
            "a successfully removed root remains in failure accounting")
    finally:
        try:
            current = os.fstat(foreign_fd)
        except OSError:
            current = None
        if (current is not None and
                (current.st_dev, current.st_ino) == foreign_ident):
            os.close(foreign_fd)
        _force_rm(d)


def _payload(d, name="loot.txt", body="DO NOT DELETE"):
    p = os.path.join(str(d), name)
    with open(p, "w") as fh:
        fh.write(body)
    return p


def _ident(p):
    st = os.lstat(str(p))
    return (st.st_dev, st.st_ino)


def _rmdir_swapper(target, swap):
    """An os.rmdir spy that fires on the call about a specific INODE.

    Keying on the NAME would tie every one of these tests to whatever the
    remover happens to call things, and the whole subject of this file is that
    a name is not an object. It also breaks silently: a remover that renames
    before it destroys -- which is the natural way to close E3 and E4 -- would
    make a name-keyed spy stop firing, and a spy that never fires is a test
    that passes over nothing.

    Returns (spy, fired). `swap` is called with (name, dir_fd) at the moment
    the doomed call is about to run.
    """
    real_rmdir, fired = os.rmdir, {"n": 0}

    def spy(name, *a, dir_fd=None, **k):
        if fired["n"] == 0 and dir_fd is not None:
            try:
                st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                hit = (st.st_dev, st.st_ino) == target
            except OSError:
                hit = False
            if hit:
                fired["n"] += 1
                swap(name, dir_fd)
        return real_rmdir(name, *a, dir_fd=dir_fd, **k)

    return spy, fired, real_rmdir


def _find_by_ident(parent, ident):
    """The entry under `parent` that IS `ident`, whatever it is called now.

    Every assertion about survival in this file has to ask this way: the
    removers rename before they destroy, so the name an object had when the
    test built it is precisely what the subject is entitled to change.
    """
    for nm in os.listdir(parent):
        try:
            if _ident(os.path.join(parent, nm)) == ident:
                return os.path.join(parent, nm)
        except OSError:
            continue
    return None


def _fds_for_ident(ident):
    """Open descriptor numbers currently bound to one exact object."""
    found = set()
    for name in os.listdir("/proc/self/fd"):
        try:
            fd = int(name)
            st = os.fstat(fd)
        except (OSError, ValueError):
            continue
        if (st.st_dev, st.st_ino) == ident:
            found.add(fd)
    return found


def test_recovery_validation_cannot_move_a_later_foreign_replacement(subject,
                                                                      tmp_path):
    """A pathname check cannot authorize the later pathname rename.

    The spy substitutes B only after the recovery helper has observed A at
    the private name.  A correct implementation must not subsequently move B
    to the public name and call that restoration of A.
    """
    arena = tmp_path / "recovery-race"
    arena.mkdir()
    public = arena / "owned"
    private = arena / "owned.bdrm-test"
    held = arena / "held-owned"
    foreign_source = arena / "foreign-source"
    public.write_text("owned")
    foreign_source.write_text("foreign")
    os.rename(public, private)
    owned_ident = _ident(private)
    foreign_ident = _ident(foreign_source)
    owned_fd = os.open(private, os.O_PATH | os.O_NOFOLLOW)
    parent_fd = os.open(arena, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    real_stat = os.stat
    fired = {"validated": 0, "substituted": 0}

    def stat_spy(path, *args, dir_fd=None, follow_symlinks=True, **kwargs):
        st = real_stat(path, *args, dir_fd=dir_fd,
                       follow_symlinks=follow_symlinks, **kwargs)
        if (fired["validated"] == 0 and dir_fd == parent_fd and
                str(path) == private.name and
                (st.st_dev, st.st_ino) == owned_ident):
            fired["validated"] += 1
            os.rename(private.name, held.name,
                      src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.rename(foreign_source.name, private.name,
                      src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            fired["substituted"] += 1
        return st

    subject.m.os.stat = stat_spy
    try:
        with pytest.raises(Exception):
            subject.m._recover_private(private.name, public.name, parent_fd,
                                       OSError(errno.EIO, "injected primary"),
                                       owned_ident, owned_fd)
    finally:
        subject.m.os.stat = real_stat
        os.close(parent_fd)
        os.close(owned_fd)

    assert fired == {"validated": 1, "substituted": 1}, fired
    assert _ident(private) == foreign_ident
    assert private.read_text() == "foreign"
    assert _ident(held) == owned_ident
    assert held.read_text() == "owned"
    assert not public.exists(), (
        "recovery moved a replacement that arrived after validation")


@pytest.mark.parametrize("kind", ["file", "symlink", "directory"])
def test_post_rename_stat_failure_does_not_leak_a_recovery_anchor(subject,
                                                                  tmp_path,
                                                                  kind):
    """Every descriptor acquired after a private rename has one owner."""
    d = subject.make()
    child = os.path.join(d, "child")
    if kind == "directory":
        os.mkdir(child)
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_text("target")
        os.symlink(target, child)
    else:
        pathlib.Path(child).write_text("payload")
    child_ident = _ident(child)
    baseline = _fds_for_ident(child_ident)
    real_stat = subject.m.os.stat
    fired = {"n": 0}

    def stat_spy(path, *args, dir_fd=None, follow_symlinks=True, **kwargs):
        st = real_stat(path, *args, dir_fd=dir_fd,
                       follow_symlinks=follow_symlinks, **kwargs)
        if (fired["n"] == 0 and dir_fd is not None and
                (st.st_dev, st.st_ino) == child_ident):
            fired["n"] += 1
            raise OSError(errno.EIO, "injected post-rename stat failure")
        return st

    subject.m.os.stat = stat_spy
    try:
        assert subject.discard(d) is False
        after = _fds_for_ident(child_ident) - subject._ledger_fds
    finally:
        subject.m.os.stat = real_stat

    assert fired["n"] == 1, "identity-keyed stat injection never fired"
    assert after == baseline, (
        "post-rename stat recovery leaked an object-bound descriptor: "
        f"before={baseline}, after={after}")
    assert "injected post-rename stat failure" in subject.reason(d)


# ==========================================================================
# A FOREIGN OBJECT IS NEVER DESTROYED, at any depth.
# ==========================================================================
def test_a_foreign_directory_swapped_onto_a_child_name_survives(subject, tmp_path):
    """E1, measured at 63be0464: the walk opened every child BY NAME, so a
    directory renamed onto a child pathname between the readdir and the open
    was entered and recursively emptied. Its inode came back nlink 0."""
    d = subject.make()
    child = os.path.join(d, "c")
    os.mkdir(child)
    _payload(child, "ours.txt")
    foreign = tempfile.mkdtemp(prefix="FOREIGN1154_", dir=str(tmp_path))
    loot = _payload(foreign, "victim.txt")
    foreign_id = _ident(foreign)

    real_scandir, fired = os.scandir, {"n": 0}

    def scandir(fd):
        ents = list(real_scandir(fd))
        if fired["n"] == 0 and any(e.name == "c" for e in ents):
            fired["n"] += 1
            os.rename(child, os.path.join(d, "c.moved"))
            os.rename(foreign, child)        # the stranger now occupies "c"
        return iter(ents)

    os.scandir = scandir
    try:
        got = subject.discard(d)
    finally:
        os.scandir = real_scandir

    # After the swap the foreign INODE lives at the child pathname, so that is
    # where its payload is looked for. (The first draft of this test also
    # renamed our child onto the foreign path, which moved the payload out from
    # under its own assertion -- a fixture that fails for a reason unrelated to
    # the subject is worth no more than one that passes for one.)
    assert fired["n"] == 1, "the swap never fired -- this test proved nothing"
    survivor = child if os.path.lexists(child) else None
    assert survivor is not None and _ident(survivor) == foreign_id, (
        "the foreign directory standing at a child pathname was removed "
        "outright")
    assert os.path.exists(os.path.join(child, "victim.txt")), (
        "a foreign directory standing at a child pathname had its CONTENTS "
        "destroyed; the walk opened the child by name with no identity "
        "carried from the entry it read")
    assert got is False, "a removal that met a foreign object reported success"
    assert R_FOREIGN in subject.reason(d), subject.reason(d)[-300:]
    _ = loot


def test_a_foreign_empty_directory_swapped_onto_a_child_name_survives(subject, tmp_path):
    """E3. rmdir can only remove an EMPTY directory, which is exactly the
    object a swap can leave at a child name once the real child has been
    emptied -- so the blast radius is small and the rule is not conditional."""
    d = subject.make()
    child = os.path.join(d, "c")
    os.mkdir(child)
    _payload(child, "ours.txt")
    foreign = tempfile.mkdtemp(prefix="FOREIGNEMPTY1154_", dir=str(tmp_path))
    foreign_id = _ident(foreign)

    # THE WINDOW A REAL ADVERSARY HAS is the one around the WELL-KNOWN name:
    # it can rename onto `c` at any moment, because `c` is a name it can see.
    # It cannot intercept our syscalls, so it cannot target a name we generate
    # and use within the same instruction stream. This fires in that real
    # window -- between the readdir that listed `c` and the remover's first
    # reach for it -- which is precisely where E3 was reproduced.
    real_scandir, fired = os.scandir, {"n": 0}

    def scandir(fd):
        ents = list(real_scandir(fd))
        if fired["n"] == 0 and any(e.name == "c" for e in ents):
            fired["n"] += 1
            os.rename(child, os.path.join(d, "c.moved"))
            os.rename(foreign, child)        # an EMPTY stranger takes "c"
        return iter(ents)

    os.scandir = scandir
    try:
        got = subject.discard(d)
    finally:
        os.scandir = real_scandir

    assert fired["n"] == 1, "the swap never fired -- this test proved nothing"
    survivor = _find_by_ident(d, foreign_id) if os.path.isdir(d) else None
    assert survivor is not None, (
        "the foreign EMPTY directory swapped onto a child name was removed. "
        "rmdir can only remove an EMPTY directory, which is exactly what a "
        "swap leaves once the real child has been emptied -- so this is the "
        "cheapest wrong-object deletion available and it must still refuse")
    assert got is False
    _force_rm(d)


def test_a_foreign_directory_swapped_onto_the_top_name_survives(subject, tmp_path):
    """E4: the top-level stat-to-rmdir window. Reviewer A measured 60 wrong
    objects in 60,000 races; this fires it deterministically instead."""
    d = subject.make()
    base = os.path.basename(d)
    # EMPTY ON PURPOSE. rmdir removes only an empty directory, so a foreign
    # directory WITH contents is refused by the kernel and the test would pass
    # without the fix -- which is exactly how the first draft of this test
    # passed at 63be0464 while the escape was live.
    foreign = tempfile.mkdtemp(prefix="FOREIGNTOP1154_", dir=str(tmp_path))
    foreign_id = _ident(foreign)

    parent = os.path.dirname(d)
    subject.also(d + ".moved")

    # FIRED IN THE REAL WINDOW: at the moment the remover first reaches for the
    # well-known basename in the parent. That is the only instant an adversary
    # who can see `/tmp` can act on, and it is exactly where E4 was reproduced
    # (60 wrong-object deletions in 60,000 races, measured by review).
    #
    # SPIED ON THE MODULE'S OWN RENAME, not on os.rename: the noclobber rename
    # reaches renameat2 through ctypes and never touches os.rename at all on
    # the path that matters, so an os.rename spy silently never fires -- which
    # reads exactly like a pass. If a future implementation has no such seam
    # this test FAILS with "never fired" rather than quietly passing.
    real_seam, fired = subject.m._rename_noclobber, {"n": 0}

    def spy_seam(old, new, dir_fd):
        if fired["n"] == 0 and str(old) == base:
            fired["n"] += 1
            os.rename(d, d + ".moved")
            os.rename(foreign, d)
        return real_seam(old, new, dir_fd)

    subject.m._rename_noclobber = spy_seam
    try:
        got = subject.discard(d)
    finally:
        subject.m._rename_noclobber = real_seam

    assert fired["n"] == 1, "the swap never fired -- this test proved nothing"
    assert _find_by_ident(parent, foreign_id) is not None, (
        "the foreign directory swapped onto the owned top-level name was "
        "destroyed: the destructive call still named a path an adversary can "
        "see and target")
    assert got is False
    assert R_FOREIGN in subject.reason(d), (
        "the refusal does not say a FOREIGN object was found; a generic "
        "[not-proven] here reads as a transient failure rather than as "
        "someone else's directory standing at our name: "
        + subject.reason(d)[-300:])


# ==========================================================================
# THE VERDICT IS ABOUT OUR INODE, NOT ABOUT THE PATHNAME.
# ==========================================================================
def test_an_owned_directory_renamed_away_is_a_reported_failure(subject):
    """E2, and the plainest statement of the whole defect class: nothing is at
    the pathname, and the tool answered "clean". The tree is intact somewhere
    else, unregistered, with no reason recorded and nothing that will ever
    collect it."""
    d = subject.make()
    loot = _payload(d)
    moved = d + ".moved"
    os.rename(d, moved)
    try:
        got = subject.discard(d)
        assert os.path.exists(loot.replace(d, moved)), "fixture: the tree moved"
        assert got is False, (
            "an owned directory that was renamed away -- payload intact, "
            "nothing at the recorded path -- was reported as successfully "
            "removed")
        assert R_RENAMED in subject.reason(d), subject.reason(d)[-300:]
        assert subject.failure_is_accounted_for(d), (
            "an unaccounted-for directory was unregistered, destroying the "
            "only record that it exists")
    finally:
        _force_rm(moved)


def test_a_known_foreign_top_name_is_never_moved_by_held_root_cleanup(subject):
    """A held owned inode does not authorize touching its stale public name."""
    d = subject.make()
    owned = _ident(d)
    _payload(d, body="owned top payload")
    moved = d + ".owned-elsewhere"
    os.rename(d, moved)
    os.mkdir(d)
    foreign = _ident(d)
    _payload(d, body="foreign top payload")
    foreign_fd = os.open(d, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        got = subject.discard(d)
        assert got is False
        assert _ident(d) == foreign, "the known foreign public object was moved"
        assert pathlib.Path(d, "loot.txt").read_text() == "foreign top payload"
        assert _ident(moved) == owned
        assert pathlib.Path(moved, "loot.txt").read_text() == "owned top payload"
        assert subject.failure_is_accounted_for(d)
        why = subject.reason(d)
        assert R_FOREIGN in why and moved in why, why[-400:]
    finally:
        try:
            st = os.fstat(foreign_fd)
            if st.st_nlink:
                current = os.readlink("/proc/self/fd/%d" % foreign_fd)
                now = os.lstat(current)
                assert (now.st_dev, now.st_ino) == foreign
                _force_rm(current)
        finally:
            os.close(foreign_fd)


def test_an_unrelated_object_reusing_the_freed_name_is_not_a_failure(subject):
    """THE OVER-SENSITIVE DIRECTION, which CLAUDE.md section 0 counts as a
    soundness bug and not a safe default. Our object is proven gone; something
    else now occupies the name it used to have. That is not our failure, and a
    remover that reports it becomes a remover nobody believes."""
    d = subject.make()
    real_rmdir, fired, owned_id = os.rmdir, {"n": 0}, _ident(d)

    def rmdir(name, *a, dir_fd=None, **k):
        # KEYED ON THE INODE, AFTER THE FACT: the stranger is created only once
        # the call that removed OUR object has returned, whatever name that
        # call used.
        hit = False
        if dir_fd is not None:
            try:
                st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                hit = (st.st_dev, st.st_ino) == owned_id
            except OSError:
                pass
        out = real_rmdir(name, *a, dir_fd=dir_fd, **k)
        if hit and not os.path.lexists(d):
            fired["n"] += 1
            os.mkdir(d)
        return out

    os.rmdir = rmdir
    try:
        got = subject.discard(d)
    finally:
        os.rmdir = real_rmdir
    assert fired["n"] == 1, "the stranger was never created -- nothing tested"
    assert got is True, (
        "a correct removal was reported as a failure because an unrelated "
        "object reused the freed pathname: " + subject.reason(d)[-300:])
    _force_rm(d)


def test_a_clean_owned_directory_is_still_removed(subject):
    """The control. Every test above is about refusing; this is the one that
    fails if the fix is "refuse everything"."""
    d = subject.make()
    os.makedirs(os.path.join(d, "a", "b"))
    _payload(os.path.join(d, "a", "b"))
    _payload(d)
    got = subject.discard(d)
    assert got is True, subject.reason(d)[-300:]
    assert not os.path.lexists(d)
    assert not subject.failure_is_accounted_for(d)


def test_a_removal_that_took_the_wrong_object_refuses(subject):
    """THE IRREDUCIBLE RESIDUAL, AND THE PROOF THAT CATCHES IT.

    Linux has no funlinkat: the terminal unlink of a directory is `rmdir` of a
    NAME, so no implementation can make that step atomic with respect to the
    identity it just verified. v3.66.1154 moves the call onto a name generated
    from os.urandom and used within the same instruction stream, which puts it
    out of reach of any adversary that has to LOOK for a name -- but not out
    of reach of the omniscient one below, which is handed the name by
    intercepting the syscall itself. No real actor can do that; the test grants
    it deliberately, because what must hold even then is that the wrong-object
    deletion is DETECTED and never reported as success.

    `st_nlink == 0` on the descriptor held since creation is that detection,
    and it is deliberately separate from the identity check that precedes the
    rmdir: with only one of the two present the tool deletes an object it never
    made and reports success, and until now each was the other's only backstop.

    The realistic window -- an adversary racing the WELL-KNOWN name -- is
    covered by the two tests above, which require the foreign object to survive
    untouched rather than merely to be noticed afterwards."""
    d = subject.make()
    parent, owned_id = os.path.dirname(d), _ident(d)

    def swap(name, dir_fd):
        # our object slips aside and an empty stranger takes the name the
        # remover is about to rmdir -- whatever name that has become
        os.rename(name, str(name) + ".hidden",
                  src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.mkdir(name, dir_fd=dir_fd)

    spy, fired, real_rmdir = _rmdir_swapper(owned_id, swap)
    os.rmdir = spy
    try:
        got = subject.discard(d)
    finally:
        os.rmdir = real_rmdir
    survivor = _find_by_ident(parent, owned_id)
    try:
        assert fired["n"] == 1, "the swap never fired -- this test proved nothing"
        assert survivor is not None, "fixture: our object did not survive the swap"
        assert got is False, (
            "the removal reported success while the object it was given is "
            "still on disk -- the entry that was unlinked belonged to "
            "something else")
        assert R_UNPROVEN in subject.reason(d), (
            "the refusal must be the nlink PROOF failing, not some other "
            "guard firing first -- an `or` across two codes is the shared "
            "refusal CLAUDE.md section 10 records four bd-jobs mutants "
            "escaping through: " + subject.reason(d)[-300:])
    finally:
        if survivor:
            _force_rm(survivor)


# ==========================================================================
# PERMISSIONS ARE PUT BACK ON EVERY PATH THAT LEAVES THE OBJECT BEHIND.
# ==========================================================================
def test_the_relaxation_is_fchmod_on_a_descriptor_and_never_chmod_on_a_path(subject):
    """S1 REPAIRED. The test this replaces spied `os.chmod` while the retry
    used `os.fchmod` only, so its assertion ran over an EMPTY denominator and
    could not have failed. The repair is the same two lines every fixture in
    this file carries: assert the seam FIRED, then assert what it did."""
    d = subject.make()
    inner = os.path.join(d, "sealed")
    os.mkdir(inner)
    _payload(inner)
    os.chmod(inner, 0o500)

    real_fchmod, real_chmod = os.fchmod, os.chmod
    seen = {"fchmod": [], "chmod": []}

    def spy_fchmod(fd, mode):
        seen["fchmod"].append(mode)
        return real_fchmod(fd, mode)

    def spy_chmod(path, mode, *a, **k):
        seen["chmod"].append(str(path))
        return real_chmod(path, mode, *a, **k)

    os.fchmod, os.chmod = spy_fchmod, spy_chmod
    try:
        got = subject.discard(d)
    finally:
        os.fchmod, os.chmod = real_fchmod, real_chmod

    assert seen["fchmod"], (
        "no relaxation happened at all -- the fixture did not build a sealed "
        "directory the remover had to unseal, so nothing here is evidence")
    assert not seen["chmod"], (
        "the remover relaxed a PATH; os.chmod follows symlinks, which is the "
        f"escape v3.66.1152 closed: {seen['chmod']}")
    assert got is True


@pytest.mark.parametrize("where", ["top", "child"])
def test_a_directory_left_behind_is_left_no_less_protected(subject, where):
    """E7 and E7b. A directory reported as leaked must not ALSO be left more
    open than it was found: `fchmod(fd, 0o700)` is a retry, and the retry has
    to be undone when it did not buy the removal.

    Measured at 63be0464: the top-level mode was restored on two of four
    refusal paths, and the CHILD-level relaxation was restored on none."""
    d = subject.make()
    # THE SEAL MUST ACTUALLY BLOCK THE FIRST WALK, or no relaxation happens and
    # the assertion below runs over an empty denominator. That is precisely how
    # v3.66.1150's `..._is_left_SEALED` test passed at 63be0464: it injected
    # OSError(ENOTEMPTY), which is caught before the PermissionError arm, so
    # nothing was ever unsealed and "the mode is unchanged" was trivially true.
    if where == "child":
        target = os.path.join(d, "sealed")
        os.mkdir(target)
        os.mkdir(os.path.join(target, "inner"))
    else:
        target = d
        _payload(d)
    os.chmod(target, 0o500)
    before = stat.S_IMODE(os.lstat(target).st_mode)
    assert before == 0o500, oct(before)

    real_fchmod, relaxed = os.fchmod, {"n": 0}

    def spy_fchmod(fd, mode):
        relaxed["n"] += 1
        return real_fchmod(fd, mode)

    # ...and the removal must then fail AFTER that relaxation. Keyed on the
    # INODE whose mode we are checking, not on a name: a remover that renames
    # before it destroys would make a name-keyed injection stop firing, and a
    # silent skip reads exactly like a pass.
    doomed_id = _ident(target)

    def boom(name, dir_fd):
        raise OSError(errno.EIO, "injected I/O error")

    spy_rmdir, fired, real_rmdir = _rmdir_swapper(doomed_id, boom)
    os.rmdir, os.fchmod = spy_rmdir, spy_fchmod
    try:
        got = subject.discard(d)
    finally:
        os.rmdir, os.fchmod = real_rmdir, real_fchmod

    assert relaxed["n"] >= 1, (
        "nothing was relaxed -- the seal did not block the walk, so this test "
        "has no relaxation to check was undone")
    assert fired["n"] >= 1, "the failure was never injected -- nothing tested"
    assert got is False
    search_parent = os.path.dirname(d) if where == "top" else d
    survivor = _find_by_ident(search_parent, doomed_id)
    after = (stat.S_IMODE(os.lstat(survivor).st_mode)
             if survivor is not None else None)
    assert after is not None, "the object was removed despite the injected failure"
    assert after == before, (
        f"a directory reported as NOT REMOVED was left at {oct(after)}; it was "
        f"{oct(before)} and nothing removed it, so the relaxation this "
        "remover applied as a retry was never undone")


def test_child_open_denial_retains_the_validated_private_object(subject):
    """A failed readable acquisition leaves an honestly reported residue."""
    d = subject.make()
    child = os.path.join(d, "child")
    payload = os.path.join(child, "payload")
    os.mkdir(child)
    with open(payload, "wb") as f:
        f.write(b"child-open payload")
    child_ident = _ident(child)
    real_open, fired = os.open, {"n": 0}

    def deny_child_open(path, flags, mode=0o777, *, dir_fd=None):
        if str(path).startswith("/proc/self/fd/"):
            try:
                st = os.fstat(int(str(path).rsplit("/", 1)[1]))
            except OSError:
                pass
            else:
                if (st.st_dev, st.st_ino) == child_ident:
                    fired["n"] += 1
                    raise PermissionError(errno.EACCES,
                                          "injected child-open denial")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    os.open = deny_child_open
    try:
        got = subject.discard(d)
    finally:
        os.open = real_open

    assert fired["n"] >= 1, (
        "the recorded child identity was never denied after its private rename")
    assert got is False
    survivor = _find_by_ident(d, child_ident)
    assert survivor is not None and survivor != child
    with open(os.path.join(survivor, "payload"), "rb") as f:
        assert f.read() == b"child-open payload"
    assert os.path.basename(survivor) in subject.reason(d)
    assert subject.failure_is_accounted_for(d)
    assert "injected child-open denial" in subject.reason(d), subject.reason(d)[-300:]


@pytest.mark.parametrize("mode", [0o000, 0o100, 0o200])
def test_an_unreadable_owned_child_is_removed_by_object_identity(subject, mode):
    d = subject.make()
    child = os.path.join(d, "child")
    os.mkdir(child)
    _payload(child)
    child_ident = _ident(child)
    held = os.open(child, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    os.chmod(child, mode)
    try:
        fd = os.open(child, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except PermissionError as e:
        assert e.errno == errno.EACCES
    else:
        os.close(fd)
        pytest.fail(f"ordinary directory open was not denied at mode {mode:#05o}")

    try:
        got = subject.discard(d)
        held_after = os.fstat(held)
    finally:
        os.close(held)

    assert got is True, subject.reason(d)[-300:]
    assert not os.path.lexists(child)
    assert not subject.failure_is_accounted_for(d)
    assert child_ident is not None
    assert (held_after.st_dev, held_after.st_ino) == child_ident
    assert held_after.st_nlink == 0


def test_post_rename_stat_failure_retains_the_owned_child(subject):
    d = subject.make()
    child = os.path.join(d, "child")
    payload = os.path.join(child, "payload")
    os.mkdir(child)
    with open(payload, "wb") as f:
        f.write(b"stat-denial payload")
    child_ident = _ident(child)
    real_stat, fired = os.stat, {"n": 0}

    def deny_private_stat(path, *args, **kwargs):
        dir_fd = kwargs.get("dir_fd")
        if dir_fd is not None and ".bdrm-" in os.fsdecode(path):
            st = real_stat(path, *args, **kwargs)
            if (st.st_dev, st.st_ino) == child_ident:
                fired["n"] += 1
                raise PermissionError(errno.EACCES,
                                      "injected post-rename stat denial")
            return st
        return real_stat(path, *args, **kwargs)

    os.stat = deny_private_stat
    try:
        got = subject.discard(d)
    finally:
        os.stat = real_stat

    assert fired["n"] >= 1, "the recorded private child was never denied stat"
    assert got is False
    survivor = _find_by_ident(d, child_ident)
    assert survivor is not None and survivor != child
    with open(os.path.join(survivor, "payload"), "rb") as f:
        assert f.read() == b"stat-denial payload"
    assert subject.failure_is_accounted_for(d)
    assert "injected post-rename stat denial" in subject.reason(d), subject.reason(d)[-300:]


def test_child_open_denial_reports_a_foreign_original_and_private_owned_child(subject):
    d = subject.make()
    child = os.path.join(d, "child")
    owned_payload = os.path.join(child, "owned-payload")
    os.mkdir(child)
    with open(owned_payload, "wb") as f:
        f.write(b"owned child")
    owned_ident = _ident(child)
    real_open, fired, foreign = os.open, {"n": 0}, {}

    def deny_after_foreign_arrives(path, flags, mode=0o777, *, dir_fd=None):
        if str(path).startswith("/proc/self/fd/"):
            st = os.fstat(int(str(path).rsplit("/", 1)[1]))
            if (st.st_dev, st.st_ino) == owned_ident:
                fired["n"] += 1
                if not foreign:
                    os.mkdir(child)
                    foreign_fd = real_open(os.path.join(child, "foreign-payload"),
                                           os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                           0o600)
                    try:
                        os.write(foreign_fd, b"foreign child")
                    finally:
                        os.close(foreign_fd)
                    fst = os.stat(child, follow_symlinks=False)
                    foreign["ident"] = (fst.st_dev, fst.st_ino)
                raise PermissionError(errno.EACCES,
                                      "injected child-open denial with foreign original")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    os.open = deny_after_foreign_arrives
    try:
        got = subject.discard(d)
    finally:
        os.open = real_open

    assert fired["n"] >= 1, "the recorded private child was never denied open"
    assert foreign, "the foreign replacement was never created"
    assert got is False
    assert _ident(child) == foreign["ident"]
    with open(os.path.join(child, "foreign-payload"), "rb") as f:
        assert f.read() == b"foreign child"
    private = [name for name in os.listdir(d) if name.startswith("child.bdrm-")]
    assert len(private) == 1, private
    private_path = os.path.join(d, private[0])
    assert _ident(private_path) == owned_ident
    with open(os.path.join(private_path, "owned-payload"), "rb") as f:
        assert f.read() == b"owned child"
    assert subject.failure_is_accounted_for(d)
    why = subject.reason(d)
    assert "injected child-open denial with foreign original" in why, why[-400:]
    assert private[0] in why, why[-400:]


def test_failure_after_unreadable_child_relaxation_restores_exact_mode(subject):
    d = subject.make()
    child = os.path.join(d, "child")
    os.mkdir(child)
    _payload(child)
    child_ident, original_mode = _ident(child), 0o100
    os.chmod(child, original_mode)
    try:
        fd = os.open(child, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except PermissionError as e:
        assert e.errno == errno.EACCES
    else:
        os.close(fd)
        pytest.fail("ordinary directory open was not denied before relaxation")
    real_scandir, fired, observed_modes = os.scandir, {"n": 0}, []

    def fail_object_bound_walk(path):
        if isinstance(path, int):
            st = os.fstat(path)
            if (st.st_dev, st.st_ino) == child_ident:
                fired["n"] += 1
                observed_modes.append(stat.S_IMODE(st.st_mode))
                raise OSError(errno.EIO, "injected failure after child relaxation")
        return real_scandir(path)

    os.scandir = fail_object_bound_walk
    try:
        got = subject.discard(d)
    finally:
        os.scandir = real_scandir

    assert fired["n"] >= 1, "the relaxed child descriptor never reached the walk"
    assert all(mode == 0o700 for mode in observed_modes), observed_modes
    assert got is False
    survivor = _find_by_ident(d, child_ident)
    assert survivor is not None
    assert stat.S_IMODE(os.lstat(survivor).st_mode) == original_mode
    assert subject.failure_is_accounted_for(d)
    assert "injected failure after child relaxation" in subject.reason(d), subject.reason(d)[-300:]


def test_top_level_post_rename_stat_failure_reports_private_owned_root(subject):
    d = subject.make()
    ident = _ident(d)
    _payload(d, body="top stat payload")
    real_walk, real_stat, fired = subject.m._rmtree_fd, os.stat, {"n": 0}
    subject.m._rmtree_fd = lambda fd, dev, depth=0: None

    def fail_stat(path, *a, **k):
        st = real_stat(path, *a, **k)
        if (not fired["n"] and k.get("dir_fd") is not None and
                ".bdrm-" in os.fsdecode(path) and
                (st.st_dev, st.st_ino) == ident):
            fired["n"] += 1
            raise OSError(errno.EIO, "injected top private stat failure")
        return st

    os.stat = fail_stat
    try:
        got = subject.discard(d)
    finally:
        os.stat, subject.m._rmtree_fd = real_stat, real_walk
    assert fired["n"] == 1
    assert got is False
    survivor = _find_by_ident(os.path.dirname(d), ident)
    assert survivor is not None and survivor != d
    assert os.path.basename(survivor) in subject.reason(d)
    assert "injected top private stat failure" in subject.reason(d)


def test_top_level_rmdir_collision_reports_private_owned_root(subject):
    d = subject.make()
    ident = _ident(d)
    real_walk, real_rmdir, fired, foreign = subject.m._rmtree_fd, os.rmdir, {"n": 0}, {}
    subject.m._rmtree_fd = lambda fd, dev, depth=0: None

    def fail_rmdir(path, *a, **k):
        st = os.stat(path, dir_fd=k.get("dir_fd"), follow_symlinks=False)
        if ".bdrm-" in os.fsdecode(path) and (st.st_dev, st.st_ino) == ident:
            fired["n"] += 1
            os.mkdir(os.path.basename(d), dir_fd=k["dir_fd"])
            fst = os.stat(os.path.basename(d), dir_fd=k["dir_fd"], follow_symlinks=False)
            foreign["ident"] = (fst.st_dev, fst.st_ino)
            raise OSError(errno.EIO, "injected top rmdir failure")
        return real_rmdir(path, *a, **k)

    os.rmdir = fail_rmdir
    try:
        got = subject.discard(d)
    finally:
        os.rmdir, subject.m._rmtree_fd = real_rmdir, real_walk
    assert fired["n"] == 1 and got is False
    assert _ident(d) == foreign["ident"]
    private = [n for n in os.listdir(os.path.dirname(d)) if n.startswith(os.path.basename(d) + ".bdrm-")]
    assert len(private) == 1 and _ident(os.path.join(os.path.dirname(d), private[0])) == ident
    assert private[0] in subject.reason(d)


@pytest.mark.parametrize("stage", ["stat", "rmdir"])
def test_top_private_baseexception_restores_mode_and_propagates(subject, stage):
    d = subject.make()
    ident = _ident(d)
    _payload(d, body="top interrupt payload")
    os.chmod(d, 0o500)
    real_walk = subject.m._rmtree_fd
    real_stat, real_rmdir = os.stat, os.rmdir
    walked, fired = {"n": 0}, {"n": 0}

    def force_relax(fd, dev, depth=0):
        st = os.fstat(fd)
        assert (st.st_dev, st.st_ino) == ident
        walked["n"] += 1
        if walked["n"] == 1:
            raise PermissionError(errno.EACCES, "force top relaxation")

    def interrupt_stat(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if (stage == "stat" and fired["n"] == 0 and
                kwargs.get("dir_fd") is not None and
                (st.st_dev, st.st_ino) == ident):
            fired["n"] += 1
            raise KeyboardInterrupt("injected top private stat interrupt")
        return st

    def interrupt_rmdir(path, *args, **kwargs):
        st = real_stat(path, dir_fd=kwargs.get("dir_fd"),
                       follow_symlinks=False)
        if (stage == "rmdir" and fired["n"] == 0 and
                (st.st_dev, st.st_ino) == ident):
            fired["n"] += 1
            raise KeyboardInterrupt("injected top private rmdir interrupt")
        return real_rmdir(path, *args, **kwargs)

    subject.m._rmtree_fd = force_relax
    os.stat, os.rmdir = interrupt_stat, interrupt_rmdir
    try:
        with pytest.raises(KeyboardInterrupt, match=f"injected top private {stage} interrupt"):
            subject.discard(d)
    finally:
        subject.m._rmtree_fd = real_walk
        os.stat, os.rmdir = real_stat, real_rmdir
    survivor = _find_by_ident(os.path.dirname(d), ident)
    assert walked["n"] == 2 and fired["n"] == 1
    assert survivor is not None and survivor != d
    assert stat.S_IMODE(os.lstat(survivor).st_mode) == 0o500
    assert pathlib.Path(survivor, "loot.txt").read_text() == "top interrupt payload"
    assert subject.failure_is_accounted_for(d)


def test_private_file_unlink_failure_retains_exact_file(subject):
    d = subject.make()
    p = _payload(d, name="owned-file", body="owned bytes")
    ident = _ident(p)
    real_unlink, fired = os.unlink, {"n": 0}

    def fail_unlink(path, *a, **k):
        st = os.stat(path, dir_fd=k.get("dir_fd"), follow_symlinks=False)
        if ".bdrm-" in os.fsdecode(path) and (st.st_dev, st.st_ino) == ident:
            fired["n"] += 1
            raise OSError(errno.EIO, "injected private file unlink failure")
        return real_unlink(path, *a, **k)

    os.unlink = fail_unlink
    try:
        got = subject.discard(d)
    finally:
        os.unlink = real_unlink
    assert fired["n"] == 1 and got is False
    survivor = _find_by_ident(d, ident)
    assert survivor is not None and pathlib.Path(survivor).read_text() == "owned bytes"
    assert os.path.basename(survivor) in subject.reason(d)
    assert "injected private file unlink failure" in subject.reason(d)


def test_recovery_refuses_a_foreign_replacement_of_private_name(subject):
    d = subject.make()
    p = _payload(d, name="owned-file", body="owned bytes")
    owned = _ident(p)
    real_unlink, fired, evidence = os.unlink, {"n": 0}, {}

    def replace_then_fail(path, *a, **k):
        st = os.stat(path, dir_fd=k.get("dir_fd"), follow_symlinks=False)
        if ".bdrm-" in os.fsdecode(path) and (st.st_dev, st.st_ino) == owned:
            fired["n"] += 1
            held = os.fsdecode(path) + ".owned"
            os.rename(path, held, src_dir_fd=k["dir_fd"], dst_dir_fd=k["dir_fd"])
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=k["dir_fd"])
            os.write(fd, b"foreign bytes"); os.close(fd)
            fst = os.stat(path, dir_fd=k["dir_fd"], follow_symlinks=False)
            evidence.update(private=os.fsdecode(path), held=held, foreign=(fst.st_dev, fst.st_ino))
            raise OSError(errno.EIO, "injected replaced-private failure")
        return real_unlink(path, *a, **k)

    os.unlink = replace_then_fail
    try:
        got = subject.discard(d)
    finally:
        os.unlink = real_unlink
    assert fired["n"] == 1 and got is False
    assert _ident(os.path.join(d, evidence["private"])) == evidence["foreign"]
    assert pathlib.Path(d, evidence["private"]).read_bytes() == b"foreign bytes"
    assert _ident(os.path.join(d, evidence["held"])) == owned
    assert evidence["private"] in subject.reason(d) or evidence["held"] in subject.reason(d)


def test_anchor_close_failure_reports_residue_and_closes_readable_descriptor(subject):
    d = subject.make(); child = os.path.join(d, "child"); os.mkdir(child); _payload(child)
    ident = _ident(child)
    real_open, real_close, anchors, fired = os.open, os.close, set(), {"n": 0}

    def spy_open(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if flags & getattr(os, "O_PATH", 0):
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino) == ident: anchors.add(fd)
        return fd

    def fail_close(fd):
        if fd in anchors and not fired["n"]:
            fired["n"] += 1; real_close(fd)
            raise OSError(errno.EIO, "injected anchor close failure")
        return real_close(fd)

    os.open, os.close = spy_open, fail_close
    try:
        got = subject.discard(d)
    finally:
        os.open, os.close = real_open, real_close
    leaked = []
    for n in os.listdir("/proc/self/fd"):
        with contextlib.suppress(OSError):
            st = os.fstat(int(n))
            if (st.st_dev, st.st_ino) == ident: leaked.append(int(n))
    assert fired["n"] == 1 and got is False
    assert not (set(leaked) - subject._ledger_fds), leaked
    survivor = _find_by_ident(d, ident)
    assert survivor is not None and survivor != child
    assert "injected anchor close failure" in subject.reason(d)


def test_anchor_close_failure_with_unknown_close_state_is_honest(subject):
    """A close error is consumed once; its fd number is never retried."""
    d = subject.make()
    child = os.path.join(d, "child")
    os.mkdir(child)
    _payload(child)
    ident = _ident(child)
    real_open, real_close = os.open, os.close
    anchors, fired = set(), {"n": 0}

    def spy_open(path, flags, mode=0o777, *, dir_fd=None):
        opened = real_open(path, flags, mode, dir_fd=dir_fd)
        if flags & getattr(os, "O_PATH", 0):
            st = os.fstat(opened)
            if (st.st_dev, st.st_ino) == ident:
                anchors.add(opened)
        return opened

    def fail_without_closing(fd):
        if fd in anchors and fired["n"] == 0:
            fired["n"] += 1
            raise OSError(errno.EIO, "injected close state unknown")
        return real_close(fd)

    os.open, os.close = spy_open, fail_without_closing
    try:
        got = subject.discard(d)
    finally:
        os.open, os.close = real_open, real_close
    still_open = sorted((anchors & _fds_for_ident(ident)) - subject._ledger_fds)
    try:
        assert fired["n"] == 1
        assert got is False
        assert len(still_open) == 1, still_open
        why = subject.reason(d)
        assert "injected close state unknown" in why
        assert "descriptor state is unknown" in why
    finally:
        for fd in still_open:
            real_close(fd)


def test_reopened_root_close_failure_never_restores_mode_through_recycled_fd(subject):
    """A close error makes its numeric fd unusable as restoration authority."""
    d = subject.make()
    ident, original_mode = _ident(d), 0o500
    os.chmod(d, original_mode)
    if subject.name == "bd-cut":
        recorded_fd = subject.m._TEMPDIR_FD.pop(d)
    elif subject.name == "bd-footguns":
        recorded_fd = subject.m._SANDBOX_FD.pop(d)
    else:
        recorded_fd = subject.m._ROOT_FD
        subject.m._ROOT_FD = subject.m._ROOT_FD_PATH = None
    os.close(recorded_fd)

    foreign = d + ".foreign-close-reuse"
    os.mkdir(foreign, 0o700)
    foreign_ident = _ident(foreign)
    real_walk, real_close = subject.m._rmtree_fd, os.close
    walked, closed, recycled = {"n": 0}, {"n": 0}, {"fd": None}

    def force_top_relaxation(fd, dev, depth=0):
        st = os.fstat(fd)
        assert (st.st_dev, st.st_ino) == ident
        walked["n"] += 1
        if walked["n"] == 1:
            raise PermissionError(errno.EACCES, "force reopened-root relaxation")

    def close_then_recycle(fd):
        st = os.fstat(fd)
        if ((st.st_dev, st.st_ino) == ident and closed["n"] == 0):
            closed["n"] += 1
            real_close(fd)
            replacement = os.open(
                foreign, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            assert replacement == fd, (
                f"fixture: descriptor {fd} was not immediately recycled; "
                f"got {replacement}")
            recycled["fd"] = replacement
            os.rmdir(foreign)
            raise OSError(errno.EIO, "injected reopened-root close failure")
        return real_close(fd)

    subject.m._rmtree_fd, os.close = force_top_relaxation, close_then_recycle
    try:
        got = subject.discard(d)
    finally:
        subject.m._rmtree_fd, os.close = real_walk, real_close
    try:
        assert walked["n"] == 2
        assert closed["n"] == 1
        assert recycled["fd"] is not None
        after = os.fstat(recycled["fd"])
        assert (after.st_dev, after.st_ino) == foreign_ident
        assert stat.S_IMODE(after.st_mode) == 0o700, (
            "cleanup restored the owned root's mode through a recycled foreign fd")
        assert got is False
        assert "injected reopened-root close failure" in subject.reason(d)
        assert subject.failure_is_accounted_for(d)
    finally:
        if recycled["fd"] is not None:
            real_close(recycled["fd"])


def test_success_removes_the_exact_top_level_inode(subject):
    d = subject.make()
    ident = _ident(d)
    held = os.open(d, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        got = subject.discard(d)
        after = os.fstat(held)
    finally:
        os.close(held)
    assert got is True, subject.reason(d)
    assert (after.st_dev, after.st_ino) == ident
    assert after.st_nlink == 0
    assert not subject.failure_is_accounted_for(d)


def test_interrupt_after_real_relaxation_restores_mode_and_reports_name(subject):
    d = subject.make(); child = os.path.join(d, "child"); os.mkdir(child); _payload(child)
    ident, mode = _ident(child), 0o100; os.chmod(child, mode)
    real_chmod, fired = os.chmod, {"n": 0}

    def interrupt(path, new_mode, *a, **k):
        result = real_chmod(path, new_mode, *a, **k)
        if str(path).startswith("/proc/self/fd/") and new_mode == 0o700:
            fd = int(str(path).rsplit("/", 1)[1]); st = os.fstat(fd)
            if (st.st_dev, st.st_ino) == ident:
                fired["n"] += 1
                raise KeyboardInterrupt("injected after real relaxation")
        return result

    os.chmod = interrupt
    try:
        with pytest.raises(KeyboardInterrupt, match="injected after real relaxation"):
            subject.discard(d)
    finally:
        os.chmod = real_chmod
    survivor = _find_by_ident(d, ident)
    assert fired["n"] == 1 and survivor is not None
    assert stat.S_IMODE(os.lstat(survivor).st_mode) == mode


def test_mode_restoration_failure_is_reported(subject):
    d = subject.make(); child = os.path.join(d, "child"); os.mkdir(child); _payload(child)
    ident, mode = _ident(child), 0o100; os.chmod(child, mode)
    real_scan, real_fchmod, walked, restore = os.scandir, os.fchmod, {"n": 0}, {"n": 0}

    def fail_walk(fd):
        if isinstance(fd, int):
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino) == ident:
                walked["n"] += 1; raise OSError(errno.EIO, "primary walk failure")
        return real_scan(fd)

    def fail_restore(fd, new_mode):
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) == ident and new_mode == mode:
            restore["n"] += 1; raise OSError(errno.EACCES, "injected mode restore failure")
        return real_fchmod(fd, new_mode)

    os.scandir, os.fchmod = fail_walk, fail_restore
    try: got = subject.discard(d)
    finally: os.scandir, os.fchmod = real_scan, real_fchmod
    assert walked["n"] and restore["n"] and got is False
    why = subject.reason(d)
    assert "primary walk failure" in why and "injected mode restore failure" in why


def test_later_readable_open_failure_restores_unreadable_child(subject):
    d = subject.make(); child = os.path.join(d, "child"); os.mkdir(child); _payload(child)
    ident, mode = _ident(child), 0o100; os.chmod(child, mode)
    real_open, fired = os.open, {"n": 0}

    def fail_later(path, flags, open_mode=0o777, *, dir_fd=None):
        if str(path).startswith("/proc/self/fd/"):
            anchor = int(str(path).rsplit("/", 1)[1]); st = os.fstat(anchor)
            if (st.st_dev, st.st_ino) == ident and stat.S_IMODE(st.st_mode) == 0o700:
                fired["n"] += 1
                raise OSError(errno.EIO, "injected later readable open failure")
        return real_open(path, flags, open_mode, dir_fd=dir_fd)

    os.open = fail_later
    try: got = subject.discard(d)
    finally: os.open = real_open
    assert fired["n"] == 1 and got is False
    survivor = _find_by_ident(d, ident)
    assert survivor is not None and stat.S_IMODE(os.lstat(survivor).st_mode) == mode
    assert "injected later readable open failure" in subject.reason(d)


# ==========================================================================
# NOTHING ESCAPES, AND NOTHING IS LOST.
# ==========================================================================
def test_a_tree_too_deep_to_walk_is_a_refusal_not_an_exception(subject):
    """E8. The walk recurses, so a deep tree raises RecursionError -- which is
    not an OSError and was therefore outside every handler. It escaped the
    remover, escaped _reap, and took the whole cleanup report with it."""
    d = subject.make()
    cur, depth = d, 0
    try:
        while depth < 1400:
            cur = os.path.join(cur, "d")
            os.mkdir(cur)
            depth += 1
    except OSError:
        pass
    assert depth > 1000, f"only built {depth} levels -- too shallow to test"
    try:
        got = subject.discard(d)
    except BaseException as e:                       # noqa: BLE001 -- the subject
        subprocess.run(["find", d, "-depth", "-type", "d", "-delete"],
                       capture_output=True)
        pytest.fail(f"{type(e).__name__} escaped the remover: {e}")
    subprocess.run(["find", d, "-depth", "-type", "d", "-delete"],
                   capture_output=True)
    assert got is False
    assert R_TOO_DEEP in subject.reason(d), subject.reason(d)[-300:]


def test_reap_reports_every_directory_even_when_one_cannot_be_walked():
    """E8b. `_reap` loops over the registered directories; one raising took the
    loop, the summary, and every other directory's attempt with it -- and
    bd-cut then exited 1, indistinguishable from die()."""
    m = _load(BDCUT, "bd_cut_reap_1154")
    deep = m._owned_tempdir("bdcut_deep1154_")
    cur = deep
    for _ in range(1400):
        cur = os.path.join(cur, "d")
        os.mkdir(cur)
    ordinary = m._owned_tempdir("bdcut_ord1154_")
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            failed = m._reap([])
    except BaseException as e:                       # noqa: BLE001
        pytest.fail(f"_reap raised {type(e).__name__}: {e}")
    finally:
        subprocess.run(["find", deep, "-depth", "-type", "d", "-delete"],
                       capture_output=True)
        _force_rm(deep)
        _force_rm(ordinary)
    assert "TEMPORARY DIRECTORIES NOT REMOVED" in err.getvalue(), err.getvalue()
    assert any(deep in f for f in failed), failed
    assert not os.path.lexists(ordinary), (
        "the ordinary directory was never attempted -- one failure ended the "
        "loop that exists to attempt them all")


def test_a_zero_SystemExit_cannot_bypass_the_cleanup_exit_code():
    """E8c. main() applied EXIT_CLEANUP_FAILED on the RETURN path only, so a
    SystemExit(0) raised anywhere inside carried straight through a failed
    cleanup and the cut exited 0 with a directory left behind."""
    m = _load(BDCUT, "bd_cut_sysexit_1154")
    stuck = m._owned_tempdir("bdcut_stuck1154_")
    m._TEMPDIR_IDENT.pop(stuck, None)               # unprovable -> must refuse
    if hasattr(m, "_TEMPDIR_FD"):
        with contextlib.suppress(OSError):
            os.close(m._TEMPDIR_FD.pop(stuck))
    real_inner = m._main_inner
    m._main_inner = lambda argv, cl: (_ for _ in ()).throw(SystemExit(0))
    code = "no-exception-raised"
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            rc = m.main([])
        code = rc
    except SystemExit as e:
        code = e.code
    finally:
        m._main_inner = real_inner
        # OBSERVE BEFORE TEARING DOWN. The precondition -- that the directory
        # really was left behind -- has to be read while it is still true, and
        # the first draft read it after the teardown and papered over the
        # result with `or True`, which the repo's own trivially-true gate
        # correctly refused.
        still_there = os.path.lexists(stuck)
        _force_rm(stuck)
    assert still_there, (
        "the unprovable directory was removed after all, so there was no "
        "failed cleanup for the exit code to be about")
    assert code == m.EXIT_CLEANUP_FAILED, (
        f"exit was {code!r}; a cleanup that did not happen must cost the "
        f"documented {m.EXIT_CLEANUP_FAILED}, on the exception path as well "
        "as the return path")


# ==========================================================================
# bd-footguns: a cleanup failure is not a verdict, and an undecidable
# detector is not an authorization.
# ==========================================================================
class _Args:
    def __init__(self, tree):
        self.tree, self.json = tree, False


# THE AUTHORIZATION LINE, READ OUT OF THE TOOL. Two assertions here named
# "OK -- no active footgun violated", which v3.66.1154 rewrote to "no
# BLOCKING footgun violated" -- so both were vacuously true and would have
# stayed green while the tool printed its authorization. A literal copied
# from a source file is a claim about that file, and it goes stale in
# silence; deriving it cannot.
_OK_LINE = "OK -- no blocking footgun violated"


def _fg():
    return _load(BDFG, "bd_fg_verdict_1154")


def _run_check(m, rows, tree, remover=None):
    """Drive the real cmd_check over `rows`.

    A failure is injected at `_remove_owned_sandbox`, NOT at `_discard`. That
    is the difference between testing the fix and bypassing it: the cleanup
    channel is fed inside `_discard`, so a test that stubs `_discard` records
    nothing and the run returns 0 for a reason unrelated to the code. The
    inner remover is the honest seam -- the real `_discard` still runs, still
    consults the descriptor, and still files the failure.
    """
    real_load = m._load_registry
    real_rm = m._remove_owned_sandbox
    m._load_registry = lambda t: rows
    if remover is not None:
        m._remove_owned_sandbox = remover
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = m.cmd_check(_Args(tree))
    finally:
        m._load_registry, m._remove_owned_sandbox = real_load, real_rm
    return rc, buf.getvalue()


def test_a_cleanup_failure_under_a_nonblocking_detector_is_not_an_authorization():
    """E5. `unknown_blocking` filtered on severity, so a sandbox that could not
    be removed under an ADVISORY detector left the run printing OK and exiting
    0 -- which bd-cut's step 0 reads as permission to cut. A cleanup failure is
    not a detector verdict at all; it is the tool failing to finish."""
    m = _fg()
    rows = [
        {"id": "PASSER", "status": "active", "severity": "blocking", "rule": "r",
         "fix": "f", "detector": {"kind": "tool", "cmd": ["sh", "-c", "exit 0"],
                                  "block_on_exit": []}},
        {"id": "DIRTY", "status": "active", "severity": "advisory", "rule": "r",
         "fix": "f", "detector": {"kind": "tool",
                                  "cmd": ["sh", "-c", "touch DIRTY_MARKER"],
                                  "block_on_exit": []}},
    ]
    real_rm, seen = m._remove_owned_sandbox, {"marked": 0, "clean": 0}

    def remover(d, ident, held_fd=None):
        # The ADVISORY detector's command drops DIRTY_MARKER in its own
        # sandbox, so the failure attaches to exactly one detector and this
        # test cannot pass because the BLOCKING one happened to fail instead.
        if os.path.lexists(os.path.join(d, "DIRTY_MARKER")):
            seen["marked"] += 1
            return False, "[not-proven] injected: this sandbox would not go"
        seen["clean"] += 1
        return real_rm(d, ident, held_fd)

    rc, blob = _run_check(m, rows, str(REPO), remover=remover)
    assert seen["marked"] == 1, (
        f"the cleanup failure was never attached to the ADVISORY detector "
        f"(marked={seen['marked']} clean={seen['clean']}); with it attached to "
        "the blocking one this test passes for the wrong reason")
    assert rc != 0, "a run that could not clean up printed an authorization"
    assert _OK_LINE not in blob, blob[-300:]


@pytest.mark.parametrize("label,row,want_zero", [
    ("blocking_kind_none",
     {"severity": "blocking", "detector": {"kind": "none"}}, False),
    ("advisory_kind_none",
     {"severity": "advisory", "detector": {"kind": "none"}}, True),
    # THREE SPELLINGS, AND THEY MUST NOT COLLAPSE INTO ONE. All three landed
    # on the same `unrecognised detector kind None` return, so two of the arms
    # were decoration -- the duplicate-arm defect this file repaired in 1153's
    # parametrized test and then reproduced here.
    ("blocking_kind_missing",
     {"severity": "blocking", "detector": {}}, False),
    ("blocking_detector_absent",
     {"severity": "blocking"}, False),
    ("blocking_kind_unsupported",
     {"severity": "blocking", "detector": {"kind": "wat"}}, False),
    ("blocking_tool_without_cmd",
     {"severity": "blocking", "detector": {"kind": "tool"}}, False),
    ("blocking_insync_without_test",
     {"severity": "blocking", "detector": {"kind": "insync"}}, False),
    ("blocking_grep_without_pattern",
     {"severity": "blocking", "detector": {"kind": "grep"}}, False),
])
def test_the_detector_kind_matrix(label, row, want_zero):
    """E6 and B6. `kind:"none"` mapped to `advisory` REGARDLESS of severity, so
    an active BLOCKING footgun with no mechanical detector returned 0 and
    printed the authorization line -- while the same row spelled
    `{"kind":"advisory"}`, or with no detector key at all, correctly returned
    2. One spelling of "there is no detector" authorized a cut and the other
    two refused it.

    A malformed row was worse than any of them: `{"kind":"tool"}` with no `cmd`
    raised KeyError out of cmd_check, so the tool died with an exit code that
    means something else entirely."""
    m = _fg()
    fg = {"id": "X", "status": "active", "rule": "r", "fix": "f"}
    fg.update(row)
    passer = {"id": "PASSER", "status": "active", "severity": "blocking",
              "rule": "r", "fix": "f",
              "detector": {"kind": "tool", "cmd": ["sh", "-c", "exit 0"],
                           "block_on_exit": []}}
    rc, blob = _run_check(m, [passer, fg], str(REPO))
    assert "Traceback" not in blob, blob[-600:]
    if want_zero:
        assert rc == 0, (
            "a row that DECLARES it has no mechanical detector, at a severity "
            f"the registry marks non-blocking, must not block: rc={rc}\n{blob[-400:]}")
    else:
        assert rc == m.sec.EXIT_CANNOT_EVALUATE, (
            f"{label} produced rc={rc}; an active blocking footgun nobody can "
            f"evaluate must not authorize a cut\n{blob[-400:]}")
        assert _OK_LINE not in blob, blob[-300:]


def test_the_shipped_registry_still_reads_the_same_way():
    """THE NON-REGRESSION THIS FILE OWES THE BOX. The rule above turns on
    severity, and it is only safe because the one shipped `kind:"none"` row is
    non-blocking. If that ever changes, `bd-footguns --check` starts refusing
    on the operator's machine and this test says why before the box does."""
    m = _fg()
    rows = m._load_registry(str(REPO))
    nones = [f for f in rows
             if (f.get("detector") or {}).get("kind") == "none"
             and f.get("status") == "active"]
    assert nones, "no active kind:none row -- the matrix above lost its subject"
    for f in nones:
        assert f.get("severity") != "blocking", (
            f"{f['id']} is an active BLOCKING footgun with no mechanical "
            "detector; under v3.66.1154 that refuses, so this is a live gate "
            "change and not a test failure")


def test_the_selftest_fails_when_its_own_cleanup_fails():
    """bd-footguns --selftest is the check an operator runs by hand, and it
    graded itself on the detector logic while ignoring whether it had cleaned
    up after itself."""
    m = _fg()
    real_disc, fired = m._discard, {"n": 0}

    def discard(d):
        fired["n"] += 1
        real_disc(d)
        return False

    m._discard = discard
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = m.selftest()
    finally:
        m._discard = real_disc
    blob = buf.getvalue()
    assert fired["n"] >= 1, "the selftest never removed a directory -- nothing tested"
    assert rc != 0, "the selftest passed while unable to clean up after itself"
    assert "SELFTEST FAIL" in blob
    assert "reclaim" in blob.lower() or "cleanup" in blob.lower(), blob[-400:]


def test_the_selftest_passes_on_a_clean_run():
    """The over-sensitive control for the test above."""
    m = _fg()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = m.selftest()
    assert rc == 0, buf.getvalue()[-600:]


# ==========================================================================
# THE NINE MUTANTS THAT ESCAPED THE FIRST v3.66.1154 BATTERY.
#
# Each of these names a behaviour the cut ASSERTS and did not CONSTRAIN, and
# each is given its OWN test rather than being left to a neighbour. Two pairs
# here were each other's only backstop -- the malformed-detector guard and the
# dispatch wrapper cover for one another exactly the way the parent-entry check
# and the nlink proof did at v3.66.1153, which is how both of those reached a
# shipped battery unnoticed.
# ==========================================================================
def test_a_child_swapped_between_the_rename_and_the_open_is_not_entered(subject, tmp_path):
    """N2. After the private rename the entry is stat'd, then OPENED, and the
    descriptor is fstat'd again. That second check looks redundant -- and is
    not: the two calls name the same path at two instants."""
    d = subject.make()
    child = os.path.join(d, "c")
    os.mkdir(child)
    _payload(child, "ours.txt")
    child_id = _ident(child)
    foreign = tempfile.mkdtemp(prefix="FOREIGNOPEN_", dir=str(tmp_path))
    _payload(foreign, "victim.txt")
    foreign_id = _ident(foreign)

    real_open, fired = os.open, {"n": 0}

    def spy_open(path, flags, *a, dir_fd=None, **k):
        if dir_fd is not None and fired["n"] == 0:
            try:
                st = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
                hit = (st.st_dev, st.st_ino) == child_id
            except OSError:
                hit = False
            if hit:
                fired["n"] += 1
                os.rename(path, str(path) + ".gone",
                          src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                os.rename(foreign, os.path.join(d, str(path)))
        return real_open(path, flags, *a, dir_fd=dir_fd, **k)

    os.open = spy_open
    try:
        got = subject.discard(d)
    finally:
        os.open = real_open
    assert fired["n"] == 1, "the swap never fired -- this test proved nothing"
    survivor = _find_by_ident(d, foreign_id) if os.path.isdir(d) else None
    assert survivor is not None and os.path.exists(
        os.path.join(survivor, "victim.txt")), (
        "a foreign directory substituted between the rename and the open was "
        "entered and emptied")
    assert got is False
    assert R_FOREIGN in subject.reason(d), subject.reason(d)[-300:]
    _force_rm(d)


def test_a_child_removal_that_took_the_wrong_object_refuses(subject, tmp_path):
    """N3. The child's terminal rmdir has the same irreducible window as the
    top-level one, and needs the same proof -- `st_nlink == 0` on the
    descriptor held across it. Without it the walk deletes a stranger and
    carries on as though it had removed its own child."""
    d = subject.make()
    child = os.path.join(d, "c")
    os.mkdir(child)
    child_id = _ident(child)

    def swap(name, dir_fd):
        os.rename(name, str(name) + ".hidden",
                  src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.mkdir(name, dir_fd=dir_fd)          # an empty stranger takes it

    spy, fired, real_rmdir = _rmdir_swapper(child_id, swap)
    os.rmdir = spy
    try:
        got = subject.discard(d)
    finally:
        os.rmdir = real_rmdir
    assert fired["n"] == 1, "the swap never fired -- this test proved nothing"
    assert got is False
    # THE DISTINCTIVE WORDS, NOT THE SHARED REFUSAL. Without the proof the
    # removal still fails -- the displaced child is left in the tree, so the
    # top-level rmdir gets ENOTEMPTY -- and that refusal carries the SAME
    # [not-proven] code. A test that stops at "it refused" passes for the wrong
    # guard, which is CLAUDE.md section 10's exact finding about bd-jobs.
    assert "was not the object held open" in subject.reason(d), (
        "the refusal came from a downstream ENOTEMPTY rather than from the "
        "proof that the entry removed was the child we held open: "
        + subject.reason(d)[-300:])
    _force_rm(d)


def test_reap_survives_a_discard_that_raises():
    """N7. `_reap` catching per directory is defence in depth: the remover now
    converts its own failures into refusals, so nothing ordinarily reaches it.
    That is exactly why it needs its own test -- one of _reap's two call sites
    is inside main()'s `except BaseException:`, where a raise would replace the
    cut's real failure reason with a cleanup exception."""
    m = _load(BDCUT, "bd_cut_reaptotal_1154")
    a = m._owned_tempdir("bdcut_raiser1154_")
    b = m._owned_tempdir("bdcut_after1154_")
    real = m._discard_tempdir

    def raiser(d):
        if d == a:
            raise RuntimeError("injected: the remover itself blew up")
        return real(d)

    m._discard_tempdir = raiser
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            failed = m._reap([])
    except BaseException as e:                       # noqa: BLE001
        pytest.fail(f"_reap raised {type(e).__name__}: {e}")
    finally:
        m._discard_tempdir = real
        _force_rm(a)
        _force_rm(b)
    assert any(a in f for f in failed), failed
    assert not os.path.lexists(b), (
        "the directory after the raiser was never attempted")
    assert "TEMPORARY DIRECTORIES NOT REMOVED" in err.getvalue()


def test_a_row_with_no_severity_is_treated_as_blocking():
    """N13. `severity == "blocking"` was a bare equality with a permissive
    default, so an ABSENT severity -- the archetype of a malformed row -- read
    as non-blocking and a printed VIOLATION exited 0. v3.66.1154 gives severity
    a second job, so it is validated; unknown fails closed."""
    m = _fg()
    rows = [{"id": "NOSEV", "status": "active", "rule": "r", "fix": "f",
             "detector": {"kind": "tool", "cmd": ["sh", "-c", "exit 3"],
                          "block_on_exit": [3]}}]
    rc, blob = _run_check(m, rows, str(REPO))
    assert "VIOLATION" in blob, blob[-400:]
    assert rc != 0, (
        "a row with no severity key printed a VIOLATION and the process "
        f"exited 0:\n{blob[-400:]}")


@pytest.mark.parametrize("sev", ["Blocking", "BLOCKING", "blocking ", "critical"])
def test_a_severity_that_is_not_exactly_blocking_or_advisory_fails_closed(sev):
    """The same defect in its other spellings, each measured to exit 0."""
    m = _fg()
    rows = [{"id": "ODDSEV", "status": "active", "severity": sev, "rule": "r",
             "fix": "f",
             "detector": {"kind": "tool", "cmd": ["sh", "-c", "exit 3"],
                          "block_on_exit": [3]}}]
    rc, _blob = _run_check(m, rows, str(REPO))
    assert rc != 0, f"severity {sev!r} silently downgraded a violation to exit 0"


def test_the_selftest_env_tranche_control_is_folded_into_the_verdict():
    """N16. This control PRINTED its result and nothing consumed it, unlike the
    two beside it -- so a forced FAIL sat directly above `SELFTEST PASS`."""
    m = _fg()
    real = m._check_one

    def fake(fg, tree):
        if fg.get("status") != "active":
            return "skip", "inactive"
        if fg.get("id") == "FG-ENV-TRANCHE-BD-LITERAL":
            return "advisory", "forced: not a wired detector verdict"
        return real(fg, tree)

    m._check_one = fake
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = m.selftest()
    finally:
        m._check_one = real
    blob = buf.getvalue()
    assert "FAIL  env-tranche detector wired" in blob, blob[-400:]
    assert rc != 0, (
        "the selftest printed FAIL for its own control and still returned 0:\n"
        + blob[-400:])


def test_the_selftest_fails_if_the_cleanup_channel_stops_recording():
    """N20. "Consult the channel" asserts nothing on a healthy machine, so a
    mutant deleting the channel's producer printed SELFTEST PASS -- a gate over
    an empty denominator inside the fix for a gate over an empty denominator.
    The selftest induces a REAL unremovable sandbox; this proves that induction
    is load-bearing rather than decorative."""
    m = _fg()
    real = m._cleanup_failed

    def silent(d, why):
        real(d, why)
        m._CLEANUP_FAILURES.pop()          # refuses, but records nothing
        return False

    m._cleanup_failed = silent
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = m.selftest()
    finally:
        m._cleanup_failed = real
    assert rc != 0, (
        "the selftest passed while the cleanup channel recorded nothing:\n"
        + buf.getvalue()[-400:])


def test_a_malformed_detector_says_what_is_missing_not_that_it_raised():
    """N17. With the field guards gone the row reaches `det["cmd"]`, raises
    KeyError, and the dispatch wrapper turns that into UNKNOWN -- the same exit
    code, for a different reason. CLAUDE.md section 10: assert the distinctive
    words of the refusal you mean, or a mutant sails through to the next guard.
    """
    m = _fg()
    for kind, field in (("tool", "cmd"), ("insync", "test"), ("grep", "pattern")):
        v, detail = m._check_one(
            {"id": "X", "status": "active", "severity": "blocking",
             "detector": {"kind": kind}}, str(REPO))
        assert v == "unknown", (kind, v, detail)
        assert field in detail and "raised" not in detail, (
            f"a {kind} detector with no {field!r} was reported as {detail!r}; "
            "it must name the missing field rather than the exception that "
            "naming it would have prevented")


def test_a_detector_that_raises_anyway_is_unknown_and_not_a_crash():
    """N18. The field guards above cannot cover everything -- an invalid regex
    reaches re.compile and raises from inside the detector. Without the wrapper
    that leaves cmd_check by way of the pool's fallback, which re-runs every
    detector and re-raises, and the tool dies with exit 1: the code `die()`
    uses somewhere else entirely."""
    m = _fg()
    rows = [{"id": "BADRE", "status": "active", "severity": "blocking",
             "rule": "r", "fix": "f",
             "detector": {"kind": "grep", "pattern": "(unclosed", "root": "."}}]
    rc, blob = _run_check(m, rows, str(REPO))
    assert "Traceback" not in blob, blob[-500:]
    assert rc == m.sec.EXIT_CANNOT_EVALUATE, (rc, blob[-400:])


def test_the_private_rename_refuses_to_clobber_an_occupied_name(subject, tmp_path):
    """N19. The undo puts a foreign object back at a name a third party may
    since have re-created, and a plain `os.rename` DESTROYS an empty directory
    standing there -- the exact act the caller just refused to perform,
    reintroduced inside the error handler. renameat2(RENAME_NOREPLACE) makes it
    EEXIST; where the flag is unavailable this test says so rather than
    pretending the guarantee holds."""
    # THE DESTINATION IS EMPTY, AND THAT IS THE WHOLE TEST. Measured on this
    # host: `os.rename` onto a NON-EMPTY directory already gives EEXIST, so a
    # fixture built that way cannot tell renameat2(RENAME_NOREPLACE) from the
    # plain rename it replaced -- the first draft of this test used exactly
    # that shape and the mutant sailed through it. Onto an EMPTY directory the
    # plain rename SUCCEEDS and destroys it, which is the act the undo path
    # exists not to perform.
    arena = tempfile.mkdtemp(prefix="noclobber1154_", dir=str(tmp_path))
    os.mkdir(os.path.join(arena, "src"))
    os.mkdir(os.path.join(arena, "dst"))
    dst_id = _ident(os.path.join(arena, "dst"))
    fd = os.open(arena, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if not (subject.m._LIBC is not None and subject.m._SYS_renameat2):
            pytest.skip("renameat2 is unavailable here, and the module says so "
                        "rather than claiming a guarantee it cannot make")
        with pytest.raises(OSError) as ei:
            subject.m._rename_noclobber("src", "dst", fd)
        assert ei.value.errno == errno.EEXIST, ei.value
        assert _ident(os.path.join(arena, "dst")) == dst_id, (
            "the rename replaced an existing directory -- a plain os.rename "
            "onto an empty destination destroys it silently, which is the "
            "second destructive path this cut removed")
    finally:
        os.close(fd)
        _force_rm(arena)


def test_an_identity_without_a_descriptor_cannot_claim_the_object_went(subject, tmp_path):
    """FOUND BY RE-RUNNING THE ORIGINAL REPRODUCTION PROBE against the fix.

    Every escape this file closes is answered by a descriptor held from
    creation, and the no-descriptor fallback was left alone as the ordinary
    already-clean case. But "no descriptor" and "no identity" are different
    states: with an IDENTITY recorded we knew enough to have owned the object,
    and an absent pathname still cannot tell a completed removal from a
    rename-away. Measured on a hand-registered root: reported clean, payload
    intact under its new name, nothing recorded.

    Where NEITHER was recorded the answer stays success -- that is a caller
    asking about a path the tool never created, and refusing it would fail
    every already-clean path. The control below is that half.
    """
    m = subject.m
    d = str(tmp_path / "owned_but_unheld")
    os.mkdir(d)
    st = os.lstat(d)
    ident = (st.st_dev, st.st_ino)

    if subject.name == "bd-cut":
        m._TEMPDIRS.append(d)
        m._TEMPDIR_IDENT[d] = ident
        assert d not in m._TEMPDIR_FD, "fixture: no descriptor may be held"
        os.rename(d, d + ".moved")
        got, why = m._discard_tempdir(d), str(m._LAST_DISCARD_ERROR.get(d, ""))
        m._TEMPDIRS[:] = [x for x in m._TEMPDIRS if x != d]
        m._TEMPDIR_IDENT.pop(d, None)
    elif subject.name == "bd-footguns":
        m._SANDBOX_IDENT[d] = ident
        assert d not in m._SANDBOX_FD, "fixture: no descriptor may be held"
        os.rename(d, d + ".moved")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = m._discard(d)
        why = err.getvalue()
        m._SANDBOX_IDENT.pop(d, None)
    else:
        saved = (m._ROOT, m._ROOT_IDENT, m._ROOT_FD, m._ROOT_FD_PATH,
                 m._LAST_FAILURE, tempfile.tempdir)
        try:
            m._ROOT, m._ROOT_IDENT = d, ident
            m._ROOT_FD, m._ROOT_FD_PATH = None, None
            os.rename(d, d + ".moved")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                got = m.finish(0)
            why = err.getvalue()
        finally:
            (m._ROOT, m._ROOT_IDENT, m._ROOT_FD, m._ROOT_FD_PATH,
             m._LAST_FAILURE, tempfile.tempdir) = saved

    _force_rm(d + ".moved")
    assert got is False, (
        "the object was renamed away and the tool reported it removed, on the "
        "strength of a pathname that says nothing about which of the two "
        "happened")
    assert R_RENAMED in why, why[-300:]


def test_a_path_the_tool_never_created_is_still_absent_and_clean(subject, tmp_path):
    """THE CONTROL for the test above, and the reason it is scoped to an
    identity rather than to a descriptor. bd-footguns' `_discard` is called on
    paths it never made; refusing those would fail every already-clean run."""
    d = str(tmp_path / "never-existed")
    m = subject.m
    if subject.name == "bd-cut":
        assert d not in m._TEMPDIR_IDENT and d not in m._TEMPDIR_FD
        got = m._discard_tempdir(d)
    elif subject.name == "bd-footguns":
        assert d not in m._SANDBOX_IDENT and d not in m._SANDBOX_FD
        with contextlib.redirect_stderr(io.StringIO()):
            got = m._discard(d)
    else:
        saved = (m._ROOT, m._ROOT_IDENT, m._ROOT_FD, m._ROOT_FD_PATH,
                 m._LAST_FAILURE, tempfile.tempdir)
        try:
            m._ROOT, m._ROOT_IDENT = d, None
            m._ROOT_FD, m._ROOT_FD_PATH = None, None
            with contextlib.redirect_stderr(io.StringIO()):
                got = m.finish(0)
        finally:
            (m._ROOT, m._ROOT_IDENT, m._ROOT_FD, m._ROOT_FD_PATH,
             m._LAST_FAILURE, tempfile.tempdir) = saved
        # _tmproot has no "never created" caller: an unidentifiable root is
        # UNKNOWN and refuses, which is the v3.66.1152 semantics preserved.
        assert got is False
        return
    assert got is True, "a path the tool never created was reported as a leak"


# ==========================================================================
# WHAT THE FINAL ADVERSARIAL REVIEW OF dcaaf50 REPRODUCED.
#
# Three escapes and three suspected ones, all in the FIX rather than in the
# code it fixed -- CLAUDE.md section 0's highest-yield rule, landing on the cut
# that quotes it. The first is the worst kind: a REGRESSION, where the new
# remover refuses a removal the old one performed.
# ==========================================================================
def test_a_legal_multibyte_filename_does_not_defeat_the_removal(subject, tmp_path):
    """ESCAPE 1, and it was over-sensitivity rather than damage -- which
    CLAUDE.md section 0 counts as a soundness bug all the same, because a
    remover that refuses valid work gets switched off.

    `_private_name` sliced the base to 180 CHARACTERS and appended 22 bytes,
    while NAME_MAX is 255 BYTES. 117 two-byte UTF-8 characters is 234 bytes --
    a name any program may create -- and the private form reached 256, so the
    rename got ENAMETOOLONG and the WHOLE TREE was refused. Measured
    end-to-end: a green pytest run turned red through `finish_session` and
    leaked its entire per-run root, every `tmp_path` in it included. The
    version this replaced removed the identical tree.
    """
    d = subject.make()
    # 233 and 253 bytes, the second as a DIRECTORY so the child path is
    # exercised too. Both are legal; 128 two-byte characters would not be.
    long_file = "a" + "é" * 116
    long_dir = "c" + "é" * 126
    assert len(os.fsencode(long_dir)) == 253, len(os.fsencode(long_dir))
    with open(os.path.join(d, long_file), "w") as fh:
        fh.write("x")
    os.mkdir(os.path.join(d, long_dir))
    with open(os.path.join(d, long_dir, "inner"), "w") as fh:
        fh.write("x")
    got = subject.discard(d)
    assert got is True, (
        "a legal multi-byte filename made the remover refuse: "
        + subject.reason(d)[-300:])
    assert not os.path.lexists(d)


def test_a_tree_too_deep_reports_its_verified_private_residue(subject):
    """Depth refusal retains the object instead of attempting racy undo."""
    d = subject.make()
    root_ident = _ident(d)
    cur, depth = d, 0
    try:
        while depth < 1200:
            cur = os.path.join(cur, "a")
            os.mkdir(cur)
            depth += 1
    except OSError:
        pass
    assert depth > 800, f"only built {depth} levels -- too shallow to test"
    got = subject.discard(d)
    try:
        assert got is False
        first = next(name for name in os.listdir(d) if ".bdrm-" in name)
        assert first in subject.reason(d), subject.reason(d)[-400:]
        assert R_TOO_DEEP in subject.reason(d), subject.reason(d)[-400:]
    finally:
        assert _ident(d) == root_ident
        subprocess.run(["find", d, "-depth", "-type", "d", "-delete"],
                       check=True, capture_output=True)


def test_the_undo_refuses_rather_than_clobbering_where_it_cannot_guarantee(subject, tmp_path):
    """SUSPECTED 4, and it was live on every host outside a two-entry syscall
    table. `_rename_noclobber` fell back to `os.rename` whenever renameat2 was
    unavailable, and the return value that says which ran was discarded by
    every caller -- so on those hosts the undo silently destroyed an empty
    directory at the restored name, which is verbatim the defect this cut
    claims to close. The undo now refuses instead, and the object is left under
    its private name with the refusal saying so."""
    arena = tempfile.mkdtemp(prefix="undofallback1154_", dir=str(tmp_path))
    os.mkdir(os.path.join(arena, "src"))
    os.mkdir(os.path.join(arena, "dst"))
    victim = _ident(os.path.join(arena, "dst"))
    fd = os.open(arena, os.O_RDONLY | os.O_DIRECTORY)
    saved = subject.m._SYS_renameat2
    try:
        subject.m._SYS_renameat2 = None          # the flag is unavailable here
        with pytest.raises(OSError):
            subject.m._rename_noclobber("src", "dst", fd, allow_fallback=False)
        assert _ident(os.path.join(arena, "dst")) == victim, (
            "with no way to rename without replacing, the undo replaced -- "
            "destroying an object it was trying to restore around")
        # ...and the FORWARD rename, onto a name we generated, may still fall
        # back: there is nothing there to clobber.
        assert subject.m._rename_noclobber("src", "fresh", fd) is False
    finally:
        subject.m._SYS_renameat2 = saved
        os.close(fd)
        _force_rm(arena)


def test_the_tmproot_report_names_the_root_and_not_the_pathname(tmp_path):
    """SUSPECTED 5. After a rename+recreate the recorded path can hold a
    STRANGER's directory, and the printed remedy was `rm -rf` on that path --
    telling the operator to destroy exactly the object the tool had just
    refused to touch, while never naming the root that actually leaked."""
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import _tmproot as t
    finally:
        sys.path.pop(0)
    saved = (t._ROOT, t._ROOT_IDENT, t._ROOT_FD, t._ROOT_FD_PATH,
             t._LAST_FAILURE, tempfile.tempdir)
    keep = os.environ.pop("KEEP_TEST_TMPDIRS", None)
    err = io.StringIO()
    try:
        t._ROOT = None
        root = t.install()
        assert root
        moved = root + ".elsewhere"
        os.rename(root, moved)
        os.mkdir(root)                     # a stranger takes the freed name
        stranger = _ident(root)
        with contextlib.redirect_stderr(err):
            got = t.finish(0)
    finally:
        if keep is not None:
            os.environ["KEEP_TEST_TMPDIRS"] = keep
        cur = getattr(t, "_ROOT_FD", None)
        if cur is not None and cur != saved[2]:
            with contextlib.suppress(OSError):
                os.close(cur)
        (t._ROOT, t._ROOT_IDENT, t._ROOT_FD, t._ROOT_FD_PATH,
         t._LAST_FAILURE, tempfile.tempdir) = saved
    blob = err.getvalue()
    try:
        assert got is False
        assert _ident(root) == stranger, "the stranger was removed"
        assert moved in blob, (
            "the report does not name where the root actually is, so the leak "
            f"cannot be recovered:\n{blob[-400:]}")
        assert ("rm -rf '%s'" % root) not in blob, (
            "the printed remedy names the pathname, which now holds a "
            f"directory _tmproot did not create:\n{blob[-400:]}")
    finally:
        _force_rm(moved)
        _force_rm(root)


def test_a_session_status_that_is_not_a_number_does_not_escape_the_hook():
    """SUSPECTED 6. `int(exitstatus)` sat outside the guarded region, so a
    non-integer raised TypeError AFTER `_ROOT` had been cleared -- the root on
    disk, unreported, and unrecoverable because the only record of it had just
    been dropped. An unreadable status is now treated as a FAILING run, which
    KEEPS the artifacts: the safe direction, since the alternative is deleting
    a debugging tree on a guess."""
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import _tmproot as t
    finally:
        sys.path.pop(0)
    saved = (t._ROOT, t._ROOT_IDENT, t._ROOT_FD, t._ROOT_FD_PATH,
             t._LAST_FAILURE, tempfile.tempdir)
    keep = os.environ.pop("KEEP_TEST_TMPDIRS", None)
    try:
        t._ROOT = None
        root = t.install()
        assert root
        got = t.finish(None)               # must not raise
        assert got is False
        assert os.path.isdir(root), (
            "an unreadable exit status deleted the artifacts anyway")
    finally:
        if keep is not None:
            os.environ["KEEP_TEST_TMPDIRS"] = keep
        cur = getattr(t, "_ROOT_FD", None)
        if cur is not None and cur != saved[2]:
            with contextlib.suppress(OSError):
                os.close(cur)
        (t._ROOT, t._ROOT_IDENT, t._ROOT_FD, t._ROOT_FD_PATH,
         t._LAST_FAILURE, tempfile.tempdir) = saved
        _force_rm(root)


def test_the_selftest_fails_when_a_detector_sandbox_cannot_be_removed():
    """ESCAPE 3. `main()` returned `selftest()` BEFORE the single cleanup
    consult, and `selftest()` cleared the whole channel on its way out -- so a
    real cleanup failure inside its own detector runs was recorded, accepted by
    a control that admits "unknown", and then erased. `--check` on the
    identical seam refused correctly; only the selftest lane was blind.

    The sandbox here is genuinely unremovable -- its PARENT is not writable, so
    the terminal rmdir gets EACCES -- because a sandbox merely sealed at 0o500
    is still removable: the remover relaxes through its own descriptor, and a
    seam that does not build the shape proves nothing.
    """
    m = _fg()
    real_sbx, state = m._sandbox, {"n": 0, "par": None}

    def sandbox():
        if state["n"]:
            return real_sbx()
        state["n"] += 1
        par = tempfile.mkdtemp(prefix="bdfg_seal_par_")
        d = os.path.join(par, "bdfg_sbx_seam")
        os.mkdir(d)
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        m._SANDBOX_FD[d] = fd
        st = os.fstat(fd)
        m._SANDBOX_IDENT[d] = (st.st_dev, st.st_ino)
        os.chmod(par, 0o500)
        state["par"] = par
        return d, dict(os.environ, BD_INSTALL_DIR=d, BD_HOME=d,
                       BD_DISABLE_KEEPALIVE="1")

    m._sandbox = sandbox
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = m.selftest()
    finally:
        m._sandbox = real_sbx
        if state["par"]:
            os.chmod(state["par"], 0o700)
            _force_rm(state["par"])
    blob = buf.getvalue()
    assert state["n"] == 1, "the unremovable sandbox was never built"
    assert rc != 0, (
        "the selftest returned 0 with one of its own sandboxes still on "
        "disk:\n" + blob[-500:])
    assert "SELFTEST FAIL" in blob
    assert "every sandbox this selftest created was reclaimed" in blob


# ==========================================================================
# MUTANTS THAT SURVIVED TWO BATTERIES -- mine and an independent reviewer's.
#
# None of these is a new way to destroy the wrong object: they are behaviours
# the code CLAIMS and nothing constrained. That distinction does not make them
# optional. A guard no test can see is a guard the next edit deletes, and two
# of these went dark BECAUSE of this cut -- adding the held descriptor made the
# top-level identity check tautological on the path the tests exercise, so a
# covered guard became an uncovered one and nothing reported it.
# ==========================================================================
def test_the_creation_identity_check_still_refuses_without_a_descriptor(subject, tmp_path):
    """D3/D4/C4. `if (st.st_dev, st.st_ino) != ident` is v3.66.1153's headline
    guard, and deleting it in ALL THREE removers left the whole band green --
    because with a held descriptor the comparison is tautological, and every
    test now takes that path. The check is still load-bearing where no
    descriptor was held, which is exactly where it must be exercised."""
    d = str(tmp_path / "owned_no_fd")
    os.mkdir(d)
    st = os.lstat(d)
    ident = (st.st_dev, st.st_ino)
    os.rename(d, d + ".stashed")
    os.mkdir(d)                                  # an imposter takes the name
    imposter = _ident(d)
    with open(os.path.join(d, "theirs"), "w") as fh:
        fh.write("NOT OURS")
    m = subject.m
    try:
        if subject.name == "bd-cut":
            ok, why = m._remove_owned_dir(d, ident, None)
        elif subject.name == "bd-footguns":
            ok, why = m._remove_owned_sandbox(d, ident, None)
        else:
            ok, why = m._force_rmtree(d, ident, None), ""
        assert ok is False, (
            "a directory the tool never created was removed on the strength of "
            "a pathname, with the recorded identity never compared")
        assert _ident(d) == imposter, "the imposter's inode was replaced"
        assert os.path.exists(os.path.join(d, "theirs")), (
            "the imposter's contents were destroyed")
        if subject.name != "_tmproot":
            assert R_FOREIGN in str(why), why
    finally:
        _force_rm(d)
        _force_rm(d + ".stashed")


def test_the_private_name_is_unguessable_and_keeps_the_prefix(subject):
    """B1/B2. Two properties the code states and nothing checked. The random
    suffix is the entire reason an adversary racing the well-known name cannot
    follow the object to its private one -- a deterministic name would leave
    every argument in this file resting on nothing. The retained prefix is what
    keeps a run killed mid-removal visible to the `bdcut_*`, `bdfg_sbx_*` and
    `bd-testrun-*` sweeps, in the cut whose subject is leaks."""
    priv = subject.m._private_name
    seen = {priv("bdcut_thing_abcd") for _ in range(64)}
    assert len(seen) == 64, (
        f"the private name repeated: {64 - len(seen)} collisions in 64 draws, "
        "so it is derivable rather than unguessable")
    one = priv("bdcut_thing_abcd")
    assert one.startswith("bdcut_thing_abcd"), (
        f"the private name dropped the caller's prefix ({one!r}); a run killed "
        "between the rename and the removal would leave residue no sweep in "
        "this project looks for")
    assert ".bdrm-" in one
    # ...and it stays a legal filename for a name that is already at the limit
    longest = priv("z" * 240 + "é" * 5)
    assert len(os.fsencode(longest)) <= 255, len(os.fsencode(longest))


def test_the_entry_list_is_read_before_anything_moves(subject):
    """SUPERSEDED, and deliberately kept as a name rather than deleted.

    This asserted that one `os.scandir` call returned every entry -- by
    patching `os.scandir` to materialise them, which makes production's
    `list()` a no-op and measures the fixture instead of the subject. It also
    turned out to be FLAKY for the reason it could not see: an unmaterialised
    cursor really does return short.

    The property it was reaching for is now covered twice, properly:
    `test_the_walk_never_meets_an_entry_it_renamed` induces the persisting
    renames the hazard needs and establishes its own control first, and
    `test_a_large_directory_is_removed_in_one_pass` covers the plain case with
    nothing patched at all.
    """
    pytest.skip("superseded by test_the_walk_never_meets_an_entry_it_renamed, "
                "which measures the subject rather than its own fixture")


def test_a_bare_SystemExit_also_cannot_bypass_the_cleanup_code():
    """B5. The arm normalises `e.code is None` to 0, and the test that covers
    it injected `SystemExit(0)` only -- so deleting the normalisation left the
    band green while a bare `sys.exit()` carried straight through a failed
    cleanup. That is the same defect the test is named for, one spelling
    along."""
    m = _load(BDCUT, "bd_cut_bare_exit_1154")
    stuck = m._owned_tempdir("bdcut_bare1154_")
    m._TEMPDIR_IDENT.pop(stuck, None)
    if hasattr(m, "_TEMPDIR_FD"):
        with contextlib.suppress(OSError):
            os.close(m._TEMPDIR_FD.pop(stuck))
    real_inner = m._main_inner
    m._main_inner = lambda argv, cl: (_ for _ in ()).throw(SystemExit())
    code = "no-exception-raised"
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            code = m.main([])
    except SystemExit as e:
        code = e.code
    finally:
        m._main_inner = real_inner
        still_there = os.path.lexists(stuck)
        _force_rm(stuck)
    assert still_there, "nothing was left behind, so there is no failure to code"
    assert code == m.EXIT_CLEANUP_FAILED, (
        f"a bare SystemExit() carried exit {code!r} through a cleanup that did "
        "not happen")


def test_the_creation_identity_comes_from_the_descriptor_that_is_kept(subject):
    """B8. Replacing `os.fstat(fd)` with a second `os.lstat(d)` at creation
    left the band green -- and that is verbatim what the comment two lines
    above it says must not happen: two lookups of one path are two chances to
    be told about different objects, and only one of them is the object the
    removal will act through."""
    real_lstat, real_fstat = os.lstat, os.fstat
    lied = {"n": 0}

    def lying_lstat(path, *a, **k):
        # every lstat of a path reports a DIFFERENT inode than the truth
        st = real_lstat(path, *a, **k)
        lied["n"] += 1
        return os.stat_result(tuple(st)[:1] + (st.st_ino ^ 0xF0F0F0,) + tuple(st)[2:])

    os.lstat = lying_lstat
    try:
        d = subject.make()
    finally:
        os.lstat = real_lstat
    try:
        # the identity must match the DESCRIPTOR, not the lie
        fd = (getattr(subject.m, "_TEMPDIR_FD", {}) or
              getattr(subject.m, "_SANDBOX_FD", {}) or {}).get(d)
        if fd is None:
            fd = getattr(subject.m, "_ROOT_FD", None)
        assert fd is not None, "no descriptor was kept at creation"
        truth = real_fstat(fd)
        recorded = (getattr(subject.m, "_TEMPDIR_IDENT", {}) or
                    getattr(subject.m, "_SANDBOX_IDENT", {}) or {}).get(d)
        if recorded is None:
            recorded = getattr(subject.m, "_ROOT_IDENT", None)
        assert recorded == (truth.st_dev, truth.st_ino), (
            f"the recorded identity {recorded} is not the descriptor's "
            f"{(truth.st_dev, truth.st_ino)} -- it came from a second lookup "
            "of the pathname")
        assert subject.discard(d) is True, subject.reason(d)[-200:]
    finally:
        _force_rm(d)


def test_a_walk_failure_that_is_not_an_OSError_names_its_type(subject):
    """N9 and B4. The depth bound now refuses before RecursionError can fire,
    which is right -- and it left the broad `except Exception` with nothing
    exercising it, so deleting the handler (or the exception TYPE in its
    message) left the band green. A handler that fails closed is only half the
    property: the reason has to say WHAT failed, or a remover bug reads exactly
    like routine leakage."""
    d = subject.make()
    _payload(d)
    real_walk, fired = subject.m._rmtree_fd, {"n": 0}

    def boom(fd, dev, depth=0):
        fired["n"] += 1
        raise ValueError("injected: not an OSError at all")

    subject.m._rmtree_fd = boom
    try:
        got = subject.discard(d)
    except BaseException as e:                       # noqa: BLE001
        subject.m._rmtree_fd = real_walk
        _force_rm(d)
        pytest.fail(f"{type(e).__name__} escaped the remover: {e}")
    finally:
        subject.m._rmtree_fd = real_walk
    why = subject.reason(d)
    _force_rm(d)
    assert fired["n"] >= 1, "the failure was never injected"
    assert got is False
    assert "ValueError" in why, (
        "the refusal does not name the exception type, so a remover BUG reads "
        f"as ordinary leakage: {why[-300:]}")


def test_an_unidentifiable_dangling_name_is_refused_not_forgotten(subject, tmp_path):
    """N6. The `lexists` on the no-descriptor fallback survived its own mutant
    once the identity branch was added, because with an identity BOTH spellings
    refuse. The predicate is only load-bearing where there is no identity
    either -- a dangling symlink at a path the tool has no evidence about --
    and `os.path.exists` follows it, reads absent, and forgets the path."""
    d = str(tmp_path / "unknown_dangle")
    os.symlink(str(tmp_path / "no-such-target"), d)
    m = subject.m
    if subject.name == "bd-cut":
        m._TEMPDIRS.append(d)
        assert d not in m._TEMPDIR_IDENT and d not in m._TEMPDIR_FD
        got, why = m._discard_tempdir(d), str(m._LAST_DISCARD_ERROR.get(d, ""))
        m._TEMPDIRS[:] = [x for x in m._TEMPDIRS if x != d]
    elif subject.name == "bd-footguns":
        assert d not in m._SANDBOX_IDENT and d not in m._SANDBOX_FD
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = m._discard(d)
        why = err.getvalue()
    else:
        pytest.skip("_tmproot has no unidentified-path caller: an unknown root "
                    "is refused by the identity branch above, which is the "
                    "v3.66.1152 semantics this file preserves elsewhere")
    os.unlink(d)
    assert got is False, (
        "a dangling symlink at a registered path with no identity was reported "
        "as clean -- os.path.exists follows the link and answers about a "
        "target that was never there")
    assert R_NO_IDENT in why, why[-200:]


def test_the_retired_undo_call_site_never_mutates(subject, tmp_path):
    """The compatibility helper is diagnostic-only, never a rename seam."""
    saved = subject.m._SYS_renameat2
    arena = tempfile.mkdtemp(prefix="undosite1154_", dir=str(tmp_path))
    os.mkdir(os.path.join(arena, "victim"))
    victim = _ident(os.path.join(arena, "victim"))
    os.mkdir(os.path.join(arena, "stranger.bdrm-0000000000000000"))
    fd = os.open(arena, os.O_RDONLY | os.O_DIRECTORY)
    try:
        subject.m._SYS_renameat2 = None          # no way to rename safely
        note = subject.m._put_back("stranger.bdrm-0000000000000000", "victim", fd)
        assert _ident(os.path.join(arena, "victim")) == victim, (
            "the undo replaced the object standing at the restored name -- "
            "the destruction this cut exists to remove, in its error handler")
        assert "was not attempted" in note, note
        assert ".bdrm-" in note, (
            "the refusal does not name where the object was left, so it cannot "
            f"be recovered: {note}")
    finally:
        subject.m._SYS_renameat2 = saved
        os.close(fd)
        _force_rm(arena)


@pytest.mark.parametrize("row,want_detail", [
    ({"severity": "blocking"}, "no detector declared"),
    ({"severity": "blocking", "detector": {}}, "declares no kind"),
    ({"severity": "blocking", "detector": {"kind": "wat"}}, "unrecognised detector kind"),
])
def test_each_spelling_of_no_detector_says_which_one_it_is(row, want_detail):
    """W6. Three arms of the matrix above all landed on ONE return, so two of
    them were decoration -- the duplicate-arm defect this cut repaired in
    v3.66.1153's parametrized test and then reproduced in its own. They are
    different mistakes (an omission, a half-written row, a typo or a stale
    tool) and each now says which."""
    m = _fg()
    fg = {"id": "X", "status": "active", "rule": "r", "fix": "f"}
    fg.update(row)
    verdict, detail = m._check_one(fg, str(REPO))
    assert verdict == "unknown", (verdict, detail)
    assert want_detail in detail, detail


def test_the_single_exit_consult_is_reachable_and_refuses():
    """B12. `main()`'s "ONE CONSULT, HERE" could not fire: `cmd_check` already
    returns EXIT_CANNOT_EVALUATE whenever the channel is non-empty, and
    `--selftest` folds it in itself. An unreachable branch written in the
    language of safety is what CLAUDE.md section 10 calls dead code that reads
    as a feature -- and it is read that way precisely because of how it is
    worded.

    It is kept, because it is the guard a FUTURE subcommand inherits rather
    than has to remember. So it is exercised directly, through a subcommand
    that creates no sandboxes: if it ever stops working, this goes red rather
    than the next command shipping without it."""
    m = _fg()

    class A:
        tree, json, list, explain, selftest = str(REPO), False, True, None, False

    real_list, real_parse = m.cmd_list, None
    m.cmd_list = lambda a: 0
    m._CLEANUP_FAILURES[:] = ["/tmp/pretend_sandbox: [not-proven] injected"]
    argv = sys.argv
    try:
        sys.argv = ["bd-footguns", "--list", "--tree", str(REPO)]
        with contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            rc = m.main()
    finally:
        sys.argv = argv
        m.cmd_list = real_list
        m._CLEANUP_FAILURES[:] = []
        _ = real_parse
    assert rc == m.sec.EXIT_CANNOT_EVALUATE, (
        f"a subcommand returned {rc!r} while a sandbox was recorded as not "
        "removed; the single-exit consult did not fire")


def test_a_large_directory_is_removed_in_one_pass(subject):
    """The plain case: a directory big enough for the readdir hazard is
    removed, in one pass, with nothing patched.

    An earlier version of this test patched `os.scandir` to hand back an
    already-materialised iterator, which made the production `list()` a no-op
    and erased the very property it was standing next to."""
    d = subject.make()
    n = 5000
    for i in range(n):
        with open(os.path.join(d, "entry%05d" % i), "w") as fh:
            fh.write("x")
    assert len(os.listdir(d)) == n, "the fixture did not build the shape"
    got = subject.discard(d)
    assert got is True, (
        f"a {n}-entry directory was not removed in one pass: "
        + subject.reason(d)[-300:])
    assert not os.path.lexists(d)


def test_the_walk_never_meets_an_entry_it_renamed(subject):
    """RB_B3, AND THE PREMISE MEASURED RATHER THAN ASSUMED.

    `list(os.scandir(fd))` completes the readdir before anything moves.
    Removing it left the plain case above green, which is why the mutant
    survived: this walk renames and then immediately DESTROYS, so the renamed
    entry is gone before the cursor could reach it again. The hazard needs
    renamed entries to PERSIST -- and the moment a future edit batches the
    destruction, defers it, or hits an error partway, they do.

    So the persistence is induced here (the destruction is suppressed) and the
    control below establishes that the hazard is real on this filesystem
    before anything is concluded from its absence. Measured on this host's XFS
    with the control: 5000 entries yielded 7024, 2024 of them re-yielded under
    their new names.
    """
    # THE CONTROL FIRST: does an unmaterialised cursor re-yield here at all?
    probe = tempfile.mkdtemp(prefix="reyield_ctl_", dir=str(tempfile.gettempdir()))
    n = 3000
    try:
        for i in range(n):
            open(os.path.join(probe, "e%05d" % i), "w").close()
        pfd = os.open(probe, os.O_RDONLY | os.O_DIRECTORY)
        seen, it = [], os.scandir(pfd)
        try:
            for e in it:
                seen.append(e.name)
                if ".bdrm-" not in e.name:
                    os.rename(e.name, e.name + ".bdrm-aaaaaaaaaaaaaaaa",
                              src_dir_fd=pfd, dst_dir_fd=pfd)
        finally:
            it.close()
            os.close(pfd)
        control_reyields = len(seen) - n
    finally:
        _force_rm(probe)
    if control_reyields <= 0:
        pytest.skip(
            "renaming during an open readdir cursor does not re-yield on this "
            f"filesystem ({n} entries in, {len(seen)} out), so the property "
            "below cannot be distinguished here -- recorded rather than "
            "silently passed")

    # THE SUBJECT: same conditions, and the walk must see each entry once.
    d = subject.make()
    for i in range(n):
        with open(os.path.join(d, "e%05d" % i), "w") as fh:
            fh.write("x")
    bases, real_priv = [], subject.m._private_name

    def spy(base):
        bases.append(base)
        return real_priv(base)

    real_unlink, real_rmdir = os.unlink, os.rmdir
    subject.m._private_name = spy
    os.unlink = lambda *a, **k: None          # renamed entries PERSIST
    os.rmdir = lambda *a, **k: None
    try:
        subject.discard(d)
    finally:
        os.unlink, os.rmdir = real_unlink, real_rmdir
        subject.m._private_name = real_priv
        # THE PRIVATE NAME IS THIS TEST'S RESIDUE TO COLLECT. Suppressing the
        # destruction is what makes the renamed entries persist, and that
        # includes the OWNED directory itself -- it ends up at
        # `<name>.bdrm-<hex>` in the parent. Measured under KEEP_TEST_TMPDIRS=1
        # before this teardown existed: 4 leaked per run. It is also the
        # clearest evidence the retained prefix works, since a dot-prefixed
        # name would not have shown up in that sweep at all.
        _force_rm(d)
        _parent = os.path.dirname(d)
        for _sib in os.listdir(_parent):
            if _sib.startswith(os.path.basename(d)) and ".bdrm-" in _sib:
                _force_rm(os.path.join(_parent, _sib))

    assert bases, "the walk never ran -- nothing was tested"
    recycled = [b for b in bases if ".bdrm-" in b]
    assert not recycled, (
        f"the walk met {len(recycled)} entries it had itself renamed "
        f"(control re-yielded {control_reyields}), so the readdir cursor was "
        "live while the directory was being mutated -- unspecified behaviour "
        "that skips as readily as it repeats")


def test_a_symlink_at_an_owned_path_says_a_foreign_object_is_there(subject, tmp_path):
    """RB_B10. The ELOOP/ENOTDIR refusal carried `[foreign-object]` and nothing
    asserted it, so relabelling it `[not-proven]` was invisible. The two mean
    different things to an operator: one says someone else's object is standing
    at your path, the other says a transient failure -- and only the first is a
    reason to go looking."""
    d = str(tmp_path / "owned_then_symlinked")
    os.mkdir(d)
    st = os.lstat(d)
    ident = (st.st_dev, st.st_ino)
    os.rmdir(d)
    os.symlink(str(tmp_path / "elsewhere"), d)
    m = subject.m
    try:
        if subject.name == "bd-cut":
            ok, why = m._remove_owned_dir(d, ident, None)
        elif subject.name == "bd-footguns":
            ok, why = m._remove_owned_sandbox(d, ident, None)
        else:
            pytest.skip("_tmproot's remover returns a bare bool; its refusal "
                        "code is asserted through finish() elsewhere")
        assert ok is False
        assert R_FOREIGN in str(why), (
            "a symlink standing at an owned pathname was reported with a "
            f"generic code rather than as a foreign object: {why}")
    finally:
        os.unlink(d)


def test_the_cleanup_channel_starts_empty_on_every_check():
    """RB_B13. The module is imported once per pytest test module, so a second
    `cmd_check` in one process inherits the first's failures and refuses for a
    reason that was already reported -- and every test here loads a fresh
    module, which is exactly why nothing noticed."""
    m = _fg()
    m._CLEANUP_FAILURES[:] = ["/tmp/stale_from_a_previous_run: [not-proven] old"]
    rows = [{"id": "PASSER", "status": "active", "severity": "blocking",
             "rule": "r", "fix": "f",
             "detector": {"kind": "tool", "cmd": ["sh", "-c", "exit 0"],
                          "block_on_exit": []}}]
    rc, blob = _run_check(m, rows, str(REPO))
    assert rc == 0, (
        "a stale cleanup failure from an earlier invocation refused this one:\n"
        + blob[-400:])
    assert "stale_from_a_previous_run" not in blob


def test_finish_releases_the_descriptor_it_held(tmp_path):
    """RB_D7. `install()` opens one descriptor per process and `finish()` is
    the only thing that closes it. A leak here is invisible -- nothing user
    facing breaks -- until a long-lived process runs enough sessions to hit
    EMFILE, and by then the cause is far away. Section 0's rule that creating a
    resource is a promise to remove it applies to descriptors as much as to
    directories."""
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import _tmproot as t
    finally:
        sys.path.pop(0)
    saved = (t._ROOT, t._ROOT_IDENT, t._ROOT_FD, t._ROOT_FD_PATH,
             t._LAST_FAILURE, tempfile.tempdir)
    keep = os.environ.pop("KEEP_TEST_TMPDIRS", None)
    before = len(os.listdir("/proc/self/fd"))
    roots = []
    try:
        for _ in range(8):
            t._ROOT = None
            r = t.install()
            assert r
            roots.append(r)
            assert t.finish(0) is True
        after = len(os.listdir("/proc/self/fd"))
    finally:
        if keep is not None:
            os.environ["KEEP_TEST_TMPDIRS"] = keep
        cur = getattr(t, "_ROOT_FD", None)
        if cur is not None and cur != saved[2]:
            with contextlib.suppress(OSError):
                os.close(cur)
        (t._ROOT, t._ROOT_IDENT, t._ROOT_FD, t._ROOT_FD_PATH,
         t._LAST_FAILURE, tempfile.tempdir) = saved
        for r in roots:
            _force_rm(r)
    assert after <= before + 1, (
        f"descriptors grew {before} -> {after} across 8 install/finish cycles; "
        "the root descriptor is not being released")
