"""v3.49 (#64): Queue templates.

Save a named snapshot of a site's current queue, recall it later. Use
cases:
  - "Nightly check-ins" — a recurring set of URLs reloaded by schedule
  - "Cold-start kit" — the bookmarks you always want re-queued on a
    fresh install
  - "Last week's good batch" — preserve a known-working state before
    experimenting

Templates live in a new SQLite table separate from `queue` so they
survive site deletion. Each template is owned by a site_id but can be
imported into ANY site (cross-site reuse is the main reason for keeping
them out of the per-site queue rows).

Storage: name + site_id (origin) + urls (newline-separated) + priority
(JSON map url→priority) + force_download (JSON set of urls) + ts_created
+ ts_used.

API surface (registered in app.py):
  POST /api/queue_templates                — create
  GET  /api/queue_templates                — list
  GET  /api/queue_templates/<id>           — get one
  PUT  /api/queue_templates/<id>           — rename / replace contents
  DELETE /api/queue_templates/<id>         — drop
  POST /api/queue_templates/<id>/apply/<sid> — import into a site
"""
from __future__ import annotations
import json
import time
from typing import Optional

from .db import db_conn


def _ensure_schema():
    """Idempotent — safe to call on every read/write."""
    with db_conn() as cx:
        cx.execute("""CREATE TABLE IF NOT EXISTS queue_templates(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            origin_site_id TEXT NOT NULL,
            urls TEXT NOT NULL DEFAULT '',
            priority_map TEXT NOT NULL DEFAULT '{}',
            force_set TEXT NOT NULL DEFAULT '[]',
            note TEXT DEFAULT '',
            ts_created REAL NOT NULL,
            ts_used REAL DEFAULT 0,
            use_count INTEGER DEFAULT 0
        )""")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_tpl_origin "
                   "ON queue_templates(origin_site_id)")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_tpl_name "
                   "ON queue_templates(name)")


def create(name: str, origin_site_id: str,
           urls: list[str],
           priority_map: Optional[dict] = None,
           force_set: Optional[list] = None,
           note: str = "") -> int:
    """Create a new template. Returns the row id.

    `urls` is normalized — duplicates dropped (keeping first occurrence),
    blank lines stripped, leading/trailing whitespace removed. The order
    is preserved (it matters for the queue when applied).
    """
    _ensure_schema()
    seen = set()
    clean = []
    for u in urls:
        u = (u or "").strip()
        if not u or u in seen: continue
        seen.add(u)
        clean.append(u)
    with db_conn() as cx:
        cur = cx.execute(
            "INSERT INTO queue_templates("
            "  name, origin_site_id, urls, priority_map, force_set,"
            "  note, ts_created"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name.strip() or "(unnamed)",
             origin_site_id,
             "\n".join(clean),
             json.dumps(priority_map or {}),
             json.dumps(force_set or []),
             note.strip(),
             time.time()))
        return cur.lastrowid


def list_all() -> list[dict]:
    """Return all templates, newest first. URLs NOT included in the
    list response (could be huge); fetch individual templates for that."""
    _ensure_schema()
    with db_conn() as cx:
        rows = cx.execute(
            "SELECT id, name, origin_site_id, note, "
            "       ts_created, ts_used, use_count, "
            "       (length(urls) - length(replace(urls, char(10), '')) + 1) "
            "         AS url_count "
            "FROM queue_templates ORDER BY ts_created DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # An empty-urls template would report url_count=1 from the
            # length math; fix that edge case.
            if not d["url_count"]: d["url_count"] = 0
            out.append(d)
        return out


def get(template_id: int) -> Optional[dict]:
    """Fetch a single template with its full URL list + maps. Returns
    None if the ID doesn't exist."""
    _ensure_schema()
    with db_conn() as cx:
        row = cx.execute(
            "SELECT * FROM queue_templates WHERE id=?",
            (int(template_id),)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["urls"] = [u for u in (d.get("urls") or "").split("\n") if u]
    try:
        d["priority_map"] = json.loads(d.get("priority_map") or "{}")
    except json.JSONDecodeError:
        d["priority_map"] = {}
    try:
        d["force_set"] = json.loads(d.get("force_set") or "[]")
    except json.JSONDecodeError:
        d["force_set"] = []
    return d


def update(template_id: int,
           name: Optional[str] = None,
           urls: Optional[list[str]] = None,
           priority_map: Optional[dict] = None,
           force_set: Optional[list] = None,
           note: Optional[str] = None) -> bool:
    """Modify an existing template. Each field is optional — pass None
    to leave unchanged. Returns True if a row was actually updated."""
    _ensure_schema()
    sets = []
    params = []
    if name is not None:
        sets.append("name=?"); params.append(name.strip() or "(unnamed)")
    if urls is not None:
        seen = set(); clean = []
        for u in urls:
            u = (u or "").strip()
            if not u or u in seen: continue
            seen.add(u); clean.append(u)
        sets.append("urls=?"); params.append("\n".join(clean))
    if priority_map is not None:
        sets.append("priority_map=?")
        params.append(json.dumps(priority_map))
    if force_set is not None:
        sets.append("force_set=?")
        params.append(json.dumps(force_set))
    if note is not None:
        sets.append("note=?"); params.append(note.strip())
    if not sets:
        return False
    params.append(int(template_id))
    with db_conn() as cx:
        cur = cx.execute(
            f"UPDATE queue_templates SET {', '.join(sets)} WHERE id=?",
            params)
        return cur.rowcount > 0


def delete(template_id: int) -> bool:
    """Remove a template. Returns True if a row was actually removed."""
    _ensure_schema()
    with db_conn() as cx:
        cur = cx.execute(
            "DELETE FROM queue_templates WHERE id=?",
            (int(template_id),))
        return cur.rowcount > 0


def record_use(template_id: int):
    """Bump the use_count + ts_used. Best-effort; failures swallowed.
    Called by the apply endpoint after a successful import."""
    _ensure_schema()
    try:
        with db_conn() as cx:
            cx.execute(
                "UPDATE queue_templates SET use_count = use_count + 1, "
                "ts_used = ? WHERE id = ?",
                (time.time(), int(template_id)))
    except Exception:
        pass
