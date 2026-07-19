"""H/consolidation — per-(site, kind) auto-apply GRANTS. Authority model (Phase H):

  * CREATING a grant is HUMAN-ONLY (CLI/host). So is REVOKING and UN-SUSPENDING.
  * The system may only AUTO-SUSPEND a grant — a contraction of authority — via the
    host job `reconcile_grants()`. It never creates a grant and never un-suspends one.

Per-(site, kind): a grant authorizes ONE apply kind for one site. Granting
`(site, live_site_config)` does NOT grant `(site, operational_rows)` or any other kind.
Writes the store the oracle reads: `governance/oracle/site_auto_grants.json`, shape
`{site: {kind: {...}}}`. Old single-kind entries `{site: {granted: …}}` are read as
`{site: {"live_site_config": …}}` (back-compat) and migrate to the nested shape on write.

`class_c_site_eligible(site, kind)` returns eligible only if a non-suspended, unexpired
grant for that kind exists AND Class C is at auto AND oracle tier >= 3 — the grant is
necessary, not sufficient. There is NO cockpit POST for granting (operator decision).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from tools import autonomy_oracle as ao
from tools import autonomy_trust as atr
from tools import autonomy_policy as ap

DEFAULT_KIND = "live_site_config"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _path() -> Path:
    return ao._grants_path()


def _log_path() -> Path:
    return _path().parent / "grants_log.jsonl"


def _read() -> Dict[str, Any]:
    """Normalized (site -> kind -> entry). Old single-kind entries map to live_site_config."""
    p = _path()
    if not p.is_file():
        return {}
    try:
        return ao._normalize_grants(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return {}


def _atomic_write(obj: Dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _log(event: str, site: str, kind: str, by: str,
         detail: Optional[Dict[str, Any]] = None) -> None:
    lp = _log_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    with lp.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now(), "event": event, "site": site, "kind": kind,
                            "by": by, "detail": detail or {}}) + "\n")


# ── human-only authority EXPANSION (CLI/host) ─────────────────────────────────

def grant_site(site: str, *, kind: str = DEFAULT_KIND, by: str, reason: str,
               expires_at: Optional[str] = None) -> Dict[str, Any]:
    """HUMAN-ONLY. Create/refresh a per-(site, kind) auto-apply grant."""
    g = _read()
    g.setdefault(site, {})[kind] = {
        "granted": True, "granted_by": by, "granted_at": _now(),
        "reason": reason, "expires_at": expires_at,
        "suspended": False, "suspend_reason": None}
    _atomic_write(g)
    _log("grant", site, kind, by, {"reason": reason, "expires_at": expires_at})
    return {"ok": True, "site": site, "kind": kind, "grant": g[site][kind]}


def revoke_site(site: str, *, kind: str = DEFAULT_KIND, by: str,
                reason: str = "") -> Dict[str, Any]:
    """HUMAN-ONLY. Remove a (site, kind) grant. In-flight changes still fail-closed-revert."""
    g = _read()
    if site in g and kind in g[site]:
        g[site].pop(kind)
        if not g[site]:
            g.pop(site)
        _atomic_write(g)
        _log("revoke", site, kind, by, {"reason": reason})
        return {"ok": True, "site": site, "kind": kind, "revoked": True}
    return {"ok": True, "site": site, "kind": kind, "revoked": False, "reason": "no grant"}


def unsuspend_site(site: str, *, kind: str = DEFAULT_KIND, by: str,
                   reason: str = "") -> Dict[str, Any]:
    """HUMAN-ONLY. Lift a suspension — authority is being RESTORED by a human."""
    g = _read()
    entry = (g.get(site) or {}).get(kind)
    if entry and entry.get("suspended"):
        entry["suspended"] = False
        entry["suspend_reason"] = None
        _atomic_write(g)
        _log("unsuspend", site, kind, by, {"reason": reason})
        return {"ok": True, "site": site, "kind": kind, "suspended": False}
    return {"ok": True, "site": site, "kind": kind,
            "suspended": bool(entry and entry.get("suspended"))}


# ── automatic authority CONTRACTION (host job; never expands) ─────────────────

def reconcile_grants(*, by: str = "system") -> Dict[str, Any]:
    """HOST job. AUTO-SUSPEND only — sets `suspended=true` (parks the operator's grant,
    never deletes it) for any (site, kind) where trust < floor, oracle tier < 3, frozen, or
    expired. It NEVER creates a grant and NEVER sets suspended=false. Authority contracts
    automatically, never expands."""
    g = _read()
    changed = []
    now = _now()
    frozen = not ap.can_autonomously("C").get("allowed", False)
    for site, kinds in g.items():
        for kind, gr in kinds.items():
            if not gr.get("granted") or gr.get("suspended"):
                continue
            reasons = []
            if frozen:
                reasons.append("automation frozen")
            try:
                if atr.effective_trust(site) < atr.MIN_TRUST:
                    reasons.append("trust below floor")
            except Exception:
                reasons.append("trust unavailable")
            try:
                if ao.oracle_verdict(site).get("tier", 0) < 3:
                    reasons.append("oracle tier below 3")
            except Exception:
                reasons.append("oracle unavailable")
            exp = gr.get("expires_at")
            if exp and str(exp) <= now:
                reasons.append("grant expired")
            if reasons:
                gr["suspended"] = True
                gr["suspend_reason"] = "; ".join(reasons)
                changed.append(f"{site}::{kind}")
                _log("auto_suspend", site, kind, by, {"reasons": reasons})
    if changed:
        _atomic_write(g)
    return {"ok": True, "suspended": changed, "suspended_count": len(changed),
            "_note": "Auto-suspend reduces authority only. Granting/un-suspending is "
                     "human-only (CLI/host)."}


# ── read-only ─────────────────────────────────────────────────────────────────

def grant_overview() -> Dict[str, Any]:
    g = _read()
    pairs = [(s, k, e) for s, kinds in g.items() for k, e in kinds.items()]
    return {"grants": g, "count": len(pairs),
            "active": sum(1 for _, _, e in pairs
                          if e.get("granted") and not e.get("suspended")),
            "suspended": sum(1 for _, _, e in pairs if e.get("suspended"))}


def is_active(site: str, kind: str = DEFAULT_KIND) -> bool:
    e = (_read().get(site) or {}).get(kind) or {}
    return bool(e.get("granted") and not e.get("suspended"))


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Per-(site, kind) auto-apply grants (human-only).")
    sub = p.add_subparsers(dest="cmd", required=True)
    pg = sub.add_parser("grant", help="grant a (site, kind) (human-only)")
    pg.add_argument("site"); pg.add_argument("--kind", default=DEFAULT_KIND)
    pg.add_argument("--by", required=True)
    pg.add_argument("--reason", required=True); pg.add_argument("--expires-at", default=None)
    pr = sub.add_parser("revoke", help="revoke a (site, kind) (human-only)")
    pr.add_argument("site"); pr.add_argument("--kind", default=DEFAULT_KIND)
    pr.add_argument("--by", required=True); pr.add_argument("--reason", default="")
    pu = sub.add_parser("unsuspend", help="lift a suspension (human-only)")
    pu.add_argument("site"); pu.add_argument("--kind", default=DEFAULT_KIND)
    pu.add_argument("--by", required=True); pu.add_argument("--reason", default="")
    sub.add_parser("reconcile", help="host job: auto-suspend (contraction only)")
    sub.add_parser("list", help="show grants (read-only)")
    a = p.parse_args(argv)
    if a.cmd == "grant":
        out = grant_site(a.site, kind=a.kind, by=a.by, reason=a.reason, expires_at=a.expires_at)
    elif a.cmd == "revoke":
        out = revoke_site(a.site, kind=a.kind, by=a.by, reason=a.reason)
    elif a.cmd == "unsuspend":
        out = unsuspend_site(a.site, kind=a.kind, by=a.by, reason=a.reason)
    elif a.cmd == "reconcile":
        out = reconcile_grants()
    else:
        out = grant_overview()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
