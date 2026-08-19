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
import fcntl
import json
import os
import pathlib
import platform
import shutil
import stat
import sys
import tempfile
import time

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

# A kernel-owned lock is the liveness fact a SIGKILL cannot fake.  The marker
# remains RUNNING when the process dies without reaching finish(); once the
# kernel releases this descriptor's flock, a sweeper can classify that same
# on-disk state as ABANDONED rather than LIVE.
_MARKER_NAME = ".bd-testrun"
_LOCK_NAME = ".bd-testrun.lock"
DURABLE_STATES = (
    "LIVE", "KEPT_FOR_FORENSICS", "RECLAIMABLE", "ABANDONED", "UNKNOWN")
_MARKER_LOCK_FDS: dict[tuple, int] = {}
_RUN_RECORDS: dict[tuple, dict] = {}


def _write_run_record(fd: int, state: str, *, exitstatus=None,
                      reason: str | None = None) -> None:
    """Atomically publish the durable outcome inside the held root object."""
    st = os.fstat(fd)
    ident = (st.st_dev, st.st_ino)
    if ident not in _RUN_RECORDS:
        raise RuntimeError("run-root identity has no registered marker record")
    record = dict(_RUN_RECORDS[ident])
    record["state"] = state
    record["updated_at"] = time.time()
    if exitstatus is not None:
        record["exitstatus"] = exitstatus
    if reason:
        record["reason"] = reason
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    tmp = "%s.tmp-%d-%s" % (_MARKER_NAME, os.getpid(), os.urandom(4).hex())
    out = None
    published = False
    try:
        out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                      0o600, dir_fd=fd)
        view = memoryview(payload)
        while view:
            view = view[os.write(out, view):]
        os.fsync(out)
        os.close(out)
        out = None
        os.rename(tmp, _MARKER_NAME, src_dir_fd=fd, dst_dir_fd=fd)
        published = True
        os.fsync(fd)
    finally:
        if out is not None:
            os.close(out)
        if not published:
            try:
                os.unlink(tmp, dir_fd=fd)
            except FileNotFoundError:
                pass


def _publish_run_record(fd: int, state: str, *, exitstatus=None,
                        reason: str | None = None) -> None:
    """Publish while restoring a root whose owner-write bit was removed."""
    st = os.fstat(fd)
    mode = stat.S_IMODE(st.st_mode)
    relaxed = False
    try:
        try:
            _write_run_record(fd, state, exitstatus=exitstatus, reason=reason)
        except PermissionError:
            os.fchmod(fd, mode | stat.S_IWUSR | stat.S_IXUSR)
            relaxed = True
            _write_run_record(fd, state, exitstatus=exitstatus, reason=reason)
    finally:
        if relaxed:
            os.fchmod(fd, mode)
            if stat.S_IMODE(os.fstat(fd).st_mode) != mode:
                raise OSError(errno.EIO, "run-root mode restoration did not verify")


def _retention_degraded(detail: str) -> None:
    """Make a lost marker decision visible without preventing the test run."""
    if not _LAST_REASON:
        _mark("[retention-marker]", detail)
    sys.stderr.write("\n_tmproot: RETENTION MARKER DEGRADED: %s\n" % detail)


def _publish_or_degrade(fd: int, state: str, *, exitstatus=None,
                        reason: str | None = None) -> bool:
    try:
        _publish_run_record(fd, state, exitstatus=exitstatus, reason=reason)
        return True
    except Exception as exc:
        _retention_degraded("%s publish failed (%s: %s)" %
                            (state, type(exc).__name__, exc))
        return False


def _release_marker_lock(ident) -> None:
    fd = _MARKER_LOCK_FDS.pop(ident, None)
    _RUN_RECORDS.pop(ident, None)
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _ensure_named_lock(fd: int, ident) -> None:
    """Leave a lock entry inside a root that survived partial removal."""
    if ident not in _RUN_RECORDS:
        return
    mode = stat.S_IMODE(os.fstat(fd).st_mode)
    relaxed_mode = mode | stat.S_IWUSR | stat.S_IXUSR
    lock_fd = None
    try:
        if relaxed_mode != mode:
            os.fchmod(fd, relaxed_mode)
        try:
            lock_fd = os.open(_LOCK_NAME, os.O_RDWR | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            lock_fd = os.open(
                _LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_EXCL,
                0o600, dir_fd=fd)
        lock_st = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_st.st_mode):
            raise OSError(errno.EINVAL, "run-root lock is not a regular file")
        if ident in _RUN_RECORDS:
            _RUN_RECORDS[ident]["lock_dev"] = lock_st.st_dev
            _RUN_RECORDS[ident]["lock_ino"] = lock_st.st_ino
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        if relaxed_mode != mode:
            os.fchmod(fd, mode)
            if stat.S_IMODE(os.fstat(fd).st_mode) != mode:
                raise OSError(
                    errno.EIO, "run-root lock mode restoration did not verify")


