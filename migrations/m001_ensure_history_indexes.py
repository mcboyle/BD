from __future__ import annotations
import sqlite3
VERSION = 1
NAME = "ensure_history_indexes"

def up(cx: sqlite3.Connection) -> None:
    cx.execute("CREATE INDEX IF NOT EXISTS idx_history_status ON history(status)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_history_site ON history(site_id)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_history_ts ON history(ts)")

def down(cx: sqlite3.Connection) -> None:
    cx.execute("DROP INDEX IF EXISTS idx_history_status")
    cx.execute("DROP INDEX IF EXISTS idx_history_site")
    cx.execute("DROP INDEX IF EXISTS idx_history_ts")
