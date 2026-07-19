"""Class B automation — reversible housekeeping (Phase B).

The first real autonomy, and deliberately the SAFEST: reversible housekeeping that
writes only derived/regenerable state, launches NO external activity, and touches
nothing correctness-critical (no templates, selectors, profiles, corpus, debt,
captures, or logins — those are Class C/D). Every action:

  * checks the kill switch first (frozen ⇒ no-op, logged as skipped);
  * supports mode="suggest" (compute what would change, apply nothing) and
    mode="apply" (apply + log + make reversible);
  * is logged to an append-only action log with enough state to reverse it;
  * is reversible via `reverse_action`.

Phase B builds the two Class B guardrails (action_logging, reversibility) so Class B
becomes *eligible* for Level 3. It does NOT turn autonomy on: the policy default for
Class B stays "suggest", so `can_autonomously("B")` is False until the operator
deliberately sets B to auto_with_guardrails — and even then the kill switch gates
every action and everything is logged + reversible. No background scheduler is
installed in this phase; actions are invoked explicitly (by the operator or an API
call), with the policy + kill switch deciding whether an auto-apply may proceed.

All state is runtime (under the governance store root, never shipped); writes are
atomic (.tmp + replace) and utf-8. No module-level I/O.
"""
from __future__ import annotations

import datetime as _dt
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools import autonomy_policy as ap
from tools.cockpit_core import _store_load, _store_save, confine, tasks_root

CLASS = "B"
ACTIONS = ("reorder_queue", "generate_notifications", "refresh_dashboard_cache",
           "generate_review_packet")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ── runtime stores (never shipped) ──────────────────────────────────────────
def _hk_root() -> Path:
    return tasks_root() / "governance" / "housekeeping"


def _log_path() -> Path:
    return _hk_root() / "housekeeping_log.jsonl"


def _notif_path() -> Path:
    return _hk_root() / "notifications.json"


def _packet_dir() -> Path:
    return _hk_root() / "review_packets"


def _dash_cache_path() -> Path:
    return _hk_root() / "dashboard_cache.json"


def _atomic_write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    tmp.replace(p)


# ── action log (the 'action_logging' guardrail) ─────────────────────────────
def _log_action(entry: Dict[str, Any]) -> None:
    p = _log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


def housekeeping_log(limit: int = 200) -> List[Dict[str, Any]]:
    p = _log_path()
    if not p.is_file():
        return []
    out = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    except Exception:
        pass
    return out[-limit:]


def _find_log_entry(action_id: str) -> Optional[Dict[str, Any]]:
    log = housekeeping_log(limit=10000)
    reversed_marker = any(e.get("marks") == action_id and e.get("reversed")
                          for e in log)
    for e in reversed(log):
        if e.get("id") == action_id and e.get("mode") != "marker":
            if reversed_marker:
                e = {**e, "reversed": True}
            return e
    return None


# ── guard: kill switch first, always ─────────────────────────────────────────
def _guard(action: str, by: str) -> Optional[Dict[str, Any]]:
    """Returns a skip-result dict if the action must NOT proceed (frozen / bad input),
    else None. The kill switch is checked before anything else."""
    if action not in ACTIONS:
        return {"ok": False, "error": f"unknown housekeeping action {action!r}"}
    if not by:
        return {"ok": False, "error": "an operator/system identity (by) is required"}
    if ap.is_frozen():
        rec = {"id": "hk_" + uuid.uuid4().hex[:10], "ts": _now(), "action": action,
               "by": by, "mode": "skipped", "skipped": True, "reason": "frozen "
               "(kill switch) — no automation runs", "reversible": False}
        _log_action(rec)
        return {"ok": False, "skipped": True, "reason": rec["reason"]}
    return None


def _auto_or_manual(by: str) -> str:
    """Label how this run is authorised: 'auto' if Class B is opted into Level 3 and
    eligible right now, else 'operator' (an explicit human-initiated run)."""
    try:
        return "auto" if ap.can_autonomously(CLASS).get("allowed") else "operator"
    except Exception:
        return "operator"


# ── 1) queue ordering ────────────────────────────────────────────────────────
def _readiness_rank() -> Dict[str, int]:
    """site -> rank (lower = needs attention sooner). Derived from site_readiness."""
    try:
        from tools.cockpit_templates import site_readiness
        rows = site_readiness().get("sites", [])
        return {r["site"]: i for i, r in enumerate(rows)}  # already least-ready first
    except Exception:
        return {}


