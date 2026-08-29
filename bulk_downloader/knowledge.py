"""Knowledge management — operator runbooks + "why it broke" notes
(Phase 111, Block P).

Two related capabilities:

1. Per-site cookbook entries: auto-generated runbook per configured
   template, describing the dispatch path, common failure modes, and
   what to do when things break. The data is read from existing
   sources (template definitions, recent history, site state); this
   module synthesizes a single "how do I fix this site" doc per site.

2. Operator-recorded notes attached to specific failure patterns:
   the operator marks a failure ("turnstile every other try") with a
   resolution note ("rotate to nl-002 endpoint"). Next time the same
   pattern appears, BD surfaces the note next to the failed row.

Both are stored in a `knowledge_notes` table. The runbook generation
side is computed on-demand (cheap; no caching).
"""
from __future__ import annotations

import re
import time
from typing import Optional


def _ensure_table():
    """Lazy schema. notes are operator-supplied + recall-on-error."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS knowledge_notes(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id TEXT DEFAULT '',
                kind TEXT NOT NULL,
                pattern TEXT NOT NULL,
                resolution TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_matched REAL DEFAULT 0,
                match_count INTEGER DEFAULT 0
            )""")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_kn_site ON knowledge_notes(site_id)")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_kn_kind ON knowledge_notes(kind)")
    except Exception as e:
        import sys
        sys.stderr.write(f"[knowledge] init failed: {e}\n")


# ─── Notes: pattern → resolution ──────────────────────────────────────

def add_note(*, site_id: str = "", kind: str = "failure",
            pattern: str, resolution: str) -> Optional[int]:
    """Add an operator note. `pattern` is a substring (case-insensitive)
    matched against future failure messages; `resolution` is the
    operator's how-to-fix text. `kind` is currently 'failure' but
    reserved for future categories ('login', 'rate_limit', etc).

    Returns row id or None on failure."""
    if not pattern or not resolution:
        return None
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("""INSERT INTO knowledge_notes(
                site_id, kind, pattern, resolution, created_at
            ) VALUES (?,?,?,?,?)""",
                (site_id or "", kind, pattern.strip(),
                 resolution.strip(), time.time()))
            return cur.lastrowid
    except Exception:
        return None


def remove_note(note_id: int) -> bool:
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("DELETE FROM knowledge_notes WHERE id = ?",
                             (int(note_id),))
            return cur.rowcount > 0
    except Exception:
        return False


def find_matching_notes(*, site_id: str = "", message: str = "") -> list:
    """Find notes whose pattern matches the given message + site.
    Site-scoped notes match only within their site_id; global notes
    (empty site_id) match across all. Updates last_matched + match_count
    on each hit. Returns list of dicts sorted by match_count DESC
    (most-useful notes first)."""
    _ensure_table()
    if not message:
        return []
    msg_norm = message.lower()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT * FROM knowledge_notes
                                  WHERE site_id = '' OR site_id = ?""",
                              (site_id or "",)).fetchall()
        hits = []
        for r in rows:
            d = dict(r)
            if d["pattern"].lower() in msg_norm:
                hits.append(d)
        if hits:
            now = time.time()
            with _db.db_conn() as cx:
                for h in hits:
                    cx.execute("""UPDATE knowledge_notes
                                  SET last_matched = ?, match_count = match_count + 1
                                  WHERE id = ?""", (now, h["id"]))
        return sorted(hits, key=lambda r: -r["match_count"])
    except Exception:
        return []


def list_notes(*, site_id: Optional[str] = None,
              kind: Optional[str] = None) -> list:
    """List all notes (optionally filtered). Newest first."""
    _ensure_table()
    sql = "SELECT * FROM knowledge_notes WHERE 1=1"
    params = []
    if site_id is not None:
        sql += " AND site_id = ?"
        params.append(site_id)
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY created_at DESC"
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            return [dict(r) for r in cx.execute(sql, params).fetchall()]
    except Exception:
        return []


# ─── Per-site runbook generation ──────────────────────────────────────

def runbook(site_id: str, *, s_cfg: Optional[dict] = None,
            history_window_days: int = 30) -> dict:
    """Generate a runbook for `site_id`. Read-only assembly from:
      • Site config (extractor name, auth method, VPN endpoint)
      • Recent history (top failure messages, success rate)
      • Operator notes (site-scoped + global)
      • Linked templates (selectors learned, last successful URL)

    Returns a structured dict the UI can render."""
    out = {"site_id": site_id, "name": "", "config": {},
           "recent_stats": {}, "top_failures": [], "notes": [],
           "generated_at": time.time()}
    cfg = (s_cfg or {}).get(site_id, {}) if s_cfg else {}
    out["name"] = cfg.get("name", site_id)
    out["config"] = {
        "template": cfg.get("template", ""),
        "auth_method": cfg.get("auth_method", "cookies"),
        "use_vpn": bool(cfg.get("use_vpn", False)),
        "vpn_tunnel": cfg.get("vpn_tunnel", ""),
        "max_concurrent": cfg.get("max_concurrent", 2),
        "min_resolution": cfg.get("min_resolution", 0),
        "use_ytdlp_fallback": bool(cfg.get("use_ytdlp_fallback", False)),
        "has_learned_selectors": bool((cfg.get("learned") or {}).get("login")),
    }
    # Recent stats: success rate + count over window
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            stats_row = cx.execute("""SELECT
                SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status='needs_review' THEN 1 ELSE 0 END) AS needs_review,
                COUNT(*) AS total
                FROM history
                WHERE site_id = ?
                  AND ts >= datetime('now', ?)""",
                (site_id, f"-{int(history_window_days)} days")).fetchone()
            stats = dict(stats_row) if stats_row else {}
        done = int(stats.get("done", 0) or 0)
        failed = int(stats.get("failed", 0) or 0)
        review = int(stats.get("needs_review", 0) or 0)
        total = int(stats.get("total", 0) or 0)
        out["recent_stats"] = {
            "window_days": history_window_days,
            "total": total, "done": done,
            "failed": failed, "needs_review": review,
            "success_rate_pct": round(100 * done / max(1, done + failed), 1),
        }
        # Top failure messages (clustered by first 80 chars)
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT message, COUNT(*) AS n
                                  FROM history
                                  WHERE site_id = ? AND status = 'failed'
                                    AND ts >= datetime('now', ?)
                                  GROUP BY substr(message, 1, 80)
                                  ORDER BY n DESC LIMIT 5""",
                              (site_id, f"-{int(history_window_days)} days")).fetchall()
        out["top_failures"] = [
            {"message": r[0] if not hasattr(r, "keys") else r["message"],
             "count": int(r[1] if not hasattr(r, "keys") else r["n"])}
            for r in rows
        ]
    except Exception:
        pass
    out["notes"] = list_notes(site_id=site_id)
    return out


