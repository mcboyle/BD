"""Cookie quality scoring (Phase 125, Block Q).

After login, BD captures a cookie jar per site. Not all cookie jars
are equal — a freshly-logged-in session vs a half-stale jar with a
stripped __cf_bm vs an injected manual export all behave differently
under load. This module publishes a 0-100 score when freshness is
measurable and an explicit UNKNOWN verdict when it is not, so the
operator can distinguish evidence from an unavailable measurement.

Scoring inputs (per site):
  • Age of newest auth cookie — fresher is better (decay over weeks)
  • Has all expected cookie names (site-specific expected list)
  • Cloudflare cookies present (cf_clearance, __cf_bm)
  • Recent success rate using this jar (last 50 jobs)
  • Last login timestamp

Output: {site_id, score, breakdown, suggested_action, measurement_status,
         checks}
where suggested_action ∈ {"ok", "refresh_soon", "refresh_now",
"unknown"}.
"""
from __future__ import annotations

import time
from typing import Optional


# Per-site expected cookie names — best-effort. Operator's site
# config can override via cfg['expected_cookies'].
_DEFAULT_EXPECTED: dict = {
    "vixen": ["sess", "PHPSESSID", "__cf_bm"],
    "blacked": ["sess", "PHPSESSID", "__cf_bm"],
    "brazzers": ["session_id", "ts_user_id", "remember_token"],
    "adulttime": ["session_token", "auth_token"],
    "bangbros": ["session", "wp-user"],
}


def _load_cookies(site_id: str,
                 s_cfg_entry: Optional[dict] = None) -> Optional[list]:
    """Load the cookie jar for `site_id`. Returns list of dicts with
    name/value/expires/domain. Empty if missing or unreadable.

    Resolves the path via s_cfg_entry["cookie_file"] (the canonical
    location in BD's site config). If no entry is given, falls back to
    the conventional INSTALL_DIR/cookies/<site_id>.json path."""
    path = ""
    if s_cfg_entry:
        path = (s_cfg_entry.get("cookie_file") or "").strip()
    if not path:
        # Fallback: conventional location. Validate site_id first (strict
        # ASCII [A-Za-z0-9_-]) so it can't traverse out of the cookies/ dir
        # (F-AUTH02-03).
        sid = str(site_id or "")
        if sid and all((c.isascii() and c.isalnum()) or c in "_-" for c in sid):
            try:
                from .constants import INSTALL_DIR
                from pathlib import Path
                candidate = Path(INSTALL_DIR) / "cookies" / f"{site_id}.json"
                if candidate.is_file():
                    path = str(candidate)
            except Exception:
                pass
    if not path:
        return []
    try:
        from . import cookies as _ck
        return _ck.load_cookies_from_file(path) or []
    except Exception:
        return []


def _recent_success_rate(site_id: str, *, window: int = 50) -> Optional[float]:
    """Return success rate from the last `window` non-pending rows.
    None if there's no history yet."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT status FROM history
                                  WHERE site_id = ?
                                    AND status IN ('done', 'failed', 'needs_review')
                                  ORDER BY id DESC LIMIT ?""",
                              (site_id, int(window))).fetchall()
    except Exception:
        return None
    if not rows:
        return None
    done = sum(1 for r in rows
               if (r[0] if not hasattr(r, "keys") else r["status"]) == "done")
    return done / len(rows)


