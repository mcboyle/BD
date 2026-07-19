"""bulk_downloader.auto_recover -- A7: self-recovery loop.

Autonomous recovery as PURE decision functions plus one gated orchestration
entry. Each decision is reversible by construction and every applied action is
recorded to an audit log:

  * ``decide_cookie_refresh``   -- expired/expiring auth -> refresh action.
  * ``backoff_delay`` / ``decide_retry`` -- bounded exponential backoff;
    ``decide_retry`` delegates the budget + delay to ``retry_policy`` by default
    (``should_retry`` / ``compute_next_delay``), both injectable.
  * ``decide_profile_resync``   -- manual session newer than runtime -> resync
    (``profile_sync.sync_manual_to_runtime`` moves the prior copy aside first,
    so the resync is reversible).
  * ``decide_health_rollback``  -- a post-deploy health REGRESSION (was ok, now
    not) -> rollback. Steady/improving/never-healthy never rolls back.
  * ``decide_requarantine_recover`` -- drift over tolerance -> requarantine;
    drift back under while quarantined -> recover.

``auto_recover_if_enabled`` is gated by the ``auto_recover`` toggle (DEFAULT
OFF, NOT keystone-required: recovery RESTORES known-good state and never
overwrites a serving template with new content; the one destructive sub-action
-- requarantine -- delegates to A3's already-keystone-gated demote). Fail-safe:
a throwing applier is isolated and reported in ``apply_errors``; the
orchestration never raises into the caller.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from . import lifecycle_automation as la


# ── backoff / retry ──────────────────────────────────────────────────────────

def backoff_delay(attempt: int, *, base: float = 2.0, cap: float = 60.0) -> float:
    """Bounded exponential backoff: ``min(cap, base * 2**attempt)``. Deterministic
    (no jitter here -- jitter is a live concern, added at apply time)."""
    try:
        a = max(0, int(attempt))
    except Exception:
        a = 0
    return float(min(cap, base * (2 ** a)))


def _default_should_retry(failure_class: str, attempt: int) -> bool:
    try:
        from . import retry_policy as rp
        return bool(rp.should_retry(failure_class, attempt))
    except Exception:
        return False


def _default_delay(failure_class: str, attempt: int) -> float:
    try:
        from . import retry_policy as rp
        return float(rp.compute_next_delay(failure_class, attempt))
    except Exception:
        return backoff_delay(attempt)


def decide_retry(job: Dict[str, Any], *,
                 should_fn: Optional[Callable[[str, int], bool]] = None,
                 delay_fn: Optional[Callable[[str, int], float]] = None
                 ) -> Dict[str, Any]:
    """Decide whether a failed job retries and after what delay. Delegates the
    budget + delay to ``retry_policy`` by default; both injectable for tests."""
    fc = str(job.get("failure_class") or "unknown")
    attempt = int(job.get("attempt") or 0)
    should = (should_fn or _default_should_retry)(fc, attempt)
    if not should:
        return {"retry": False, "delay": 0.0, "failure_class": fc, "attempt": attempt}
    delay = (delay_fn or _default_delay)(fc, attempt)
    return {"retry": True, "delay": float(delay), "failure_class": fc,
            "attempt": attempt}


# ── cookie refresh / profile resync ──────────────────────────────────────────

def decide_cookie_refresh(site_id: str, auth: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Auth expired/expiring -> a refresh action; else None."""
    if not auth:
        return None
    if auth.get("expired") or auth.get("expiring"):
        return {"action": "refresh_cookies", "site_id": site_id,
                "reason": "auth_expired" if auth.get("expired") else "auth_expiring"}
    return None


