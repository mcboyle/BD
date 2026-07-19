"""Drift → AI repair candidate (Track F · F3.2).

When a site's selectors stop matching (selector_drift flags it stale), this
module asks the AI assist diff-repair path to propose replacement selectors
against the failing page's DOM and lands them as a REVIEW-ONLY draft —
exactly the template-review lane the inventory already surfaces. The
operator still promotes by hand; nothing here ever enables a template or
edits an enabled one.

Hard guarantees (all load-bearing):

  • SUGGESTION-ONLY. Repairs are written via dom_analyzer.pin_candidate,
    which forces status="draft_review_required" + review_required=True and
    runs the unconditional redact_artifact chokepoint. The enabled template
    is never read-modified-written here.

  • AI-DOWN = INERT. ai_status() is checked first; if AI assist is disabled
    or unreachable, the proposal is a silent no-op (no draft, no error). The
    download pipeline is never blocked or slowed — this lives in a bg sweep,
    off the hot extractor path.

  • TOGGLE-GATED, DEFAULT OFF. scheduled_drift_repair no-ops unless
    automation.drift_repair_enabled is on. Registering the bg task is
    behaviour-neutral (one global_config read per cadence) — mirrors the
    lifecycle.drift_sweep / template_canary.daily precedent.

  • FAIL-SOFT. Any error degrades to a quiet skip.

Page context (the DOM the AI repairs against) comes from an injected
``dom_provider(site_id) -> ctx|None``. The default provider
(``_default_dom_provider``, v3.66.512) builds it from the capture store —
the site's most-recent captured DOM, gated through F2 redaction — and
returns None only when there is no usable captured DOM (-> skip). The
proposal/gating/draft-write logic is fully exercisable in-sandbox via
injected ai_fn / status_fn / dom_provider.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ENABLE_KEY = "automation.drift_repair_enabled"
_PROVENANCE_PREFIX = "drift_repair"


# ── Item 4: last-sweep persistence (value-free; counts + ts + site_ids) ───────
def _last_run_path(state_file=None) -> Path:
    if state_file:
        return Path(state_file)
    # Runtime state -- resolve under the install/runtime dir (honouring
    # BD_INSTALL_DIR, like the sqlite db) so a sweep never writes into the source
    # tree (tests set BD_INSTALL_DIR to a tmp dir; in production it is the
    # deployed tree). Falls back to PROJECT_ROOT when unset.
    import os as _os
    install = _os.environ.get("BD_INSTALL_DIR")
    if install:
        base = Path(install)
    else:
        from .template_registry import PROJECT_ROOT
        base = Path(PROJECT_ROOT)
    return base / "reports" / "drift_repair_last.json"


def _persist_last_run(result: Dict[str, Any], site_ids, state_file=None) -> Dict[str, Any]:
    """Persist a sweep result so the GUI can show "last ran at X". Value-free:
    counts + a timestamp + the considered site_ids only (no secrets/paths).
    Never raises."""
    rec = {
        "ts": time.time(),
        "ran": bool(result.get("ran")),
        "considered": int(result.get("considered", 0) or 0),
        "repaired": int(result.get("repaired", 0) or 0),
        "skipped": int(result.get("skipped", 0) or 0),
        "site_ids": [str(s) for s in (site_ids or [])][:200],
    }
    try:
        p = _last_run_path(state_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass
    return rec


def read_last_run(state_file=None) -> Optional[Dict[str, Any]]:
    """Read the persisted last-sweep record, or None if none/unreadable."""
    try:
        return json.loads(_last_run_path(state_file).read_text(encoding="utf-8"))
    except Exception:
        return None


# ── enable gate (fail-safe OFF) ──────────────────────────────────────────────
def _enabled() -> bool:
    try:
        from . import global_config
        return bool(global_config.get(ENABLE_KEY, False))
    except Exception:
        return False


# ── context helpers ──────────────────────────────────────────────────────────
def _load_reviewed_selectors(host: str, reviewed_dir) -> List[str]:
    """Best-effort: gather the still-configured selectors for a host from its
    reviewed template, as 'working_selectors' context for the AI. Returns []
    on any miss (no template, unreadable) — context is optional."""
    try:
        from . import dom_analyzer as _da  # for _safe_host
        safe = _da._safe_host(host)
    except Exception:
        safe = str(host)
    out: List[str] = []
    try:
        rd = Path(reviewed_dir)
        for cand in (rd / f"{safe}.template.json", rd / f"{host}.template.json"):
            if cand.is_file():
                doc = json.loads(cand.read_text("utf-8"))
                sels = (doc or {}).get("selectors") or {}
                for role_map in sels.values():
                    if isinstance(role_map, dict):
                        for v in role_map.values():
                            if isinstance(v, str) and v:
                                out.append(v)
                    elif isinstance(role_map, str) and role_map:
                        out.append(role_map)
                break
    except Exception:
        return []
    return out


# ── core proposal (pure-ish; AI + write injected for tests) ──────────────────
def propose_repairs(
    host: str,
    *,
    broken_selectors: List[str],
    working_selectors: Optional[List[str]] = None,
    dom_excerpt: str,
    page_url: str = "",
    drafts_dir,
    ai_fn: Optional[Callable] = None,
    status_fn: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Ask the AI to repair the broken selectors against ``dom_excerpt`` and
    land each proposed replacement as a review-only draft candidate.

    Returns {ok, repairs: int, skipped?: str, draft_file?: str,
             proposed?: [...]}. Never raises; never enables a template.
    """
    broken = [s for s in (broken_selectors or []) if s]
    if not broken:
        return {"ok": True, "repairs": 0, "note": "no broken selectors"}

    # 1) AI availability gate — down/disabled => inert no-op.
    try:
        st = (status_fn or _default_status)()
        if not (isinstance(st, dict) and st.get("ok")):
            return {"ok": True, "repairs": 0, "skipped": "ai_unavailable"}
    except Exception:
        return {"ok": True, "repairs": 0, "skipped": "ai_unavailable"}

    # 2) Ask for repairs.
    try:
        repair_fn = ai_fn or _default_diff_repair
        res = repair_fn(broken, working_selectors or [],
                        (dom_excerpt or "")[:16000], page_url=page_url)
    except Exception as e:
        return {"ok": True, "repairs": 0, "skipped": "ai_error",
                "error": str(e)[:200]}
    if not (isinstance(res, dict) and res.get("ok")):
        return {"ok": True, "repairs": 0, "skipped": "ai_error",
                "error": (res or {}).get("error", "")[:200]
                if isinstance(res, dict) else ""}

    repairs = [r for r in (res.get("repairs") or []) if isinstance(r, dict)]
    if not repairs:
        return {"ok": True, "repairs": 0, "note": "model proposed no repairs"}

    # 3) Land each as a REVIEW-ONLY draft candidate (never enables).
    written = 0
    draft_file = None
    proposed: List[Dict[str, Any]] = []
    try:
        from . import dom_analyzer as _da
    except Exception:
        return {"ok": True, "repairs": 0, "skipped": "writer_unavailable"}
    for r in repairs:
        new_sel = (r.get("new_selector") or "").strip()
        role = r.get("role") or "row_selectors"
        if not new_sel:
            continue
        try:
            out = _da.pin_candidate(
                new_sel, role, host=host, drafts_dir=drafts_dir,
                name="repair",
                capture_name=f"{_PROVENANCE_PREFIX}:{host}")
            if out.get("ok"):
                # belt-and-braces: a candidate must never be enabled
                if out.get("enabled"):
                    continue
                written += 1
                draft_file = out.get("file")
                proposed.append({
                    "old_selector": r.get("old_selector", ""),
                    "new_selector": new_sel,
                    "role": role,
                    "confidence": r.get("confidence", 0),
                })
        except Exception:
            continue
    return {"ok": True, "repairs": written, "draft_file": draft_file,
            "proposed": proposed}


