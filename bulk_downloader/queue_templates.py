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


def job_url(raw) -> str:
    """The queue key of one URL entry. An entry may carry tab-separated
    ``Header=value`` pairs after the URL (runner load_urls, Phase 68); the
    job is keyed by the URL alone, so row metadata must be looked up by it."""
    return str(raw or "").split("\t", 1)[0].strip()


def masked_entry(entry):
    """queue-headers-redact r1: ``entry`` as a client sees it -- the URL, and
    each ``Name=value`` after it as ``Name=`` + QUEUE_HEADER_MASK (a part with
    no name is masked whole). Anything but a str is returned as it is."""
    if not isinstance(entry, str) or "\t" not in entry:
        return entry
    from .db import QUEUE_HEADER_MASK
    parts = entry.split("\t")
    return "\t".join(parts[:1] + [
        (p.split("=", 1)[0] + "=" if "=" in p else "") + QUEUE_HEADER_MASK
        for p in parts[1:]])


def held_header_values(entries) -> dict:
    """{(job URL, header name): value} written in ``entries`` (stored ones)."""
    held = {}
    for e in entries or []:
        if not isinstance(e, str): continue
        parts = e.split("\t")
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                held[(parts[0].strip(), k.strip())] = v.strip()
    return held


def unmasked_entry(entry, held: dict):
    """An entry handed back by a client: a ``Name=`` + QUEUE_HEADER_MASK pair
    takes the value held for that URL and name, or is dropped when none is --
    the mask is never stored as a header value."""
    if not isinstance(entry, str) or "\t" not in entry:
        return entry
    from .db import QUEUE_HEADER_MASK
    parts = entry.split("\t")
    keep = parts[:1]
    for p in parts[1:]:
        k, _, v = p.partition("=")
        if v.strip() != QUEUE_HEADER_MASK and p.strip() != QUEUE_HEADER_MASK:
            keep.append(p)
        elif v and (parts[0].strip(), k.strip()) in held:
            keep.append(f"{k}={held[(parts[0].strip(), k.strip())]}")
    return "\t".join(keep)


def clean_urls(urls) -> list:
    """Template URL entries: one per line (an entry holding a line break is
    several), stripped, blanks and repeats dropped, order kept."""
    seen = set()
    clean = []
    for entry in urls:
        for u in str(entry or "").splitlines():
            u = u.strip()
            if not u or u in seen: continue
            seen.add(u)
            clean.append(u)
    return clean


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
    clean = clean_urls(urls)
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
            "       (CASE WHEN urls = '' THEN 0 ELSE "
            "        length(urls) - length(replace(urls, char(10), '')) + 1 END) "
            "         AS url_count "
            "FROM queue_templates ORDER BY ts_created DESC"
        ).fetchall()
        out = []
        for r in rows:
            # qt-validate L4: an empty-urls template is 0 in the SQL itself (the length math alone gives 1).
            out.append(dict(r))
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
    # clean_urls, not split("\n"): a row stored before the fix can hold "\r"
    d["urls"] = clean_urls([d.get("urls") or ""])
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
        sets.append("urls=?"); params.append("\n".join(clean_urls(urls)))
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
