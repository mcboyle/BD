"""Per-run temp root: every mkdtemp in the suite lands in one place, reclaimed.

MEASURED on the fleet 2026-08-13: `/tmp` held 15392 entries on test5 and grew
2373 in a single capture round, on every host, forever. CLAUDE.md section 0
states the rule -- creating a path is a promise to remove it, and nothing gates
that promise -- and records the same shape at 744 directories. It reached 15000.

WHY THE ROOT AND NOT THE CALL SITES. There are 579 `mkdtemp` call sites in
`tests/` and **366 pass no prefix**, so their output is named `tmp*` and cannot
be attributed to a test by name: 6793 entries, 38% of the total, invisible to
any census working backwards from the directory. Fixing call sites closes the
attributable half and misses that one entirely. `mkdtemp` resolves its parent
through `tempfile.tempdir`, so pointing that at one per-process root covers
every call site, prefixed or not, and every one written later.

A SEPARATE MODULE so it can be DRIVEN, not just described. Living in conftest,
the only way to exercise it was to run pytest inside pytest -- and the first
attempt to do that proved nothing in both directions at once: the probe file
sat outside `tests/`, so conftest never loaded and both arms behaved
identically, while the assertions globbed the ALREADY-REDIRECTED temp dir and
so looked in the wrong place. Two harness defects, one green-looking test.
"""
from __future__ import annotations

import ctypes
import errno
import os
import pathlib
import platform
import shutil
import stat
import sys
import tempfile

# The real system temp directory, captured before anything redirects it.
SYSTEM_TMP = pathlib.Path(tempfile.gettempdir())

_ROOT: str | None = None

# Set when a removal was ATTEMPTED and did not complete. Deliberately distinct
# from finish() returning False after a FAILING run, which means "kept on
# purpose so the artifacts survive" -- conflating the two would add a false
# cleanup complaint to every red run (v3.66.1152).
_LAST_FAILURE: str | None = None

# (st_dev, st_ino) of the root install() created. None means UNKNOWN,
# which fails: a present pathname we cannot identify is never removed.
_ROOT_IDENT: tuple | None = None

# A DESCRIPTOR ON THE ROOT install() CREATED, plus THE PATH IT WAS OPENED ON.
# The second half is not bookkeeping, it is the safety property (v3.66.1154).
# `install()` runs in `pytest_configure` for every process in the suite, so
# `_ROOT_FD` is a live descriptor on the SESSION root at all times -- while
# four tracked tests legitimately hand-set `_ROOT` to a directory of their own
# without going through install(). A remover that trusted the descriptor
# because its identity matched `_ROOT_IDENT` would then walk the session root
# and delete every other test's temporary directory, mid-run. Measured on a
# literal implementation of exactly that: `live root contents: []`, and in one
# case behind a test that stayed GREEN.
#
# So the descriptor is used ONLY when it was opened on the path being asked
# about. Anything else falls back to the identity-checked pathname removal,
# which is what those tests have always exercised.
# WHICH refusal fired, in the vocabulary the three removers share. finish()
# returns a bare bool and both hooks only ever ask "did it go", so without
# this a caller cannot distinguish "renamed away" from "too deep to walk"
# from "a stranger is standing there" -- and CLAUDE.md section 10 records
# four mutants escaping precisely because every refusal looked the same.
_LAST_REASON: str | None = None
_ROOT_FD: int | None = None
_ROOT_FD_PATH: str | None = None


def _release_root_fd():
    """Pop, then close -- never the other way round.

    A descriptor number is REUSED the moment it is free, and `st_nlink == 0` on
    a reused number is a confident answer about somebody else's object.
    Measured: after a premature close and one intervening open, the same fd
    number reported nlink 0 while the owned directory sat on disk with its
    contents. Popping in the same statement is what makes that unreachable.
    """
    global _ROOT_FD, _ROOT_FD_PATH
    fd, _ROOT_FD, _ROOT_FD_PATH = _ROOT_FD, None, None
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def _root_fd_for(root):
    """The held descriptor, but only if it belongs to `root`."""
    return _ROOT_FD if (_ROOT_FD is not None and _ROOT_FD_PATH == root) else None


def _where(fd):
    """Where the object behind `fd` lives NOW, so a rename-away is
    recoverable rather than merely reported."""
    try:
        return os.readlink("/proc/self/fd/%d" % fd)
    except OSError:
        return "its new location could not be read"


