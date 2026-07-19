"""v3.66.795 -- MOD-3 cut 1: history-DB seam consolidation.

MOD-3 migrates the app backend from SQLite to Postgres in five staged cuts
(seam-consolidation -> dual-write -> shadow-read -> migration-rehearsal ->
cutover). Every later cut needs ONE interception point: if some module opens
`downloader_history.db` with its own `sqlite3.connect`, dual-write silently
misses those writes and shadow-read compares against an incomplete picture.

This cut makes `db._open_history_conn()` that single point -- `db.db_conn()` is
the only context manager, and it is the only place a history-DB connection is
created. `push.py` was the one genuine bypass: it kept a parallel `_conn()` on
`sqlite3.connect(_db_path())` against the SAME file (`_DB_REL =
"downloader_history.db"` == `constants.DB_PATH`), so its `push_subscriptions`
writes never saw the seam's pragmas and would never have been intercepted.

The gates below are deliberately of two kinds, because either alone is weak:
  * BEHAVIOURAL -- proves push's connection really is seam-configured now
    (a raw connect cannot produce the seam's busy_timeout), and
  * SOURCE -- proves no OTHER module reintroduces a bypass later. Its
    denominator is enclosing-FUNCTION scope, not the literal connect argument:
    `p = _resolve_db_path(); sqlite3.connect(p)` must be caught too, or the
    check would report clean while missing the very thing it exists to find.

RED before the cut: push.py opens the history DB raw.
"""
import ast
import glob
import os

import pytest

PKG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "bulk_downloader")

# Markers that identify an expression as targeting the HISTORY db specifically
# (not video_hashes.db, not a backup file, not ":memory:").
_HISTORY_MARKERS = {"_resolve_db_path", "_db_path", "_DB_REL", "DB_PATH"}
_HISTORY_LITERAL = "downloader_history"

# Modules allowed to open the history DB raw, each for a stated reason.
#   db.py       -- IS the seam; _open_history_conn is the one intended connect.
#   selftest.py -- startup diagnostic that must observe RAW file state: it
#                  reports whether journal_mode is already WAL, and going
#                  through the seam would SET WAL and make the check certify
#                  its own side effect (and its 2s timeout is deliberate).
_ALLOWED = {"db.py", "selftest.py"}


def _history_connect_offenders():
    """Modules that create a history-DB connection outside the seam.

    Scope is the enclosing function: a connect() whose argument is a local
    that was derived from a history-path resolver counts, so the check cannot
    be evaded (or silently defeated) by one level of indirection.
    """
    offenders = []
    for path in sorted(glob.glob(os.path.join(PKG, "*.py"))):
        base = os.path.basename(path)
        try:
            tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
        except SyntaxError:                                  # pragma: no cover
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            has_connect = False
            has_marker = False
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    f = node.func
                    if isinstance(f, ast.Attribute) and f.attr == "connect":
                        has_connect = True
                if isinstance(node, ast.Name) and node.id in _HISTORY_MARKERS:
                    has_marker = True
                elif isinstance(node, ast.Attribute) and node.attr in _HISTORY_MARKERS:
                    has_marker = True
                elif (isinstance(node, ast.Constant)
                      and isinstance(node.value, str)
                      and _HISTORY_LITERAL in node.value):
                    has_marker = True
            if has_connect and has_marker:
                offenders.append("%s:%s" % (base, fn.name))
    return offenders


def test_no_module_opens_the_history_db_outside_the_seam():
    """The regression net for MOD-3 cuts 2-5. Any NEW module that opens the
    history DB directly breaks dual-write/shadow-read interception, and fails
    here rather than silently dropping rows during the migration."""
    offenders = [o for o in _history_connect_offenders()
                 if o.split(":")[0] not in _ALLOWED]
    assert not offenders, (
        "history DB opened outside db.db_conn(): %s -- route it through the "
        "seam, or allowlist it here with a reason" % offenders)


def test_seam_itself_is_a_single_connect_point():
    """The allowlist must not become a hiding place: db.py may create the
    history connection in exactly ONE function (`_open_history_conn`). If a
    second connect appears in db.py, MOD-3 has two interception points again
    and this fails."""
    db_fns = [o.split(":")[1] for o in _history_connect_offenders()
              if o.startswith("db.py:")]
    assert db_fns == ["_open_history_conn"], (
        "db.py must open the history DB in exactly one function "
        "(_open_history_conn); found: %s" % db_fns)


def test_push_connection_is_seam_configured(tmp_path, monkeypatch):
    """Behavioural proof that push routes through the seam.

    A raw `sqlite3.connect(path)` leaves busy_timeout at the sqlite3 module's
    default of 5000ms; the seam sets it to 10000ms (the v3.43.13 contention
    fix). Reading the pragma back off push's own connection therefore
    distinguishes 'went through db_conn()' from 'opened its own handle'
    without mocking anything.
    """
    from bulk_downloader import db as _db
    from bulk_downloader import push as _push

    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "downloader_history.db"),
                        raising=False)
    with _push._conn() as cx:
        busy = cx.execute("PRAGMA busy_timeout").fetchone()[0]
        journal = cx.execute("PRAGMA journal_mode").fetchone()[0]
    assert int(busy) == 10000, (
        "push._conn() is not seam-configured (busy_timeout=%s) -- it is still "
        "opening its own raw connection" % busy)
    assert str(journal).lower() == "wal"


def test_db_vacuum_still_works_through_the_seam(tmp_path, monkeypatch):
    """db_vacuum keeps its own CONNECTION (VACUUM cannot run inside a
    transaction) but no longer its own CONNECT. Routing it through the opener
    must not break it -- if the seam ever set an isolation level that wraps
    VACUUM in an implicit transaction, this fails instead of silently
    returning False and leaving the DB un-vacuumed."""
    from bulk_downloader import db as _db

    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "downloader_history.db"),
                        raising=False)
    _db.db_init()
    with _db.db_conn() as cx:
        cx.execute("INSERT INTO history(site_id, url, status) "
                   "VALUES ('s', 'u', 'done')")
    assert _db.db_vacuum() is True


def test_push_and_seam_resolve_the_same_database(tmp_path, monkeypatch):
    """The bypass was invisible precisely because both paths pointed at the
    same file. Pin that they still do -- if they ever diverge, push's rows
    would land in a database MOD-3 never migrates."""
    from bulk_downloader import db as _db
    from bulk_downloader import push as _push

    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(_db, "DB_PATH", "downloader_history.db", raising=False)
    assert os.path.abspath(str(_push._db_path())) == \
        os.path.abspath(str(_db._resolve_db_path()))
