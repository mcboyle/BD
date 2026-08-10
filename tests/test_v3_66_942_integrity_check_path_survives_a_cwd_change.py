"""v3.66.942 -- the integrity check captured a RELATIVE path across a thread
boundary, so it verified whichever database the cwd pointed at when it woke.

WHAT v3.66.927 GOT RIGHT, AND THE HALF IT MISSED. That cut moved the path
resolution out of the daemon thread and into the caller, with a comment stating
the intent exactly: *"Capturing at schedule time is the only reading of 'check
the database' that stays true across a thread boundary: a check scheduled for
database A verifies A even if the process later points DB_PATH elsewhere."*
It even records the same instrument this file's finding used -- "wrapping
sqlite3.connect with a stack recorder pinned the repo-root
`downloader_history.db` ... to exactly this frame".

The resolution moved. The VALUE did not become absolute.
`_resolve_db_path()` returns a bare relative name when `BD_INSTALL_DIR` is
unset and `DB_PATH` has not been monkeypatched -- measured:
`'downloader_history.db'`, `os.path.isabs` False, and its own docstring says
so ("use DB_PATH as-is, which sqlite3.connect() resolves against cwd").

A relative string captured across a thread boundary captures NOTHING. It is
re-resolved against whatever cwd exists when the thread runs. So the guarantee
in the comment cannot be delivered by the value beneath it, and the failure is
invisible because the comment reads as though it had been.

MEASURED at v3.66.941, with a plugin wrapping `sqlite3.connect` over a
156-suite band: one hit, `/home/user/BD/downloader_history.db`, cwd the
checkout, **thread `bd-db-integrity`** --

    db.py:2079  _do_check
    db.py:2113  _row_count_estimate -> db_conn(path)
    db.py:558   sqlite3.connect(path or _resolve_db_path(), timeout=10.0)

-- because a test's autouse fixture chdir'd to a tmpdir, scheduled the check,
finished, and restored cwd to the checkout before the thread woke.

`sqlite3.connect` CREATES ON CONTACT, so this does not merely read the wrong
database: it makes one. That is how two test rows reached the operator's
production history during the v3.66.926 capture.

WHY THE ATTRIBUTION LOOKED LIKE A TEST AND IS NOT. Two runs of the identical
band blamed two DIFFERENT tests, because a background thread has no nodeid --
the plugin could only record whichever test's protocol was live when it woke.
No test is at fault; every test's fixture behaves correctly.

THE FIX IS `abspath` AT SCHEDULE TIME, which is the idiom `app.py:137` already
uses for the same reason. The tests below drive the boundary directly: the
thread target is captured WITHOUT being started, the cwd is moved, and only
then is the target run -- so the interleaving is deterministic rather than a
race the suite would hit once in a hundred runs.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

pytestmark = pytest.mark.bd_module_wipe


@contextmanager
def _logging_may_initialize_here():
    """Make `bulk_downloader.log`'s one-shot setup run inside this block.

    WHY THIS EXISTS -- MEASURED ON THE DEPLOY BOX, v3.66.1007 capture
    (2026-08-10). `test_no_database_is_created_where_the_cwd_happened_to_point`
    failed there with "the excluded `logs/` no longer appears", and passed in
    every container. Nothing about the exclusion had changed:

        log.py   _INITIALIZED: bool = False
                 _LOG_DIR: Path = Path("logs")        # relative -> cwd
                 def _init(): if _INITIALIZED: return

    `logs/` is therefore created exactly ONCE per process, at whatever cwd was
    current the first time anything built a logger. The guard asserted it
    appears in a directory the test chdir'd to LATER -- true only while nothing
    earlier in the process had logged. That is a precondition the test never
    established and never checked, which is the same defect the file's own
    subject is about: a value that means one thing at schedule time and another
    when it is used.

    Reproduced in-container by building one logger after conftest's module wipe
    and before the test body: the box's failure message, verbatim.

    RESTORING THE HANDLERS IS NOT TIDINESS. `_init()` ADDS to
    `logging.getLogger("bulk_downloader")`, a stdlib global that survives
    bd_module_wipe, and never clears what is there. Flipping the flag without
    putting the handler list back would leave a duplicate RotatingFileHandler --
    an open file descriptor and a double-written line -- in every later test on
    this xdist worker. A repair that leaks state across files to fix a test that
    was broken by state leaking across files is CLAUDE.md section 0 wearing the
    fix's clothes.
    """
    from bulk_downloader import log as _log
    root = logging.getLogger("bulk_downloader")
    handlers, filters = root.handlers[:], root.filters[:]
    was_initialized = _log._INITIALIZED
    _log._INITIALIZED = False
    try:
        yield
    finally:
        for h in root.handlers:
            if h not in handlers:
                try:
                    h.close()
                except Exception:
                    pass
        root.handlers[:] = handlers
        root.filters[:] = filters
        _log._INITIALIZED = was_initialized


@pytest.fixture(params=["logging_not_yet_up", "logging_already_up"])
def ambient_logging(request):
    """Run the guard under both halves of the process state it inherits.

    Clearing the flag without also running the half that broke the box would
    make the red go away while proving nothing -- the same reasoning as the
    parametrized ambient state in tests/test_u50_widget_backfills.py, which is
    the other failure from this capture and the same class.
    """
    if request.param == "logging_already_up":
        from bulk_downloader import log as _log
        _log.get_logger("an_earlier_test_on_this_worker")
    return request.param


class _CapturedThread:
    """A Thread stand-in that captures the target instead of running it.

    The whole point of this file is the gap BETWEEN scheduling and running. A
    real thread closes that gap immediately and non-deterministically; holding
    the target lets the test put a cwd change inside it, every time.
    """

    def __init__(self, target=None, daemon=None, name=None, args=(), kwargs=None):
        self.target = target
        self.name = name
        self.started = False

    def start(self):
        self.started = True          # deliberately does NOT run the target

    def is_alive(self):
        return False


@pytest.fixture
def db_mod(monkeypatch, tmp_path):
    """`bulk_downloader.db` with a real database in an isolated directory.

    Both halves, per CLAUDE.md section 5: chdir AND BD_INSTALL_DIR. Without
    them a probe writes downloader_history.db into the repo, gitignored so
    nothing warns, and the rows accumulate into the next probe.
    """
    home = tmp_path / "install"
    home.mkdir()
    monkeypatch.chdir(home)
    monkeypatch.setenv("BD_INSTALL_DIR", str(home))
    from bulk_downloader import db as _db
    monkeypatch.setattr(_db, "DB_PATH", "downloader_history.db", raising=False)
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)   # the unset case: relative
    _db.db_init()
    return _db, home


def _connect_recorder(monkeypatch):
    """Record every path sqlite3.connect is handed -- RAW and resolved.

    BOTH forms, because they answer different questions and the first draft of
    this helper stored only the resolved one. That made
    `test_the_scheduled_path_is_absolute` unable to fail: the recorder
    normalised every relative path to an absolute one before the assertion saw
    it, so the assertion was over the recorder's own output rather than over
    the value the code passed. A check that cannot fail is CLAUDE.md section 0
    in the harness, and it passed on pristine source while the defect it named
    was live two tests away.
    """
    raw: list[str] = []

    class _L(list):
        raw: list

    seen = _L()
    real = sqlite3.connect

    def traced(target, *a, **kw):
        s = str(target)
        if s != ":memory:":
            raw.append(s)
            seen.append(s if os.path.isabs(s)
                        else os.path.realpath(os.path.join(os.getcwd(), s)))
        return real(target, *a, **kw)

    monkeypatch.setattr(sqlite3, "connect", traced)
    seen.raw = raw          # type: ignore[attr-defined]
    return seen


# ── the boundary ─────────────────────────────────────────────────────────────

def test_the_scheduled_path_survives_a_cwd_change(db_mod, monkeypatch, tmp_path):
    """RED on pristine: the check follows the cwd instead of the database.

    Schedule with cwd=install, move to elsewhere, THEN run the captured
    target. A check scheduled for database A must verify A.
    """
    db, home = db_mod
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    monkeypatch.setattr(db, "_threading", _FakeThreading())
    t = db.run_integrity_check(force=True)
    assert isinstance(t, _CapturedThread) and t.started, (
        "the check no longer runs on a background thread -- this file's "
        "subject is the thread boundary, so that is a failure, not a pass")

    seen = _connect_recorder(monkeypatch)
    monkeypatch.chdir(elsewhere)
    t.target()

    assert seen, "the check opened no database at all"
    strayed = [p for p in seen if not p.startswith(os.path.realpath(str(home)))]
    assert not strayed, (
        f"the integrity check followed the cwd instead of the database it was "
        f"scheduled for. Opened {strayed}, expected everything under {home}. "
        f"sqlite3.connect CREATES on contact, so this does not merely read the "
        f"wrong database -- it makes one.")


def test_no_database_is_created_where_the_cwd_happened_to_point(db_mod, monkeypatch,
                                                               tmp_path,
                                                               ambient_logging):
    """The harm, not the mechanism. A stray file is the operator-visible part."""
    db, home = db_mod
    elsewhere = tmp_path / "elsewhere2"
    elsewhere.mkdir()

    monkeypatch.setattr(db, "_threading", _FakeThreading())
    t = db.run_integrity_check(force=True)
    monkeypatch.chdir(elsewhere)
    # The `logs/` exclusion below is asserted to still be LIVE, and that is only
    # observable in the process that runs log._init(). Own it here rather than
    # inheriting whichever answer the worker happened to leave.
    with _logging_may_initialize_here():
        t.target()

    # SCOPED TO THE DATABASE, and the exclusion is stated rather than silent.
    # `logs/` also lands here, from `_log.get_logger` resolving its directory
    # against the cwd -- the SAME class of defect, in a different module, and
    # with a different decision behind it (where should a log go when the cwd
    # moves out from under a running thread?). Folding it in would widen this
    # cut past one feature; it is recorded in SESSION_CARRY instead. A test
    # that quietly ignored it would be the mute-button shape @938 rejected.
    _NOT_THIS_CUT = {"logs"}
    stray = sorted(p.name for p in elsewhere.iterdir()
                   if p.name not in _NOT_THIS_CUT)
    assert not stray, (
        f"the integrity check created {stray} in a directory that merely "
        f"happened to be the cwd when the thread woke")
    assert (elsewhere / "logs").exists(), (
        "the excluded `logs/` no longer appears, so the exclusion above is "
        "dead and must be removed -- an exception nobody is watching will "
        "silently excuse a future real one of the same name.")


def test_the_scheduled_path_is_absolute(db_mod, monkeypatch):
    """The mechanism, asserted directly.

    A relative capture is indistinguishable from an absolute one until the cwd
    moves -- which is why the defect survived a cut written to fix exactly this
    boundary. Pin the property, not just the symptom.
    """
    db, home = db_mod
    seen = _connect_recorder(monkeypatch)
    monkeypatch.setattr(db, "_threading", _FakeThreading())
    t = db.run_integrity_check(force=True)
    t.target()

    assert seen, "no connect observed"
    relative = [p for p in seen.raw if not os.path.isabs(p)]   # RAW, not resolved
    assert not relative, (
        f"connect was handed a relative path: {relative}. It resolves against "
        f"whatever cwd exists at the moment the thread runs, so capturing it "
        f"at schedule time captures nothing.")


# ── the guards ───────────────────────────────────────────────────────────────

def test_it_still_checks_the_right_database_without_a_cwd_change(db_mod, monkeypatch):
    """Regression: the ordinary path must be unchanged."""
    db, home = db_mod
    seen = _connect_recorder(monkeypatch)
    monkeypatch.setattr(db, "_threading", _FakeThreading())
    t = db.run_integrity_check(force=True)
    t.target()
    assert seen and all(p.startswith(os.path.realpath(str(home))) for p in seen), (
        f"the check opened something outside {home}: {seen}")


def test_a_monkeypatched_absolute_db_path_is_still_honoured(db_mod, monkeypatch,
                                                            tmp_path):
    """The over-correction guard.

    `_resolve_db_path` returns an ABSOLUTE DB_PATH verbatim when one is set --
    the conftest and Docker both rely on it. Making the capture absolute must
    not rewrite a path that already was.
    """
    db, home = db_mod
    other = tmp_path / "explicit"
    other.mkdir()
    target_db = other / "explicit.db"
    monkeypatch.setattr(db, "DB_PATH", str(target_db), raising=False)
    db.db_init()

    seen = _connect_recorder(monkeypatch)
    monkeypatch.setattr(db, "_threading", _FakeThreading())
    t = db.run_integrity_check(force=True)
    t.target()
    assert seen, "no connect observed"
    assert all(os.path.realpath(p) == os.path.realpath(str(target_db))
               for p in seen), (
        f"an explicitly-set absolute DB_PATH was not honoured: {seen}")


def test_the_sync_path_is_unaffected(db_mod, monkeypatch):
    """`sync=True` never crosses a thread boundary, so it was never broken --
    and must stay working."""
    db, home = db_mod
    seen = _connect_recorder(monkeypatch)
    out = db.run_integrity_check(force=True, sync=True)
    assert isinstance(out, dict) and out.get("ok") is True, out
    assert seen and all(p.startswith(os.path.realpath(str(home))) for p in seen), seen


class _FakeThreading:
    """Stands in for the `_threading` module inside db.py."""
    Thread = _CapturedThread


def test_a_symlinked_db_path_is_not_rewritten_to_its_target(db_mod, monkeypatch,
                                                            tmp_path):
    """`abspath`, not `resolve()` -- and this is the only test that can tell
    them apart.

    A mutation battery swapped abspath for Path(...).resolve() and every other
    test in this file stayed green, because the two agree on every path that
    contains no symlink. The source comment claimed abspath was chosen so an
    explicitly-set absolute DB_PATH passes through verbatim; nothing backed the
    claim until this test.

    It matters beyond pedantry: an operator whose install directory is reached
    through a symlink (a moved data volume, a /var -> /mnt indirection) would
    see the check silently verify, and stamp its sentinel beside, a path they
    never configured.
    """
    real = tmp_path / "real_store"
    real.mkdir()
    link = tmp_path / "linked_store"
    link.symlink_to(real, target_is_directory=True)
    assert os.path.realpath(str(link)) != str(link), (
        "the symlink did not resolve to a different path, so this test cannot "
        "distinguish abspath from resolve() and proves nothing")

    via_link = link / "history.db"
    monkeypatch.setattr(db_mod[0], "DB_PATH", str(via_link), raising=False)
    db_mod[0].db_init()

    seen = _connect_recorder(monkeypatch)
    monkeypatch.setattr(db_mod[0], "_threading", _FakeThreading())
    t = db_mod[0].run_integrity_check(force=True)
    t.target()

    assert seen.raw, "no connect observed"
    rewritten = [p for p in seen.raw if p != str(via_link)]
    assert not rewritten, (
        f"an explicitly-set absolute DB_PATH was rewritten: {rewritten}, "
        f"expected {str(via_link)!r} verbatim. resolve() follows symlinks; "
        f"abspath does not, and the conftest and Docker both set an absolute "
        f"DB_PATH that must pass through unchanged.")