# ── default seams (kept thin so tests can inject) ────────────────────────────
def _default_status() -> Dict[str, Any]:
    from . import aiassist
    return aiassist.ai_status()


def _default_diff_repair(broken, working, dom_excerpt, *, page_url=""):
    from . import aiassist
    return aiassist.diff_repair(broken, working, dom_excerpt,
                                page_url=page_url)


_MAX_DOM_EXCERPT = 24000


def _site_cfg_for(site_id: str) -> Dict[str, Any]:
    """Live site config for ``site_id`` ({} if unavailable). Lazy import of
    app.s_cfg to avoid an import cycle; never raises."""
    try:
        import importlib
        s_cfg = getattr(importlib.import_module("bulk_downloader.app"), "s_cfg", {})
        return dict((s_cfg or {}).get(site_id) or {})
    except Exception:
        return {}


def _host_for_cfg(cfg: Dict[str, Any]) -> str:
    import urllib.parse as _u
    for k in ("login_url", "start_url", "base_url", "url", "homepage"):
        v = (cfg.get(k) or "").strip()
        if v.startswith(("http://", "https://")):
            try:
                return _u.urlsplit(v).netloc or ""
            except Exception:
                pass
    return ""


def _configured_selectors(cfg: Dict[str, Any]) -> List[str]:
    """The site's run-driving selectors to probe against a captured DOM."""
    out: List[str] = []
    for k in ("trigger_selector", "dl_selector"):
        v = (cfg.get(k) or "").strip()
        if v:
            out.append(v)
    ds = cfg.get("dismiss_selectors")
    if isinstance(ds, str):
        out += [x.strip() for x in ds.split(",") if x.strip()]
    elif isinstance(ds, (list, tuple)):
        out += [str(x).strip() for x in ds if str(x).strip()]
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def _latest_capture_for_host(host: str, *, root=None) -> Optional[Dict[str, Any]]:
    """Newest capture (by file mtime) whose page-context host matches ``host``,
    as a loaded capture dict. None when nothing matches. Best-effort per item."""
    if not host:
        return None
    from . import dom_analyzer as _da
    best, best_mt = None, -1.0
    for c in _da.list_captures(root=root):
        p = _da.resolve_capture(c.get("name", ""), root=root)
        if p is None:
            continue
        try:
            cap = _da.load_capture(p)
        except Exception:
            continue
        if _da.capture_host(cap) != host:
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            mt = 0.0
        if mt > best_mt:
            best, best_mt = cap, mt
    return best


