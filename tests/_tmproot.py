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

import errno
import os
import pathlib
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
    global _ROOT_IDENT
    _ROOT = tempfile.mkdtemp(prefix="bd-testrun-", dir=str(SYSTEM_TMP))
    # THE IDENTITY OF THE OBJECT WE JUST CREATED (v3.66.1153). Until now this
    # kept only the pathname, so reclamation removed whatever directory later
    # occupied that name -- measured: a foreign inode deleted, the created root
    # leaked, failed_root() None, and the session still green.
    _st = os.lstat(_ROOT)
    _ROOT_IDENT = (_st.st_dev, _st.st_ino)
    _LAST_FAILURE = None
    tempfile.tempdir = _ROOT
    return _ROOT


def _rmtree_fd(fd):
    """Delete everything UNDER an open directory descriptor.

    No pathname is resolved for any child, so renaming the root -- or any
    ancestor -- cannot redirect these calls. A sealed child is relaxed through
    ITS OWN descriptor, which is also what keeps the directories-only rule:
    only a directory's mode can block the removal of its entries, and only
    directories are ever opened here, so an in-tree hard link to an outside
    FILE can never be chmodded.
    """
    for entry in os.scandir(fd):
        if entry.is_dir(follow_symlinks=False):
            child = os.open(entry.name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=fd)
            try:
                try:
                    _rmtree_fd(child)
                except PermissionError:
                    os.fchmod(child, 0o700)
                    _rmtree_fd(child)
            finally:
                os.close(child)
            os.rmdir(entry.name, dir_fd=fd)
        else:
            os.unlink(entry.name, dir_fd=fd)


def _force_rmtree(path: str, ident=None) -> bool:
    """Remove the tree at `path`, bound to `ident` when one is supplied.

    Returns True only if the object identified by `ident` is gone.

    WHY shutil.rmtree IS NOT USED HERE ANY MORE (v3.66.1153). Three separate
    defects lived in that call, all measured at 3d5f1bb8:

      * it acts on a PATHNAME, so it removed whatever directory occupied the
        root's name -- a foreign inode deleted, the created root leaked, and
        the session still green;
      * the `onexc` handler called `func(p)` blindly, and two of the seven
        functions shutil can hand it (os.open, os.close) do not take that
        shape, so the handler raised TypeError;
      * that TypeError was caught by an `except TypeError` meant to detect an
        OLD rmtree signature, so rmtree was re-entered with the legacy
        `onerror=` kwarg on a half-modified tree -- double invocation observed
        twice -- and on a dangling symlink the TypeError escaped `finish()`
        entirely, so neither pytest hook ran at all.

    A descriptor-bound walk has no callback and no signature question.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as e:
        if e.errno == errno.ENOENT:
            return True                      # nothing to remove IS success
        return False                         # symlink, non-directory, or denied
    try:
        st = os.fstat(fd)
        if ident is not None and (st.st_dev, st.st_ino) != ident:
            return False                     # a replacement: never remove it
        try:
            _rmtree_fd(fd)
        except PermissionError:
            try:
                os.fchmod(fd, 0o700)
                _rmtree_fd(fd)
            except OSError:
                return False
        except OSError:
            return False
        parent, name = os.path.dirname(path) or ".", os.path.basename(path)
        try:
            pfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            return False
        try:
            try:
                ent = os.stat(name, dir_fd=pfd, follow_symlinks=False)
            except OSError:
                return False
            if (ent.st_dev, ent.st_ino) != (st.st_dev, st.st_ino):
                return False                 # the entry no longer names it
            try:
                os.rmdir(name, dir_fd=pfd)
            except OSError:
                return False
        finally:
            os.close(pfd)
        # PROOF, not hope: the object we held open is the one that was unlinked.
        return os.fstat(fd).st_nlink == 0
    finally:
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
    tempfile.tempdir = None                # new allocations leave the doomed tree
    if int(exitstatus) != 0:
        return False                       # KEPT ON PURPOSE, not a failure
    if ident is None and os.path.lexists(root):
        # UNKNOWN: a name is here and we cannot prove it is the root we made.
        ok = False
    else:
        ok = _force_rmtree(root, ident)
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