def decide_profile_resync(site_id: str, profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Manual session newer than runtime -> resync (reversible; profile_sync
    backs up the prior copy first); else None."""
    if profile and profile.get("manual_newer"):
        return {"action": "resync_profile", "site_id": site_id}
    return None


# ── health rollback (post-deploy regression only) ────────────────────────────

def decide_health_rollback(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """Roll back ONLY on a genuine regression: healthy before, unhealthy after.
    Never roll back onto an already-broken base (never-healthy) and never on a
    steady/improving probe."""
    was_ok = bool((before or {}).get("ok"))
    now_ok = bool((after or {}).get("ok"))
    regressed = was_ok and not now_ok
    return {"rollback": regressed,
            "reason": "post_deploy_health_regression" if regressed else None}


# ── requarantine / recover on drift transition ───────────────────────────────

def decide_requarantine_recover(*, drift: float, tolerance: float,
                                quarantined: bool) -> Dict[str, Any]:
    """Over tolerance while serving -> requarantine; back under while quarantined
    -> recover; otherwise no action."""
    try:
        d, tol = float(drift), float(tolerance)
    except Exception:
        return {"action": None}
    if d > tol and not quarantined:
        return {"action": "requarantine", "drift": d, "tolerance": tol}
    if d <= tol and quarantined:
        return {"action": "recover", "drift": d, "tolerance": tol}
    return {"action": None, "drift": d, "tolerance": tol}


# ── gated orchestration ──────────────────────────────────────────────────────

def auto_recover_if_enabled(ctx: Dict[str, Any], *,
                            should_fn: Optional[Callable[[str, int], bool]] = None,
                            delay_fn: Optional[Callable[[str, int], float]] = None,
                            tolerance: float = 0.3,
                            apply_fns: Optional[Dict[str, Callable]] = None
                            ) -> Dict[str, Any]:
    """Gated A7 entry. No-op when ``auto_recover`` is disabled (DEFAULT OFF).

    ``ctx`` shape (all optional): ``{sites: {sid: {auth, profile, drift,
    quarantined}}, jobs: [{id, attempt, failure_class}], health: {before,
    after}}``. Returns ``{ok, plan: {actions}, audit: [...]}`` and, if
    ``apply_fns`` is supplied, applies each action through the matching callable
    (keyed by action name) and reports ``applied`` / ``apply_errors``."""
    if not la.is_enabled("auto_recover"):
        return {"ok": True, "skipped": "auto_recover disabled"}

    actions: List[Dict[str, Any]] = []
    sites = (ctx or {}).get("sites") or {}
    for sid, st in sites.items():
        st = st or {}
        a = decide_cookie_refresh(sid, st.get("auth") or {})
        if a:
            actions.append(a)
        a = decide_profile_resync(sid, st.get("profile") or {})
        if a:
            actions.append(a)
        if "drift" in st:
            r = decide_requarantine_recover(drift=st.get("drift", 0.0),
                                            tolerance=tolerance,
                                            quarantined=bool(st.get("quarantined")))
            if r.get("action"):
                actions.append({"action": r["action"], "site_id": sid,
                                "drift": r.get("drift")})

    for job in ((ctx or {}).get("jobs") or []):
        r = decide_retry(job, should_fn=should_fn, delay_fn=delay_fn)
        if r.get("retry"):
            actions.append({"action": "retry", "job_id": job.get("id"),
                            "delay": r["delay"]})

    health = (ctx or {}).get("health") or {}
    if health:
        r = decide_health_rollback(health.get("before") or {}, health.get("after") or {})
        if r.get("rollback"):
            actions.append({"action": "rollback", "reason": r.get("reason")})

    # Audit every planned action (reversible + traceable) BEFORE applying.
    audit = [{"action": a["action"], **{k: v for k, v in a.items() if k != "action"}}
             for a in actions]

    out: Dict[str, Any] = {"ok": True, "plan": {"actions": actions}, "audit": audit}

    if apply_fns:
        applied = 0
        errors: List[str] = []
        for a in actions:
            fn = apply_fns.get(a["action"])
            if not fn:
                continue
            try:
                # Pass the natural argument: site_id for site-scoped actions,
                # else the whole action dict.
                if "site_id" in a and a["action"] in ("refresh_cookies",
                                                      "resync_profile",
                                                      "requarantine", "recover"):
                    fn(a["site_id"])
                else:
                    fn(a)
                applied += 1
            except Exception as e:
                errors.append(f"{a['action']}: {str(e)[:100]}")
        out["applied"] = applied
        if errors:
            out["apply_errors"] = errors
    return out