def _release_root_fd(ident):
    """Pop first, then close only while the descriptor still identifies root.

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
            st = os.fstat(fd)
        except OSError:
            return
        if ident is not None and (st.st_dev, st.st_ino) == ident:
            os.close(fd)


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
    marker_lock_fd = None
    try:
        marker_lock_fd = os.open(
            _LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=_fd)
        fcntl.flock(marker_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _MARKER_LOCK_FDS[_ROOT_IDENT] = marker_lock_fd
        _RUN_RECORDS[_ROOT_IDENT] = {
            "schema": 1,
            "pid": os.getpid(),
            "host": platform.node(),
            "started_at": time.time(),
            "root_dev": _st.st_dev,
            "root_ino": _st.st_ino,
        }
        _lock_st = os.fstat(marker_lock_fd)
        _RUN_RECORDS[_ROOT_IDENT].update(
            lock_dev=_lock_st.st_dev, lock_ino=_lock_st.st_ino)
        _publish_run_record(_fd, "RUNNING")
    except Exception as exc:
        # Marker/lock accounting is retention metadata, not a prerequisite for
        # running tests.  A full tmpfs or exhausted descriptor table must not
        # abort pytest_configure.  Drop every partial in-memory authority,
        # release the kernel lock if acquired, and make the degraded state
        # unmissable; the descriptor-bound session cleanup still operates.
        _RUN_RECORDS.pop(_ROOT_IDENT, None)
        held = _MARKER_LOCK_FDS.pop(_ROOT_IDENT, None)
        if held is not None:
            try:
                fcntl.flock(held, fcntl.LOCK_UN)
            finally:
                os.close(held)
            marker_lock_fd = None
        elif marker_lock_fd is not None:
            os.close(marker_lock_fd)
            marker_lock_fd = None
        try:
            os.unlink(_LOCK_NAME, dir_fd=_fd)
        except FileNotFoundError:
            pass
        except OSError as cleanup_exc:
            _retention_degraded("unheld lock-name cleanup failed (%s: %s)" %
                                (type(cleanup_exc).__name__, cleanup_exc))
        _retention_degraded("initial publish failed (%s: %s)" %
                            (type(exc).__name__, exc))
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


def _rename_noclobber(old, new, dir_fd, allow_fallback=True):
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
    if not allow_fallback:
        # THE UNDO MAY NOT FALL BACK. `os.rename` replaces silently, and the
        # only caller that passes allow_fallback=False is the one restoring a
        # FOREIGN object to a name a third party may have re-created -- where
        # replacing is exactly the destruction being refused. The syscall table
        # here has two architectures in it and any filesystem may answer
        # EINVAL, so this is reachable, and leaving the object under its
        # private name with a report is strictly better than destroying
        # something to tidy up.
        raise OSError(errno.EOPNOTSUPP,
                      "cannot rename without replacing on this platform", old)
    os.rename(old, new, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    return False


def _private_name(base):
    """The name the destructive syscall uses instead of the well-known one.

    KEEPS THE CALLER'S OWN PREFIX. A run killed between the rename and the
    removal leaves this on disk, and a dot-prefixed random name would be
    invisible to the `bdcut_*`, `bdfg_sbx_*` and `bd-testrun-*` sweeps this
    project's whole leak accounting is built on -- turning a recognisable leak
    into an unrecognisable one, in the cut whose subject is leaks.

    TRUNCATED IN BYTES, NOT CHARACTERS (v3.66.1154, found by adversarial
    review). NAME_MAX is 255 BYTES; a character slice let 117 two-byte UTF-8
    characters (234 bytes) plus the 22-byte suffix reach 256, so the rename
    got ENAMETOOLONG and the remover REFUSED a removal it had to perform --
    a green pytest run turned red and leaked its whole per-run root, on a
    filename any program is free to create. Over-sensitivity is a soundness
    bug (CLAUDE.md section 0), and this one was a regression against the
    version it replaced.

    A byte slice can split a multi-byte sequence; `os.fsdecode` keeps the
    stray bytes as surrogates and `os.fsencode` puts them back unchanged, so
    the name round-trips to the same bytes the kernel sees. Two long names can
    therefore truncate to the same prefix -- which is why the 64 random bits
    are appended AFTER the truncation, and why every rename that uses this
    name refuses to clobber rather than replacing.
    """
    suffix = ".bdrm-%s" % os.urandom(8).hex()
    raw = os.fsencode(base)[:255 - len(suffix)]
    return os.fsdecode(raw) + suffix


def _put_back(priv, name, fd):
    """Compatibility diagnostic; automatic pathname put-back is forbidden."""
    return (" -- automatic put-back is unsafe and was not attempted; the "
            "private entry %r was left untouched" % priv)


def _add_note(error, note):
    add = getattr(error, "add_note", None)
    if add is not None:
        add(note)


def _recover_private(priv, name, fd, error, expected=None, held=None, extra=""):
    """Report failed private-name work without a racy pathname put-back."""
    controls = (KeyboardInterrupt, SystemExit, MemoryError)
    propagating = error if isinstance(error, controls) else None
    if getattr(error, "__notes__", None):
        extra += " -- " + " -- ".join(error.__notes__)
    location = "unavailable"
    held_path = parent_path = None
    if held is not None:
        try:
            hst = os.fstat(held)
            held_ident = (hst.st_dev, hst.st_ino)
            if expected is None:
                expected = held_ident
            elif held_ident != expected:
                extra += (" -- held descriptor identity %r does not match "
                          "expected %r" % (held_ident, expected))
            else:
                held_path = os.readlink("/proc/self/fd/%d" % held)
                parent_path = os.readlink("/proc/self/fd/%d" % fd)
                location = repr(held_path)
        except BaseException as e:
            extra += " -- held identity/location unavailable (%s: %s)" % (
                type(e).__name__, e)
            if propagating is None and isinstance(e, controls):
                propagating = e
    try:
        pst = os.stat(priv, dir_fd=fd, follow_symlinks=False)
    except BaseException as e:
        diagnostic = (" -- expected owned identity %r; private identity could "
                      "not be verified (%s); owned object location is %s" %
                      (expected, e, location))
        if propagating is None and isinstance(e, controls):
            propagating = e
    else:
        if expected is None or (pst.st_dev, pst.st_ino) != expected:
            diagnostic = (" -- expected owned identity %r; private name %r is "
                          "a foreign identity; it was "
                          "left untouched; owned object location is %s"
                          % (expected, priv, location))
        else:
            diagnostic = (" -- expected owned identity %r; verified owned "
                          "object remains at private name %r" %
                          (expected, priv))
    if propagating is not None:
        try:
            if propagating is not error:
                _add_note(propagating, "primary cleanup failure (%s: %s)" %
                          (type(error).__name__, error))
            _add_note(propagating, "private-name recovery: %s%s" %
                      (diagnostic.lstrip(), extra))
        except BaseException:
            if isinstance(error, controls):
                raise error
            raise
        raise propagating
    code = error.code if isinstance(error, _Refused) else R_UNPROVEN
    detail = error.detail if isinstance(error, _Refused) else "%s: %s" % (type(error).__name__, error)
    raise _Refused(code, "%s%s%s" % (detail, diagnostic, extra)) from error


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


def _max_walk_depth():
    """How deep this walk may go before it refuses.

    NOT a taste question: at the interpreter's own recursion limit the
    UNWINDING calls fail too, so the handler that puts a foreign object back
    under its real name cannot run and the tree is left holding a private
    `.bdrm-*` name -- measured by review at depth 497 in all three removers,
    with `_put_back`'s own comment promising it could not happen. Refusing
    early leaves the stack the recovery needs.
    """
    return max(50, (sys.getrecursionlimit() - 100) // 3)


def _rmtree_fd(fd, dev, depth=0):
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
    who swaps the well-known name is detected after the no-clobber private
    rename and cleanup refuses without further pathname mutation. Linux has
    no inode-bound unlinkat: substitution after the final private-name identity
    check remains a terminal namespace race, and the held descriptor proves
    only whether our inode reached its required postcondition.

    v3.66.1153 opened every child BY NAME with nothing carried from the entry
    it had just read, so a directory renamed onto a child pathname mid-walk was
    entered and recursively emptied -- measured, victim inode nlink 0.
    """
    if depth > _max_walk_depth():
        raise _Refused(R_TOO_DEEP,
                       "the tree is deeper than this walk may safely recurse "
                       "(%d levels); refusing while there is still stack to "
                       "undo with" % depth)
    for entry in list(os.scandir(fd)):
        want = (dev, entry.inode())
        anchor = child = None
        priv = _private_name(entry.name)
        relaxed = False
        was = None

        def _close_once(value, label):
            if value is None:
                return "", None
            try:
                os.close(value)
                return "", None
            except BaseException as close_error:
                return (" -- %s close failed; descriptor state is unknown "
                        "(%s: %s)" % (label, type(close_error).__name__,
                                      close_error)), close_error

        def _held_location(value):
            if value is None:
                return ""
            try:
                hst = os.fstat(value)
                got = (hst.st_dev, hst.st_ino)
                if got != want:
                    return " -- held identity %r does not match expected %r" % (got, want)
                return " -- held owned object location is %r" % os.readlink(
                    "/proc/self/fd/%d" % value)
            except OSError as loc_error:
                return " -- held owned-object location unavailable (%s)" % loc_error

        def _close_then_raise(primary, value, label):
            note, close_error = _close_once(value, label)
            if close_error is not None:
                if isinstance(primary,
                              (KeyboardInterrupt, SystemExit, MemoryError)):
                    _add_note(primary, note.strip())
                    raise primary
                if isinstance(close_error,
                              (KeyboardInterrupt, SystemExit, MemoryError)):
                    _add_note(close_error, "primary cleanup failure (%s: %s)" %
                              (type(primary).__name__, primary))
                    raise close_error
                if isinstance(primary, Exception):
                    raise _Refused(R_UNPROVEN, "%s: %s%s" %
                                   (type(primary).__name__, primary, note)) from primary
                _add_note(primary, note.strip())
            raise primary

        def _abort(primary):
            nonlocal anchor, child
            notes = _held_location(child if child is not None else anchor)
            propagating = (primary if isinstance(
                primary, (KeyboardInterrupt, SystemExit, MemoryError)) else None)
            if relaxed:
                restore_fd = child if child is not None else anchor
                try:
                    if child is not None:
                        os.fchmod(child, was)
                    elif anchor is not None:
                        os.chmod("/proc/self/fd/%d" % anchor, was)
                    rst = os.fstat(restore_fd)
                    if stat.S_IMODE(rst.st_mode) != was:
                        raise OSError(errno.EIO, "restored mode did not verify")
                except BaseException as restore_error:
                    notes += " -- mode restoration failed (%s: %s)" % (
                        type(restore_error).__name__, restore_error)
                    if (propagating is None and isinstance(
                            restore_error,
                            (KeyboardInterrupt, SystemExit, MemoryError))):
                        propagating = restore_error
            value, child = child, None
            close_note, close_error = _close_once(value, "readable descriptor")
            notes += close_note
            if (propagating is None and isinstance(
                    close_error, (KeyboardInterrupt, SystemExit, MemoryError))):
                propagating = close_error
            value, anchor = anchor, None
            close_note, close_error = _close_once(value, "anchor descriptor")
            notes += close_note
            if (propagating is None and isinstance(
                    close_error, (KeyboardInterrupt, SystemExit, MemoryError))):
                propagating = close_error
            if propagating is not None and propagating is not primary:
                notes += " -- primary cleanup failure (%s: %s)" % (
                    type(primary).__name__, primary)
            _recover_private(priv, entry.name, fd,
                             propagating or primary, want, None, notes)

        try:
            anchor = os.open(entry.name, os.O_PATH | os.O_NOFOLLOW, dir_fd=fd)
            ast = os.fstat(anchor)
            if (ast.st_dev, ast.st_ino) != want:
                raise _Refused(R_FOREIGN,
                               "%r changed before an ownership anchor was acquired"
                               % entry.name)
        except FileNotFoundError:
            continue
        except BaseException as error:
            value, anchor = anchor, None
            _close_then_raise(error, value, "anchor descriptor")
        try:
            _rename_noclobber(entry.name, priv, fd)
        except FileNotFoundError:
            value, anchor = anchor, None
            note, close_error = _close_once(value, "anchor descriptor")
            if close_error is not None:
                if isinstance(close_error,
                              (KeyboardInterrupt, SystemExit, MemoryError)):
                    raise close_error
                raise _Refused(R_UNPROVEN, note.strip()) from close_error
            continue
        except BaseException as error:
            value, anchor = anchor, None
            _close_then_raise(error, value, "anchor descriptor")
        try:
            st = os.stat(priv, dir_fd=fd, follow_symlinks=False)
        except BaseException as error:
            _abort(error)
        if (st.st_dev, st.st_ino) != want:
            _abort(_Refused(R_FOREIGN,
                            "%r is not the object this walk listed there"
                            % entry.name))
        if not stat.S_ISDIR(ast.st_mode):
            before_links = ast.st_nlink
            try:
                os.unlink(priv, dir_fd=fd)
                after = os.fstat(anchor)
                if ((after.st_dev, after.st_ino) != want or
                        after.st_nlink != before_links - 1):
                    raise _Refused(R_UNPROVEN,
                                   "unlink did not remove the held object")
            except BaseException as error:
                _abort(error)
            value, anchor = anchor, None
            close_note, close_error = _close_once(value, "anchor descriptor")
            if close_error is not None:
                if isinstance(close_error,
                              (KeyboardInterrupt, SystemExit, MemoryError)):
                    raise close_error
                raise _Refused(R_UNPROVEN, close_note.strip()) from close_error
            continue
        was = stat.S_IMODE(ast.st_mode)
        bound = "/proc/self/fd/%d" % anchor
        try:
            try:
                child = os.open(bound, os.O_RDONLY | os.O_DIRECTORY)
            except PermissionError:
                relaxed = True
                os.chmod(bound, 0o700)
                child = os.open(bound, os.O_RDONLY | os.O_DIRECTORY)
            cst = os.fstat(child)
            if (cst.st_dev, cst.st_ino) != want:
                raise _Refused(R_FOREIGN,
                               "%r changed before readable acquisition"
                               % entry.name)
        except BaseException as error:
            _abort(error)
        value, anchor = anchor, None
        close_note, close_error = _close_once(value, "anchor descriptor")
        if close_error is not None:
            _add_note(close_error, close_note.strip())
            _abort(close_error)
        try:
            try:
                _rmtree_fd(child, cst.st_dev, depth + 1)
            except PermissionError:
                relaxed = True
                os.fchmod(child, 0o700)
                _rmtree_fd(child, cst.st_dev, depth + 1)
            os.rmdir(priv, dir_fd=fd)
            if os.fstat(child).st_nlink != 0:
                raise _Refused(R_UNPROVEN,
                               "the entry removed for %r was not the object held open"
                               % entry.name)
        except BaseException as error:
            _abort(error)
        value, child = child, None
        close_note, close_error = _close_once(value, "readable descriptor")
        if close_error is not None:
            if isinstance(close_error,
                          (KeyboardInterrupt, SystemExit, MemoryError)):
                raise close_error
            raise _Refused(R_UNPROVEN, close_note.strip()) from close_error