def _default_dom_provider(site_id: str, *, cfg=None, captures_root=None):
    """Stash-side integration point: build repair context for a stale site from
    the capture store as ``{dom_excerpt, page_url, host, broken_selectors,
    working_selectors}`` — or None when there's no usable captured DOM.

    Resolves the site's host + configured selectors (from live config, or the
    injected ``cfg`` in tests), finds the newest capture for that host, gates it
    through the F2 redaction (``dom_analyzer.redacted_dom`` — fails closed on
    residual secrets), and splits the configured selectors into broken (0
    matches in the captured DOM) vs working. The DOM excerpt is the REDACTED
    html, bounded to keep the AI prompt sane. Review-only / fail-open: returns
    None (sweep skips) rather than raising on any missing piece. The ``cfg`` /
    ``captures_root`` params are keyword-only so the live ``provider(sid)`` call
    is unchanged.
    """
    if cfg is None:
        cfg = _site_cfg_for(site_id)
    host = _host_for_cfg(cfg)
    if not host:
        return None
    selectors = _configured_selectors(cfg)
    cap = _latest_capture_for_host(host, root=captures_root)
    if cap is None:
        return None
    try:
        from . import dom_analyzer as _da
        gate = _da.redacted_dom(cap)
        if not gate.get("ok") or not gate.get("has_dom"):
            return None
        html = gate.get("html") or ""
        page_url = cap.get("url") or cap.get("page_url") or ""
        broken: List[str] = []
        working: List[str] = []
        for sel in selectors:
            try:
                res = _da.test_selectors(html, [sel])
                count = int(res[0].get("count", 0)) if res else 0
            except Exception:
                count = 0
            (working if count > 0 else broken).append(sel)
        return {
            "dom_excerpt": html[:_MAX_DOM_EXCERPT],
            "page_url": page_url,
            "host": host,
            "broken_selectors": broken,
            "working_selectors": working,
        }
    except Exception:
        return None


# ── bg sweep (toggle-gated) ──────────────────────────────────────────────────
def scheduled_drift_repair(
    *,
    dom_provider: Optional[Callable] = None,
    drafts_dir=None,
    reviewed_dir=None,
    ai_fn: Optional[Callable] = None,
    status_fn: Optional[Callable] = None,
    force: bool = False,
    state_file=None,
) -> Dict[str, Any]:
    """Sweep selector_drift's flagged-stale sites and propose AI repairs for
    those with available page context. NO-OPS unless the opt-in flag is on (or
    ``force`` is set, for an operator run-now that does not flip the daily
    automation). Never raises. On a real run the value-free result is persisted
    for the GUI (read_last_run). Returns {ran, considered, repaired, skipped,
    reason?}."""
    try:
        if not _enabled() and not force:
            return {"ran": False, "reason": "disabled"}
        from . import selector_drift as _sd
        if drafts_dir is None or reviewed_dir is None:
            from . import template_manager as _tm
            drafts_dir = drafts_dir or _tm.DRAFTS_DIR
            reviewed_dir = reviewed_dir or _tm.REVIEWED_DIR
        provider = dom_provider or _default_dom_provider

        stale = [s for s in _sd.status_all()
                 if isinstance(s, dict) and s.get("flagged_stale")]
        considered = 0
        repaired = 0
        skipped = 0
        considered_ids: List[str] = []
        for s in stale:
            considered += 1
            sid = s.get("site_id") or ""
            considered_ids.append(str(sid))
            try:
                ctx = provider(sid)
            except Exception:
                ctx = None
            if not ctx or not isinstance(ctx, dict):
                skipped += 1
                continue
            host = ctx.get("host") or sid
            broken = ctx.get("broken_selectors")
            if not broken:
                ls = s.get("last_selector") or ""
                broken = [ls] if ls else []
            working = ctx.get("working_selectors")
            if working is None:
                working = _load_reviewed_selectors(host, reviewed_dir)
            res = propose_repairs(
                host,
                broken_selectors=broken,
                working_selectors=working,
                dom_excerpt=ctx.get("dom_excerpt", ""),
                page_url=ctx.get("page_url", ""),
                drafts_dir=drafts_dir,
                ai_fn=ai_fn, status_fn=status_fn)
            if res.get("repairs", 0) > 0:
                repaired += 1
            else:
                skipped += 1
        result = {"ran": True, "considered": considered,
                  "repaired": repaired, "skipped": skipped}
        _persist_last_run(result, considered_ids, state_file=state_file)
        return result
    except Exception as e:
        return {"ran": False, "reason": f"error:{type(e).__name__}"}
