"""Gamification — streaks, achievements, throughput records
(Phase 118, Block Q — optional/cosmetic).

Lightweight motivator surface. Tracks:

  • Daily download streak (consecutive days with ≥1 successful download)
  • Lifetime totals (downloads, bytes, hours of content)
  • Personal-best records (most in a day, biggest file, fastest hour)
  • Achievement badges (unlocked on milestones)

Stored in a single `gamification_state` row + `achievements_unlocked`
table. Pure read on history for derivations; only writes the
unlocked timestamps.

Operator can disable in config (gamification_enabled=False) — silent
no-op when disabled.
"""
from __future__ import annotations

import datetime
import time
from typing import Optional


_ACHIEVEMENTS = [
    # (id, label, predicate fn returning bool given stats dict, icon)
    ("first_download", "First Download",
     lambda s: s.get("total_done", 0) >= 1, "🎬"),
    ("century", "Centurion",
     lambda s: s.get("total_done", 0) >= 100, "💯"),
    ("kilodown", "1k Club",
     lambda s: s.get("total_done", 0) >= 1000, "🏆"),
    ("ten_k", "10k Club",
     lambda s: s.get("total_done", 0) >= 10000, "🌟"),
    ("terabyte", "Terabyte Hoarder",
     lambda s: s.get("total_bytes", 0) >= 1024**4, "💾"),
    ("week_streak", "Week Streak",
     lambda s: s.get("current_streak_days", 0) >= 7, "🔥"),
    ("month_streak", "Month Streak",
     lambda s: s.get("current_streak_days", 0) >= 30, "🏅"),
    ("hundred_streak", "Centennial Streak",
     lambda s: s.get("current_streak_days", 0) >= 100, "👑"),
    ("speedrun", "Speedrun (50/hour)",
     lambda s: s.get("max_hourly", 0) >= 50, "🚀"),
    ("big_file", "Whale (10+ GB)",
     lambda s: s.get("max_file_size", 0) >= 10 * 1024**3, "🐋"),
    ("multi_site", "Cosmopolitan (5+ sites)",
     lambda s: s.get("unique_sites", 0) >= 5, "🌍"),
]


def _ensure_tables():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS gamification_state(
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_active_date TEXT,
                current_streak_days INTEGER DEFAULT 0,
                longest_streak_days INTEGER DEFAULT 0,
                max_hourly INTEGER DEFAULT 0,
                max_daily INTEGER DEFAULT 0
            )""")
            cx.execute("""CREATE TABLE IF NOT EXISTS achievements_unlocked(
                id TEXT PRIMARY KEY,
                unlocked_at REAL NOT NULL
            )""")
            cx.execute("""INSERT OR IGNORE INTO gamification_state(id)
                          VALUES (1)""")
    except Exception as e:
        import sys
        sys.stderr.write(f"[gamification] schema init failed: {e}\n")


def stats() -> dict:
    """Aggregate stats used for streak + achievement evaluation.
    Cheap — single-pass over history table aggregations."""
    out = {
        "total_done": 0, "total_bytes": 0, "unique_sites": 0,
        "current_streak_days": 0, "longest_streak_days": 0,
        "max_hourly": 0, "max_daily": 0, "max_file_size": 0,
        "today_count": 0, "today_bytes": 0,
        "lifetime_hours": 0.0,
    }
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            r = cx.execute("""SELECT COUNT(*) AS n,
                                     COALESCE(SUM(file_size), 0) AS b,
                                     COUNT(DISTINCT site_id) AS s,
                                     COALESCE(MAX(file_size), 0) AS mx
                              FROM history WHERE status='done'""").fetchone()
            out["total_done"] = int(r[0] or 0)
            out["total_bytes"] = int(r[1] or 0)
            out["unique_sites"] = int(r[2] or 0)
            out["max_file_size"] = int(r[3] or 0)
            # Today
            today = cx.execute("""SELECT COUNT(*) AS n,
                                          COALESCE(SUM(file_size), 0) AS b
                                   FROM history
                                   WHERE status='done'
                                     AND date(ts) = date('now')""").fetchone()
            out["today_count"] = int(today[0] or 0)
            out["today_bytes"] = int(today[1] or 0)
            # Max per-day historically
            r = cx.execute("""SELECT MAX(n) FROM (
                                SELECT COUNT(*) AS n FROM history
                                WHERE status='done'
                                GROUP BY date(ts))""").fetchone()
            out["max_daily"] = int(r[0] or 0)
            # Max per-hour historically
            r = cx.execute("""SELECT MAX(n) FROM (
                                SELECT COUNT(*) AS n FROM history
                                WHERE status='done'
                                GROUP BY strftime('%Y-%m-%d %H', ts))""").fetchone()
            out["max_hourly"] = int(r[0] or 0)
            # Compute streak by walking distinct dates backwards
            dates = [r[0] for r in cx.execute(
                """SELECT DISTINCT date(ts) FROM history
                   WHERE status='done' ORDER BY date(ts) DESC""").fetchall()]
        streak = 0
        if dates:
            today = datetime.date.today()
            yesterday = today - datetime.timedelta(days=1)
            anchor = datetime.date.fromisoformat(dates[0])
            # Streak counts if last activity was today OR yesterday
            if anchor in (today, yesterday):
                expected = anchor
                for d in dates:
                    actual = datetime.date.fromisoformat(d)
                    if actual == expected:
                        streak += 1
                        expected -= datetime.timedelta(days=1)
                    else:
                        break
        out["current_streak_days"] = streak
        # Longest streak from state table
        with _db.db_conn() as cx:
            r = cx.execute("""SELECT longest_streak_days FROM gamification_state
                              WHERE id = 1""").fetchone()
            out["longest_streak_days"] = max(
                int(r[0] or 0) if r else 0, streak)
            # Persist if we set a new longest
            if streak > (int(r[0] or 0) if r else 0):
                cx.execute("""UPDATE gamification_state
                              SET longest_streak_days = ?,
                                  current_streak_days = ?,
                                  last_active_date = date('now')
                              WHERE id = 1""", (streak, streak))
    except Exception as e:
        import sys
        sys.stderr.write(f"[gamification] stats failed: {e}\n")
    # Lifetime hours assuming 30min avg per video; rough proxy
    out["lifetime_hours"] = round(out["total_done"] * 0.5, 1)
    return out


def check_achievements() -> list:
    """Evaluate all achievement predicates against current stats.
    Returns list of {id, label, icon, unlocked_at, just_unlocked}."""
    _ensure_tables()
    s = stats()
    out = []
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            already = {r[0]: r[1] for r in cx.execute(
                "SELECT id, unlocked_at FROM achievements_unlocked").fetchall()}
        now = time.time()
        with _db.db_conn() as cx:
            for aid, label, predicate, icon in _ACHIEVEMENTS:
                hit = bool(predicate(s))
                entry = {"id": aid, "label": label, "icon": icon,
                         "unlocked": False, "just_unlocked": False,
                         "unlocked_at": already.get(aid)}
                if aid in already:
                    entry["unlocked"] = True
                elif hit:
                    cx.execute("""INSERT INTO achievements_unlocked(
                        id, unlocked_at) VALUES (?, ?)""", (aid, now))
                    entry["unlocked"] = True
                    entry["just_unlocked"] = True
                    entry["unlocked_at"] = now
                out.append(entry)
    except Exception as e:
        import sys
        sys.stderr.write(f"[gamification] check failed: {e}\n")
    return out


def report() -> dict:
    """One-call dashboard summary."""
    return {"stats": stats(), "achievements": check_achievements()}
