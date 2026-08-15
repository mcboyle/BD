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
    def registered(self, d): raise NotImplementedError
    def reset(self):       pass


class _BdCut(_Subject):
    def make(self):
        return self.m._owned_tempdir("bdcut_1154_")

    def discard(self, d):
        return self.m._discard_tempdir(d)

    def reason(self, d):
        return str(self.m._LAST_DISCARD_ERROR.get(d) or "")

    def registered(self, d):
        return d in self.m._TEMPDIRS

    def reset(self):
        for d in list(self.m._TEMPDIRS):
            _force_rm(d)
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

    def registered(self, d):
        return d in self.m._SANDBOX_IDENT

    def reset(self):
        for d in list(self.m._SANDBOX_IDENT):
            _force_rm(d)
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

    def registered(self, d):
        return self.m.failed_root() is not None

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


@pytest.fixture(autouse=True)
def _reclaim_footgun_sandboxes():
    """Some tests here make a sandbox removal FAIL on purpose, so bd-footguns
    correctly refuses it and the residue is THIS FILE'S to collect.

    Measured under KEEP_TEST_TMPDIRS=1 -- the only mode that can see it, since
    the redirection erases everything on a green run: this file leaked one
    bdfg_sbx_* per run before the sweep. No assertion in this file reads
    bdfg_* residue, so sweeping it cannot mask a production leak.
    """
    t = pathlib.Path(tempfile.gettempdir())

    def snap():
        return set(t.glob("bdfg_sbx_*")) | set(t.glob("bdfg_selftest_*"))

    before = snap()
    yield
    for leftover in snap() - before:
        _force_rm(leftover)


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
    made = []
    orig_make = s.make

    def make():
        d = orig_make()
        made.extend([d, d + ".moved", d + ".hidden"])
        return d
    s.make = make
    s.also = made.append
    try:
        yield s
    finally:
        s.reset()
        for d in made:
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
        assert subject.registered(d), (
            "an unaccounted-for directory was unregistered, destroying the "
            "only record that it exists")
    finally:
        _force_rm(moved)


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
    assert not subject.registered(d)


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
        assert R_UNPROVEN in subject.reason(d) or R_FOREIGN in subject.reason(d), \
            subject.reason(d)[-300:]
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
    after = stat.S_IMODE(os.lstat(target).st_mode) if os.path.lexists(target) else None
    assert after is not None, "the object was removed despite the injected failure"
    assert after == before, (
        f"a directory reported as NOT REMOVED was left at {oct(after)}; it was "
        f"{oct(before)} and nothing removed it, so the relaxation this "
        "remover applied as a retry was never undone")
    subprocess.run(["chmod", "-R", "u+rwx", d], capture_output=True)


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
    assert "OK -- no active footgun violated" not in blob


@pytest.mark.parametrize("label,row,want_zero", [
    ("blocking_kind_none",
     {"severity": "blocking", "detector": {"kind": "none"}}, False),
    ("advisory_kind_none",
     {"severity": "advisory", "detector": {"kind": "none"}}, True),
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
        assert "OK -- no active footgun violated" not in blob


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
