"""Row 1015 deep lane (O809, ORDERS-2208): RED contract for the third build.

RULING-2200: the operator's explicit vacuum (db_vacuum, POST /api/history/vacuum)
must stay a full VACUUM with the v3.66.795 seam/timeout/autocommit semantics; the
bounded incremental step belongs ONLY to the idle scheduler task and the boot sweep.
RULING-2126: the wiring (boot sweep, scheduler task) and the auto_vacuum transition
in db_init must stay fixed.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "bulk_downloader"

# The incremental-step entry points. Implementation modules are exempt from the
# caller census; every other product call site must be an allowed one.
INCREMENTAL_ENTRY = {
    "run_sqlite_maintenance",
    "run_idle_freelist_maintenance",
    "step_vacuum",
    "run_idle_cycle",
}
IMPLEMENTATION = {"db_maintenance.py", "sqlite_freelist_vacuum.py"}
ALLOWED_CALLERS = {
    ("app.py", "boot_once", "run_sqlite_maintenance"),
    ("bg_scheduler.py", "register_default_tasks._run_sqlite_freelist_maintenance",
     "run_idle_freelist_maintenance"),
}


def _history_db(tmp_path, monkeypatch):
    from bulk_downloader import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "deeplane_history.db"))
    db.db_init()
    return db


def _pruned_history(db):
    """3000 old failed rows + 50 fresh ones, then the product prune."""
    old = [(f"s{i}", f"http://example.invalid/{i}", "X" * 2000) for i in range(3000)]
    fresh = [(f"n{i}", f"http://example.invalid/n{i}", "Y" * 200) for i in range(50)]
    with db.db_conn() as cx:
        cx.executemany(
            "INSERT INTO history (site_id, url, status, message, ts) "
            "VALUES (?, ?, 'failed', ?, '2000-01-01T00:00:00')", old)
        cx.executemany(
            "INSERT INTO history (site_id, url, status, message) "
            "VALUES (?, ?, 'failed', ?)", fresh)
    removed = db.db_prune(30)
    assert removed == 3000, f"fixture: prune removed {removed}, expected exactly 3000"
    return removed


def _freelist(path):
    cx = sqlite3.connect(path)
    try:
        return cx.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        cx.close()


class _RecordingConn:
    """Delegating proxy over the seam's connection; records execute() SQL."""

    def __init__(self, real, statements, fail_on=None):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_statements", statements)
        object.__setattr__(self, "_fail_on", fail_on)

    def execute(self, sql, *args):
        self._statements.append(" ".join(str(sql).split()).upper())
        if self._fail_on and str(sql).strip().upper() == self._fail_on:
            raise sqlite3.OperationalError("deeplane: injected VACUUM failure")
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        setattr(self._real, name, value)


def _spy_seam(monkeypatch, db, fail_on=None):
    opened, statements = [], []
    real_open = db._open_history_conn

    def seam(*a, **kw):
        cx = _RecordingConn(real_open(*a, **kw), statements, fail_on)
        opened.append(cx)
        return cx

    monkeypatch.setattr(db, "_open_history_conn", seam)
    return opened, statements


def _spy_incremental(monkeypatch):
    from bulk_downloader import sqlite_freelist_vacuum as sfv

    calls = []
    cls = sfv.IncrementalVacuumController
    for name in ("step_vacuum", "run_idle_cycle"):
        orig = getattr(cls, name)

        def wrapped(self, *a, _orig=orig, _name=name, **kw):
            calls.append(_name)
            return _orig(self, *a, **kw)

        monkeypatch.setattr(cls, name, wrapped)
    return calls


# ── (a) RULING-2200 RED node ───────────────────────────────────────────────

def test_a_db_vacuum_after_prune_leaves_freelist_zero(tmp_path, monkeypatch):
    db = _history_db(tmp_path, monkeypatch)
    _pruned_history(db)
    before = _freelist(db.DB_PATH)
    assert before > 100, f"fixture: prune must leave >100 free pages, got {before}"

    assert db.db_vacuum() is True
    after = _freelist(db.DB_PATH)
    assert after == 0, (
        f"db_vacuum must fully VACUUM: freelist_count {before} -> {after}, expected 0")


def test_a_control_full_vacuum_reaches_zero_on_same_fixture(tmp_path, monkeypatch):
    """Positive control: the probe can say 0 -- a plain VACUUM on this fixture does."""
    db = _history_db(tmp_path, monkeypatch)
    _pruned_history(db)
    assert _freelist(db.DB_PATH) > 100
    cx = sqlite3.connect(db.DB_PATH, isolation_level=None)
    try:
        cx.execute("VACUUM")
    finally:
        cx.close()
    assert _freelist(db.DB_PATH) == 0


# ── (b) v3.66.795 seam / timeout / autocommit semantics ────────────────────

def test_b_db_vacuum_runs_vacuum_on_one_seam_connection(tmp_path, monkeypatch):
    db = _history_db(tmp_path, monkeypatch)
    opened, statements = _spy_seam(monkeypatch, db)

    assert db.db_vacuum() is True
    assert len(opened) == 1, f"db_vacuum must open exactly 1 seam connection, got {len(opened)}"
    assert statements.count("VACUUM") == 1, (
        f"db_vacuum must execute VACUUM exactly once on the seam connection; executed {statements}")


def test_b_db_vacuum_reports_false_when_vacuum_fails(tmp_path, monkeypatch):
    db = _history_db(tmp_path, monkeypatch)
    _spy_seam(monkeypatch, db, fail_on="VACUUM")
    assert db.db_vacuum() is False, (
        "db_vacuum must return False when its VACUUM raises (v3.66.795 contract)")


