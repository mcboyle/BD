"""Account health scoring + rotation hints (Phase 100, Block M).

Reads the existing AccountPool state (per-site) and exposes:

  • score_account()  — 0-100 health score derived from fail/lease
                       ratios + recency of last error
  • rotation_hint()  — which account the runner SHOULD pick next
                       (least-failed, least-recently-used, longest-
                       in-cooldown weighted blend)
  • ban_risk()       — boolean prediction: this account is heading
                       toward a ban; cool it down preemptively
  • report_all()     — flat list of {site_id, idx, username, score,
                       state, hint} for dashboards

Doesn't mutate state — read-only over AccountPool. The pool itself
(account_pool.py) stays the single writer; this module just gives the
runner smarter selection guidance + the UI a health view.

Heuristics, not learned models. Operator can override any score via
manual rotation if a ranking looks wrong.
"""
from __future__ import annotations

import time
from typing import Optional


class AccountHealthUnavailable(RuntimeError):
    """The account-pool census could not be measured."""


def _safe_get_pool(site_id: str):
    """Returns AccountPool or None. Avoids hard-importing in module
    scope so account_pool import order doesn't matter."""
    try:
        from . import account_pool as _ap
        return _ap.get_pool(site_id)
    except Exception:
        return None


def score_account(account_state) -> int:
    """0-100 health score for one _AccountState. Higher = healthier.

    Inputs (all read off the existing _AccountState):
      • fail_count     — total observed failures since pool init
      • lease_count    — total leases (denominator)
      • last_error     — most recent error string (presence flags risk)
      • cooldown_until — non-zero = currently sin-binned
      • state          — "available" | "in_use" | "cooldown" | etc.

    Score components (additive, clamped to [0, 100]):
      • base: 100 if state != "cooldown" else 30
      • penalty: -50 * (fail_count / lease_count) if lease_count > 0
      • recency penalty: -20 if last_error contains "429" or "ban"
      • bonus: +10 if no failures observed yet

    Caller passes the state object directly; we don't deep-import."""
    if account_state is None:
        return 0
    state = getattr(account_state, "state", "")
    fail_count = getattr(account_state, "fail_count", 0)
    lease_count = max(1, getattr(account_state, "lease_count", 0))
    last_error = (getattr(account_state, "last_error", "") or "").lower()
    cooldown_until = getattr(account_state, "cooldown_until", 0.0)

    score = 100 if state != "cooldown" else 30
    if cooldown_until > time.time():
        score = min(score, 40)
    # Failure-ratio penalty: at 100% fail rate, deducts 50 points
    score -= int(50 * min(1.0, fail_count / lease_count))
    # Specific risk patterns
    if any(token in last_error for token in ("429", "rate limit", "ban", "blocked",
                                              "suspended", "captcha")):
        score -= 20
    # First-use grace bonus
    if fail_count == 0:
        score += 10
    return max(0, min(100, score))


def ban_risk(account_state) -> bool:
    """Predict imminent ban. Currently: True if last_error matches
    a known-bad pattern AND fail_count is rising. Operator should
    cool down preemptively."""
    if account_state is None:
        return False
    last_error = (getattr(account_state, "last_error", "") or "").lower()
    fail_count = getattr(account_state, "fail_count", 0)
    if fail_count < 2:
        return False
    risky = ("429", "captcha rate", "too many", "suspended", "blocked",
             "abuse", "stop spamming")
    return any(token in last_error for token in risky)


def rotation_hint(site_id: str) -> Optional[int]:
    """Suggest which account index the runner should claim next.
    Returns idx, or None when no clear preference / no pool.

    Strategy:
      1. Filter to currently-available accounts (state == "available"
         AND cooldown_until <= now)
      2. Rank by: score DESC, then last_used ASC (LRU breaks ties)
      3. Return the top idx
    """
    pool = _safe_get_pool(site_id)
    if pool is None:
        return None
    try:
        now = time.time()
        candidates = []
        for st in getattr(pool, "_accounts", []):
            if getattr(st, "state", "") != "available":
                continue
            if getattr(st, "cooldown_until", 0) > now:
                continue
            candidates.append((score_account(st),
                              -getattr(st, "last_used", 0),  # negative so older=greater
                              getattr(st, "idx", 0)))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][2]
    except Exception:
        return None


def report_all() -> list:
    """Return [{site_id, idx, username, score, state, ban_risk, ...}]
    across all configured pools. Used by the operator dashboard.

    Raises AccountHealthUnavailable when the pool census cannot be measured;
    an empty list is reserved for a successful census with no accounts.
    """
    out = []
    try:
        from . import account_pool as _ap
        for entry in _ap.get_all_pools_status():
            # get_all_pools_status returns dicts; we re-score + add risk.
            # Defensive against shape changes — only consult expected keys.
            sid = entry.get("site_id", "")
            for acc in entry.get("accounts", []):
                # Reconstruct a duck-typed object so score_account works
                # against the dict shape too.
                class _Duck:
                    pass
                duck = _Duck()
                for k, v in acc.items():
                    setattr(duck, k, v)
                out.append({
                    "site_id": sid,
                    "idx": acc.get("idx", -1),
                    "username": acc.get("username", ""),
                    "state": acc.get("state", ""),
                    "fail_count": acc.get("fail_count", 0),
                    "lease_count": acc.get("lease_count", 0),
                    "last_error": (acc.get("last_error") or "")[:120],
                    "score": score_account(duck),
                    "ban_risk": ban_risk(duck),
                })
    except Exception as e:
        # An empty list is a valid, measured census (there may be no configured
        # pools), so it cannot also be the exception fallback.  Let callers map
        # the unavailable state into their own status vocabulary.
        raise AccountHealthUnavailable(
            f"account pool census unavailable: {e}"
        ) from e
    return out


def summary() -> dict:
    """Aggregate report. Counts accounts in each bucket so the operator
    sees portfolio-level health at a glance."""
    rows = report_all()
    n = len(rows)
    if n == 0:
        return {"total": 0, "healthy": 0, "warning": 0, "critical": 0,
                "at_risk": 0, "avg_score": 0}
    healthy = sum(1 for r in rows if r["score"] >= 70)
    warning = sum(1 for r in rows if 40 <= r["score"] < 70)
    critical = sum(1 for r in rows if r["score"] < 40)
    at_risk = sum(1 for r in rows if r["ban_risk"])
    avg = sum(r["score"] for r in rows) / n
    return {
        "total": n,
        "healthy": healthy,
        "warning": warning,
        "critical": critical,
        "at_risk": at_risk,
        "avg_score": round(avg, 1),
    }
