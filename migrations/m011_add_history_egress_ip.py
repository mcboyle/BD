from __future__ import annotations
import sqlite3
VERSION = 11
NAME = "add_history_egress_ip"

def up(cx: sqlite3.Connection) -> None:
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "egress_ip" not in cols:
        cx.execute("ALTER TABLE history ADD COLUMN egress_ip TEXT NOT NULL DEFAULT 'UNKNOWN'")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_history_egress_ip_archive" in tables:
        cx.execute("""
            UPDATE history
            SET egress_ip = (
                SELECT egress_ip FROM _downgrade_history_egress_ip_archive
                WHERE _downgrade_history_egress_ip_archive.id = history.id
            )
            WHERE id IN (SELECT id FROM _downgrade_history_egress_ip_archive)
        """)

def down(cx: sqlite3.Connection) -> None:
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "egress_ip" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_history_egress_ip_archive (
            id INTEGER PRIMARY KEY,
            egress_ip TEXT,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_history_egress_ip_archive (id, egress_ip)
        SELECT id, egress_ip FROM history WHERE egress_ip != 'UNKNOWN'
    """)
    try:
        cx.execute("ALTER TABLE history DROP COLUMN egress_ip")
    except Exception:
        cols_keep = [c for c in cols if c != "egress_ip"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE history_recreate_tmp AS SELECT {cols_csv} FROM history WHERE 0")
        cx.execute(f"INSERT INTO history_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_recreate_tmp RENAME TO history")
