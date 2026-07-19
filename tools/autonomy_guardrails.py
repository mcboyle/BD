"""Guardrail infrastructure (Phase C). Builds the safety apparatus that Class C auto
would require — WITHOUT enabling it.

Critical safety property: building these guardrails does NOT turn on Class C
autonomy. After Phase C the only unbuilt guardrail is `correctness_oracle` (Phase E),
so `set_policy_level("C", "auto_with_guardrails", ...)` is still refused, and Class C
stays at Approve-each by default. This module is the brakes, rollback, caps,
fail-closed review windows, and self-throttle for a capability that remains off.

What it provides (doc §5.3–§5.8):
  * Rollback engine — records before/after/diff per change and reverses it via a
    registered reverser. Fully functional + tested end-to-end on a CONFINED staging
    target; it never writes live config or the corpus (Class C apply, which is off).
  * Backlog + blast-radius caps — cap outstanding-unreviewed auto-changes; one site
    at a time; no family-wide.
  * Review windows — FAIL-CLOSED for Class C (expired-unreviewed ⇒ auto-revert);
    fail-open (stay provisional) is allowed only for reversible Class B.
  * Self-throttle — computes auto-apply / rollback / review-expiry / oracle-
    disagreement rates; on a breach, AUTOMATICALLY DEMOTES Class C to Approve-each
    (lower-only) and alerts. Oracle-disagreement-rate is unavailable until Phase E.
  * Guardrail-failure branch — if a guardrail itself fails, FREEZE-AND-ALERT; never
    proceed.

No background scheduler (the sweep / throttle checks are invoked explicitly, as in
Phase B). All state is runtime (under governance/guardrails/, never shipped); writes
are atomic + utf-8; no module-level I/O.
"""
from __future__ import annotations

import datetime as _dt
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools import autonomy_policy as ap
from tools.cockpit_core import confine, tasks_root

# config (conservative defaults)
BACKLOG_CAP = 5            # max outstanding unreviewed auto-changes
MAX_INFLIGHT_SITES = 1     # one site at a time (blast-radius)
REVIEW_WINDOW_HOURS = 24   # Class C auto-change must be reviewed within this window
THROTTLE_ROLLBACK_RATE = 0.30      # demote C if rollback rate ≥ this
THROTTLE_EXPIRY_RATE = 0.30        # demote C if review-expiry rate ≥ this
_THROTTLE_MIN_SAMPLE = 4           # don't throttle on tiny samples


def _now_dt() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


# ── runtime stores (never shipped) ──────────────────────────────────────────
def _gr_root() -> Path:
    return tasks_root() / "governance" / "guardrails"


def _changes_dir() -> Path:
    return _gr_root() / "changes"


def _staging_dir() -> Path:
    return _gr_root() / "staging"


def _pending_path() -> Path:
    return _gr_root() / "pending_reviews.json"


def _alerts_path() -> Path:
    return _gr_root() / "alerts.jsonl"


def _atomic_write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    tmp.replace(p)


def _alert(kind: str, detail: str, by: str = "system") -> None:
    p = _alerts_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": _now(), "kind": kind, "detail": detail, "by": by,
           "external_push": False}  # in-GUI only; no external push (posture)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")


def alerts(limit: int = 100) -> List[Dict[str, Any]]:
    p = _alerts_path()
    if not p.is_file():
        return []
    out = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                out.append(json.loads(ln))
    except Exception:
        pass
    return out[-limit:]


# ── guardrail-failure branch (doc §5.8): freeze-and-alert, never proceed ─────
def guardrail_failure(reason: str, by: str = "system") -> Dict[str, Any]:
    """A guardrail itself failed (rollback errored, a record could not be written,
    etc.). The only safe response is to FREEZE all automation and alert — never
    proceed without the guardrail. Absence of a working guardrail is treated like the
    kill switch being on."""
    ap.freeze(f"guardrail:{by}", f"guardrail failure: {reason}")
    _alert("guardrail_failure", reason, by)
    return {"ok": False, "frozen": True, "reason": reason}