def score(site_id: str, *, s_cfg_entry: Optional[dict] = None) -> dict:
    """Score the cookie jar for `site_id`. Returns:
      {site_id, score, breakdown, suggested_action, jar_size, newest_age_days,
       measurement_status, checks}.

    ``score`` is ``None`` when a non-empty jar has no measurable freshness.
    Other checks may make that result ``partial`` rather than wholly
    ``unmeasured``, but cannot manufacture an aggregate numeric grade. This is
    different from a missing jar, whose measured remediation state remains
    score 0 / ``refresh_now``.
    """
    out = {"site_id": site_id, "score": None, "breakdown": {},
           "suggested_action": "unknown",
           "jar_size": 0, "newest_age_days": None,
           "measurement_status": "unmeasured",
           "checks": {"applicable": [], "ran": []}}
    jar = _load_cookies(site_id, s_cfg_entry=s_cfg_entry)
    out["jar_size"] = len(jar)
    if not jar:
        out["score"] = 0
        out["breakdown"]["missing_jar"] = -100
        out["suggested_action"] = "refresh_now"
        out["measurement_status"] = "missing"
        return out

    score_total = 100  # start at perfect
    applicable = out["checks"]["applicable"]
    ran = out["checks"]["ran"]

    # 1. Freshness — find newest cookie expiration. Lower-bound: 7 days
    #    out from now is fresh, <1 day is stale.
    applicable.append("freshness")
    now = time.time()
    expires = [c["expires"] for c in jar
               if isinstance(c.get("expires"), (int, float))
               and not isinstance(c.get("expires"), bool)
               and c["expires"] > 0]
    if expires:
        ran.append("freshness")
        newest_exp = max(expires)
        days_remaining = (newest_exp - now) / 86400
        out["newest_age_days"] = round(days_remaining, 1)
        if days_remaining < 1:
            score_total -= 50
            out["breakdown"]["near_expiry"] = -50
        elif days_remaining < 7:
            score_total -= 25
            out["breakdown"]["expiring_soon"] = -25
        else:
            out["breakdown"]["freshness_ok"] = 0
    else:
        out["breakdown"]["freshness_unmeasured"] = (
            "no expiring cookies in the jar"
        )

    # 2. Expected cookies present?
    expected_names = (s_cfg_entry or {}).get("expected_cookies") or _DEFAULT_EXPECTED.get(site_id, [])
    names_present = {c.get("name", "") for c in jar}
    if expected_names:
        applicable.append("expected_cookies")
        ran.append("expected_cookies")
        missing = [n for n in expected_names if n not in names_present]
        if missing:
            penalty = min(30, len(missing) * 10)
            score_total -= penalty
            out["breakdown"]["missing_cookies"] = -penalty
            out["missing_cookies"] = missing
        else:
            out["breakdown"]["expected_cookies_ok"] = 0
    else:
        out["breakdown"]["expected_cookies_unmeasured"] = (
            "no expected_cookies configured"
        )

    # 3. Cloudflare cookies — important for many adult sites
    cf_names = {"__cf_bm", "cf_clearance"}
    cf_present = bool(cf_names & names_present)
    cf_expected = bool(cf_names & set(expected_names or []))
    if cf_present or cf_expected:
        applicable.append("cloudflare")
        ran.append("cloudflare")
        if cf_present:
            out["breakdown"]["has_cloudflare"] = 0
        else:
            # Expected but missing
            score_total -= 15
            out["breakdown"]["missing_cloudflare"] = -15
    else:
        out["breakdown"]["cloudflare_unmeasured"] = (
            "no Cloudflare cookies expected or present"
        )

    # 4. Recent success rate
    applicable.append("recent_success_rate")
    rate = _recent_success_rate(site_id)
    if rate is not None:
        ran.append("recent_success_rate")
        if rate >= 0.9:
            out["breakdown"]["success_rate_high"] = 0
        elif rate >= 0.5:
            score_total -= 15
            out["breakdown"]["success_rate_med"] = -15
        else:
            score_total -= 30
            out["breakdown"]["success_rate_low"] = -30
    else:
        out["breakdown"]["success_rate_unmeasured"] = (
            "no measurable recent job history"
        )

    # Freshness is the core basis for this aggregate. Other checks can provide
    # partial evidence, but they cannot turn an unmeasurable session-cookie
    # lifetime into a perfect numeric score.
    if "freshness" not in ran:
        if ran:
            out["measurement_status"] = "partial"
        return out

    out["score"] = max(0, score_total)
    out["measurement_status"] = (
        "measured" if len(ran) == len(applicable) else "partial"
    )
    # Suggested action thresholds
    if out["score"] >= 80:
        out["suggested_action"] = "ok"
    elif out["score"] >= 50:
        out["suggested_action"] = "refresh_soon"
    else:
        out["suggested_action"] = "refresh_now"
    return out


def report_all(s_cfg: Optional[dict] = None) -> list:
    """Score every configured site.

    Unscored/error rows sort before numeric rows so an unavailable measurement
    cannot be buried where a former score of 100 appeared. Numeric rows retain
    their previous ascending (worst-first) order.
    """
    if not s_cfg:
        return []
    out = []
    for sid, cfg in s_cfg.items():
        try:
            out.append(score(sid, s_cfg_entry=cfg))
        except Exception as e:
            out.append({"site_id": sid, "error": str(e)[:200]})
    def _sort_key(row: dict) -> tuple:
        value = row.get("score")
        numeric = (isinstance(value, (int, float))
                   and not isinstance(value, bool))
        return (1, value) if numeric else (0, 0)

    return sorted(out, key=_sort_key)
