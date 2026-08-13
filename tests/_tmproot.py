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
    shutil.rmtree(root, ignore_errors=True)
    return True


# Hook forms, so this file also works as a standalone `-p _tmproot` plugin.
def pytest_configure(config):
    install()


def pytest_sessionfinish(session, exitstatus):
    finish(exitstatus)