# ── rollback engine (doc §5.3) ───────────────────────────────────────────────
def _diff(before: Any, after: Any) -> Dict[str, Any]:
    """Shallow structural diff for dict change records; falls back to before/after."""
    if isinstance(before, dict) and isinstance(after, dict):
        keys = set(before) | set(after)
        return {k: {"from": before.get(k), "to": after.get(k)}
                for k in sorted(keys) if before.get(k) != after.get(k)}
    return {"from": before, "to": after}


# registered reversers by target_kind. Phase C ships ONE concrete safe target —
# 'staging_json' (a confined regenerable file) — so the engine is fully exercised
# without touching live config. A future Class C apply registers its own reverser.
def _restore_staging_json(target_ref: str, before: Any) -> None:
    safe = confine(target_ref, _staging_dir())
    if safe is None:
        raise ValueError("staging target escaped the staging root")
    if before is None:
        if safe.is_file():
            safe.unlink()
    else:
        _atomic_write_json(safe, before)


_REVERSERS = {"staging_json": _restore_staging_json}


def register_reverser(target_kind: str, fn) -> None:
    """Bind a reverser for a target kind. Class C apply (later phases) uses this to
    register reversal for its real target; Phase C ships only the safe staging one."""
    _REVERSERS[target_kind] = fn


def has_reverser(target_kind: str) -> bool:
    """True iff a reverser is registered for this kind. The live apply path's existence is
    derived from this (H): no reverser ⇒ no apply path ⇒ no participation."""
    return target_kind in _REVERSERS


def record_change(target_kind: str, target_ref: str, before: Any, after: Any,
                  by: str, action_class: str = "C",
                  snapshot_id: Optional[str] = None) -> Dict[str, Any]:
    """Record an immutable change record (before/after/diff + how to reverse).
    Recording is bookkeeping only — it does not itself apply anything. `snapshot_id`
    optionally links the change to its decision snapshot (Phase A) for the evidence
    chain. Returns the change id."""
    if target_kind not in _REVERSERS:
        return {"ok": False, "error": f"no reverser registered for {target_kind!r}"}
    cid = "chg_" + uuid.uuid4().hex[:12]
    rec = {"id": cid, "ts": _now(), "by": by, "action_class": action_class,
           "target_kind": target_kind, "target_ref": target_ref,
           "before": before, "after": after, "diff": _diff(before, after),
           "snapshot_id": snapshot_id, "rolled_back": False}
    _atomic_write_json(_changes_dir() / f"{cid}.json", rec)
    return {"ok": True, "id": cid, "diff": rec["diff"]}


