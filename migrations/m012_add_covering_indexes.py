from __future__ import annotations
import sqlite3
VERSION = 12
NAME = "add_covering_indexes"

def up(cx: sqlite3.Connection) -> None:
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "library" in tables:
        cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_history_id ON library(history_id)")
    if "history" in tables:
        cx.execute("CREATE INDEX IF NOT EXISTS idx_hist_url_status ON history(url, status)")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_hist_active_site_ts ON history(site_id, ts DESC) WHERE removed_at IS NULL")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_hist_library_id ON history(library_id) WHERE library_id IS NOT NULL")
    if "queue" in tables:
        cx.execute("CREATE INDEX IF NOT EXISTS idx_q_site_status_ord ON queue(site_id, status, ord)")
    if "tags" in tables:
        cx.execute("CREATE INDEX IF NOT EXISTS idx_tags_created_at ON tags(created_at)")

def down(cx: sqlite3.Connection) -> None:
    cx.execute("DROP INDEX IF EXISTS idx_lib_history_id")
    cx.execute("DROP INDEX IF EXISTS idx_hist_url_status")
    cx.execute("DROP INDEX IF EXISTS idx_hist_active_site_ts")
    cx.execute("DROP INDEX IF EXISTS idx_hist_library_id")
    cx.execute("DROP INDEX IF EXISTS idx_q_site_status_ord")
    cx.execute("DROP INDEX IF EXISTS idx_tags_created_at")