def reorder_queue(mode: str = "suggest", by: str = "") -> Dict[str, Any]:
    """Reorder the operator's PLAN queue by site readiness (least-ready first), then
    priority. The queue is a plan — nothing runs from it — so reordering is reversible
    housekeeping. suggest: return proposed order. apply: rewrite `order`, log the
    previous order for reversal."""
    g = _guard("reorder_queue", by)
    if g:
        return g
    st = _store_load()
    q = list(st.get("queue", []))
    rank = _readiness_rank()
    prio = {"high": 0, "medium": 1, "low": 2}
    before = [{"id": i.get("id"), "order": i.get("order", 0)} for i in q]

    def key(i):
        return (rank.get(i.get("site"), 10_000),
                prio.get(i.get("priority"), 1), i.get("created", 0))
    new_seq = sorted(q, key=key)
    proposed = [{"id": i.get("id"), "site": i.get("site"), "label": i.get("label"),
                 "old_order": i.get("order", 0), "new_order": n}
                for n, i in enumerate(new_seq)]
    changed = [p for p in proposed if p["old_order"] != p["new_order"]]
    if mode != "apply":
        return {"ok": True, "mode": "suggest", "action": "reorder_queue",
                "would_change": len(changed), "proposed": proposed,
                "_note": "suggest only — nothing applied."}
    # apply
    for n, i in enumerate(new_seq):
        i["order"] = n
    st["queue"] = new_seq
    _store_save(st)
    aid = "hk_" + uuid.uuid4().hex[:10]
    _log_action({"id": aid, "ts": _now(), "action": "reorder_queue", "by": by,
                 "mode": "apply", "auth": _auto_or_manual(by),
                 "changed": len(changed), "before": before, "reversible": True,
                 "reversed": False})
    return {"ok": True, "mode": "apply", "action": "reorder_queue", "id": aid,
            "changed": len(changed)}


# ── 2) notifications (in-GUI only; NO external push) ─────────────────────────
def _derive_notifications() -> List[Dict[str, Any]]:
    out = []
    try:
        from tools.cockpit_templates import operator_mission_control
        mc = operator_mission_control()
        na = mc.get("needs_attention", {})
        for s in na.get("broken_login_templates", []):
            out.append({"severity": "high", "kind": "broken_login_template", "site": s,
                        "title": f"{s}: login template missing"})
        for s in na.get("broken_video_templates", []):
            out.append({"severity": "high", "kind": "broken_video_template", "site": s,
                        "title": f"{s}: video template missing or stale"})
        for h in na.get("high_drift_sites", []):
            out.append({"severity": "medium", "kind": "high_drift", "site": h.get("site"),
                        "title": f"{h.get('site')}: {h.get('events')} drift event(s)"})
        for s in na.get("not_ready_sites", []):
            out.append({"severity": "medium", "kind": "not_ready", "site": s.get("site"),
                        "title": f"{s.get('site')}: not ready ({s.get('readiness')})"})
        if na.get("open_reviews"):
            out.append({"severity": "low", "kind": "open_reviews", "site": None,
                        "title": f"{na['open_reviews']} template review(s) pending"})
    except Exception:
        pass
    return out


def generate_notifications(mode: str = "suggest", by: str = "") -> Dict[str, Any]:
    """Derive in-GUI alerts from current state (broken templates, high drift, not-ready
    sites, review backlog). In-GUI only — NO external push. suggest: return would-be
    alerts. apply: write the alert set, log the ids for reversal (dismiss)."""
    g = _guard("generate_notifications", by)
    if g:
        return g
    derived = _derive_notifications()
    if mode != "apply":
        return {"ok": True, "mode": "suggest", "action": "generate_notifications",
                "would_create": len(derived), "notifications": derived,
                "_note": "suggest only — nothing written. In-GUI only; no external push."}
    stamped = [{"id": "n_" + uuid.uuid4().hex[:8], "ts": _now(), "dismissed": False, **d}
               for d in derived]
    _atomic_write_json(_notif_path(), {"generated_at": _now(), "notifications": stamped})
    aid = "hk_" + uuid.uuid4().hex[:10]
    _log_action({"id": aid, "ts": _now(), "action": "generate_notifications", "by": by,
                 "mode": "apply", "auth": _auto_or_manual(by),
                 "created": len(stamped), "ids": [n["id"] for n in stamped],
                 "reversible": True, "reversed": False})
    return {"ok": True, "mode": "apply", "action": "generate_notifications", "id": aid,
            "created": len(stamped)}