def _walk_split(e):
    """(code, detail) -- `_walk_failed` already prefixes the code, so passing
    its whole string as a detail printed `[too-deep] [too-deep] ...`."""
    return R_TOO_DEEP, _walk_failed(e)[len(R_TOO_DEEP):].strip()


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
            _mark(R_FOREIGN, "the path is a symlink, is not a directory, or cannot be opened (%s)" % e)
            return False                     # symlink, non-directory, denied
        ours = True
    was, relaxed = None, False
    propagating = None

    def _refuse(code=R_UNPROVEN, why=""):
        global _LAST_REASON
        if relaxed and was is not None:
            try:
                os.fchmod(fd, was)
                if stat.S_IMODE(os.fstat(fd).st_mode) != was:
                    raise OSError(errno.EIO, "restored mode did not verify")
            except (KeyboardInterrupt, SystemExit, MemoryError) as restore_error:
                why += " -- mode restoration failed (%s: %s)" % (type(restore_error).__name__, restore_error)
                _LAST_REASON = ("%s %s" % (code, why)).strip()
                _add_note(restore_error, _LAST_REASON)
                raise
            except Exception as restore_error:
                why += " -- mode restoration failed (%s: %s)" % (type(restore_error).__name__, restore_error)
        _LAST_REASON = ("%s %s" % (code, why)).strip()
        return False
    try:
        st = os.fstat(fd)
        if ident is not None and (st.st_dev, st.st_ino) != ident:
            _mark(R_FOREIGN, "the path holds a directory _tmproot did not create")
            return False                     # a replacement: never remove it
        was, relaxed = stat.S_IMODE(st.st_mode), False
        try:
            named = os.lstat(path)
        except FileNotFoundError:
            return _refuse(R_RENAMED,
                           "the owned root is at %r, not its recorded name"
                           % _where(fd))
        except OSError as error:
            return _refuse(R_UNPROVEN,
                           "the recorded root name could not be identified (%s)"
                           % error)
        if (named.st_dev, named.st_ino) != (st.st_dev, st.st_ino):
            return _refuse(R_FOREIGN,
                           "the recorded root name is foreign and was left "
                           "untouched; the owned root is at %r" % _where(fd))

        parent_propagating = None
        try:
            _rmtree_fd(fd, st.st_dev)
        except PermissionError:
            try:
                relaxed = True
                os.fchmod(fd, 0o700)
                _rmtree_fd(fd, st.st_dev)
            except _Refused as r:
                return _refuse(r.code, r.detail)
            except OSError as e:
                return _refuse(R_UNPROVEN, str(e))
            except (KeyboardInterrupt, SystemExit, MemoryError) as e:
                try:
                    _refuse(R_UNPROVEN, "%s: %s" % (type(e).__name__, e))
                except (KeyboardInterrupt, SystemExit, MemoryError) as restore_error:
                    if restore_error is not e:
                        _add_note(e, "mode restoration control failure (%s: %s)" %
                                  (type(restore_error).__name__, restore_error))
                _add_note(e, _LAST_REASON)
                raise
            except Exception as e:
                return _refuse(*_walk_split(e))
        except _Refused as r:
            return _refuse(r.code, r.detail)
        except OSError as e:
            return _refuse(R_UNPROVEN, str(e))
        except (KeyboardInterrupt, SystemExit, MemoryError) as e:
            try:
                _refuse(R_UNPROVEN, "%s: %s" % (type(e).__name__, e))
            except (KeyboardInterrupt, SystemExit, MemoryError) as restore_error:
                if restore_error is not e:
                    _add_note(e, "mode restoration control failure (%s: %s)" %
                              (type(restore_error).__name__, restore_error))
            _add_note(e, _LAST_REASON)
            raise
        except Exception as e:
            # NOT BaseException: KeyboardInterrupt must still stop a reclaim.
            # RecursionError is converted at the shallow frame; MemoryError is
            # handled above and propagated after accounting because recovery
            # must not disguise resource exhaustion as an ordinary refusal.
            return _refuse(*_walk_split(e))
        parent, name = os.path.dirname(path) or ".", os.path.basename(path)
        try:
            pfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            return _refuse()
        try:
            priv = _private_name(name)
            try:
                named = os.stat(name, dir_fd=pfd, follow_symlinks=False)
            except OSError as e:
                return _refuse(R_UNPROVEN,
                               "the recorded root name could not be revalidated (%s)" % e)
            if (named.st_dev, named.st_ino) != (st.st_dev, st.st_ino):
                return _refuse(R_FOREIGN,
                               "the recorded root name became foreign and was left "
                               "untouched; the owned root is at %r" % _where(fd))
            try:
                _rename_noclobber(name, priv, pfd)
            except OSError as e:
                return _refuse(R_UNPROVEN, str(e))
            try:
                ent = os.stat(priv, dir_fd=pfd, follow_symlinks=False)
            except BaseException as e:
                try:
                    _recover_private(priv, name, pfd, e, ident, fd)
                except (KeyboardInterrupt, SystemExit, MemoryError):
                    raise
                except Exception as recovered:
                    return _refuse(R_UNPROVEN, str(recovered))
            if (ent.st_dev, ent.st_ino) != (st.st_dev, st.st_ino):
                return _refuse(R_FOREIGN,
                               "the private name %r is foreign; it was left "
                               "untouched and the owned root location is %r"
                               % (priv, _where(fd)))
            try:
                os.rmdir(priv, dir_fd=pfd)
            except BaseException as e:
                try:
                    _recover_private(priv, name, pfd, e, ident, fd)
                except (KeyboardInterrupt, SystemExit, MemoryError):
                    raise
                except Exception as recovered:
                    return _refuse(R_UNPROVEN, str(recovered))
        except BaseException as escaped:
            parent_propagating = escaped
            raise
        finally:
            if parent_propagating is not None and relaxed:
                try:
                    os.fchmod(fd, was)
                    if stat.S_IMODE(os.fstat(fd).st_mode) != was:
                        raise OSError(errno.EIO, "restored mode did not verify")
                except BaseException as restore_error:
                    _add_note(parent_propagating, "mode restoration failed (%s: %s)" %
                              (type(restore_error).__name__, restore_error))
            try:
                os.close(pfd)
            except BaseException as close_error:
                note = "parent descriptor close failed; state unknown (%s: %s)" % (type(close_error).__name__, close_error)
                if parent_propagating is not None:
                    _add_note(parent_propagating, note)
                elif isinstance(close_error,
                                (KeyboardInterrupt, SystemExit, MemoryError)):
                    raise
                elif isinstance(close_error, Exception):
                    return _refuse(R_UNPROVEN, note)
                else:
                    raise
        # PROOF, not hope: the object we held open is the one that was
        # unlinked. Valid because the subject is a DIRECTORY -- os.link on one
        # is EPERM, so no second link can exist to confuse the count. Do not
        # reuse this form for a file, where nlink 1 after an unlink is normal.
        if os.fstat(fd).st_nlink != 0:
            return _refuse(R_UNPROVEN,
                           "the entry removed was not the root held open")
        return True
    except BaseException as escaped:
        propagating = escaped
        raise
    finally:
        if ours:
            try:
                os.close(fd)
            except BaseException as close_error:
                note = "owned descriptor close failed; state unknown (%s: %s)" % (type(close_error).__name__, close_error)
                if propagating is not None:
                    _add_note(propagating, note)
                elif isinstance(close_error,
                                (KeyboardInterrupt, SystemExit, MemoryError)):
                    raise
                elif isinstance(close_error, Exception):
                    # close may have succeeded before its wrapper raised. The
                    # numeric slot can already denote a foreign object, so it
                    # is no longer valid authority for mode restoration.
                    relaxed = False
                    return _refuse(R_UNPROVEN, note)
                else:
                    raise


