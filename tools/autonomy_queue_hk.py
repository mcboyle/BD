"""Phase I — `queue_housekeeping`: the first operational target on the generic Class-C apply
harness (after H=`live_site_config` and v1=`staging_json`).

WHAT IT DOES (per site, only once a `(site, queue_housekeeping)` grant is active):
  • I-A  garbage-collect terminal queue rows  — delete rows whose status is terminal
         (done/error/failed) and that have not been touched in `GC_AGE_DAYS`. Zero live-work
         impact; the rows are finished. Fully reversible (the deleted rows are snapshotted and
         re-inserted on rollback).
  • I-B  abandon retry-exhausted / stuck rows — transition pending/running rows with
         `retries >= MAX_RETRIES` and no update in `STALE_HOURS` to `failed`. Higher blast
         radius (touches non-terminal rows), so it is **OFF by default** (BD_QUEUE_HK_ABANDON)
         and is meant to be enabled only after a dry-run observation week. Reversible: the prior
         row state is restored on rollback (which re-queues the job).

POSTURE / SAFETY
  • Operational kind: it does NOT use the oracle tier-3 gate (that is about held-out capture
    evidence agreeing on a site's template — meaningless for queue rows). Its gate is an active
    per-(site, kind) GRANT plus an objective per-row predicate. Dark by default: with no grant,
    nothing is eligible and nothing runs.
  • Every change goes through the harness: record_change → register_pending (fail-closed review
    window) → optional validator (revert on miss) → rollback available. Nothing is irreversible.
  • DB access is via the injectable `_q_*` module wrappers so tests run without a real database
    (monkeypatch the wrappers); they late-bind to `bulk_downloader.db` at call time.
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Any, Dict, List, Optional

from tools import autonomy_apply as aap
from tools import autonomy_grant as ag
from tools import autonomy_promotion as apr

KIND = "queue_housekeeping"

# terminal statuses whose rows are finished and safe to garbage-collect (I-A)
_GC_TERMINAL = ("done", "error", "failed")
# non-terminal statuses considered for abandon (I-B)
_ABANDON_FROM = ("pending", "running")


# ── tunables (global_config store > env seed > hard default) ─────────────────
# Phase 4.2a (CLI->GUI parity): these read the runtime global_config store first
# so a Settings write takes effect on the next housekeeping run; the matching
# env var is the seed/override default when the store key is unset. Reads are
# call-time, so a GUI change is picked up without a restart. global_config is
# thin (no Flask) and imported lazily; on any failure we fall back to env->default.
def _cfg(store_key, env_name, env_default):
    val = os.environ.get(env_name, env_default)
    try:
        from bulk_downloader import global_config as _gc
        return _gc.get(store_key, val)
    except Exception:
        return val


def _gc_age_days() -> int:
    try:
        return max(0, int(_cfg("queue_hk_gc_age_days", "BD_QUEUE_HK_GC_AGE_DAYS", "7")))
    except Exception:
        return 7


def _abandon_enabled() -> bool:
    # I-B is OFF by default. Enable only after the dry-run observation week.
    v = _cfg("queue_hk_abandon", "BD_QUEUE_HK_ABANDON", "")
    if isinstance(v, bool):
        return v
    return (str(v) or "").strip().lower() in ("1", "true", "yes", "on")


def _max_retries() -> int:
    try:
        return max(0, int(_cfg("queue_hk_max_retries", "BD_QUEUE_HK_MAX_RETRIES", "10")))
    except Exception:
        return 10


def _stale_hours() -> int:
    try:
        return max(1, int(_cfg("queue_hk_stale_hours", "BD_QUEUE_HK_STALE_HOURS", "24")))
    except Exception:
        return 24


# ── time (UTC only — queue ts_* columns are sqlite strftime('now') = UTC) ─────
_TS_FMT = "%Y-%m-%dT%H:%M:%S"


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def _cutoff_iso(*, days: int = 0, hours: int = 0) -> str:
    return (_utc_now() - _dt.timedelta(days=days, hours=hours)).strftime(_TS_FMT)


def _older_than(ts_updated: Any, cutoff_iso: str) -> bool:
    # ISO 'YYYY-MM-DDTHH:MM:SS' sorts lexically; a string compare is correct and tz-safe.
    if not ts_updated:
        return True
    return str(ts_updated)[:19] < cutoff_iso


# ── injectable DB wrappers (tests monkeypatch these; lazy import of db) ────────
def _q_load(site: str) -> List[Dict[str, Any]]:
    from bulk_downloader import db
    return db.queue_load(site)


def _q_delete(site: str, url: str) -> None:
    from bulk_downloader import db
    db.queue_delete(site, url)


def _q_upsert(site: str, url: str, **fields: Any) -> None:
    from bulk_downloader import db
    db.queue_upsert(site, url, **fields)


def _q_mark(site: str, url: str, status: str, message: str) -> None:
    from bulk_downloader import db
    db.queue_upsert(site, url, status=status, message=message)


# ── target selection (single source of truth for current + proposer) ──────────
def _targets(site: str) -> List[Dict[str, Any]]:
    """Rows this kind would act on, each annotated with its op. Pure read."""
    gc_cut = _cutoff_iso(days=_gc_age_days())
    stale_cut = _cutoff_iso(hours=_stale_hours())
    abandon_on = _abandon_enabled()
    maxr = _max_retries()
    out: List[Dict[str, Any]] = []
    for r in (_q_load(site) or []):
        status = (r.get("status") or "").lower()
        if status in _GC_TERMINAL and _older_than(r.get("ts_updated"), gc_cut):
            out.append({"row": dict(r), "op": "gc"})
        elif (abandon_on and status in _ABANDON_FROM
              and int(r.get("retries") or 0) >= maxr
              and _older_than(r.get("ts_updated"), stale_cut)):
            out.append({"row": dict(r), "op": "abandon"})
    return out


# ── harness hooks (all late-bound via the lambdas in the registration) ────────
def _gate(site: str) -> bool:
    # operational kind: active per-(site, kind) grant only. No oracle tier. Dark by default.
    return ag.is_active(site, KIND)


def _proposer(site: str) -> Optional[List[Dict[str, Any]]]:
    tg = _targets(site)
    if not tg:
        return None
    return [{"site_id": t["row"]["site_id"], "url": t["row"]["url"], "op": t["op"]} for t in tg]


def _current(site: str) -> List[Dict[str, Any]]:
    # full pre-state of every target row — the snapshot the reverser restores from.
    return [t["row"] for t in _targets(site)]


def _unchanged(before: Any, after: Any) -> bool:
    return not after


def _applier(site: str, after: List[Dict[str, Any]]) -> None:
    for item in after or []:
        sid, url, op = item["site_id"], item["url"], item["op"]
        if op == "gc":
            _q_delete(sid, url)
        elif op == "abandon":
            _q_mark(sid, url, "failed", "auto-abandoned: retries exhausted (queue_housekeeping)")


def _reverser(target_ref: str, before: Any) -> None:
    # exact restore: re-upsert each snapshotted row by its (site_id, url) PK.
    for r in (before or []):
        fields = {k: v for k, v in r.items() if k not in ("site_id", "url")}
        _q_upsert(r["site_id"], r["url"], **fields)


def _validator(site: str, after: List[Dict[str, Any]]) -> Dict[str, Any]:
    # confirm the apply took: gc rows gone, abandon rows now terminal. Lenient on read error
    # (a transient read glitch must not trigger a re-write storm on an already-applied delete).
    try:
        live = {(r.get("site_id"), r.get("url")): (r.get("status") or "").lower()
                for r in (_q_load(site) or [])}
    except Exception:
        return {"ok": True, "note": "validation skipped (queue read unavailable)"}
    for item in after or []:
        key = (item["site_id"], item["url"])
        if item["op"] == "gc" and key in live:
            return {"ok": False, "reason": f"gc row still present: {item['url']}"}
        if item["op"] == "abandon" and live.get(key) not in ("failed", "error", "done"):
            return {"ok": False, "reason": f"abandon row not terminal: {item['url']}"}
    return {"ok": True}


def _transition(site: str, before: Any, after: Any, *, by: str,
                phase: str = "applied", detail: Any = None) -> None:
    if phase == "reverted":
        frm, to = "applied_pending", "reverted_validation"
        reason = "queue_housekeeping reverted: validation miss"
    else:
        frm, to = "stable", "applied_pending"
        n = len(after or [])
        reason = f"queue_housekeeping applied: {n} row(s) (review window open)"
    try:
        apr.record_transition(site, KIND, frm, to, by=by, reason=reason)
    except Exception:
        pass


# ── register the kind on import (cheap: dict insert + reverser registration) ──
aap.register_apply_kind(
    KIND,
    gate=lambda s: _gate(s),
    current=lambda s: _current(s),
    proposer=lambda s: _proposer(s),
    applier=lambda s, after: _applier(s, after),
    reverser=lambda target_ref, before: _reverser(target_ref, before),
    validator=lambda s, after: _validator(s, after),
    target_ref=lambda s: f"queue::{s}",
    transition=lambda s, b, a, *, by, phase="applied", detail=None: _transition(
        s, b, a, by=by, phase=phase, detail=detail),
    action_class="C",
    transition_field=KIND,
)


# ── thin operator entry points (mirror H/v1) ──────────────────────────────────
def housekeep_site(site: str, *, by: str = "system") -> Dict[str, Any]:
    """Run queue housekeeping for one site through the harness."""
    return aap.apply_for_kind(site, KIND, by=by)


def housekeep_all(*, by: str = "system", sites: Optional[List[str]] = None) -> Dict[str, Any]:
    """Host-scheduled loop. Dark until sites are granted (site, queue_housekeeping)."""
    return aap.apply_all(KIND, by=by, sites=sites)


def dry_run(site: str) -> Dict[str, Any]:
    """Observation helper (no writes): what housekeep_site WOULD do right now. Use this for the
    pre-activation week before enabling I-B (BD_QUEUE_HK_ABANDON)."""
    plan = _proposer(site) or []
    return {"site": site, "kind": KIND, "would_act_on": len(plan),
            "gc": [p["url"] for p in plan if p["op"] == "gc"],
            "abandon": [p["url"] for p in plan if p["op"] == "abandon"],
            "abandon_enabled": _abandon_enabled(),
            "grant_active": _gate(site)}