def test_b_control_seam_spy_sees_the_seam_and_succeeds(tmp_path, monkeypatch):
    """Positive control: the seam spy intercepts db_vacuum's connection and does not break it."""
    db = _history_db(tmp_path, monkeypatch)
    opened, statements = _spy_seam(monkeypatch, db)
    cx = db._open_history_conn()
    try:
        cx.execute("SELECT 1")
    finally:
        cx.close()
    assert statements == ["SELECT 1"], f"spy must record a seam execute, got {statements}"
    assert db.db_vacuum() is True
    assert len(opened) == 2, f"db_vacuum must go through the spied seam, opened {len(opened)}"


def test_b_db_vacuum_leaves_isolation_level_at_sqlite_default(tmp_path, monkeypatch):
    """Autocommit semantics: VACUUM runs under the default isolation level, untouched."""
    db = _history_db(tmp_path, monkeypatch)
    opened, _ = _spy_seam(monkeypatch, db)
    seen = []
    real_exec = _RecordingConn.execute

    def exec_(self, sql, *a):
        if str(sql).strip().upper() == "VACUUM":
            seen.append(self._real.isolation_level)
        return real_exec(self, sql, *a)

    monkeypatch.setattr(_RecordingConn, "execute", exec_)
    assert db.db_vacuum() is True
    assert seen == [""], f"VACUUM must run once at the sqlite3 default isolation level '', saw {seen}"


# ── (c) incremental step only from the idle task and the boot sweep ────────

def _census():
    found = set()
    for path in sorted(PKG.rglob("*.py")):
        if path.name in IMPLEMENTATION:
            continue
        raw = path.read_text(encoding="utf-8")
        if not any(n in raw for n in INCREMENTAL_ENTRY):
            continue
        tree = ast.parse(raw)

        def walk(node, scope):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    walk(child, scope + [child.name])
                    continue
                if isinstance(child, ast.Call):
                    f = child.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                    if name in INCREMENTAL_ENTRY:
                        found.add((str(path.relative_to(PKG)), ".".join(scope), name))
                walk(child, scope)

        walk(tree, [])
    return found


def test_c_incremental_step_callers_are_only_idle_task_and_boot_sweep():
    found = _census()
    extra = found - ALLOWED_CALLERS
    assert not extra, f"incremental step reachable from non-idle product paths: {sorted(extra)}"
    assert found == ALLOWED_CALLERS, f"census {sorted(found)} != allowed {sorted(ALLOWED_CALLERS)}"


def test_c_control_census_finds_the_allowed_callers():
    """Positive control: the census can say YES -- both allowed callers are found."""
    found = _census()
    assert ALLOWED_CALLERS <= found, f"census missed allowed callers: {sorted(ALLOWED_CALLERS - found)}"
    assert len(ALLOWED_CALLERS) == 2


def test_c_db_vacuum_takes_no_incremental_step(tmp_path, monkeypatch):
    db = _history_db(tmp_path, monkeypatch)
    _pruned_history(db)
    calls = _spy_incremental(monkeypatch)
    db.db_vacuum()
    assert calls == [], f"db_vacuum must not take the bounded incremental step; called {calls}"


def test_c_control_idle_task_takes_the_incremental_step(tmp_path, monkeypatch):
    """Positive control: the spy sees the idle scheduler task take the step."""
    from bulk_downloader import bg_scheduler

    db = _history_db(tmp_path, monkeypatch)
    _pruned_history(db)
    monkeypatch.setattr(bg_scheduler, "is_idle", lambda: True)
    calls = _spy_incremental(monkeypatch)
    bg_scheduler.register_default_tasks()
    with bg_scheduler._lock:
        task = bg_scheduler._tasks.get("sqlite.idle_freelist_maintenance")
    assert task is not None
    task["fn"]()
    assert "run_idle_cycle" in calls, f"idle task did not reach run_idle_cycle; calls {calls}"


# ── (d) RULING-2126 defects stay fixed ─────────────────────────────────────

def test_d_idle_scheduler_task_is_registered():
    from bulk_downloader import bg_scheduler

    bg_scheduler.register_default_tasks()
    with bg_scheduler._lock:
        task = bg_scheduler._tasks.get("sqlite.idle_freelist_maintenance")
    assert task is not None and callable(task["fn"]), "E1: idle freelist task not registered"


def test_d_boot_sweep_calls_sqlite_maintenance():
    raw = (PKG / "app.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(raw))
              if isinstance(n, ast.FunctionDef) and n.name == "boot_once")
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
             and isinstance(c.func, ast.Attribute) and c.func.attr == "run_sqlite_maintenance"]
    assert len(calls) == 1, f"E1: boot_once must call run_sqlite_maintenance once, found {len(calls)}"


def test_d_db_init_transitions_plain_db_to_incremental(tmp_path, monkeypatch):
    from bulk_downloader import db

    path = tmp_path / "plain.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE dummy (x INT)")
    raw.commit()
    raw.close()
    assert _freelist(str(path)) == 0
    cx = sqlite3.connect(path)
    assert cx.execute("PRAGMA auto_vacuum").fetchone()[0] == 0, "control: fixture starts NONE"
    cx.close()

    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.db_init()
    cx = sqlite3.connect(path)
    mode = cx.execute("PRAGMA auto_vacuum").fetchone()[0]
    cx.close()
    assert mode == 2, f"E2: db_init must switch a plain DB to auto_vacuum=INCREMENTAL (2), got {mode}"
