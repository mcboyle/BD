"""Cookie relogin scheduler (Phase 131, Block Q).

Most BD failures trace back to expired cookies. Operator notices
"failed: 401" pile up, manually re-logs in, queue catches up. This
module proactively checks every site's cookie quality on a schedule
and triggers a re-login when a jar drops below threshold —
ideally before any job has failed.

Strategy:
  • Every N minutes (default 60), score all jars via cookie_quality
  • If score < relogin_threshold (default 50), enqueue a relogin
  • Re-login workflow is BD-internal: trigger the existing teach
    flow via the site keeper. We only mark the site as needing it;
    actual auth replay is handled by the workers.

Records: `cookie_relogin_log` audit table tracks every scheduled
relogin, the cookie score at trigger time, and the outcome.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Optional


_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _ensure_table():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS cookie_relogin_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                site_id TEXT NOT NULL,
                triggered_score INTEGER,
                action TEXT NOT NULL,
                outcome TEXT DEFAULT '',
                outcome_ts REAL
            )""")
    except Exception as e:
        sys.stderr.write(f"[cookie_relogin] schema init: {e}\n")


def _record(site_id: str, score: int, action: str) -> Optional[int]:
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("""INSERT INTO cookie_relogin_log(
                ts, site_id, triggered_score, action
            ) VALUES (?,?,?,?)""",
                (time.time(), site_id, int(score or 0), action))
            return cur.lastrowid
    except Exception:
        return None


def _record_outcome(log_id: int, outcome: str):
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""UPDATE cookie_relogin_log
                          SET outcome = ?, outcome_ts = ?
                          WHERE id = ?""",
                       (outcome[:200], time.time(), int(log_id)))
    except Exception:
        pass


def _ratelimit_ok(site_id: str, *, min_seconds_between: int = 3600) -> bool:
    """Don't trigger relogin more than once an hour per site."""
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            r = cx.execute("""SELECT ts FROM cookie_relogin_log
                              WHERE site_id = ? ORDER BY id DESC LIMIT 1""",
                          (site_id,)).fetchone()
        if not r:
            return True
        last_ts = float(r[0])
        return (time.time() - last_ts) > min_seconds_between
    except Exception:
        return True


def check_and_schedule(s_cfg: Optional[dict] = None,
                      *, relogin_threshold: int = 50,
                      runners: Optional[dict] = None) -> dict:
    """One-shot check across all sites. Logs and returns the list of
    sites that got a relogin scheduled.

    v3.47.7: respect per-site `auto_relogin_enabled` (default True) and
    `auto_relogin_interval_hours` (default 12). When disabled, the site
    is skipped entirely — the user has opted out and the scheduler
    never spawns a fresh login session for them. When enabled, the
    per-site interval becomes the minimum gap between successive
    relogins, overriding the default 1h rate-limit floor for users who
    want LESS frequent relogins on stable sites.
    """
    if not s_cfg:
        return {"checked": 0, "scheduled": 0, "actions": []}
    scheduled = []
    skipped = []
    try:
        from . import cookie_quality as _cq
        for sid, cfg in s_cfg.items():
            if not cfg or not cfg.get("auth_required", True):
                continue
            # v3.47.7: opt-out switch
            if not cfg.get("auto_relogin_enabled", True):
                skipped.append({"site_id": sid,
                                "reason": "auto_relogin_enabled=False (user opted out)"})
                continue
            # v3.47.7: per-site interval (hours → seconds). Clamp to
            # [0.25h, 168h] so a misconfig can't either DoS the keeper
            # (0) or effectively disable relogin for a week+ (we'd
            # rather the user use the toggle for that).
            try:
                _hours = float(cfg.get("auto_relogin_interval_hours", 12) or 12)
            except (TypeError, ValueError):
                _hours = 12.0
            _hours = max(0.25, min(168.0, _hours))
            _gap_seconds = int(_hours * 3600)
            try:
                score_result = _cq.score(sid, s_cfg_entry=cfg)
            except Exception as e:
                skipped.append({"site_id": sid, "reason": f"score failed: {e}"})
                continue
            score = score_result.get("score", 100)
            if score >= relogin_threshold:
                continue
            if not _ratelimit_ok(sid, min_seconds_between=_gap_seconds):
                skipped.append({"site_id": sid,
                                "reason": f"rate-limited (interval {_hours}h)"})
                continue
            log_id = _record(sid, score, "schedule_relogin")
            # Mark on the runner if available; the keeper will see
            # the flag next iteration and act on it.
            if runners and sid in runners:
                try:
                    runners[sid].needs_relogin = True  # type: ignore
                except Exception:
                    pass
            scheduled.append({"site_id": sid, "score": score,
                              "log_id": log_id,
                              "action": score_result.get("suggested_action")})
    except Exception as e:
        return {"error": str(e)[:200]}
    return {
        "checked": len(s_cfg),
        "scheduled": len(scheduled),
        "skipped": skipped,
        "actions": scheduled,
    }


def report_recent(limit: int = 50) -> list:
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT * FROM cookie_relogin_log
                                  ORDER BY id DESC LIMIT ?""",
                              (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ─── Background loop ──────────────────────────────────────────────────

def start_loop(*, s_cfg_provider, runners_provider,
              check_interval_seconds: int = 3600,
              threshold: int = 50):
    """Spin up a daemon thread that calls check_and_schedule on a
    fixed cadence. Callers pass lambdas so we always see the live
    s_cfg / runners snapshot."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        _stop.clear()

        def loop():
            # Initial delay to let BD finish startup
            _stop.wait(60)
            while not _stop.is_set():
                try:
                    s_cfg = s_cfg_provider() if callable(s_cfg_provider) else s_cfg_provider
                    runners = runners_provider() if callable(runners_provider) else runners_provider
                    check_and_schedule(s_cfg=s_cfg, runners=runners,
                                      relogin_threshold=threshold)
                except Exception as e:
                    sys.stderr.write(f"[cookie_relogin] loop: {e}\n")
                _stop.wait(check_interval_seconds)

        _thread = threading.Thread(target=loop, daemon=True,
                                   name="bd-cookie-relogin")
        _thread.start()
        return True


def stop_loop():
    _stop.set()