def install() -> str | None:
    """Point `tempfile.tempdir` at a fresh per-process root. Returns its path.

    Keyed by pid via mkdtemp because xdist workers are separate processes: each
    makes its own and removes its own, so there is nothing to race on.
    """
    global _ROOT, _LAST_FAILURE
    if _ROOT is not None:
        return _ROOT                       # already installed; do not nest
    if os.environ.get("KEEP_TEST_TMPDIRS") == "1":
        return None
    global _ROOT_IDENT, _ROOT_FD, _ROOT_FD_PATH
    _ROOT = tempfile.mkdtemp(prefix="bd-testrun-", dir=str(SYSTEM_TMP))
    # THE IDENTITY OF THE OBJECT WE JUST CREATED (v3.66.1153), TAKEN FROM THE
    # DESCRIPTOR WE KEEP (v3.66.1154). Until 1153 this kept only the pathname,
    # so reclamation removed whatever directory later occupied that name.
    # Until 1154 the identity came from a SECOND lstat of the same path, which
    # is a second chance to be told about a different object, and the removal
    # then re-resolved the name a third time.
    #
    # A FAILURE HERE LEAVES BOTH UNSET, AND finish() REFUSES. It used to leave
    # _ROOT set with _ROOT_IDENT None, and finish() fell through to a removal
    # whose identity check is skipped when the identity is None -- an entirely
    # UNBOUND recursive deletion of whatever stood at the recorded path.
    try:
        _fd = os.open(_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        _LAST_FAILURE = None
        tempfile.tempdir = _ROOT
        return _ROOT
    _ROOT_FD, _ROOT_FD_PATH = _fd, _ROOT
    _st = os.fstat(_fd)
    _ROOT_IDENT = (_st.st_dev, _st.st_ino)
    _LAST_FAILURE = None
    tempfile.tempdir = _ROOT
    return _ROOT


# ---------------------------------------------------------------------------
# DESTRUCTIVE PRIMITIVES (v3.66.1154). THREE NEAR-IDENTICAL COPIES OF THIS
# BLOCK EXIST -- here, in tests/_tmproot.py and in toolchain/bin/bd-footguns --
# because _tmproot may import nothing from the repo and the bd-* tools are
# standalone scripts. tests/test_v3_66_1154_the_object_not_the_name.py runs ONE
# behavioural matrix against ALL THREE, so a drift between the copies is red
# rather than a fourth implementation nobody compares.
# ---------------------------------------------------------------------------

# Refusal CODES. CLAUDE.md section 10: when every refusal shares an exit code
# or a vague phrase, a test asserting "it refused" passes whichever guard
# fired, and four bd-jobs mutants escaped exactly that way. These name WHICH.
R_RENAMED = "[renamed-away]"
R_FOREIGN = "[foreign-object]"
R_UNPROVEN = "[not-proven]"
R_NO_IDENT = "[no-identity]"
R_TOO_DEEP = "[too-deep]"

_RENAME_NOREPLACE = 1 << 0
_SYS_renameat2 = {"x86_64": 316, "aarch64": 276}.get(platform.machine())
try:
    _LIBC = ctypes.CDLL(None, use_errno=True)
    _LIBC.syscall.restype = ctypes.c_long
except OSError:                                    # no libc: fall back below
    _LIBC = None


class _Refused(Exception):
    """A removal that must not proceed.

    DELIBERATELY NOT AN OSError. The OSError handlers around the walk exist to
    convert KERNEL failures into refusals; a refusal raised by our own identity
    logic must not be caught by them and re-described as an errno.
    """

    def __init__(self, code, detail):
        super().__init__("%s %s" % (code, detail))
        self.code, self.detail = code, detail

    @property
    def reason(self):
        return "%s %s" % (self.code, self.detail)


def _rename_noclobber(old, new, dir_fd):
    """Rename inside `dir_fd`, REFUSING to replace an existing `new`.

    `os.rename` replaces silently, and one caller of this is the path where a
    FOREIGN object has to be put back at a name a third party may since have
    re-created. Measured: a plain rename undo DESTROYS an empty directory
    standing at the restored name -- the exact act we just refused to perform,
    reintroduced inside the error handler that exists to be safe.

    `renameat2(RENAME_NOREPLACE)` turns that into EEXIST. It is reached through
    ctypes because `os` does not expose it; measured working on this kernel for
    xfs and tmpfs. Where the flag is rejected (ENOSYS on an old kernel, EINVAL
    or EOPNOTSUPP on a filesystem that does not implement it) the fallback is
    `os.rename`, and the return value says which ran -- "we guaranteed it" and
    "we could not guarantee it" must not look the same to a caller.
    """
    if _LIBC is not None and _SYS_renameat2 is not None:
        ctypes.set_errno(0)
        rc = _LIBC.syscall(ctypes.c_long(_SYS_renameat2),
                           ctypes.c_int(dir_fd), ctypes.c_char_p(os.fsencode(old)),
                           ctypes.c_int(dir_fd), ctypes.c_char_p(os.fsencode(new)),
                           ctypes.c_uint(_RENAME_NOREPLACE))
        if rc == 0:
            return True
        _eno = ctypes.get_errno()
        if _eno not in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
            raise OSError(_eno, os.strerror(_eno), old)
    os.rename(old, new, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    return False


def _private_name(base):
    """The name the destructive syscall uses instead of the well-known one.

    KEEPS THE CALLER'S OWN PREFIX. A run killed between the rename and the
    removal leaves this on disk, and a dot-prefixed random name would be
    invisible to the `bdcut_*`, `bdfg_sbx_*` and `bd-testrun-*` sweeps this
    project's whole leak accounting is built on -- turning a recognisable leak
    into an unrecognisable one, in the cut whose subject is leaks.
    """
    return "%s.bdrm-%s" % (base[:180], os.urandom(8).hex())


def _put_back(priv, name, fd):
    """Return a foreign object to the name it was found under.

    THIS CAN FAIL, and saying it cannot is how the undo became a second
    destructive path. Measured against a re-created `name`: a non-empty
    directory, a regular file and a symlink all make the undo raise, and an
    EMPTY directory is silently destroyed by a plain rename. Where the object
    cannot go back it is left under the private name and the caller SAYS SO --
    an operator told "we refused to delete your directory" while it sits under
    a name they have never seen has not been told anything.
    """
    try:
        _rename_noclobber(priv, name, fd)
        return " (it was put back untouched)"
    except OSError as _e:
        return (" -- and it could NOT be put back (%s); it is now named %r in "
                "that directory and nothing else will collect it" % (_e, priv))


def _walk_failed(e):
    """A walk that could not finish is INCOMPLETE, not finished-and-clean.

    The reason names the exception TYPE deliberately: a broad `except
    Exception` fails closed, which is right, but it also converts a genuine
    remover bug (TypeError, AttributeError) into something that reads like
    routine leakage. Naming the type is what keeps the two distinguishable.

    RecursionError is the measured case at depth ~1400, and on a host with a
    small RLIMIT_NOFILE the same walk dies of EMFILE first, so this must not be
    narrowed to RecursionError alone.
    """
    return ("%s the tree could not be walked to completion (%s: %s) -- the "
            "removal is INCOMPLETE and what remains is unknown"
            % (R_TOO_DEEP, type(e).__name__, e))


def _rmtree_fd(fd, dev):
    """Delete everything UNDER an open directory descriptor.

    READ ONCE, THEN ACT. Renaming entries while a readdir cursor is open is
    unspecified, and it is not theoretical here: measured on this host's XFS,
    renaming during the walk re-yielded 38 of 5000 entries under their new
    names. `list()` completes the readdir before anything moves. Anything
    created after that listing is not ours; the terminal rmdir will refuse with
    ENOTEMPTY and that is REPORTED rather than swallowed.

    EVERY ENTRY IS BOUND TO THE INODE THE READDIR REPORTED, and the destructive
    syscall never names it. The entry is first renamed to a private name that
    cannot clobber, then identified, and only then removed -- so an adversary
    who swaps the well-known name costs us a REVERSIBLE rename instead of an
    irreversible unlink, and is put back where they were found.

    v3.66.1153 opened every child BY NAME with nothing carried from the entry
    it had just read, so a directory renamed onto a child pathname mid-walk was
    entered and recursively emptied -- measured, victim inode nlink 0.
    """
    for entry in list(os.scandir(fd)):
        want = (dev, entry.inode())
        priv = _private_name(entry.name)
        try:
            _rename_noclobber(entry.name, priv, fd)
        except FileNotFoundError:
            continue          # listed, then vanished: not ours to account for
        st = os.stat(priv, dir_fd=fd, follow_symlinks=False)
        if (st.st_dev, st.st_ino) != want:
            raise _Refused(R_FOREIGN,
                           "%r is not the object this walk listed there%s"
                           % (entry.name, _put_back(priv, entry.name, fd)))
        if not stat.S_ISDIR(st.st_mode):
            os.unlink(priv, dir_fd=fd)
            continue
        child = os.open(priv, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=fd)
        try:
            cst = os.fstat(child)
            if (cst.st_dev, cst.st_ino) != want:
                raise _Refused(R_FOREIGN,
                               "%r changed identity between the rename and "
                               "the open" % entry.name)
            was, relaxed = stat.S_IMODE(cst.st_mode), False
            try:
                try:
                    _rmtree_fd(child, cst.st_dev)
                except PermissionError:
                    os.fchmod(child, 0o700)
                    relaxed = True
                    _rmtree_fd(child, cst.st_dev)
                # INSIDE THE DESCRIPTOR'S LIFETIME. Until v3.66.1154 this rmdir
                # sat after `finally: os.close(child)`, so when it failed there
                # was no descriptor left to reseal through and the child stayed
                # at 0o700 -- a directory reported as leaked, left less
                # protected than it was found, on every path.
                os.rmdir(priv, dir_fd=fd)
                if os.fstat(child).st_nlink != 0:
                    raise _Refused(R_UNPROVEN,
                                   "the entry removed for %r was not the "
                                   "object held open" % entry.name)
            except BaseException:
                if relaxed:
                    try:
                        os.fchmod(child, was)
                    except OSError:
                        pass
                # PUT IT BACK UNDER ITS OWN NAME. The private name exists only
                # for the duration of the destroy; a failure that leaves it in
                # place renames a directory inside a tree we are about to
                # report as leaked, so the report and the disk disagree.
                _put_back(priv, entry.name, fd)
                raise
        finally:
            os.close(child)

def _mark(code, why=""):
    global _LAST_REASON
    _LAST_REASON = ("%s %s" % (code, why)).strip()


def _force_rmtree(path: str, ident=None, held_fd=None) -> bool:
    """Remove the tree at `path`. True only if the object `ident` names is gone.

    WHY shutil.rmtree IS NOT USED HERE (v3.66.1153, and the reasons still
    stand). It acted on a PATHNAME, so it removed whatever directory occupied
    the root's name; its `onexc` handler called `func(p)` blindly and two of
    the seven functions shutil can hand it do not take that shape; and the
    resulting TypeError was caught by an `except TypeError` meant to detect an
    OLD rmtree signature, so on a dangling symlink it escaped `finish()`
    entirely and NEITHER pytest hook ran.

    v3.66.1154 adds what 1153 left out: identity for every CHILD, a destructive
    syscall that never names a well-known path, and a mode restored on every
    return that leaves the object behind -- this function captured no mode at
    all, so two of its refusal paths that bd-cut resealed were left relaxed.
    """
    fd, ours = held_fd, False
    if fd is None:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as e:
            if e.errno == errno.ENOENT:
                return True                  # nothing to remove IS success
            return False                     # symlink, non-directory, denied
        ours = True
    try:
        st = os.fstat(fd)
        if ident is not None and (st.st_dev, st.st_ino) != ident:
            _mark(R_FOREIGN, "the path holds a directory _tmproot did not create")
            return False                     # a replacement: never remove it
        was, relaxed = stat.S_IMODE(st.st_mode), False

        def _refuse(code=R_UNPROVEN, why=""):
            global _LAST_REASON
            _LAST_REASON = ("%s %s" % (code, why)).strip()
            if relaxed:
                try:
                    if os.fstat(fd).st_nlink != 0:
                        os.fchmod(fd, was)
                except OSError:
                    pass
            return False

        try:
            _rmtree_fd(fd, st.st_dev)
        except PermissionError:
            try:
                os.fchmod(fd, 0o700)
                relaxed = True
                _rmtree_fd(fd, st.st_dev)
            except _Refused as r:
                return _refuse(r.code, r.detail)
            except OSError as e:
                return _refuse(R_UNPROVEN, str(e))
            except Exception as e:
                return _refuse(R_TOO_DEEP, _walk_failed(e))
        except _Refused as r:
            return _refuse(r.code, r.detail)
        except OSError as e:
            return _refuse(R_UNPROVEN, str(e))
        except Exception as e:
            # NOT BaseException: KeyboardInterrupt must still stop a reclaim.
            # RecursionError and MemoryError are Exception and are caught here,
            # at the shallow frame -- a handler inside the walk would run with
            # the stack still exhausted.
            return _refuse(R_TOO_DEEP, _walk_failed(e))
        parent, name = os.path.dirname(path) or ".", os.path.basename(path)
        try:
            pfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            return _refuse()
        try:
            priv = _private_name(name)
            try:
                _rename_noclobber(name, priv, pfd)
            except OSError:
                return _refuse()
            try:
                ent = os.stat(priv, dir_fd=pfd, follow_symlinks=False)
            except OSError:
                return _refuse()
            if (ent.st_dev, ent.st_ino) != (st.st_dev, st.st_ino):
                _put_back(priv, name, pfd)
                return _refuse()
            try:
                os.rmdir(priv, dir_fd=pfd)
            except OSError:
                _put_back(priv, name, pfd)
                return _refuse()
        finally:
            os.close(pfd)
        # PROOF, not hope: the object we held open is the one that was
        # unlinked. Valid because the subject is a DIRECTORY -- os.link on one
        # is EPERM, so no second link can exist to confuse the count. Do not
        # reuse this form for a file, where nlink 1 after an unlink is normal.
        if os.fstat(fd).st_nlink != 0:
            return _refuse()
        return True
    finally:
        if ours:
            os.close(fd)


def finish(exitstatus: int) -> bool:
    """Remove the root. Returns True if it was removed.

    ARTIFACTS SURVIVE A FAILING RUN: a debugging directory deleted on the one
    run that needed it is a worse defect than the leak this closes.
    """
    global _ROOT
    if _ROOT is None:
        return False
    global _ROOT_IDENT
    root, _ROOT = _ROOT, None
    ident, _ROOT_IDENT = _ROOT_IDENT, None
    fd = _root_fd_for(root)                # None unless it belongs to `root`
    tempfile.tempdir = None                # new allocations leave the doomed tree
    try:
        if int(exitstatus) != 0:
            return False                   # KEPT ON PURPOSE, not a failure
        _mark("")
        if ident is None:
            # UNKNOWN, AND UNKNOWN FAILS. This read `if ident is None and
            # os.path.lexists(root)`, so when the NAME was absent it fell
            # through to a removal whose identity check is skipped for a None
            # identity: an entirely unbound recursive deletion of whatever
            # stood there. Measured at 63be0464 -- a foreign directory and its
            # payload destroyed, reported as success.
            _mark(R_NO_IDENT, "no creation identity was recorded")
            ok = False
        elif fd is not None:
            # THE OBJECT, NOT THE NAME (v3.66.1154).
            try:
                dead = os.fstat(fd).st_nlink == 0
            except OSError:
                dead = False
            if dead:
                ok = True                  # truly gone, whatever holds the name
            elif not os.path.lexists(root):
                # RENAMED AWAY: nothing at the path, and the root we made is
                # still on disk with every mkdtemp of the run inside it.
                # v3.66.1153 answered "absent, therefore clean".
                sys.stderr.write(
                    "\n_tmproot: PER-RUN TEMP ROOT RENAMED AWAY, NOT REMOVED: "
                    "%s\n  it is now at %s\n" % (root, _where(fd)))
                _mark(R_RENAMED, "the per-run root is on disk elsewhere")
                ok = False
            else:
                ok = _force_rmtree(root, ident, fd)
        else:
            ok = _force_rmtree(root, ident)
    finally:
        _release_root_fd()
    if not ok:
        global _LAST_FAILURE
        _LAST_FAILURE = root
        # REPORTED HERE, WHERE NO CALL SITE CAN DROP IT (v3.66.1151). Both
        # session-finish hooks -- this module's and tests/conftest.py's --
        # discard the return value, and `_ROOT` has already been cleared by the
        # time the removal is attempted, so a failure is unrecoverable AND
        # unreported and the run stays green. A cleanup that did not happen is
        # never silent; putting the report inside the function is the only
        # placement a caller cannot forget.
        sys.stderr.write(
            f"\n_tmproot: PER-RUN TEMP ROOT NOT REMOVED: {root}\n"
            f"  reason: {_LAST_REASON or R_UNPROVEN}\n"
            "  every mkdtemp in this run is under it, and nothing else will "
            "collect it.\n"
            "  recover with: chmod -R u+w '%s' && rm -rf '%s'\n" % (root, root))
    return ok


def failed_root():
    """The root a reclamation attempt could not remove, or None."""
    return _LAST_FAILURE


def finish_session(session, exitstatus):
    """The whole session-finish behaviour, in ONE place both hooks call.

    v3.66.1152. `finish()` reported honestly and returned False, and BOTH call
    sites -- this module's hook and tests/conftest.py's -- discarded that value.
    pytest computes its exit status from test outcomes alone, so a leaked
    per-run root left the run GREEN: every mkdtemp in the session lives under
    that root, and nothing else will ever collect it. A report nothing acts on
    is not a gate.

    NOT for a root kept deliberately: finish() returns False on a FAILING run
    because artifacts must survive the one run that needed them, and turning
    that into a second complaint would make every red run also look like a
    cleanup defect.
    """
    finish(exitstatus)
    if failed_root() is not None and getattr(session, "exitstatus", 0) in (0, None):
        session.exitstatus = 1


# Hook forms, so this file also works as a standalone `-p _tmproot` plugin.
def pytest_configure(config):
    install()


def pytest_sessionfinish(session, exitstatus):
    finish_session(session, exitstatus)
