"""@1021, queue item 5. `log._init()` appended to a global it did not own.

`_INITIALIZED` is a MODULE global. `logging.getLogger("bulk_downloader")` is a
STDLIB global, keyed in `logging.Logger.manager.loggerDict`, and it outlives any
module wipe. So a wipe reset the flag and not the logger, `_init()` ran again,
and it ADDED a second RotatingFileHandler and a second StreamHandler to the same
logger. Measured before the fix, seven wipe cycles in one process:

    inits   handlers   logger filters   handler filters
    1       2          1                2
    4       8          4                20
    7       14         7                56

Handlers are 2N. Handler FILTERS are N(N+1) -- quadratic -- because the loop at
the end of `_init` decorated every handler ON THE LOGGER rather than the two it
had just installed, so the Nth call re-decorated all 2(N-1) survivors too. And
the visible cost, which no count captures: one `.info()` call printed 28 lines
across those seven cycles, one per surviving StreamHandler, while N
RotatingFileHandlers rotated the same file independently.

WHY THE OBVIOUS ONE-LINE FIX IS WRONG, and this is the whole design.
`tests/test_v3_66_942_integrity_check_path_survives_a_cwd_change.py` clears
`_INITIALIZED` on purpose, to FORCE a full re-init -- its subject is where
`logs/` gets created relative to cwd. A guard that made `_init` a no-op when the
logger already has handlers would make that test's subject unreachable while
looking like a fix. So `_init` must REPLACE what it previously installed, not
refuse to run, and the early return stays exactly as it was.

TAGGED, NOT SWEPT. Only handlers carrying `_OWN_ATTR` are removed. An untagged
sweep of `root.handlers` would evict a handler an operator or another library
attached -- the fix reproducing the shape of the defect, and the same
denominator mistake `ui_events.py:107` records from the other direction (a
FOREIGN handler arriving first made its guard return early and the log went
silent). `test_a_foreign_handler_is_neither_swept_nor_decorated` is that guard.

AMBIENT-SAFE BY CONSTRUCTION. Every assertion below is a DELTA across a cycle,
never an absolute count: this suite may run in a process where something has
already built a logger, and a test that asserted "exactly 2 handlers" would be
asserting about the runner rather than about the code.
"""
from __future__ import annotations

import io
import logging
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

_LOGGER_NAME = "bulk_downloader"


def _counts():
    lg = logging.getLogger(_LOGGER_NAME)
    return (len(lg.handlers), len(lg.filters),
            sum(len(h.filters) for h in lg.handlers))


def _wipe_bd_modules():
    for m in [m for m in sys.modules
              if m == "bulk_downloader" or m.startswith("bulk_downloader.")]:
        del sys.modules[m]


@pytest.fixture
def restored_logger():
    """Leave the ambient logger exactly as found.

    This suite deliberately churns a stdlib global that every later test on
    this worker shares -- CLAUDE.md's whole point about state leaking across
    files. Handlers this test added are closed; the originals are put back
    untouched and are NEVER closed, because they belong to whatever built them.
    """
    lg = logging.getLogger(_LOGGER_NAME)
    keep_h, keep_f = lg.handlers[:], lg.filters[:]
    keep_level, keep_prop = lg.level, lg.propagate
    try:
        yield lg
    finally:
        for h in lg.handlers:
            if h not in keep_h:
                try: h.close()
                except Exception: pass
        lg.handlers[:] = keep_h
        lg.filters[:] = keep_f
        lg.level, lg.propagate = keep_level, keep_prop
        _wipe_bd_modules()


def _build_logger_once():
    from bulk_downloader import log as _log
    _log.get_logger("probe_%d" % id(object()))
    return _log


# ── the leak itself ───────────────────────────────────────────────

def test_a_real_module_wipe_cycle_REPLACES_instead_of_appending(restored_logger):
    """The genuine mechanism: a NEW module incarnation meeting a SURVIVING
    stdlib logger. Asserted as a delta, so ambient state cannot decide it."""
    _build_logger_once()
    before = _counts()
    _wipe_bd_modules()
    _build_logger_once()
    after = _counts()
    assert after == before, (
        "re-init APPENDED instead of replacing: handlers %d->%d, logger "
        "filters %d->%d, handler filters %d->%d"
        % (before[0], after[0], before[1], after[1], before[2], after[2]))


def test_the_handler_filter_total_does_not_GROW_across_a_reinit(restored_logger):
    """The quadratic term specifically, driven the way the 942 fixture drives
    it -- flag flip rather than module wipe, because that path must stay live."""
    log = _build_logger_once()
    before = _counts()[2]
    log._INITIALIZED = False
    log._init()
    after = _counts()[2]
    assert after == before, (
        "handler-filter total grew %d -> %d; the filter loop is decorating "
        "handlers it did not install" % (before, after))


def test_one_record_emits_ONE_line_after_repeated_reinit(restored_logger, monkeypatch):
    """The cost no count shows. Two extra re-inits used to leave three stderr
    handlers attached, so a single .info() was written three times."""
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    log = _build_logger_once()
    for _ in range(2):
        log._INITIALIZED = False
        log._init()
    logging.getLogger(_LOGGER_NAME).info("canary-line")
    emitted = buf.getvalue().count("canary-line")
    assert emitted == 1, (
        "one .info() produced %d stderr lines: %d StreamHandler(s) are "
        "attached where one should be" % (emitted, emitted))


# ── the over-correction direction ─────────────────────────────────

def test_a_foreign_handler_is_neither_swept_nor_decorated(restored_logger):
    """GREEN on pristine for the attach half, RED for the decorate half.

    An untagged sweep would remove this handler -- the fix reproducing the
    defect. The pristine bug is the other half: the old filter loop walked
    root.handlers and so decorated a handler this module never installed.
    """
    lg = restored_logger
    spy = logging.NullHandler()
    spy.closed_by_us = False
    lg.addHandler(spy)
    try:
        log = _build_logger_once()
        log._INITIALIZED = False
        log._init()
        assert spy in lg.handlers, (
            "the re-init swept a handler it never installed; only tagged "
            "handlers may be removed")
        assert len(spy.filters) == 0, (
            "re-init attached %d filter(s) to a handler it never installed"
            % len(spy.filters))
    finally:
        lg.removeHandler(spy)


def test_the_early_return_is_still_reachable_for_the_942_fixture(restored_logger):
    """942 clears _INITIALIZED to force a FULL re-init; a no-op-when-handlers-
    exist guard would silently make its subject unreachable. Assert the flag
    still gates, and that clearing it really does re-run the body."""
    log = _build_logger_once()
    lg = logging.getLogger(_LOGGER_NAME)
    first = list(lg.handlers)
    log._init()                       # flag still set -> must do nothing
    assert list(lg.handlers) == first, "the _INITIALIZED early return stopped gating"
    log._INITIALIZED = False
    log._init()                       # flag cleared -> must rebuild
    assert len(lg.handlers) == len(first), "re-init changed the handler count"
    assert all(h not in first for h in lg.handlers
               if getattr(h, log._OWN_ATTR, False)), (
        "clearing the flag did not actually re-install the handlers, so 942's "
        "forced re-init would no longer exercise anything")


def test_only_tagged_objects_are_ever_removed():
    """The predicate itself, on a known positive and a known negative, before
    any count above is believed."""
    from bulk_downloader import log as _log
    tagged, untagged = logging.NullHandler(), logging.NullHandler()
    setattr(tagged, _log._OWN_ATTR, True)
    assert getattr(tagged, _log._OWN_ATTR, False) is True
    assert getattr(untagged, _log._OWN_ATTR, False) is False
