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
import tempfile

# The real system temp directory, captured before anything redirects it.
SYSTEM_TMP = pathlib.Path(tempfile.gettempdir())

_ROOT: str | None = None


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
    """
    root = os.path.abspath(path)

    def _inside(p):
        p = os.path.abspath(p)
        return p == root or p.startswith(root + os.sep)

    def _retry(func, p, _exc):
        for target in (os.path.dirname(p), p):
            if not _inside(target):
                continue
            try:
                os.chmod(target, 0o700)
            except OSError:
                pass
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
    return _force_rmtree(root)


# Hook forms, so this file also works as a standalone `-p _tmproot` plugin.
def pytest_configure(config):
    install()


def pytest_sessionfinish(session, exitstatus):
    finish(exitstatus)
