"""staging-integrity F3 F6 F7 (sweep finder bd-worker-S56-C): migration rollback and quarantine leftovers.

F3: apply_pending's abort-to-prior copied <db>.premigration.bak over the live file with shutil.copy2 and left
<db>-wal/-shm behind. The WAL frames won, so the migrated schema and ledger survived a reported
restored_to_prior=True. The restore now goes through SQLite's online backup API.
F6: _m5 ran ALTER TABLE and CREATE INDEX as two autocommitted statements under a column guard. A kill between them
left the column without its index, and every later run skipped the index. The index is now created unconditionally.
F7: _quarantine_failure reserved _failed/<name> with O_EXCL and left the 0-byte placeholder behind when os.replace
failed. The placeholder is now removed and the error re-raised.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

import bulk_downloader.db as db
import bulk_downloader.migrations as mg
from bulk_downloader import runner_integrity


BD_GATE_SCOPE = "module"


def _seed_history(path, rows, duplicate_sites):
    cx = sqlite3.connect(path)
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("CREATE TABLE history(id INTEGER PRIMARY KEY, site_id TEXT, url TEXT, status TEXT, ts REAL)")
    cx.executemany(
        "INSERT INTO history(site_id,url,status,ts) VALUES (?,?,?,?)",
        ((("s%d" % (i % 3)) if duplicate_sites else ("s%d" % i),
          "https://x.invalid/%08d/%s" % (i, "p" * 200), "done", i) for i in range(rows)))
    cx.commit()
    cx.close()


def _m100(cx):
    cx.execute("CREATE INDEX IF NOT EXISTS idx_probe_url ON history(url)")


def _m101(cx):
    # Schema-only dry-run passes; the real rows carry duplicate site_ids when seeded so.
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_probe_site ON history(site_id)")


def _fresh_view(path):
    c = sqlite3.connect(path)
    try:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        ledger = (c.execute("SELECT version, success FROM schema_migrations ORDER BY version").fetchall()
                  if "schema_migrations" in names else None)
        rows = c.execute("SELECT count(*) FROM history").fetchone()[0]
        return names, ledger, rows
    finally:
        c.close()


@pytest.fixture()
def isolated_registry(tmp_path):
    saved = list(mg._MIGRATIONS)
    saved_path = db.DB_PATH
    path = str(tmp_path / "history.db")
    db.DB_PATH = path
    mg._MIGRATIONS[:] = [{"version": 100, "name": "probe_big_index", "fn": _m100},
                         {"version": 101, "name": "probe_unique_site", "fn": _m101}]
    try:
        yield path
    finally:
        db.close_all_pooled_connections()
        mg._MIGRATIONS[:] = saved
        db.DB_PATH = saved_path


def test_f3_failed_migration_restores_the_prior_schema_and_ledger(isolated_registry):
    path = isolated_registry
    _seed_history(path, 200, duplicate_sites=True)
    out = mg.apply_pending()
    names, ledger, rows = _fresh_view(path)
    assert out.get("errors") == 1 and out.get("backup"), out
    assert out.get("restored_to_prior") is True and "idx_probe_url" not in names and not ledger, (
        "MIG-F3: apply_pending reported restored_to_prior=%r, yet a fresh connection still sees "
        "idx_probe_url=%s and ledger %r; side files %r"
        % (out.get("restored_to_prior"), "idx_probe_url" in names, ledger,
           sorted(os.listdir(os.path.dirname(path)))))
    assert "ux_probe_site" not in names, names
    assert rows == 200, rows
    # The restored DB is usable: the next run retries the migrations and fails the same way.
    again = mg.apply_pending()
    assert again.get("errors") == 1 and again.get("restored_to_prior") is True, again
    assert "idx_probe_url" not in _fresh_view(path)[0]


def test_f3_control_clean_batch_applies_without_restore(isolated_registry):
    path = isolated_registry
    _seed_history(path, 200, duplicate_sites=False)
    out = mg.apply_pending()
    names, ledger, rows = _fresh_view(path)
    assert out.get("applied") == 2 and not out.get("errors"), out
    assert "restored_to_prior" not in out, out
    assert {"idx_probe_url", "ux_probe_site"} <= names, names
    assert ledger == [(100, 1), (101, 1)], ledger
    assert rows == 200, rows


def _commit_new_rows_behind_an_open_reader(path, n):
    """An open read txn pins the WAL: a TRUNCATE checkpoint is busy and these commits stay in <db>-wal."""
    reader = sqlite3.connect(path, isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM history").fetchone()
    with db.db_conn() as cx:
        cx.executemany("INSERT INTO history(site_id,url,status,ts) VALUES ('new',?,'done',0)",
                       [("u%d" % i,) for i in range(n)])
    return reader


def _new_rows(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT count(*) FROM history WHERE site_id='new'").fetchone()[0]
    finally:
        c.close()


def test_r2_f1_restore_keeps_commits_that_were_in_the_wal_at_backup_time(isolated_registry):
    # REFUTE R239-C F1 (probe_stale.py): the .bak was a copy2 of the main file only.
    path = isolated_registry
    _seed_history(path, 2000, duplicate_sites=True)
    reader = _commit_new_rows_behind_an_open_reader(path, 50)
    try:
        out = mg.apply_pending()
    finally:
        reader.execute("COMMIT")
        reader.close()
    db.close_all_pooled_connections()
    names, ledger, rows = _fresh_view(path)
    assert out.get("errors") == 1, out
    assert rows == 2050 and _new_rows(path) == 50, (
        "MIG-R2-F1: restored_to_prior=%r with a reader open, yet history has %d rows (%d new of 50): "
        "commits in the WAL at backup time were dropped" % (out.get("restored_to_prior"), rows, _new_rows(path)))
    assert out.get("restored_to_prior") is True and "idx_probe_url" not in names and not ledger, (out, ledger)


def test_r2_f1_control_no_reader_keeps_the_commits(isolated_registry):
    path = isolated_registry
    _seed_history(path, 2000, duplicate_sites=True)
    with db.db_conn() as cx:
        cx.executemany("INSERT INTO history(site_id,url,status,ts) VALUES ('new',?,'done',0)",
                       [("u%d" % i,) for i in range(50)])
    out = mg.apply_pending()
    db.close_all_pooled_connections()
    names, ledger, rows = _fresh_view(path)
    assert out.get("restored_to_prior") is True and rows == 2050 and _new_rows(path) == 50, (out, rows)
    assert "idx_probe_url" not in names and not ledger


def test_r2_f1_backup_is_complete_and_leaves_no_temp(isolated_registry):
    path = isolated_registry
    _seed_history(path, 200, duplicate_sites=False)
    reader = _commit_new_rows_behind_an_open_reader(path, 7)
    try:
        bak = mg._backup_db_before_migration()
    finally:
        reader.execute("COMMIT")
        reader.close()
    assert bak == path + ".premigration.bak" and not os.path.exists(bak + ".tmp"), sorted(os.listdir(os.path.dirname(path)))
    c = sqlite3.connect(bak)
    try:
        assert c.execute("SELECT count(*) FROM history").fetchone()[0] == 207
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        c.close()


_SAME_DB_CHILD = r"""
import sqlite3, sys
import bulk_downloader.db as db
import bulk_downloader.migrations as mg
uri = "file:mig-r2-same?mode=memory&cache=shared"
real = sqlite3.connect
anchor = real(uri, uri=True)
sqlite3.connect = lambda *a, **k: real(uri, uri=True)  # as inmemory_sqlite routes every name
db.DB_PATH = sys.argv[1]
print("BAK=%r" % (mg._backup_db_before_migration(),))
"""


def test_r2_f1_backup_into_the_source_itself_returns_none_not_a_hang(isolated_registry):
    # test_u24_migration_status under inmemory_sqlite: src and the .tmp both open the one shared-cache DB,
    # and Connection.backup retries SQLITE_LOCKED forever. Child process so a hang fails, not wedges.
    import subprocess
    import sys
    path = isolated_registry
    _seed_history(path, 20, duplicate_sites=False)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        r = subprocess.run([sys.executable, "-c", _SAME_DB_CHILD, path], cwd=root, capture_output=True,
                           text=True, timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail("MIG-R2-F1b: _backup_db_before_migration hung >30s backing a DB up into itself")
    assert r.returncode == 0 and "BAK=None" in r.stdout, (r.returncode, r.stdout, r.stderr[-800:])
    assert not os.path.exists(path + ".premigration.bak.tmp")


def test_r3_restore_from_the_live_db_itself_returns_false_not_a_hang(isolated_registry):
    # r3: the restore's destination is now a db.db_conn() lease, so the same-DB guard sits on the .bak side.
    import subprocess
    import sys
    path = isolated_registry
    _seed_history(path, 20, duplicate_sites=False)
    bak = path + ".premigration.bak"
    sqlite3.connect(bak).close()
    child = _SAME_DB_CHILD.replace("mg._backup_db_before_migration()", "mg._restore_db_from_backup(sys.argv[2])")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        r = subprocess.run([sys.executable, "-c", child, path, bak], cwd=root, capture_output=True,
                           text=True, timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail("MIG-R3-RESTORE: _restore_db_from_backup hung >30s restoring a DB from itself")
    assert r.returncode == 0 and "BAK=False" in r.stdout, (r.returncode, r.stdout, r.stderr[-800:])


def _after_backup(monkeypatch, then):
    real = mg._backup_db_before_migration

    def wrapped(*a, **k):
        bak = real(*a, **k)
        then()
        return bak

    monkeypatch.setattr(mg, "_backup_db_before_migration", wrapped)


def _late_rows(path, n=30):
    w = sqlite3.connect(path)
    w.executemany("INSERT INTO history(site_id,url,status,ts) VALUES ('late',?,'done',0)",
                  [("l%d" % i,) for i in range(n)])
    w.commit()
    w.close()


def _late_kept(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT count(*) FROM history WHERE site_id='late'").fetchone()[0]
    finally:
        c.close()


def test_window_commit_between_backup_and_failing_migration_is_kept(isolated_registry, monkeypatch):
    # mif-backup-window (r2 DETAIL probe_window.py): abort-to-prior discarded rows another connection
    # committed after the backup was taken.
    path = isolated_registry
    _seed_history(path, 2000, duplicate_sites=True)
    _after_backup(monkeypatch, lambda: _late_rows(path))
    out = mg.apply_pending()
    db.close_all_pooled_connections()
    assert out.get("errors") == 1 and out.get("backup"), out
    assert _late_kept(path) == 30, (
        "MIG-WIN: %d/30 rows committed after the backup survived; restored_to_prior=%r restore_refused=%r"
        % (_late_kept(path), out.get("restored_to_prior"), out.get("restore_refused")))
    assert "restored_to_prior" not in out and "data_version" in out.get("restore_refused", ""), out
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_window_control_no_foreign_commit_still_restores(isolated_registry, monkeypatch):
    path = isolated_registry
    _seed_history(path, 2000, duplicate_sites=True)
    _after_backup(monkeypatch, lambda: None)
    out = mg.apply_pending()
    db.close_all_pooled_connections()
    assert out.get("restored_to_prior") is True and "restore_refused" not in out, out
    assert "idx_probe_url" not in _fresh_view(path)[0]


def test_window_replaced_handle_refuses_rather_than_guesses(isolated_registry, monkeypatch):
    # Policy: with the pinned handle gone, data_version proves nothing, so no restore.
    path = isolated_registry
    _seed_history(path, 200, duplicate_sites=True)
    evict = []
    real_conn = db.db_conn

    def conn(*a, **k):
        if evict:  # the next borrow after the failing migration finds the pinned handle gone
            evict.clear()
            mg._evict_idle_connections()
        return real_conn(*a, **k)

    def m101(cx):
        # The preflight runs on a bare :memory: connection; the live apply gets the pooled
        # handle (_HistoryConnection, or _DualWriteConn when MOD3_PG_DSN is set).
        if type(cx) is not sqlite3.Connection:
            evict.append(1)
        _m101(cx)

    monkeypatch.setattr(db, "db_conn", conn)
    mg._MIGRATIONS[1]["fn"] = m101
    out = mg.apply_pending()
    assert "restored_to_prior" not in out and "replaced" in out.get("restore_refused", ""), out


class _NoPagesConnection(sqlite3.Connection):
    def backup(self, *a, **k):
        return None


def test_restore_reports_false_when_the_pages_did_not_arrive(tmp_path, monkeypatch):
    # Kills lens mutant M2 (schema compare -> True): the docstring promises True only when the
    # live schema reads back as the backup's.
    live, bak = str(tmp_path / "live.db"), str(tmp_path / "bak.db")
    for p, extra in ((live, True), (bak, False)):
        c = sqlite3.connect(p)
        c.execute("CREATE TABLE history(id INTEGER PRIMARY KEY)")
        if extra:
            c.execute("CREATE INDEX idx_extra ON history(id)")
        c.commit()
        c.close()
    monkeypatch.setattr(db, "DB_PATH", live)
    real_connect = sqlite3.connect
    monkeypatch.setattr(mg, "sqlite3", type("S", (), {
        "connect": staticmethod(lambda p, *a, **k: real_connect(p, *a, factory=_NoPagesConnection, **k))}))
    assert mg._restore_db_from_backup(bak) is False, "MIG-M2: restore reported True though no page was copied"


def _history_table(cx, with_library_id):
    cx.execute("CREATE TABLE history(id INTEGER PRIMARY KEY, url TEXT)")
    if with_library_id:
        # The state a kill between _m5's ALTER and its CREATE INDEX leaves behind.
        cx.execute("ALTER TABLE history ADD COLUMN library_id INTEGER DEFAULT NULL")


def _has_index(cx):
    return bool(cx.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_history_lib'").fetchone())


def test_f6_m5_rerun_creates_the_index_a_partial_run_left_out():
    cx = sqlite3.connect(":memory:")
    _history_table(cx, with_library_id=True)
    mg._m5(cx)
    assert _has_index(cx), (
        "MIG-F6: _m5 rerun on a history table that already has library_id left idx_history_lib absent")


def test_f6_control_fresh_table_gets_column_and_index_and_rerun_is_harmless():
    cx = sqlite3.connect(":memory:")
    _history_table(cx, with_library_id=False)
    mg._m5(cx)
    mg._m5(cx)
    assert "library_id" in {r[1] for r in cx.execute("PRAGMA table_info(history)")}
    assert _has_index(cx)


def test_f7_failed_move_leaves_no_placeholder_and_burns_no_slot(tmp_path, monkeypatch):
    clip = tmp_path / "V.mp4"
    clip.write_bytes(b"V" * 10)
    real_replace = os.replace

    def boom(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(runner_integrity.os, "replace", boom)
    with pytest.raises(OSError):
        runner_integrity._quarantine_failure(clip)
    left = sorted((p.name, p.stat().st_size) for p in (tmp_path / "_failed").iterdir())
    assert left == [], "MIG-F7: os.replace failed and _failed still holds %r (original kept: %s)" % (
        left, clip.exists())
    assert clip.read_bytes() == b"V" * 10

    monkeypatch.setattr(runner_integrity.os, "replace", real_replace)
    target = runner_integrity._quarantine_failure(clip)
    assert target == tmp_path / "_failed" / "V.mp4", target
    assert target.read_bytes() == b"V" * 10
    assert not clip.exists()
