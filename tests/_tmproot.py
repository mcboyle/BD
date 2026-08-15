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


def install() -> str | None:
    """Point `tempfile.tempdir` at a fresh per-process root. Returns its path.

    Keyed by pid via mkdtemp because xdist workers are separate processes: each
    makes its own and removes its own, so there is nothing to race on.
    """
    global _ROOT
    if _ROOT is not None:
        return _ROOT                       # already installed; do not nest
    if os.environ.get("KEEP_TEST_TMPDIRS") == "1":
        return None
    _ROOT = tempfile.mkdtemp(prefix="bd-testrun-", dir=str(SYSTEM_TMP))
    tempfile.tempdir = _ROOT
    return _ROOT


def _force_rmtree(path: str) -> bool:
    """Remove a tree even when it holds a directory we cannot write into.

    Returns True only if the path is gone -- never `ignore_errors=True`, which
    is what made the defect below invisible.

    MEASURED at v3.66.1150. `shutil.rmtree(root, ignore_errors=True)` CANNOT
    remove a tree containing a read-only directory: unlinking a name needs the
    write bit on its parent, so it fails inside that directory and the flag
    swallows the error. One such directory anywhere under the root therefore
    left the ENTIRE per-run root on disk, silently -- the 15392-entries-in-/tmp
    problem this module exists to fix, reintroduced from three files away.

    It was not hypothetical: bd-cut seals its archive snapshot to 0500 on every
    --resume-zip run (so nothing can unlink or replace the archive the band and
    verify agreed on), and 23 such directories had accumulated under /tmp
    during the cut that introduced the seal.

    The handler chmods the offending entry and its parent and retries once.
    That is enough for a tree we own, and it deliberately does NOT recurse
    forever: a path that still refuses is reported by the return value rather
    than retried until something else breaks.

    NEVER CHMOD OUTSIDE THE TREE. The first version of this handler chmod'd
    `os.path.dirname(p)` unconditionally, and `finish()` calls it with
    /tmp/bd-testrun-<rand> -- so when the failing entry was the ROOT ITSELF the
    handler reached for `/tmp`. On a developer box that fails with EPERM and
    the bare `except OSError` hides it; under CI or any container where the
    suite runs as root, the chmod SUCCEEDS and takes /tmp from 1777 to 0700,
    silently, breaking every other user on the machine. A cleanup helper must
    never touch a path it was not handed.

    A LEXICAL CONTAINMENT CHECK IS NOT AN ANSWER ABOUT WHAT A PATH RESOLVES TO.
    The v3.66.1150 guard compared strings, and os.chmod FOLLOWS symlinks -- so
    a link anywhere inside the tree was "inside" by string comparison while the
    chmod landed on its target. Measured at 55ae94f8: a directory outside the
    tree went 0755 -> 0700 and survived. Symlinks are never chmod'd (rmtree
    unlinks them; their mode is irrelevant), and containment is decided on the
    REAL path.
    """
    root = os.path.realpath(path)

    def _inside(p):
        rp = os.path.realpath(p)
        return rp == root or rp.startswith(root + os.sep)

    def _relax(target):
        try:
            st = os.lstat(target)
        except OSError:
            return
        # ONLY A DIRECTORY (v3.66.1152). The previous predicate was "not a
        # symlink", which let a HARD LINK through -- and a hard link is not a
        # symlink, shares the target's inode, and has no target to resolve, so
        # realpath returns the IN-TREE path and the containment check says yes.
        # Reproduced at dcf34528: an in-tree hard link to an outside file took
        # that file from 0644 to 0700, same inode, file surviving.
        #
        # Only a directory's mode can block the removal of its entries, so
        # directories are the entire population worth relaxing. This also
        # subsumes the symlink case: a symlink is not a directory.
        if not stat.S_ISDIR(st.st_mode):
            return
        if not _inside(target):
            return                      # never touch anything we were not given
        try:
            os.chmod(target, 0o700)
        except OSError:
            pass

    def _retry(func, p, _exc):
        _relax(os.path.dirname(p))
        _relax(p)
        try:
            func(p)
        except OSError:
            pass

    try:
        shutil.rmtree(path, onexc=_retry)          # Python 3.12+
    except TypeError:                              # older signature
        shutil.rmtree(path, onerror=lambda f, p, e: _retry(f, p, e))
    except OSError:
        pass
    return not os.path.exists(path)


def finish(exitstatus: int) -> bool:
    """Remove the root. Returns True if it was removed.

    ARTIFACTS SURVIVE A FAILING RUN: a debugging directory deleted on the one
    run that needed it is a worse defect than the leak this closes.
    """
    global _ROOT
    if _ROOT is None:
        return False
    root, _ROOT = _ROOT, None
    tempfile.tempdir = None                # new allocations leave the doomed tree
    if int(exitstatus) != 0:
        return False
    ok = _force_rmtree(root)
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
