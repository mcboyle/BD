"""Validation Operations (Phase G / G4) — ADVISORY held-out re-validation scheduling.

Held-out evidence ages. Once it passes the eligibility freshness floor (30 days) a site is
no longer evidence-qualified (the G1 freshness decay). This module surfaces that on a
schedule, with LEAD TIME: it flags a site as "due for re-validation" at a shorter interval
(21 days) so the operator can refresh evidence BEFORE eligibility decays — and as
"overdue" once evidence is already stale.

It is purely ADVISORY and READ-ONLY. It recommends *when* re-validation should happen; it
NEVER performs it. Re-capturing held-out evidence, logging into a site, or re-running the
oracle are operator/host actions — this module does not capture, does not log in, does not
drive a browser, and does not touch the network. It only reads oracle provenance and
computes a schedule. It also adds no eligibility gate of its own: freshness is already
enforced in G1; this is the operational heads-up on top of it.

POSTURE: read-only; no module-level I/O; no capture/login/browser/network; no forbidden
mutation (corpus/policy/credentials/apply).
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from tools import autonomy_oracle as ao
from tools import autonomy_eligibility as el

# Advisory re-validation interval (lead time). Deliberately SHORTER than the eligibility
# freshness floor so "due_soon" fires before evidence actually goes stale.
VALIDATION_INTERVAL_DAYS = 21
# The hard freshness floor is owned by eligibility; mirror it so "overdue" aligns with the
# point at which a site stops being evidence-qualified.
FRESH_FLOOR_DAYS = el.EVIDENCE_FRESH_DAYS  # 30


def _now_dt() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _as_dt(value: Any) -> Optional[_dt.datetime]:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        dt = value
    else:
        try:
            dt = _dt.datetime.fromisoformat(str(value))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def _designated_at(site: str) -> Optional[str]:
    return ao._provenance().get(site, {}).get("held_out_designated_at")


def _held_out_count(site: str) -> int:
    return len(ao._provenance().get(site, {}).get("held_out", []))


def validation_schedule(site: str, *, now: Any = None) -> Dict[str, Any]:
    """Re-validation schedule for one site (read-only, advisory). status is one of:
    `never` (no held-out designated), `current`, `due_soon` (past the advisory interval
    but evidence still fresh), `overdue` (evidence past the freshness floor — already
    not evidence-qualified)."""
    now_dt = _as_dt(now) or _now_dt()
    designated = _designated_at(site)
    dts = _as_dt(designated)
    held = _held_out_count(site)

    if held == 0 or dts is None:
        return {"site": site, "held_out_count": held, "designated_at": designated,
                "age_days": None, "status": "never", "recommended_date": None,
                "interval_days": VALIDATION_INTERVAL_DAYS,
                "fresh_floor_days": FRESH_FLOOR_DAYS,
                "_note": "No held-out evidence designated — re-validation cannot be "
                         "scheduled until a human designates held-out captures. Advisory "
                         "only; this never captures or logs in."}

    age = (now_dt - dts).total_seconds() / 86400.0
    if age > FRESH_FLOOR_DAYS:
        status = "overdue"
    elif age > VALIDATION_INTERVAL_DAYS:
        status = "due_soon"
    else:
        status = "current"
    recommended = (dts + _dt.timedelta(days=VALIDATION_INTERVAL_DAYS)).isoformat()
    return {"site": site, "held_out_count": held, "designated_at": designated,
            "age_days": round(age, 1), "status": status,
            "recommended_date": recommended,
            "interval_days": VALIDATION_INTERVAL_DAYS, "fresh_floor_days": FRESH_FLOOR_DAYS,
            "_note": "Advisory. 'overdue' means evidence is past the freshness floor and "
                     "the site is already not evidence-qualified. Re-validation is an "
                     "operator/host action; this module never performs it."}


def validation_due(now: Any = None, sites: Optional[List[str]] = None) -> Dict[str, Any]:
    """Sites that should be re-validated (status due_soon / overdue / never). Read-only."""
    sites = sites if sites is not None else ao._all_sites()
    rows = [validation_schedule(s, now=now) for s in sites]
    due = [r for r in rows if r["status"] in ("due_soon", "overdue", "never")]
    return {"due": due, "due_count": len(due),
            "overdue": [r["site"] for r in rows if r["status"] == "overdue"],
            "due_soon": [r["site"] for r in rows if r["status"] == "due_soon"],
            "never": [r["site"] for r in rows if r["status"] == "never"],
            "_note": "Advisory re-validation queue. Action is human/host; never executed "
                     "here."}


def validation_overview(sites: Optional[List[str]] = None,
                        now: Any = None) -> Dict[str, Any]:
    """Per-site re-validation schedule + status counts. Read-only."""
    sites = sites if sites is not None else ao._all_sites()
    rows = [validation_schedule(s, now=now) for s in sites]
    counts = {k: 0 for k in ("current", "due_soon", "overdue", "never")}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"sites": rows, "site_count": len(rows), "counts": counts,
            "interval_days": VALIDATION_INTERVAL_DAYS, "fresh_floor_days": FRESH_FLOOR_DAYS,
            "_note": "Read-only advisory schedule. Re-validation (re-capture / re-run "
                     "oracle) is an operator/host action; this module never captures or "
                     "logs in."}


def validation_status() -> Dict[str, Any]:
    """Compact status for the cockpit header. Read-only."""
    ov = validation_overview()
    c = ov["counts"]
    return {"interval_days": VALIDATION_INTERVAL_DAYS, "fresh_floor_days": FRESH_FLOOR_DAYS,
            "site_count": ov["site_count"],
            "due_soon_count": c.get("due_soon", 0), "overdue_count": c.get("overdue", 0),
            "never_count": c.get("never", 0),
            "_note": "Advisory re-validation scheduling. Recommends WHEN to re-validate "
                     "held-out evidence; never performs capture or login. Freshness is "
                     "enforced separately by eligibility."}