def change_record(change_id: str) -> Optional[Dict[str, Any]]:
    safe = confine(f"{change_id}.json", _changes_dir())
    if not safe or not safe.is_file():
        return None
    try:
        return json.loads(safe.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_changes(limit: int = 200) -> List[Dict[str, Any]]:
    d = _changes_dir()
    if not d.is_dir():
        return []
    rows = []
    for f in sorted(d.glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            rows.append({"id": r.get("id"), "ts": r.get("ts"),
                         "action_class": r.get("action_class"),
                         "target_kind": r.get("target_kind"),
                         "rolled_back": r.get("rolled_back")})
        except Exception:
            continue
    return rows[-limit:]


def rollback(change_id: str, by: str) -> Dict[str, Any]:
    """Reverse a recorded change via its registered reverser, restoring before-state.
    If the reversal ERRORS, trigger the guardrail-failure branch (freeze-and-alert)
    rather than leaving state half-reverted."""
    if not by:
        return {"ok": False, "error": "identity (by) required"}
    rec = change_record(change_id)
    if not rec:
        return {"ok": False, "error": "change not found"}
    if rec.get("rolled_back"):
        return {"ok": True, "already_rolled_back": True}
    reverser = _REVERSERS.get(rec.get("target_kind"))
    if reverser is None:
        return guardrail_failure(f"no reverser for {rec.get('target_kind')!r}", by)
    try:
        reverser(rec["target_ref"], rec["before"])
    except Exception as e:
        return guardrail_failure(f"rollback of {change_id} failed: {str(e)[:120]}", by)
    rec["rolled_back"] = True
    rec["rolled_back_ts"] = _now()
    rec["rolled_back_by"] = by
    _atomic_write_json(_changes_dir() / f"{change_id}.json", rec)
    _alert("rollback", f"{change_id} reversed ({rec.get('target_kind')})", by)
    return {"ok": True, "rolled_back": change_id}


# ── pending review windows + backlog/blast-radius caps (doc §5.4/§5.5) ───────
def _load_pending() -> Dict[str, Any]:
    p = _pending_path()
    if not p.is_file():
        return {"pending": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d.setdefault("pending", {})
        return d
    except Exception:
        return {"pending": {}}


def _save_pending(d: Dict[str, Any]) -> None:
    _atomic_write_json(_pending_path(), d)


def outstanding_unreviewed() -> List[Dict[str, Any]]:
    d = _load_pending()
    return [v for v in d["pending"].values() if not v.get("reviewed")]


def inflight_sites() -> List[str]:
    return sorted({v.get("site") for v in outstanding_unreviewed() if v.get("site")})


def backlog_ok() -> Dict[str, Any]:
    n = len(outstanding_unreviewed())
    return {"ok": n < BACKLOG_CAP, "outstanding": n, "cap": BACKLOG_CAP}


def blast_radius_ok(site: Optional[str]) -> Dict[str, Any]:
    """One site at a time; never family-wide. A new auto-change is allowed only if no
    OTHER site currently has an unreviewed auto-change in flight."""
    others = [s for s in inflight_sites() if s and s != site]
    ok = len(others) < MAX_INFLIGHT_SITES
    return {"ok": ok, "inflight_sites": inflight_sites(),
            "max_inflight_sites": MAX_INFLIGHT_SITES, "blocking_sites": others}


def register_pending(change_id: str, action_class: str, site: Optional[str],
                     by: str) -> Dict[str, Any]:
    """Register an applied auto-change as pending review, with a fail-closed deadline
    for Class C. (In Phase C nothing produces real auto-changes; this is the mechanism,
    exercised by tests.)"""
    deadline = (_now_dt() + _dt.timedelta(hours=REVIEW_WINDOW_HOURS)).isoformat() \
        if action_class == "C" else None
    d = _load_pending()
    d["pending"][change_id] = {"change_id": change_id, "action_class": action_class,
                               "site": site, "applied_ts": _now(), "by": by,
                               "deadline": deadline, "reviewed": False,
                               "decision": None}
    _save_pending(d)
    return {"ok": True, "change_id": change_id, "deadline": deadline}


def mark_reviewed(change_id: str, decision: str, by: str) -> Dict[str, Any]:
    if decision not in ("accept", "reject"):
        return {"ok": False, "error": "decision must be accept or reject"}
    d = _load_pending()
    if change_id not in d["pending"]:
        return {"ok": False, "error": "not pending"}
    d["pending"][change_id].update({"reviewed": True, "decision": decision,
                                    "reviewed_by": by, "reviewed_ts": _now()})
    _save_pending(d)
    # a rejected change is rolled back immediately
    if decision == "reject":
        rollback(change_id, by)
    return {"ok": True, "change_id": change_id, "decision": decision}


def sweep_review_windows(by: str = "system") -> Dict[str, Any]:
    """FAIL-CLOSED sweep (doc §5.5): expired-unreviewed CLASS C changes auto-revert.
    Class B pending may stay provisional (fail-open). Invoked explicitly (no
    scheduler). If a rollback errors mid-sweep, the guardrail-failure branch fires."""
    d = _load_pending()
    now = _now_dt()
    reverted, kept_provisional = [], []
    for cid, p in list(d["pending"].items()):
        if p.get("reviewed"):
            continue
        if p.get("action_class") == "C" and p.get("deadline"):
            try:
                expired = now > _dt.datetime.fromisoformat(p["deadline"])
            except Exception:
                expired = False
            if expired:
                rb = rollback(cid, by)
                if not rb.get("ok"):
                    # guardrail_failure already froze + alerted
                    return {"ok": False, "frozen": True, "stopped_at": cid}
                p.update({"reviewed": True, "decision": "auto_revert_expired",
                          "reviewed_by": "system", "reviewed_ts": _now()})
                reverted.append(cid)
        else:
            kept_provisional.append(cid)  # Class B: fail-open
    _save_pending(d)
    if reverted:
        _alert("review_window_expired",
               f"auto-reverted {len(reverted)} expired-unreviewed Class C change(s)", by)
    return {"ok": True, "auto_reverted": reverted,
            "kept_provisional": kept_provisional}


# ── self-throttle (doc §5.7) ─────────────────────────────────────────────────
def throttle_metrics() -> Dict[str, Any]:
    """The autonomy system's own health signals. A rising rollback or review-expiry
    rate means humans have stopped trusting/keeping up — the leading indicator that
    automation is misbehaving."""
    changes = list_changes(limit=10000)
    applied = len(changes)
    rolled = sum(1 for c in changes if c.get("rolled_back"))
    d = _load_pending()
    pend = list(d["pending"].values())
    expired = sum(1 for p in pend if p.get("decision") == "auto_revert_expired")
    total_pending = len(pend)
    return {
        "applied_changes": applied,
        "rollback_rate": round(rolled / applied, 3) if applied else 0.0,
        "review_expiry_rate": round(expired / total_pending, 3) if total_pending else 0.0,
        "oracle_disagreement_rate": None,   # unavailable until Phase E (the oracle)
        "_oracle_note": "oracle-disagreement-rate requires the held-out correctness "
                        "oracle (Phase E); reported as null until then.",
        "sample": {"applied": applied, "pending": total_pending},
    }


def self_throttle_check(by: str = "system") -> Dict[str, Any]:
    """If rollback-rate or review-expiry-rate crosses a threshold, AUTOMATICALLY
    DEMOTE Class C to Approve-each (lower-only) and alert. Demotion can only reduce
    autonomy, so it is always safe; it never raises a level."""
    m = throttle_metrics()
    if m["sample"]["applied"] < _THROTTLE_MIN_SAMPLE and \
       m["sample"]["pending"] < _THROTTLE_MIN_SAMPLE:
        return {"ok": True, "action": "none", "reason": "sample too small",
                "metrics": m}
    breach = []
    if m["rollback_rate"] >= THROTTLE_ROLLBACK_RATE:
        breach.append(f"rollback_rate {m['rollback_rate']} ≥ {THROTTLE_ROLLBACK_RATE}")
    if m["review_expiry_rate"] >= THROTTLE_EXPIRY_RATE:
        breach.append(f"review_expiry_rate {m['review_expiry_rate']} ≥ {THROTTLE_EXPIRY_RATE}")
    if not breach:
        return {"ok": True, "action": "none", "metrics": m}
    dem = ap.safety_demote("C", "approve_each", by, "self-throttle: " + "; ".join(breach))
    _alert("self_throttle_demote", "; ".join(breach), by)
    return {"ok": True, "action": "demoted_C_to_approve_each", "breach": breach,
            "demote": dem, "metrics": m}


# ── read-only status ─────────────────────────────────────────────────────────
def guardrails_status() -> Dict[str, Any]:
    reg = ap.guardrail_registry()
    c_avail = ap._level3_availability("C")
    return {
        "guardrails": {g: reg.get(g, {}).get("built") for g in sorted(reg)},
        "class_c_level3": c_avail,
        "class_c_auto_possible": c_avail.get("available", False),
        "config": {"backlog_cap": BACKLOG_CAP, "max_inflight_sites": MAX_INFLIGHT_SITES,
                   "review_window_hours": REVIEW_WINDOW_HOURS,
                   "throttle_rollback_rate": THROTTLE_ROLLBACK_RATE,
                   "throttle_expiry_rate": THROTTLE_EXPIRY_RATE},
        "backlog": backlog_ok(),
        "inflight_sites": inflight_sites(),
        "throttle_metrics": throttle_metrics(),
        "kill_switch": ap.freeze_status(),
        "recorded_changes": len(list_changes()),
        "pending_reviews": len(outstanding_unreviewed()),
        "recent_alerts": alerts(limit=10),
        "_note": "Guardrail infrastructure (Phase C). Built but Class C auto is still "
                 "impossible — the correctness oracle (Phase E) is the only remaining "
                 "guardrail, and Class C stays at Approve-each by default. Review "
                 "windows are FAIL-CLOSED for Class C; the kill switch and self-"
                 "throttle (lower-only) protect against guardrail failure and "
                 "declining trust. No external push; no background scheduler.",
    }
