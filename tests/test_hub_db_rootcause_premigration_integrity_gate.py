"""hub-db-corruption-rootcause r1 (O3292, bd-worker-D76-D): apply_pending must not back up or migrate a damaged DB.

The 1904 hub deploy log reads `database disk image is malformed` at PRE, before migration 12 ran. In the quarantined
set, the index roots idx_runs_site_id and idx_lib_performer had been overwritten with another table's leaf page.
quick_check passed. At boot, apply_pending took a VACUUM INTO backup, which read clean because it rebuilds from rows,
and then applied migration 12 on top of the damage. The fixture below rebuilds that page-level shape.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

import bulk_downloader.db as db
import bulk_downloader.migrations as mg


BD_GATE_SCOPE = "module"


def _seed(path):
    cx = sqlite3.connect(path)
    cx.execute("PRAGMA journal_mode=DELETE")
    cx.execute("CREATE TABLE relogin_log(id INTEGER PRIMARY KEY, ts REAL, site_id TEXT)")
    cx.executemany("INSERT INTO relogin_log(ts, site_id) VALUES (?,?)", ((1792125456.0 + i, "0b698210") for i in range(21)))
    cx.execute("CREATE TABLE job_runs(id INTEGER PRIMARY KEY, site_id TEXT)")
    cx.execute("CREATE INDEX idx_runs_site_id ON job_runs(site_id)")
    cx.commit()
    roots = dict(cx.execute("SELECT name, rootpage FROM sqlite_master WHERE name IN ('relogin_log','idx_runs_site_id')"))
    page_size = cx.execute("PRAGMA page_size").fetchone()[0]
    cx.close()
    return roots, page_size


def _overwrite_index_root_with_table_leaf(path, roots, page_size):
    with open(path, "r+b") as f:
        f.seek((roots["relogin_log"] - 1) * page_size)
        leaf = f.read(page_size)
        f.seek((roots["idx_runs_site_id"] - 1) * page_size)
        f.write(leaf)


def _m100(cx):
    cx.execute("CREATE INDEX IF NOT EXISTS idx_probe_ts ON relogin_log(ts)")


@pytest.fixture()
def isolated_registry(tmp_path):
    saved = list(mg._MIGRATIONS)
    saved_path = db.DB_PATH
    path = str(tmp_path / "history.db")
    db.DB_PATH = path
    mg._MIGRATIONS[:] = [{"version": 100, "name": "probe_covering_index", "fn": _m100}]
    try:
        yield path
    finally:
        db.close_all_pooled_connections()
        mg._MIGRATIONS[:] = saved
        db.DB_PATH = saved_path


def _ledger(path):
    c = sqlite3.connect(path)
    try:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        if "schema_migrations" not in names:
            return []
        return c.execute("SELECT version, success FROM schema_migrations").fetchall()
    finally:
        c.close()


def test_damaged_db_is_refused_before_backup_and_migration(isolated_registry, capsys):
    path = isolated_registry
    roots, page_size = _seed(path)
    _overwrite_index_root_with_table_leaf(path, roots, page_size)
    probe = sqlite3.connect(path)
    try:
        # The quarantine shape: quick_check is blind to it, so the gate has to run the full check.
        assert probe.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        try:
            full = [r[0] for r in probe.execute("PRAGMA integrity_check").fetchall()]
        except sqlite3.DatabaseError as e:
            full = [str(e)]
        assert full != ["ok"]
    finally:
        probe.close()

    out = mg.apply_pending()

    assert out.get("aborted") is True, out
    assert out["applied"] == 0
    assert out["abort_reason"].startswith("pre-migration integrity gate: integrity_check"), out["abort_reason"]
    assert "backup" not in out
    assert not os.path.exists(path + ".premigration.bak")
    assert (100, 1) not in _ledger(path)
    assert "[migrations] REFUSED: pre-migration integrity gate" in capsys.readouterr().err


def test_negative_control_undamaged_db_still_migrates(isolated_registry, capsys):
    path = isolated_registry
    _seed(path)

    out = mg.apply_pending()

    assert not out.get("aborted"), out
    assert out["applied"] == 1
    assert os.path.exists(path + ".premigration.bak")
    assert (100, 1) in _ledger(path)
    assert "REFUSED" not in capsys.readouterr().err
