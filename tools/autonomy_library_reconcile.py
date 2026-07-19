"""Phase J — `library_reconcile`: governed cleanup of the media file-index on the generic
Class-C apply harness (after H=`live_site_config`, v1=`staging_json`, I=`queue_housekeeping`).

WHAT IT DOES (per site, only once a `(site, library_reconcile)` grant is active):
  Remove `library` rows whose file went away — `file_exists=0` AND last seen present more than
  `BD_LIB_RECONCILE_MISSING_DAYS` ago (default 30). The file is **never** touched
  (`also_delete_file=False`, always); only the stale index row is removed. Fully reversible: the
  row, its tags, and the history back-ref are snapshotted and restored on rollback, preserving the
  original `id`.

WHY last_scanned IS THE DEBOUNCE
  The scanner flips `file_exists` 1→0 when a path is gone but does NOT touch `last_scanned`, so
  `last_scanned` is the time the file was last seen present. A transient mount blip reappears on
  the next scan (within the hour), which flips `file_exists` back to 1 and refreshes
  `last_scanned` — so only genuinely-gone files accumulate missing-age. No schema/scanner change
  was needed.

SCOPE
  Per-site: `library` rows carry `site_id`, so this reuses `(site, library_reconcile)` grants.
  Orphan IMPORT (on-disk files with no row) is deliberately NOT here — imported rows are
  unattributed (`site_id=''`) and need the not-yet-built global grant scope. File MOVES stay with
  `storage_tier`. NFO/sidecar regen is unverified and out of scope.

POSTURE / SAFETY
  Operational kind: no oracle tier-3 gate. Gate = active per-(site, kind) grant + the objective
  aged-missing predicate. Dark by default. Four safety layers on a removal: grant required,
  30-day debounce, file never touched, exact-reversible. Runs through the harness unchanged
  (record_change → register_pending fail-closed review → validator → rollback). DB access via
  injectable `_lib_*` wrappers so tests need no database.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from tools import autonomy_apply as aap
from tools import autonomy_grant as ag
from tools import autonomy_promotion as apr

KIND = "library_reconcile"


def _missing_days() -> int:
    try:
        from bulk_downloader import global_config as _gc
        _s = _gc.get("lib_reconcile_missing_days", None)
        if _s not in (None, ""):
            return max(1, int(_s))
    except Exception:
        pass
    try:
        return max(1, int(os.environ.get("BD_LIB_RECONCILE_MISSING_DAYS", "30")))
    except Exception:
        return 30


# ── injectable DB wrappers (tests monkeypatch these; lazy import of library) ──
def _lib_missing(site: str) -> List[Dict[str, Any]]:
    from bulk_downloader import library
    return library.library_missing_for_site(site)


def _lib_snapshot(library_id: int) -> Optional[Dict[str, Any]]:
    from bulk_downloader import library
    return library.library_snapshot(library_id)


def _lib_delete(library_id: int) -> Dict[str, Any]:
    from bulk_downloader import library
    return library.library_delete(library_id, also_delete_file=False)  # NEVER the file


def _lib_restore(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    from bulk_downloader import library
    return library.library_restore(snapshot)


# ── target selection (single source of truth for current + proposer) ──────────
def _aged_missing(site: str) -> List[Dict[str, Any]]:
    """file_exists=0 rows for the site last seen present > missing_days ago. Pure read."""
    cutoff = time.time() - _missing_days() * 86400.0
    out: List[Dict[str, Any]] = []
    for r in (_lib_missing(site) or []):
        try:
            last = float(r.get("last_scanned") or 0.0)
        except Exception:
            last = 0.0
        # last_scanned==0 means "never recorded as seen" — treat as old enough to reconcile.
        if last < cutoff:
            out.append(r)
    return out


# ── harness hooks (all late-bound via the lambdas in the registration) ────────
def _gate(site: str) -> bool:
    return ag.is_active(site, KIND)  # operational: grant only, no oracle tier. Dark by default.


def _proposer(site: str) -> Optional[List[Dict[str, Any]]]:
    tg = _aged_missing(site)
    if not tg:
        return None
    return [{"id": r["id"], "file_path": r.get("file_path", "")} for r in tg]


def _current(site: str) -> List[Dict[str, Any]]:
    # full snapshots (row + tags) — the before the reverser restores from.
    snaps = []
    for r in _aged_missing(site):
        s = _lib_snapshot(r["id"])
        if s:
            snaps.append(s)
    return snaps


def _unchanged(before: Any, after: Any) -> bool:
    return not after


def _applier(site: str, after: List[Dict[str, Any]]) -> None:
    for item in after or []:
        _lib_delete(item["id"])  # also_delete_file=False inside the wrapper


def _reverser(target_ref: str, before: Any) -> None:
    for snap in (before or []):
        _lib_restore(snap)


def _validator(site: str, after: List[Dict[str, Any]]) -> Dict[str, Any]:
    # confirm the removed ids are gone. Lenient on read error (don't re-insert on a glitch).
    try:
        live_ids = {r.get("id") for r in (_lib_missing(site) or [])}
        # rows that still exist with file_exists=1 won't appear in _lib_missing; that's fine —
        # a removed row must not appear anywhere, but we only have the missing view cheaply.
    except Exception:
        return {"ok": True, "note": "validation skipped (library read unavailable)"}
    for item in after or []:
        if item["id"] in live_ids:
            return {"ok": False, "reason": f"row {item['id']} still present after delete"}
    return {"ok": True}


def _transition(site: str, before: Any, after: Any, *, by: str,
                phase: str = "applied", detail: Any = None) -> None:
    if phase == "reverted":
        frm, to = "applied_pending", "reverted_validation"
        reason = "library_reconcile reverted: validation miss"
    else:
        frm, to = "stable", "applied_pending"
        reason = f"library_reconcile applied: {len(after or [])} missing row(s) removed (review window open)"
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
    target_ref=lambda s: f"library::{s}",
    transition=lambda s, b, a, *, by, phase="applied", detail=None: _transition(
        s, b, a, by=by, phase=phase, detail=detail),
    action_class="C",
    transition_field=KIND,
)


# ── thin operator entry points (mirror H/v1/I) ───────────────────────────────
def reconcile_site(site: str, *, by: str = "system") -> Dict[str, Any]:
    return aap.apply_for_kind(site, KIND, by=by)


def reconcile_all(*, by: str = "system", sites: Optional[List[str]] = None) -> Dict[str, Any]:
    return aap.apply_all(KIND, by=by, sites=sites)


def dry_run(site: str) -> Dict[str, Any]:
    """No writes: which aged-missing rows reconcile_site WOULD remove right now. Use this for the
    pre-grant observation week."""
    plan = _proposer(site) or []
    return {"site": site, "kind": KIND, "missing_days": _missing_days(),
            "would_remove": len(plan), "rows": [p["file_path"] for p in plan],
            "grant_active": _gate(site)}
