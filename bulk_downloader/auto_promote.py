"""bulk_downloader.auto_promote -- A5: auto-promote a reviewed candidate on a
clean staged diff vs the current gold.

A ``reviewed_not_enabled`` candidate is auto-promoted to enabled ONLY when its
staged diff vs the current gold is CLEAN:

  * no new API base host (vs gold's API host set),
  * no blocked term (promote_gate_errors / BAD_TERMS),
  * no risky-selector delta (blocking lint), and
  * the candidate is not marked ``auto_promotable=False`` (the A3 risky
    quarantine flag).

Any boundary-crossing case -- a new API base host, a first-time host (no gold),
a blocked term, a risky selector -- stays a MANUAL operator confirm (staged with
evidence, never auto-enabled). The promote is A0-backed (the prior gold is
snapshotted + restorable) and gated by the ``auto_promote`` toggle, which is
keystone-required (it enables a serving template).
"""
from __future__ import annotations

import time
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional

from . import lifecycle_automation as la
from . import template_keystone as tk

_SUFFIX = ".template.json"
_GOLD_SUFFIX = ".template.json.bak"


def _host_of_url(u: str) -> str:
    if not u:
        return ""
    s = str(u)
    try:
        netloc = urlparse(s if "://" in s else "https://" + s).netloc.lower()
        return netloc or s.lower()
    except Exception:
        return s.lower()


def _api_hosts(t: Dict[str, Any]) -> set:
    """Every API host the template would reach: api.base netloc + observed hosts
    + the normalizer's api_base_candidate."""
    hosts = set()
    api = t.get("api") or {}
    base = api.get("base") or ""
    h = _host_of_url(base)
    if h:
        hosts.add(h)
    for oh in (t.get("observed_api_hosts") or []):
        if oh:
            hosts.add(str(oh).lower())
    abc = t.get("api_base_candidate")
    if abc:
        hh = _host_of_url(str(abc))
        if hh:
            hosts.add(hh)
    return hosts


def _default_gate(t: Dict[str, Any]) -> List[str]:
    try:
        from .template_manager import promote_gate_errors as _pge
        return _pge(t or {})
    except Exception as e:
        return [f"gate unavailable: {e}"[:120]]


def _default_lint_blocking(t: Dict[str, Any]) -> bool:
    try:
        from . import selector_lint as _sl
        return _sl.has_blocking_issues(_sl.lint_template(t or {}))
    except Exception:
        return False


def compute_clean_diff(candidate: Dict[str, Any], gold: Optional[Dict[str, Any]],
                       *, reviewed_dir=None, host: Optional[str] = None,
                       lint_fn=None, gate_fn=None) -> Dict[str, Any]:
    """Classify a candidate's staged diff vs gold. Returns
    ``{clean, boundary, new_api_hosts, drift, lines}``. ``clean`` is True iff
    there are no boundary reasons (the only auto-promotable shape)."""
    if not isinstance(candidate, dict):
        return {"clean": False, "boundary": ["candidate is not a dict"],
                "new_api_hosts": [], "drift": None, "lines": []}
    boundary: List[str] = []

    if gold is None:
        boundary.append("first-time host (no gold) -- manual enable required")

    if candidate.get("auto_promotable") is False:
        boundary.append("candidate marked auto_promotable=False")

    new_hosts: List[str] = []
    if gold is not None:
        new_hosts = sorted(_api_hosts(candidate) - _api_hosts(gold))
        if new_hosts:
            boundary.append(f"new api host(s): {new_hosts}")

    errs = (gate_fn or _default_gate)(candidate)
    if errs:
        boundary.append(f"gate/blocked-term failure: {list(errs)[:3]}")

    if (lint_fn or _default_lint_blocking)(candidate):
        boundary.append("risky selector (blocking lint)")

    drift, lines = None, []
    if host:
        try:
            dr = tk.drift_against_gold(host, candidate, reviewed_dir=reviewed_dir)
            if dr.get("ok"):
                drift = dr.get("drift")
                lines = dr.get("lines") or []
        except Exception:
            pass

    return {"clean": not boundary, "boundary": boundary,
            "new_api_hosts": new_hosts, "drift": drift, "lines": lines}


def _resolve_gold(h: str, rd) -> Optional[Dict[str, Any]]:
    """Current gold for the host: the .bak gold if present, else the live
    template only when it is actually ENABLED. A reviewed/disabled live is NOT a
    gold (so a candidate over it is first-time-ish -> boundary)."""
    import json
    from pathlib import Path
    rd = Path(rd) if rd is not None else None
    gold_fp = (rd / f"{h}{_GOLD_SUFFIX}") if rd else None
    live_fp = (rd / f"{h}{_SUFFIX}") if rd else None
    try:
        if gold_fp and gold_fp.is_file():
            return json.loads(gold_fp.read_text("utf-8"))
        if live_fp and live_fp.is_file():
            lv = json.loads(live_fp.read_text("utf-8"))
            if lv.get("status") == la.STATUS_ENABLED:
                return lv
    except Exception:
        return None
    return None


def auto_promote_if_clean(host: str, candidate: Dict[str, Any], *,
                          reviewed_dir=None, clean_fn=None) -> Dict[str, Any]:
    """Gated A5 entry. Auto-promote a CLEAN candidate to enabled (A0-backed) with
    diff + evidence; stage a boundary-crossing candidate for manual confirm
    (never enabled). Default OFF -> a no-op."""
    if not la.is_enabled("auto_promote"):
        return {"ok": True, "skipped": "auto_promote disabled or keystone absent"}
    h = tk._safe_host(host)
    if not h:
        return {"ok": False, "error": "invalid host"}

    if clean_fn is not None:
        res = clean_fn()
    else:
        gold = _resolve_gold(h, reviewed_dir)
        res = compute_clean_diff(candidate, gold, reviewed_dir=reviewed_dir, host=h)

    if not res.get("clean"):
        # Boundary-crossing: stage evidence, route to manual confirm, never enable.
        return {"ok": True, "auto_promoted": False, "needs_confirm": True,
                "staged_for_review": True, "boundary": res.get("boundary"),
                "evidence": {"drift": res.get("drift"),
                             "diff_sample": (res.get("lines") or [])[:5]}}

    # Clean diff: A0-backed write to enabled (safe_overwrite snapshots + takes the
    # generational backup first; no drift gate here -- a clean diff is, by
    # definition, an approved-shape change).
    promoted = dict(candidate)
    promoted["status"] = la.STATUS_ENABLED
    promoted["auto_promoted_at"] = int(time.time())
    sw = tk.safe_overwrite(h, promoted, reviewed_dir=reviewed_dir)
    if not sw.get("ok"):
        return {"ok": False, "error": sw.get("error")}
    if not sw.get("swapped"):
        return {"ok": True, "auto_promoted": False, "needs_confirm": True,
                "reason": "keystone declined the swap", "drift": sw.get("drift")}
    return {"ok": True, "auto_promoted": True, "enabled": True,
            "evidence": {"drift": res.get("drift"),
                         "diff_sample": (res.get("lines") or [])[:5]},
            "gold": sw.get("gold")}
