"""Controlled Class B autonomy (Phase F). The operating layer that lets Class B
housekeeping run autonomously — while Class C and D stay human-controlled.

Class B itself (the reversible actions) was built in Phase B. Phase F adds:
  * a few more Class B actions (artifact maintenance + read-only monitoring:
    freshness, governance, review-deadline);
  * an explicitly-invoked AUTONOMY CYCLE (`run_autonomy_cycle`) the HOST schedules
    (cron/timer) — NOT a daemon thread in the import path. It only APPLIES when all of:
    not frozen (kill switch), Class B at auto_with_guardrails, and the host has set
    `BD_AUTONOMY_ENABLED`. Otherwise it runs as a dry-run (suggest). Each cycle records
    a decision snapshot (Phase A) and is logged + explainable;
  * six read-only operating views (Autonomy Center, Queue Intelligence, Review
    Operations, Notification Center, Governance Health, Automation Metrics).

Strictly Class B: reversible, regenerable-state-only, no external activity, nothing
correctness-critical. The cycle never touches Class C/D actions (no selector/template/
login/workflow/corpus/debt/capture/login changes). No module-level I/O.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools import autonomy_policy as ap
from tools import autonomy_housekeeping as ahk
from tools import autonomy_guardrails as agr
from tools.cockpit_core import confine, tasks_root

# the host's final apply switch for the autonomy runner (default OFF)
AUTONOMY_ENV_FLAG = "BD_AUTONOMY_ENABLED"
FRESHNESS_STALE_DAYS = 30
REVIEW_SOON_HOURS = 6
PACKET_RETENTION = 10   # keep this many review packets; archive older

# Class B actions Phase F adds on top of Phase B's four.
EXTRA_ACTIONS = ("artifact_maintenance", "freshness_monitoring",
                 "governance_monitoring", "review_deadline_tracking")
# the full Class B action set the cycle runs (Phase B + Phase F)
CYCLE_ACTIONS = tuple(ahk.ACTIONS) + EXTRA_ACTIONS


def _autonomy_armed() -> bool:
    """The host's final apply switch — CLI->GUI parity 4.3b precedence:
    global Settings store wins when explicitly set, else the
    ``BD_AUTONOMY_ENABLED`` env var is the seed, else default OFF. A GUI write to
    the store therefore arms/disarms live apply without a restart.

    DANGER: this is the last factor gating autonomous Class-B state changes. Apply
    still ALSO requires (not frozen) AND (Class B == auto_with_guardrails); this
    switch never bypasses those. Fail-soft: if the store is unreachable (pure
    stdlib CLI without the package path), fall back to the env var.
    """
    stored = None
    try:
        from bulk_downloader import global_config as _gc
        stored = _gc.get("autonomy_enabled", None)
    except Exception:
        stored = None
    if stored is not None and str(stored) != "":
        return str(stored).strip().lower() in ("1", "true", "on", "yes")
    return bool(os.environ.get(AUTONOMY_ENV_FLAG))


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _ac_root() -> Path:
    return tasks_root() / "governance" / "autonomy_center"


def _cycle_log_path() -> Path:
    return _ac_root() / "cycle_log.jsonl"


def _archive_dir() -> Path:
    return ahk._hk_root() / "archive"


def _atomic_write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    tmp.replace(p)


def _log_cycle(entry: Dict[str, Any]) -> None:
    p = _cycle_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


def cycle_log(limit: int = 100) -> List[Dict[str, Any]]:
    p = _cycle_log_path()
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


# ── extra Class B actions ────────────────────────────────────────────────────
def artifact_maintenance(mode: str = "suggest", by: str = "") -> Dict[str, Any]:
    """Archive review packets beyond the retention count (regenerable artifacts;
    reversible by moving them back). suggest: report what would be archived."""
    if ap.is_frozen():
        return {"ok": False, "skipped": True, "reason": "frozen (kill switch)"}
    if not by:
        return {"ok": False, "error": "identity (by) required"}
    packets = ahk.list_review_packets()
    old = packets[:-PACKET_RETENTION] if len(packets) > PACKET_RETENTION else []
    if mode != "apply":
        return {"ok": True, "mode": "suggest", "action": "artifact_maintenance",
                "would_archive": len(old)}
    moved = []
    for p in old:
        src = confine(f"{p['id']}.json", ahk._packet_dir())
        if src and src.is_file():
            dst = _archive_dir() / f"{p['id']}.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dst)
            moved.append(p["id"])
    _log_cycle({"id": "ac_" + uuid.uuid4().hex[:8], "ts": _now(),
                "action": "artifact_maintenance", "by": by, "mode": "apply",
                "archived": moved, "reversible": True})
    return {"ok": True, "mode": "apply", "action": "artifact_maintenance",
            "archived": len(moved)}


def freshness_monitoring(mode: str = "suggest", by: str = "") -> Dict[str, Any]:
    """READ-ONLY monitoring: flag sites whose newest evidence is older than the stale
    threshold. Produces findings; changes no state."""
    findings = []
    try:
        from tools.cockpit_templates import site_readiness
        for r in site_readiness().get("sites", []):
            age = (r.get("inputs") or {}).get("evidence_age_days")
            if age is not None and age > FRESHNESS_STALE_DAYS:
                findings.append({"site": r["site"], "evidence_age_days": age})
    except Exception:
        pass
    return {"ok": True, "action": "freshness_monitoring", "mode": "monitor",
            "stale_threshold_days": FRESHNESS_STALE_DAYS, "stale_sites": findings,
            "_note": "read-only monitoring — no state changed"}


def governance_monitoring(mode: str = "suggest", by: str = "") -> Dict[str, Any]:
    """READ-ONLY monitoring: governance health — kill switch, policy version, guardrail
    completeness, and anomalies (e.g. Class C/D unexpectedly above Approve-each).
    Produces findings; changes no state."""
    pol = ap.load_policy()
    reg = ap.guardrail_registry()
    anomalies = []
    if pol["levels"].get("C") == "auto_with_guardrails":
        anomalies.append("Class C is at auto_with_guardrails (unexpected — review)")
    if pol["levels"].get("D") == "auto_with_guardrails":
        anomalies.append("Class D is at auto_with_guardrails (must never be)")
    return {"ok": True, "action": "governance_monitoring", "mode": "monitor",
            "kill_switch": ap.freeze_status(),
            "policy_version": pol.get("version"), "policy_hash": ap.policy_hash(pol),
            "guardrails_complete": all(v["built"] for v in reg.values()),
            "throttle_metrics": agr.throttle_metrics(),
            "anomalies": anomalies or None,
            "_note": "read-only monitoring — no state changed"}


def review_deadline_tracking(mode: str = "suggest", by: str = "") -> Dict[str, Any]:
    """READ-ONLY monitoring: pending Class C reviews with approaching/expired
    fail-closed deadlines. Produces findings; changes no state."""
    now = _dt.datetime.now(_dt.timezone.utc)
    approaching, expired = [], []
    for p in agr.outstanding_unreviewed():
        dl = p.get("deadline")
        if not dl:
            continue
        try:
            d = _dt.datetime.fromisoformat(dl)
        except Exception:
            continue
        if d < now:
            expired.append({"change_id": p.get("change_id"), "site": p.get("site")})
        elif (d - now).total_seconds() <= REVIEW_SOON_HOURS * 3600:
            approaching.append({"change_id": p.get("change_id"), "site": p.get("site"),
                                "deadline": dl})
    return {"ok": True, "action": "review_deadline_tracking", "mode": "monitor",
            "approaching": approaching, "expired": expired,
            "soon_window_hours": REVIEW_SOON_HOURS,
            "_note": "read-only monitoring — fail-closed expired reviews auto-revert on "
                     "the guardrail sweep (a separate Class C safety mechanism)"}


_EXTRA_FNS = {"artifact_maintenance": artifact_maintenance,
              "freshness_monitoring": freshness_monitoring,
              "governance_monitoring": governance_monitoring,
              "review_deadline_tracking": review_deadline_tracking}


# ── the autonomy cycle (explicit tick; host-scheduled) ───────────────────────
def _effective_mode() -> Dict[str, Any]:
    """Decide whether the cycle APPLIES or runs dry, and explain why. Apply requires
    ALL of: not frozen, Class B at auto, host env flag set."""
    if ap.is_frozen():
        return {"mode": "skipped", "reason": "frozen (kill switch)"}
    b_level = ap.load_policy()["levels"].get("B")
    if b_level != "auto_with_guardrails":
        return {"mode": "suggest", "reason": f"Class B at '{b_level}', not auto "
                "(dry-run)"}
    if not _autonomy_armed():
        return {"mode": "suggest", "reason": f"{AUTONOMY_ENV_FLAG} not armed "
                "(dry-run — the final apply switch is off; set it on the host or in "
                "Settings)"}
    return {"mode": "apply", "reason": "not frozen, Class B at auto, host flag set"}


def run_autonomy_cycle(by: str, force_mode: Optional[str] = None) -> Dict[str, Any]:
    """Run one Class B autonomy cycle. Explicitly invoked (host scheduler / CLI) — not
    a background thread. Gated by kill switch + Class B level + host env flag; default
    is a dry-run. Records a decision snapshot. Strictly Class B — never touches Class
    C/D. `force_mode='suggest'` forces a dry-run regardless of gates."""
    if not by:
        return {"ok": False, "error": "identity (by) required"}
    decided = _effective_mode()
    mode = "suggest" if force_mode == "suggest" else decided["mode"]

    if mode == "skipped":
        rec = {"id": "cyc_" + uuid.uuid4().hex[:10], "ts": _now(), "by": by,
               "mode": "skipped", "reason": decided["reason"]}
        _log_cycle(rec)
        return {"ok": True, "mode": "skipped", "reason": decided["reason"]}

    run_mode = "apply" if mode == "apply" else "suggest"
    results: Dict[str, Any] = {}
    # Phase B actions (reversible; apply or dry-run)
    b_fns = {"reorder_queue": ahk.reorder_queue,
             "generate_notifications": ahk.generate_notifications,
             "refresh_dashboard_cache": ahk.refresh_dashboard_cache,
             "generate_review_packet": ahk.generate_review_packet}
    for name, fn in b_fns.items():
        results[name] = fn(mode=run_mode, by=by)
    # Phase F actions
    results["artifact_maintenance"] = artifact_maintenance(mode=run_mode, by=by)
    for name in ("freshness_monitoring", "governance_monitoring",
                 "review_deadline_tracking"):
        results[name] = _EXTRA_FNS[name](mode="monitor", by=by)

    # decision snapshot for the cycle (Phase A)
    snap = ap.record_decision_snapshot(
        {"action_class": "B", "action": "autonomy_cycle", "site": None,
         "proposed_change": {"mode": run_mode, "actions": list(results.keys())},
         "scores_used": {}, "thresholds_used": {}}, by)
    cyc_id = "cyc_" + uuid.uuid4().hex[:10]
    applied = sum(1 for r in results.values() if r.get("mode") == "apply")
    _log_cycle({"id": cyc_id, "ts": _now(), "by": by, "mode": run_mode,
                "reason": decided["reason"], "applied_actions": applied,
                "snapshot_id": snap.get("id"),
                "actions": list(results.keys())})
    return {"ok": True, "cycle_id": cyc_id, "mode": run_mode,
            "reason": decided["reason"], "snapshot_id": snap.get("id"),
            "results": results,
            "_note": "Class B autonomy cycle. Kill-switch + level + host-env gated; "
                     "default dry-run. Strictly Class B — Class C/D untouched. "
                     "Reversible, logged, snapshotted."}


# ── the six operating views (read-only) ──────────────────────────────────────
def autonomy_center() -> Dict[str, Any]:
    """Overview: Class B level, whether the cycle would apply (and why), kill switch,
    last cycle, what the next cycle would do (dry-run preview). Read-only."""
    decided = _effective_mode()
    log = cycle_log()
    return {
        "class_b_level": ap.load_policy()["levels"].get("B"),
        "class_b_can_autonomously": ap.can_autonomously("B"),
        "cycle_would": decided,
        "host_env_flag": AUTONOMY_ENV_FLAG,
        "host_env_set": bool(os.environ.get(AUTONOMY_ENV_FLAG)),
        "armed": _autonomy_armed(),  # 4.3b: store > env > default (the true switch)
        "kill_switch": ap.freeze_status(),
        "last_cycle": log[-1] if log else None,
        "cycles_run": len(log),
        "class_c_level": ap.load_policy()["levels"].get("C"),
        "class_d_level": ap.load_policy()["levels"].get("D"),
        "_note": "Class B autonomy operating center. Class B can run autonomously when "
                 "opted in + host flag set; Class C and D remain human-controlled "
                 "(Approve-each). The cycle is host-scheduled and kill-switch-gated; "
                 "default is a dry-run. Read-only view.",
    }


def cycle_preview() -> Dict[str, Any]:
    """A forced dry-run of the next cycle — what it WOULD do, applying nothing."""
    return run_autonomy_cycle(by="cockpit-preview", force_mode="suggest")


def queue_intelligence() -> Dict[str, Any]:
    """The plan queue + the proposed reordering (dry-run) + simple health. Read-only."""
    try:
        from tools.cockpit_core import queue_list
        q = queue_list().get("queue", [])
    except Exception:
        q = []
    proposed = ahk.reorder_queue(mode="suggest", by="cockpit")
    return {"queue_size": len(q),
            "proposed_reorder": proposed.get("proposed", []),
            "would_change": proposed.get("would_change", 0),
            "_note": "The queue is a plan; reordering it is reversible Class B "
                     "housekeeping. Read-only view (apply happens in the cycle)."}


def review_operations() -> Dict[str, Any]:
    """Review-deadline tracking + the pending review dashboard. Read-only."""
    rdt = review_deadline_tracking()
    try:
        from tools.autonomy_review import review_dashboard
        dash = review_dashboard()
    except Exception:
        dash = {"pending": [], "pending_count": 0}
    return {"approaching": rdt["approaching"], "expired": rdt["expired"],
            "pending_count": dash.get("pending_count", 0),
            "pending": dash.get("pending", []),
            "_note": "Pending Class C reviews and their fail-closed deadlines. "
                     "Tracking is read-only; expired reviews auto-revert on the "
                     "guardrail sweep. Read-only view."}


def notification_center() -> Dict[str, Any]:
    """Current in-GUI notifications + what the next generation would add. Read-only."""
    cur = ahk.list_notifications()
    would = ahk.generate_notifications(mode="suggest", by="cockpit")
    return {"notifications": cur.get("notifications", []),
            "generated_at": cur.get("generated_at"),
            "would_create": would.get("would_create", 0),
            "_note": "In-GUI notifications only — no external push. Read-only view."}


def governance_health() -> Dict[str, Any]:
    """Governance monitoring (read-only): kill switch, policy, guardrails, throttle,
    anomalies."""
    return governance_monitoring()


def automation_metrics() -> Dict[str, Any]:
    """Cycle history + action counts + reversal counts + throttle metrics. Read-only."""
    log = cycle_log(limit=10000)
    applied_cycles = [c for c in log if c.get("mode") == "apply"]
    dry = [c for c in log if c.get("mode") == "suggest"]
    skipped = [c for c in log if c.get("mode") == "skipped"]
    hk = ahk.housekeeping_status()
    return {
        "cycles_total": len(log),
        "cycles_applied": len(applied_cycles),
        "cycles_dry_run": len(dry),
        "cycles_skipped_frozen": len(skipped),
        "housekeeping_applied": hk.get("applied_count"),
        "housekeeping_reversed": hk.get("reversed_count"),
        "notifications": hk.get("notifications"),
        "review_packets": hk.get("review_packets"),
        "throttle_metrics": agr.throttle_metrics(),
        "last_cycle": log[-1] if log else None,
        "_note": "Class B autonomy metrics. Read-only view.",
    }