def list_notifications() -> Dict[str, Any]:
    p = _notif_path()
    if not p.is_file():
        return {"notifications": [], "generated_at": None}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d.setdefault("notifications", [])
        return d
    except Exception:
        return {"notifications": [], "generated_at": None}


# ── 3) dashboard cache (regenerable; recompute-and-store) ────────────────────
def refresh_dashboard_cache(mode: str = "suggest", by: str = "") -> Dict[str, Any]:
    """Recompute a dashboard snapshot (mission control + readiness summary) and cache
    it. The cache is regenerable, so this is reversible housekeeping (reverse = delete
    the cache). suggest: report what would be cached. apply: write the cache."""
    g = _guard("refresh_dashboard_cache", by)
    if g:
        return g
    snap: Dict[str, Any] = {"generated_at": _now()}
    try:
        from tools.cockpit_templates import operator_mission_control, site_readiness
        mc = operator_mission_control()
        rd = site_readiness()
        snap["needs_attention_count"] = mc.get("needs_attention", {}).get("count")
        snap["ready"] = rd.get("ready")
        snap["caution"] = rd.get("caution")
        snap["not_ready"] = rd.get("not_ready")
        snap["site_count"] = rd.get("site_count")
    except Exception as e:
        snap["error"] = str(e)[:120]
    if mode != "apply":
        return {"ok": True, "mode": "suggest", "action": "refresh_dashboard_cache",
                "would_cache": snap, "_note": "suggest only — nothing written."}
    _atomic_write_json(_dash_cache_path(), snap)
    aid = "hk_" + uuid.uuid4().hex[:10]
    _log_action({"id": aid, "ts": _now(), "action": "refresh_dashboard_cache", "by": by,
                 "mode": "apply", "auth": _auto_or_manual(by),
                 "reversible": True, "reversed": False})
    return {"ok": True, "mode": "apply", "action": "refresh_dashboard_cache", "id": aid,
            "cached": snap}


def dashboard_cache() -> Dict[str, Any]:
    p = _dash_cache_path()
    if not p.is_file():
        return {"cached": None}
    try:
        return {"cached": json.loads(p.read_text(encoding="utf-8"))}
    except Exception:
        return {"cached": None}


# ── 4) review packet (read-only artifact assembled from pending reviews) ─────
def generate_review_packet(mode: str = "suggest", by: str = "") -> Dict[str, Any]:
    """Assemble a read-only review packet — the pending template reviews with their
    evidence pointers — as a regenerable artifact. suggest: report the packet contents.
    apply: write the packet, log the id for reversal (delete)."""
    g = _guard("generate_review_packet", by)
    if g:
        return g
    items = []
    try:
        from tools.cockpit_templates import template_review_queue
        for it in template_review_queue().get("items", []):
            if not it.get("decision"):
                items.append({"site": it.get("site"), "kind": it.get("kind"),
                              "item_key": it.get("item_key")})
    except Exception:
        pass
    packet = {"generated_at": _now(), "pending_count": len(items), "items": items}
    if mode != "apply":
        return {"ok": True, "mode": "suggest", "action": "generate_review_packet",
                "would_include": len(items), "packet": packet,
                "_note": "suggest only — nothing written."}
    pid = "pkt_" + uuid.uuid4().hex[:10]
    _atomic_write_json(_packet_dir() / f"{pid}.json", {"id": pid, **packet})
    aid = "hk_" + uuid.uuid4().hex[:10]
    _log_action({"id": aid, "ts": _now(), "action": "generate_review_packet", "by": by,
                 "mode": "apply", "auth": _auto_or_manual(by), "packet_id": pid,
                 "pending_count": len(items), "reversible": True, "reversed": False})
    return {"ok": True, "mode": "apply", "action": "generate_review_packet", "id": aid,
            "packet_id": pid, "pending_count": len(items)}


def list_review_packets() -> List[Dict[str, Any]]:
    d = _packet_dir()
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": r.get("id"), "generated_at": r.get("generated_at"),
                        "pending_count": r.get("pending_count")})
        except Exception:
            continue
    return out


