"""bulk_downloader.disco_runner -- A-DISCO cut 4: activation.

Wires ``disco_triage``'s injected seams to the live app -- a real HTTP fetch, the
per-site queue (``runners[sid].load_urls``), the ``discovery`` dedup set, and the
operator's per-host content pattern -- and exposes ``scheduled_disco``: the
DEFAULT-OFF, toggle-gated, scheduler-safe entry point. Each pass is persisted to
``disco_runs`` so the operator can see what discovery did, surfaced through
``automation_status`` alongside the other automation nets.

SAFETY: ``scheduled_disco`` no-ops unless the ``auto_disco`` toggle is on (default
OFF), so registering the bg task is behaviour-neutral. The master off-switch still
dominates (each site's pass runs through ``run_discovery_triage``, inert when the
switch is engaged). Never raises -- it is called from the scheduler.
"""
from __future__ import annotations

import re
import sys
import time
from typing import Any, Callable, Dict, List, Optional

ENABLE_TOGGLE = "auto_disco"           # lifecycle_automation toggle name


def _enabled() -> bool:
    """The A-DISCO master toggle (default OFF). Fail-safe OFF on any error."""
    try:
        from . import lifecycle_automation as la
        return la.is_enabled(ENABLE_TOGGLE)
    except Exception:
        return False


def _content_match_fn(pattern: Optional[str]) -> Optional[Callable[[str], bool]]:
    """Compile the per-host content url_pattern (the discovery.py mechanism) into a
    matcher, or None. A bad pattern -> None (no promotion; conservative)."""
    if not pattern:
        return None
    try:
        rx = re.compile(pattern)
    except re.error:
        return None
    return lambda u: bool(rx.search(u))


def _budget_from_cfg(dcfg: Dict[str, Any]):
    """Start from the bounded default_safe and apply any per-host overrides. The
    default is bounded, so an under-specified host is never enumerated unbounded."""
    from . import host_enumerator as he
    b = he.EnumBudget.default_safe()
    for field in ("max_pages", "max_candidates", "max_depth"):
        if dcfg.get(field) is not None:
            try:
                setattr(b, field, int(dcfg[field]))
            except Exception:
                pass
    for field in ("wall_s", "delay_s"):
        if dcfg.get(field) is not None:
            try:
                setattr(b, field, float(dcfg[field]))
            except Exception:
                pass
    return b


def _real_fetch(url: str) -> Optional[str]:
    """Delegate to discovery's fetcher (the tested HTTP path); bytes -> text."""
    try:
        from . import discovery as _disc
        body = _disc._fetch(url)
        if body is None:
            return None
        return body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    except Exception:
        return None


# ── persistence: disco_runs (this module is the single schema owner) ─────────