# ─── Diagnostic recipes ───────────────────────────────────────────────

# Static catalog of "try these steps" recipes keyed on common failure
# substrings. The UI surfaces a recipe next to any failure whose
# message matches one of these. Easy to extend.
_RECIPES: list = [
    {
        "pattern": "cloudflare",
        "title": "Cloudflare challenge",
        "steps": [
            "Check FlareSolverr is running (see /api/flaresolverr/status)",
            "If FlareSolverr is up, try Take Over on one URL to refresh cf_clearance",
            "If repeated, the site enabled stricter ruleset — try nodriver via Scrapling adapter",
        ],
    },
    {
        "pattern": "captcha",
        "title": "Captcha required",
        "steps": [
            "Verify 2Captcha/Capsolver API key is set in /options",
            "Check captcha balance: low balance silently disables solving",
            "If hCaptcha persists, switch to nodriver-mode in the site config",
        ],
    },
    {
        "pattern": "rate limit",
        "title": "Rate limited (429)",
        "steps": [
            "Workers will back off automatically",
            "If chronic, lower max_concurrent for this site",
            "Add a delay_between_downloads if not already set",
        ],
    },
    {
        "pattern": "out of disk",
        "title": "Disk full",
        "steps": [
            "Free space on download_dir or move it",
            "Check the soft-throttle is configured: disk_threshold_gb",
            "Review /api/bitrot/issues for orphan files to clean up",
        ],
    },
    {
        "pattern": "moov atom not found",
        "title": "MP4 corruption (no moov atom)",
        "steps": [
            "File is incomplete — usually a mid-download crash",
            "Check .part file in download_dir; it may be resumable",
            "If chronic, suspect storage tier — run /api/bitrot/scan",
        ],
    },
    {
        "pattern": "401",
        "title": "Authentication failure",
        "steps": [
            "Cookies expired — re-login via Take Over",
            "Check cookie_info.earliest in status (see countdown badge)",
            "If account is banned, mark cooldown in /api/accounts/health",
        ],
    },
]


def _turnstile_recipe_step(turnstile_bypass: Optional[dict]) -> str:
    """Render capability-aware advice without inventing a measurement."""
    state = turnstile_bypass if isinstance(turnstile_bypass, dict) else {
        "available": False,
        "status": "unknown",
        "reason": "measurement_not_supplied",
    }
    status = state.get("status")
    available = state.get("available")
    reason = state.get("reason") or "measurement_not_supplied"
    if status == "available" and available is True:
        return (
            "If repeated, the site enabled stricter ruleset — try nodriver "
            "via Scrapling adapter"
        )
    if status == "unavailable" and available is False:
        return (
            f"Scrapling bypass unavailable ({reason}); "
            "use Take Over or FlareSolverr"
        )
    return (
        f"Scrapling bypass availability unknown ({reason}); "
        "use Take Over or FlareSolverr"
    )


def _recipes_with_capability(turnstile_bypass: Optional[dict]) -> list:
    recipes = []
    for recipe in _RECIPES:
        rendered = dict(recipe)
        rendered["steps"] = list(recipe["steps"])
        if recipe["pattern"] == "cloudflare":
            rendered["steps"][-1] = _turnstile_recipe_step(turnstile_bypass)
        recipes.append(rendered)
    return recipes


def diagnostic_recipes_for(
        message: str, *, turnstile_bypass: Optional[dict] = None) -> list:
    """Return all recipes whose pattern matches the given failure
    message. Multiple may match for complex errors."""
    if not message:
        return []
    msg = message.lower()
    return [
        recipe for recipe in _recipes_with_capability(turnstile_bypass)
        if recipe["pattern"].lower() in msg
    ]


def all_recipes(*, turnstile_bypass: Optional[dict] = None) -> list:
    """Full catalog. Used by the UI to render a docs page."""
    return _recipes_with_capability(turnstile_bypass)