# ── reversibility (the 'reversibility' guardrail) ────────────────────────────
def reverse_action(action_id: str, by: str) -> Dict[str, Any]:
    """Undo a logged, reversible Class B action. Restores queue order / deletes
    generated notifications, packet, or dashboard cache. Records the reversal."""
    if not by:
        return {"ok": False, "error": "an operator/system identity (by) is required"}
    e = _find_log_entry(action_id)
    if not e:
        return {"ok": False, "error": "action not found"}
    if e.get("reversed"):
        return {"ok": True, "already_reversed": True}
    if not e.get("reversible"):
        return {"ok": False, "error": "action is not reversible"}
    act = e.get("action")
    if act == "reorder_queue":
        order_map = {b["id"]: b["order"] for b in e.get("before", [])}
        st = _store_load()
        for i in st.get("queue", []):
            if i.get("id") in order_map:
                i["order"] = order_map[i["id"]]
        st["queue"] = sorted(st.get("queue", []), key=lambda i: i.get("order", 0))
        _store_save(st)
    elif act == "generate_notifications":
        # remove only the notifications this action generated
        ids = set(e.get("ids", []))
        cur = list_notifications()
        cur["notifications"] = [n for n in cur.get("notifications", [])
                                if n.get("id") not in ids]
        _atomic_write_json(_notif_path(), cur)
    elif act == "refresh_dashboard_cache":
        p = _dash_cache_path()
        if p.is_file():
            p.unlink()
    elif act == "generate_review_packet":
        pid = e.get("packet_id")
        safe = confine(f"{pid}.json", _packet_dir()) if pid else None
        if safe and safe.is_file():
            safe.unlink()
    else:
        return {"ok": False, "error": f"no reversal for action {act!r}"}
    _log_action({"id": "hk_" + uuid.uuid4().hex[:10], "ts": _now(),
                 "action": "reverse", "by": by, "mode": "apply",
                 "reversed_id": action_id, "reversed_action": act,
                 "reversible": False})
    # mark original reversed by appending a marker the reader can fold in
    _log_action({"id": action_id + ":reversed", "ts": _now(), "action": act,
                 "by": by, "mode": "marker", "marks": action_id, "reversed": True,
                 "reversible": False})
    return {"ok": True, "reversed": action_id, "action": act}


# ── dispatcher (no background scheduler in Phase B) ──────────────────────────
def run_housekeeping(actions: Optional[List[str]] = None, mode: str = "suggest",
                     by: str = "") -> Dict[str, Any]:
    """Run a set of Class B actions in one pass (default: all four). Honors the kill
    switch per action. `auth` records whether Class B is currently opted into auto.
    No background loop — this is invoked explicitly."""
    if not by:
        return {"ok": False, "error": "an operator/system identity (by) is required"}
    todo = actions or list(ACTIONS)
    fns = {"reorder_queue": reorder_queue,
           "generate_notifications": generate_notifications,
           "refresh_dashboard_cache": refresh_dashboard_cache,
           "generate_review_packet": generate_review_packet}
    results = {}
    for a in todo:
        fn = fns.get(a)
        results[a] = fn(mode=mode, by=by) if fn else {"ok": False, "error": "unknown"}
    return {"ok": True, "mode": mode, "auth": _auto_or_manual(by),
            "frozen": ap.is_frozen(), "results": results,
            "_note": "Class B reversible housekeeping. Kill switch checked per action; "
                     "nothing launches external activity; all applies are logged + "
                     "reversible. No background scheduler in this phase."}


def housekeeping_status() -> Dict[str, Any]:
    """Read-only summary for the cockpit view."""
    log = housekeeping_log()
    applied = [e for e in log if e.get("mode") == "apply"]
    reversed_ids = {e.get("marks") for e in log if e.get("reversed") and e.get("marks")}
    return {
        "class_b_level": ap.load_policy()["levels"].get("B"),
        "class_b_can_autonomously": ap.can_autonomously(CLASS),
        "kill_switch": ap.freeze_status(),
        "guardrails": {g: ap.guardrail_registry().get(g, {}).get("built")
                       for g in ("kill_switch", "action_logging", "reversibility")},
        "actions": list(ACTIONS),
        "applied_count": len(applied),
        "reversed_count": len(reversed_ids),
        "notifications": len(list_notifications().get("notifications", [])),
        "review_packets": len(list_review_packets()),
        "_note": "Class B automation built and guarded. Default level is 'suggest' — "
                 "nothing runs autonomously until the operator sets Class B to "
                 "auto_with_guardrails. Kill switch gates every action; all applies "
                 "are logged and reversible. No external activity. Read-only view.",
    }