def _ensure_table() -> None:
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS disco_runs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                site_id TEXT NOT NULL,
                root TEXT,
                host TEXT,
                enumerated INTEGER DEFAULT 0,
                enqueued INTEGER DEFAULT 0,
                review INTEGER DEFAULT 0,
                reject INTEGER DEFAULT 0,
                halted INTEGER DEFAULT 0,
                halt_reason TEXT DEFAULT ''
            )""")
    except Exception as e:  # pragma: no cover - schema init is best-effort
        sys.stderr.write(f"[disco_runner] schema init failed: {e}\n")


def _persist(rec: Dict[str, Any]) -> None:
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute(
                "INSERT INTO disco_runs(ts, site_id, root, host, enumerated, "
                "enqueued, review, reject, halted, halt_reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (time.time(), rec.get("site_id", ""), rec.get("root", ""),
                 rec.get("host", ""), int(rec.get("enumerated") or 0),
                 int(rec.get("enqueued") or 0), int(rec.get("review") or 0),
                 int(rec.get("reject") or 0), 1 if rec.get("enum_halted") else 0,
                 str(rec.get("enum_halt_reason") or "")[:120]))
    except Exception as e:
        sys.stderr.write(f"[disco_runner] could not persist run ({type(e).__name__}: {e})\n")


def latest_run() -> Optional[Dict[str, Any]]:
    """The most recent disco run, or None if none has completed."""
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute(
                "SELECT ts, site_id, root, host, enumerated, enqueued, review, "
                "reject, halted, halt_reason FROM disco_runs "
                "ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row is not None else None
    except Exception:
        return None


def recent_runs(limit: int = 50) -> List[Dict[str, Any]]:
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("SELECT * FROM disco_runs ORDER BY id DESC LIMIT ?",
                              (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── readout: A-DISCO's automation net (UNKNOWN is a third state) ─────────────

def disco_status() -> Dict[str, Any]:
    """A-DISCO's net for automation_status. DISABLED -> neutral (an opt-in
    feature being off is not a red light); enabled-but-never-ran -> UNKNOWN (a
    third state, NOT a green light); a completed run -> its summary. `ok` is a
    neutral True when disabled so the aggregate is not dragged by an off feature.
    """
    if not _enabled():
        return {"state": "disabled", "ok": True, "enabled": False,
                "detail": "A-DISCO is off"}
    row = latest_run()
    if not row:
        return {"state": "unknown", "ok": False, "enabled": True,
                "detail": "A-DISCO is on but has never completed a run",
                "ran_at": None}
    return {"state": "ok", "ok": True, "enabled": True,
            "detail": "the last A-DISCO pass completed",
            "ran_at": row.get("ts"), "site_id": row.get("site_id", ""),
            "enumerated": int(row.get("enumerated") or 0),
            "enqueued": int(row.get("enqueued") or 0),
            "review": int(row.get("review") or 0)}


# ── the scheduled entry point ────────────────────────────────────────────────

def scheduled_disco(*, s_cfg: Optional[Dict[str, Any]] = None,
                    runners: Optional[Dict[str, Any]] = None,
                    fetch_fn: Optional[Callable[[str], Optional[str]]] = None,
                    off_switch_fn: Optional[Callable[[], bool]] = None,
                    enabled_fn: Optional[Callable[[], bool]] = None,
                    ) -> Dict[str, Any]:
    """Run A-DISCO for every site whose ``disco`` config is enabled. NO-OP unless
    the ``auto_disco`` toggle is on, so registering the bg task is behaviour-neutral.
    Never raises. ``fetch_fn`` / ``off_switch_fn`` are injectable for testing; the
    defaults are the real HTTP fetch and the master off-switch.
    """
    try:
        if not (enabled_fn or _enabled)():
            return {"ran": False, "reason": "disabled", "sites": 0, "runs": []}
        from . import disco_triage as dtr
        from . import discovery as _disc
        fetch = fetch_fn or _real_fetch
        runs: List[Dict[str, Any]] = []
        for sid, cfg in (s_cfg or {}).items():
            dcfg = (cfg or {}).get("disco") or {}
            if not dcfg.get("enabled"):
                continue
            root = dcfg.get("root_url")
            if not root:
                continue

            def _enqueue(url: str, _sid: str = sid) -> int:
                r = (runners or {}).get(_sid)
                if r is None:
                    return 0
                try:
                    r.load_urls([url])
                    return 1
                except Exception:
                    return 0

            def _seen(u: str, _sid: str = sid) -> bool:
                try:
                    return bool(_disc._already_seen(_sid, [u]))
                except Exception:
                    return False

            out = dtr.run_discovery_triage(
                root, fetch_fn=fetch, enqueue_fn=_enqueue, seen_fn=_seen,
                content_match_fn=_content_match_fn(dcfg.get("url_pattern")),
                budget=_budget_from_cfg(dcfg),
                max_enqueue=int(dcfg.get("max_enqueue") or 0),
                off_switch_fn=off_switch_fn)

            # record the queued URLs as seen so the next pass doesn't re-queue them.
            try:
                _disc._record_seen(sid, list(out.get("high") or []))
            except Exception:
                pass

            rec = {
                "site_id": sid, "root": out.get("root", root),
                "host": out.get("host", ""),
                "enumerated": out.get("enumerated", 0),
                "enqueued": out.get("enqueued", 0),
                "review": len(out.get("review") or []),
                "reject": out.get("reject", 0),
                "enum_halted": out.get("enum_halted", False),
                "enum_halt_reason": out.get("enum_halt_reason", ""),
                "inert": out.get("inert", False),
            }
            _persist(rec)
            runs.append(rec)
        return {"ran": True, "reason": "ok", "sites": len(runs), "runs": runs}
    except Exception as e:
        return {"ran": False, "reason": f"error:{type(e).__name__}",
                "sites": 0, "runs": []}