def finish(exitstatus: int) -> bool:
    """Remove the root. Returns True if it was removed.

    ARTIFACTS SURVIVE A FAILING RUN: a debugging directory deleted on the one
    run that needed it is a worse defect than the leak this closes.
    """
    global _ROOT, _LAST_FAILURE
    if _ROOT is None:
        return False
    global _ROOT_IDENT
    root, _ROOT = _ROOT, None
    ident, _ROOT_IDENT = _ROOT_IDENT, None
    fd = _root_fd_for(root)                # None unless recorded for `root`
    fd_stat = None
    if fd is not None:
        try:
            fd_stat = os.fstat(fd)
        except OSError:
            pass
        if (ident is None or fd_stat is None or
                (fd_stat.st_dev, fd_stat.st_ino) != ident):
            # The saved number is stale or recycled. It is foreign: do not
            # close it or pass it to the remover. Detach only the bookkeeping;
            # the existing no-fd path safely reopens and validates `root`.
            global _ROOT_FD, _ROOT_FD_PATH
            if _ROOT_FD == fd and _ROOT_FD_PATH == root:
                _ROOT_FD, _ROOT_FD_PATH = None, None
            fd, fd_stat = None, None
    record_fd, record_fd_owned = fd, False
    if record_fd is None and ident in _RUN_RECORDS:
        try:
            reopened = os.open(
                root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            reopened_st = os.fstat(reopened)
            if (reopened_st.st_dev, reopened_st.st_ino) != ident:
                raise OSError(errno.ESTALE,
                              "reopened run root has a different identity")
            record_fd, record_fd_owned = reopened, True
        except Exception as exc:
            if "reopened" in locals():
                os.close(reopened)
            _retention_degraded("terminal record descriptor unavailable "
                                "(%s: %s)" % (type(exc).__name__, exc))
    # WHERE THE ROOT ACTUALLY IS, read while the descriptor is still open.
    # The report below runs AFTER the finally that releases it, so resolving
    # it there produced "its new location could not be read" -- a remedy line
    # naming nothing, which is barely better than one naming the wrong thing.
    _actual = root
    tempfile.tempdir = None                # new allocations leave the doomed tree
    propagating = None
    try:
        # A STATUS WE CANNOT READ IS NOT ZERO. `int(exitstatus)` sat outside
        # this try, so a non-integer raised TypeError after _ROOT had already
        # been cleared -- root on disk, unreported, and unrecoverable because
        # the only record of it had just been dropped. Moving it inside is not
        # enough: a session hook that raises is still a hook that does not run.
        # An unreadable status is treated as a FAILING run, which KEEPS the
        # artifacts -- the safe direction, since the alternative is deleting a
        # debugging tree on a guess.
        status_readable = True
        try:
            _status = int(exitstatus)
        except (TypeError, ValueError):
            _status = 1
            status_readable = False
        if _status != 0:
            if record_fd is not None:
                if status_readable:
                    _publish_or_degrade(
                        record_fd, "KEPT_FOR_FORENSICS", exitstatus=_status,
                        reason="pytest exitstatus is nonzero")
                else:
                    _publish_or_degrade(
                        record_fd, "UNKNOWN",
                        reason="pytest exitstatus is unreadable")
            return False                   # KEPT ON PURPOSE, not a failure
        if (record_fd is not None and
                (fd_stat is None or fd_stat.st_nlink != 0)):
            _publish_or_degrade(record_fd, "RECLAIMABLE", exitstatus=0,
                                reason="clean run; removal is beginning")
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
            dead = fd_stat.st_nlink == 0
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
        elif not os.path.lexists(root):
            # A RECORDED IDENTITY WITH NO DESCRIPTOR IS UNKNOWN (v3.66.1154). Knowing enough to have owned it and not enough to prove it went are different states, and 'nothing is at the path' cannot tell removal from a rename-away without the descriptor. Reported clean before this -- measured on a hand-registered root, payload intact under its new name. Where NEITHER an identity nor a descriptor was recorded the answer stays success: that is a caller asking about a path this tool never created, and refusing it would fail every already-clean path.
            _mark(R_RENAMED, "an identity was recorded but no descriptor")
            ok = False
        else:
            ok = _force_rmtree(root, ident)
        if not ok and record_fd is not None:
            # The removal walk deletes the first terminal marker along with
            # the contents.  If the root itself survives, recreate the marker
            # through the held object descriptor so the refusal is still
            # decidable after the process exits (even after rename-away).
            _ensure_named_lock(record_fd, ident)
            _publish_or_degrade(record_fd, "RECLAIMABLE", exitstatus=0,
                                reason=_LAST_REASON or R_UNPROVEN)
    except BaseException as error:
        propagating = error
        _LAST_FAILURE = root
        if not _LAST_REASON:
            _mark(R_UNPROVEN, "%s: %s" % (type(error).__name__, error))
        raise
    finally:
        if record_fd_owned and record_fd is not None:
            try:
                os.close(record_fd)
            except BaseException as release_error:
                if propagating is None:
                    raise
                _add_note(propagating,
                          "record descriptor release failed (%s: %s)" %
                          (type(release_error).__name__, release_error))
        if fd is not None:
            try:
                _actual = os.readlink("/proc/self/fd/%d" % fd)
            except OSError:
                pass
        try:
            _release_marker_lock(ident)
        except BaseException as release_error:
            if propagating is None:
                raise
            _add_note(propagating,
                      "run-root lock release failed (%s: %s)" %
                      (type(release_error).__name__, release_error))
        try:
            _release_root_fd(ident)
        except BaseException as release_error:
            if propagating is None:
                raise
            _add_note(propagating,
                      "root descriptor release failed (%s: %s)" %
                      (type(release_error).__name__, release_error))
    if not ok:
        _LAST_FAILURE = root
        # REPORTED HERE, WHERE NO CALL SITE CAN DROP IT (v3.66.1151). Both
        # session-finish hooks -- this module's and tests/conftest.py's --
        # discard the return value, and `_ROOT` has already been cleared by the
        # time the removal is attempted, so a failure is unrecoverable AND
        # unreported and the run stays green. A cleanup that did not happen is
        # never silent; putting the report inside the function is the only
        # placement a caller cannot forget.
        # THE REMEDY MUST NAME THE ROOT, NOT THE PATHNAME. After a
        # rename+recreate the recorded path can hold a STRANGER's directory,
        # and `rm -rf` on it would destroy exactly the object this function
        # just refused to touch. The descriptor knows where the root actually
        # is; the pathname does not.
        sys.stderr.write(
            f"\n_tmproot: PER-RUN TEMP ROOT NOT REMOVED: {root}\n"
            f"  reason: {_LAST_REASON or R_UNPROVEN}\n"
            "  every mkdtemp in this run is under it, and nothing else will "
            "collect it.\n"
            "  it is at: %s\n"
            "  recover with: chmod -R u+w '%s' && rm -rf '%s'\n"
            % (_actual, _actual, _actual))
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
