"""cockpit_templates.py — Template Intelligence (v3.66.110).

Read-only, recognition-only analysis of the per-site TEMPLATES (the `learned`
blocks in sites_config.json) and the download-decision SCORING model. It tells the
operator WHY login/download detection works or fails, WHAT changed, and WHAT to
review — without ever crossing the posture line.

Boundaries (enforced by construction, asserted by tests):
- reads sites_config.json READ-ONLY; never writes it; no corpus writes
- credential and signing VALUES are never read or echoed — only field NAMES /
  CSS selectors / query-stripped paths
- no request replay, no captured-token reuse, no generated browser replay scripts
- no live page fetch and NO model/network call (uses the pure scorer only)
- suggested template updates are returned as DATA only — never auto-applied, never
  auto-promoted, never auto-retire debt

This module is intentionally separate from cockpit_core's recognition allowlist:
it adds no runnable tools and imports nothing that executes captures.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from tools.cockpit_core import redact  # reuse the same redaction the viewer uses

import contextvars as _contextvars
import functools as _functools

# ── request-scoped memo (MC-memoize, v3.66.463) ──────────────────────────────
# operator_mission_control() rolls up ~7 read-only sub-views, several of which
# recompute the SAME expensive primitives at multiple depths -- e.g. both
# site_readiness AND the review queue recompute login/video template health ->
# a 2x+ fan-out (~1.5s live). _mc_scope establishes a per-request memo around the
# whole rollup; the @_mc_memoized sub-aggregations then compute ONCE per request
# and serve cached results to repeat callers within that scope. OUTSIDE a scope
# (standalone endpoints) the wrapped functions are unchanged -- they recompute
# fresh, so no cross-request staleness. Only zero-arg calls are memoized; the
# results are read-only and never mutated by callers, so sharing the object is
# safe.
_MC_MEMO: "_contextvars.ContextVar" = _contextvars.ContextVar("_mc_memo", default=None)


def _mc_scope(fn):
    """Wrap an aggregator so its body runs inside one fresh request-memo."""
    @_functools.wraps(fn)
    def _wrap(*args, **kwargs):
        token = _MC_MEMO.set({})
        try:
            return fn(*args, **kwargs)
        finally:
            _MC_MEMO.reset(token)
    return _wrap


def _mc_memoized(fn):
    """Memoize a zero-arg read-only sub-aggregation for the active _mc_scope."""
    @_functools.wraps(fn)
    def _wrap(*args, **kwargs):
        memo = _MC_MEMO.get()
        if memo is None or args or kwargs:
            return fn(*args, **kwargs)
        key = fn.__name__
        if key not in memo:
            memo[key] = fn(*args, **kwargs)
        return memo[key]
    return _wrap


# ── safety helpers ───────────────────────────────────────────────────────────

def _strip_url_query(u: str) -> str:
    """Drop query + fragment from a URL — signing material lives there and must
    never be echoed. Returns scheme://host/path only."""
    if not isinstance(u, str) or not u:
        return ""
    try:
        s = urlsplit(u)
        return urlunsplit((s.scheme, s.netloc, s.path, "", ""))
    except Exception:
        return ""


def _safe(s: Any) -> str:
    """Redact then return a string safe to surface (selectors, labels)."""
    return redact(s if isinstance(s, str) else str(s or ""))


# ── read-only config access ──────────────────────────────────────────────────

def _config_path() -> Path:
    return Path(os.environ.get("BD_SITES_CONFIG_PATH", "sites_config.json"))


def _load_sites_config() -> List[Dict[str, Any]]:
    """Read sites_config.json READ-ONLY. The on-disk format is a bare list of
    site dicts (see sites_config.example.json). Missing/malformed → []."""
    p = _config_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):  # tolerate {"sites": [...]} too
        data = data.get("sites", [])
    return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []


def _site_id(cfg: Dict[str, Any]) -> str:
    return str(cfg.get("id") or cfg.get("name") or "")


def _learned(cfg: Dict[str, Any]) -> Dict[str, Any]:
    lr = cfg.get("learned")
    return lr if isinstance(lr, dict) else {}


def _drift_status(site_id: str) -> Dict[str, Any]:
    """selector_drift state for a site, guarded (the DB may be absent in some
    environments). Read-only."""
    try:
        from bulk_downloader import selector_drift as sd
        return sd.status_for(site_id)
    except Exception:
        return {"site_id": site_id, "consecutive_failures": 0,
                "flagged_stale": False, "last_success_ts": None,
                "last_failure_ts": None, "unavailable": True}


def _selector_confidence(selectors: List[Any], stale: bool, fails: int) -> Dict[str, Any]:
    """A transparent, DEFINED selector-confidence signal (not an objective
    measure): more distinct selectors + no drift → higher. Every input shown."""
    n = len([s for s in (selectors or []) if s])
    base = min(n, 4) / 4.0            # 0..1 from selector breadth (caps at 4)
    penalty = 0.5 if stale else min(fails * 0.1, 0.4)
    score = round(max(0.0, base - penalty), 2)
    band = "high" if score >= 0.7 else "medium" if score >= 0.4 else "low"
    return {"score": score, "band": band,
            "inputs": {"distinct_selectors": n, "drift_stale": stale,
                       "consecutive_failures": fails},
            "_note": "DEFINED signal: selector breadth (caps at 4) minus a drift "
                     "penalty. Not an objective measure; shown so you can judge it."}


# ── corpus-derived rendition signal (read-only, honest) ──────────────────────

def _renditions_for_site(site_id: str) -> List[str]:
    """Rendition labels seen for a site in the corpus, if any. Honest: returns
    what the corpus actually carries — often empty until captures are ingested."""
    try:
        from tools.cockpit_core import _corpus, _site_of_entry
        labels = set()
        for e in _corpus():
            if _site_of_entry(e) != site_id:
                continue
            for key in ("rendition", "renditions", "highest_rendition", "label"):
                v = e.get(key)
                if isinstance(v, str) and v:
                    labels.add(v)
                elif isinstance(v, list):
                    labels.update(x for x in v if isinstance(x, str))
        return sorted(labels)
    except Exception:
        return []


# ── PRIORITY 1a — Video Template Health ──────────────────────────────────────

@_mc_memoized
def video_template_health() -> Dict[str, Any]:
    """Per-site health of the video/download template (the `learned.download`
    block) + drift history. Read-only; recognition-only. Tells you, per site,
    whether the template exists, how confident its selectors are, whether a
    two-step (trigger→reveal) flow is modelled, what renditions the corpus has
    seen, and whether selectors have drifted."""
    sites = _load_sites_config()
    rows = []
    for cfg in sites:
        sid = _site_id(cfg)
        dl = _learned(cfg).get("download") if isinstance(_learned(cfg).get("download"), dict) else {}
        row_sel = dl.get("row_selectors") or []
        trig = dl.get("trigger_selectors") or []
        drift = _drift_status(sid)
        stale = bool(drift.get("flagged_stale"))
        fails = int(drift.get("consecutive_failures") or 0)
        renditions = _renditions_for_site(sid)
        rows.append({
            "site": _safe(sid),
            "template_present": bool(dl and row_sel),
            "row_selector_count": len([s for s in row_sel if s]),
            "url_attribute": _safe(dl.get("url_attribute", "")),
            "two_step_flow": bool(trig),
            "two_step_trigger_count": len([s for s in trig if s]),
            "selector_confidence": _selector_confidence(row_sel, stale, fails),
            "highest_rendition_seen": (renditions[-1] if renditions else None),
            "renditions_in_corpus": renditions or None,
            "rendition_signal": ("corpus" if renditions else "none_yet"),
            "drift": {
                "flagged_stale": stale,
                "consecutive_failures": fails,
                "last_success_ts": drift.get("last_success_ts"),
                "last_failure_ts": drift.get("last_failure_ts"),
                "last_selector": _safe(drift.get("last_selector", "")),
                "last_url": _strip_url_query(drift.get("last_url", "")),
                "history_available": not drift.get("unavailable", False),
            },
        })
    # missing-template sites first, then stale, then by failures
    rows.sort(key=lambda r: (r["template_present"], not r["drift"]["flagged_stale"],
                             -r["drift"]["consecutive_failures"]))
    return {
        "sites": rows,
        "site_count": len(rows),
        "missing_templates": sum(1 for r in rows if not r["template_present"]),
        "stale": sum(1 for r in rows if r["drift"]["flagged_stale"]),
        "config_present": _config_path().is_file(),
        "_note": "Read-only health of the video/download templates (learned.download "
                 "blocks) + selector-drift history. Recognition-only: no live fetch, "
                 "no replay, no model call. Rendition signal comes from the corpus and "
                 "is empty until captures are ingested — honest, not a gap to hide.",
    }


# ── PRIORITY 1b — Download Decision Explainer ────────────────────────────────

def _sample_candidates() -> List[Dict[str, Any]]:
    """A small, clearly-labelled SAMPLE candidate set so the cockpit can show how
    the scorer ranks options even with no live page. Synthetic illustration only."""
    return [
        {"text": "Download 4K", "href": "https://cdn.example.com/v/abc/2160p.mp4",
         "tag": "a", "data_download": "1"},
        {"text": "Download 1080p", "href": "https://cdn.example.com/v/abc/1080p.mp4",
         "tag": "a", "data_download": "1"},
        {"text": "Download 720p", "href": "https://cdn.example.com/v/abc/720p.mp4",
         "tag": "a"},
        {"text": "Share", "href": "https://example.com/share/abc", "tag": "a"},
        {"text": "Trailer", "href": "https://cdn.example.com/v/abc/trailer_480p.mp4",
         "tag": "a"},
    ]


def download_decision_explainer(candidates: Optional[List[Dict[str, Any]]] = None,
                                min_score: int = 25) -> Dict[str, Any]:
    """Explain the download-candidate decision: for each candidate, the score, the
    reasons behind it, the resolution tier; then which option is chosen and which
    are rejected and why. Uses the project's PURE scorer (no live page, no model,
    no network, no replay). If no candidates are given, a labelled sample set is
    used so the model is demonstrable. Read-only; recognition-only."""
    try:
        from bulk_downloader.heuristic_scoring import score_candidate, rank_candidates
    except Exception as e:  # pragma: no cover - defensive
        return {"error": f"scorer unavailable: {e}", "candidates": []}

    is_sample = candidates is None
    cands = candidates if isinstance(candidates, list) else _sample_candidates()

    scored = []
    for c in cands:
        r = score_candidate(c, fingerprint=None) if isinstance(c, dict) else {
            "score": 0, "reasons": [], "resolution_tier": 0, "estimated_size_bytes": 0}
        scored.append({
            # echo only safe, query-stripped fields — never raw signing in href
            "label": _safe(c.get("text", "")) if isinstance(c, dict) else "",
            "url": _strip_url_query(c.get("href", "")) if isinstance(c, dict) else "",
            "tag": _safe(c.get("tag", "")) if isinstance(c, dict) else "",
            "score": r["score"],
            "resolution_tier": r["resolution_tier"],
            "estimated_size_bytes": r["estimated_size_bytes"],
            "reasons": [{"delta": d, "label": _safe(lbl)} for d, lbl in r.get("reasons", [])],
            "passes_threshold": r["score"] >= min_score,
        })

    ranked = rank_candidates([c for c in cands if isinstance(c, dict)],
                             fingerprint=None, min_score=min_score)
    chosen = None
    if ranked:
        top = ranked[0]
        chosen = {"label": _safe(top.get("text", "")),
                  "url": _strip_url_query(top.get("href", "")),
                  "score": top.get("score"),
                  "resolution_tier": top.get("resolution_tier"),
                  "why": [_safe(lbl) for _, lbl in top.get("score_reasons", [])]}
    rejected = [s for s in scored if not (chosen and s["url"] == chosen["url"] and s["label"] == chosen["label"])]

    return {
        "is_sample": is_sample,
        "min_score": min_score,
        "candidates": sorted(scored, key=lambda s: -s["score"]),
        "chosen": chosen,
        "rejected": rejected,
        "_method": "Each candidate is scored by the project's pure heuristic scorer "
                   "(score_candidate) — text/href/data-attr signals + resolution tier; "
                   "ranked by score, then resolution tier, then size. The HIGHEST "
                   "rendition is still chosen from LIVE state at download time; this "
                   "explainer narrates the same scoring on recorded/sample candidates. "
                   "No live fetch, no model, no network, no replay. Read-only.",
        "_note": ("Sample candidates shown (no live page in this context)." if is_sample
                  else "Explained the supplied candidates."),
    }


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 2 — Login Template Intelligence (v3.66.111). Read-only, recognition-only.
#
# Answers, per site: why did login fail? which selector broke? did the form change?
# was MFA/captcha added? was the session expired? should I update the template?
# WITHOUT submitting credentials, replaying anything, or auto-applying suggestions.
# Credential VALUES are never read or echoed — only field names/selectors/markers.
# ═════════════════════════════════════════════════════════════════════════════

# the invisible-captcha token markers the login path already knows about
_CAPTCHA_MARKERS = ("cf-turnstile-response", "h-captcha-response",
                    "g-recaptcha-response", "turnstile", "hcaptcha", "recaptcha")


def _login_block(cfg: Dict[str, Any]) -> Dict[str, Any]:
    lg = _learned(cfg).get("login")
    return lg if isinstance(lg, dict) else {}


def _cookie_quality(site_id: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Guarded read of the site's cookie/session quality. Read-only."""
    try:
        from bulk_downloader import cookie_quality as cq
        result = cq.score(site_id, s_cfg_entry=cfg) or {}
        if (result.get("measurement_status") == "unmeasured"
                or result.get("score") is None):
            return {**result, "unavailable": True}
        return result
    except Exception:
        return {"unavailable": True}


def _recent_login_success_rate(site_id: str) -> Optional[float]:
    try:
        from bulk_downloader import cookie_quality as cq
        return cq._recent_success_rate(site_id)
    except Exception:
        return None


def _login_selector_counts(lb: Dict[str, Any]) -> Dict[str, int]:
    def _n(key_variants):
        for k in key_variants:
            v = lb.get(k)
            if isinstance(v, list):
                return len([s for s in v if s])
        return 0
    return {
        "user_field": _n(("user_field", "username", "username_selector")),
        "pass_field": _n(("pass_field", "password", "password_selector")),
        "submit_btn": _n(("submit_btn", "submit", "submit_selector")),
    }


def _captcha_in_config(cfg: Dict[str, Any], lb: Dict[str, Any]) -> bool:
    blob = (json.dumps(cfg) + json.dumps(lb)).lower()
    return any(m in blob for m in _CAPTCHA_MARKERS)


@_mc_memoized
def login_template_health() -> Dict[str, Any]:
    """Per-site health of the login template (the `learned.login` block) + session
    freshness + recent login outcomes. Read-only; recognition-only. Tells you, per
    site, whether the login template exists, how confident its selectors are,
    whether the session is fresh, the recent success rate, and whether MFA/captcha
    has been observed."""
    sites = _load_sites_config()
    rows = []
    for cfg in sites:
        sid = _site_id(cfg)
        lb = _login_block(cfg)
        counts = _login_selector_counts(lb)
        present = bool(lb and counts["user_field"] and counts["pass_field"])
        cq = _cookie_quality(sid, cfg)
        rate = _recent_login_success_rate(sid)
        all_sel = []
        for k in ("user_field", "pass_field", "submit_btn", "username", "password",
                  "submit"):
            v = lb.get(k)
            if isinstance(v, list):
                all_sel += v
        drift = _drift_status(sid)  # selector_drift is download-side; informational
        conf = _selector_confidence(all_sel, False, 0)
        rows.append({
            "site": _safe(sid),
            "template_present": present,
            "login_url_set": bool(cfg.get("login_url")),
            "selector_counts": counts,
            "selector_confidence": conf,
            "session": {
                "cookie_score": cq.get("score"),
                "band": cq.get("band") or cq.get("label"),
                "expired": cq.get("expired"),
                "available": not cq.get("unavailable", False),
                "measurement_status": cq.get("measurement_status"),
                "suggested_action": cq.get("suggested_action"),
            },
            "recent_success_rate": (round(rate, 2) if isinstance(rate, (int, float)) else None),
            "mfa_captcha_indicated": _captcha_in_config(cfg, lb),
            "_history_available": not drift.get("unavailable", False),
        })
    # An unknown cookie score needs operator attention; sorting null as 999
    # used to bury it after every measured score, recreating wrong-green at the
    # presentation layer.
    rows.sort(key=lambda r: (
        r["session"]["cookie_score"] is not None,
        r["template_present"],
        (r["session"]["cookie_score"] or 0),
    ))
    return {
        "sites": rows,
        "site_count": len(rows),
        "missing_templates": sum(1 for r in rows if not r["template_present"]),
        "mfa_captcha_sites": sum(1 for r in rows if r["mfa_captcha_indicated"]),
        "config_present": _config_path().is_file(),
        "_note": "Read-only login-template health (learned.login) + cookie/session "
                 "quality + recent login outcomes. No credential values are read or "
                 "shown; no login is attempted here. Recognition-only.",
    }


def _relogin_history(site_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Recent relogin events for a site (cookie_relogin_log), guarded. Read-only —
    timestamps + outcomes only, never credential values."""
    try:
        from bulk_downloader import db as _db
        with _db.db_conn() as cx:
            rs = cx.execute(
                "SELECT ts, cookie_score, action, outcome FROM cookie_relogin_log "
                "WHERE site_id = ? ORDER BY ts DESC LIMIT ?", (site_id, limit)).fetchall()
        return [{"ts": r["ts"], "cookie_score": r["cookie_score"],
                 "action": _safe(r["action"] or ""), "outcome": _safe(r["outcome"] or "")}
                for r in rs]
    except Exception:
        return []


def login_history() -> Dict[str, Any]:
    """Recent login/relogin success-failure history per site, from the relogin log.
    Read-only; timestamps + outcomes only."""
    sites = _load_sites_config()
    out = []
    for cfg in sites:
        sid = _site_id(cfg)
        ev = _relogin_history(sid)
        if not ev:
            continue
        ok = sum(1 for e in ev if "ok" in (e["outcome"] or "").lower()
                 or "success" in (e["outcome"] or "").lower())
        out.append({"site": _safe(sid), "events": ev,
                    "recent_ok": ok, "recent_total": len(ev),
                    "last": ev[0] if ev else None})
    return {"sites": out, "site_count": len(out),
            "history_available": bool(out),
            "_note": "Recent relogin outcomes from cookie_relogin_log (read-only). "
                     "Empty until the deployment has logged relogins. No credentials."}


def login_drift_report() -> Dict[str, Any]:
    """Per-site login drift signals, classified. Some categories are detectable from
    state (cookie expired, captcha observed, login selectors failing); others
    (field moved, success-marker changed) require a Safe Dry Run / fresh capture to
    confirm — and are honestly marked as such rather than guessed. Read-only."""
    sites = _load_sites_config()
    rows = []
    for cfg in sites:
        sid = _site_id(cfg)
        lb = _login_block(cfg)
        cq = _cookie_quality(sid, cfg)
        rate = _recent_login_success_rate(sid)
        signals = {
            "cookie_expired": bool(cq.get("expired")) if not cq.get("unavailable") else None,
            "login_failing": (rate is not None and rate < 0.5),
            "mfa_captcha_present": _captcha_in_config(cfg, lb),
            # these require a live DOM diff — honestly not inferable from state
            "user_field_changed": "needs_dry_run",
            "pass_field_changed": "needs_dry_run",
            "submit_changed": "needs_dry_run",
            "form_moved": "needs_dry_run",
            "success_marker_changed": "needs_dry_run",
        }
        any_state = bool(signals["cookie_expired"]) or signals["login_failing"]
        rows.append({"site": _safe(sid), "signals": signals,
                     "state_drift_suspected": any_state,
                     "recent_success_rate": (round(rate, 2) if isinstance(rate, (int, float)) else None)})
    rows.sort(key=lambda r: not r["state_drift_suspected"])
    return {
        "sites": rows, "site_count": len(rows),
        "_note": "Login drift classified from observable STATE (cookie expiry, "
                 "success rate, captcha). Field/form/marker changes need a Safe Dry "
                 "Run or fresh capture to confirm — marked 'needs_dry_run', never "
                 "guessed. Read-only.",
    }


def _sample_login_html() -> str:
    return ("<form action='/login' method='post'>"
            "<input type='text' name='username' autocomplete='username'>"
            "<input type='password' name='password'>"
            "<button type='submit'>Sign in</button>"
            "<div class='cf-turnstile'></div></form>")


def login_dry_run(html: Optional[str] = None) -> Dict[str, Any]:
    """SAFE login dry-run: given a login page's HTML, identify which login fields
    and buttons are present and report confidence — WITHOUT submitting anything and
    WITHOUT reading or echoing any credential values. If no HTML is supplied a
    labelled sample is analysed (the live page-open leg runs on the deployment via
    the existing authorized path; this analysis is the recognition step).

    Recognition-only: presence/structure checks; no credential submission, no value
    echo, no network here."""
    is_sample = html is None
    h = (html if isinstance(html, str) else _sample_login_html())
    low = h.lower()
    # presence checks ONLY — never capture input values
    has_user = ("autocomplete='username'" in low or 'autocomplete="username"' in low
                or "name='username'" in low or 'name="username"' in low
                or "name='login'" in low or "type='email'" in low or 'type="email"' in low)
    has_pass = "type='password'" in low or 'type="password"' in low
    has_submit = ("type='submit'" in low or 'type="submit"' in low
                  or ">sign in<" in low or ">log in<" in low or ">login<" in low)
    has_form = "<form" in low
    captcha = next((m for m in _CAPTCHA_MARKERS if m in low), None)
    found = sum([has_user, has_pass, has_submit])
    confidence = round(found / 3.0, 2)
    return {
        "is_sample": is_sample,
        "fields_identified": {
            "username_field": has_user, "password_field": has_pass,
            "submit_button": has_submit, "login_form": has_form,
        },
        "captcha_detected": bool(captcha),
        "captcha_kind": captcha,
        "confidence": confidence,
        "band": ("high" if confidence >= 0.99 else "partial" if confidence >= 0.34 else "low"),
        "would_submit": False,
        "_note": "Recognition only. Fields are identified by structure; NO credential "
                 "value is read or echoed and NOTHING is submitted. The live login is "
                 "only ever performed via the existing approved login path, not here. "
                 + ("Sample login form analysed." if is_sample else "Supplied HTML analysed."),
    }


def suggested_login_template_update(site: Optional[str] = None) -> Dict[str, Any]:
    """Data-only suggested login-template update for a site whose login looks
    drifted. Returns candidate selectors to consider — NEVER applied, NEVER
    promoted. The operator reviews and applies manually via the existing path."""
    sites = _load_sites_config()
    cfg = next((c for c in sites if _site_id(c) == site), None) if site else None
    suggestion = {
        "site": _safe(site or ""),
        "applies_automatically": False,
        "suggested": {
            "user_field": ["input[autocomplete='username']", "input[name='username']",
                           "input[type='email']"],
            "pass_field": ["input[type='password']", "input[autocomplete='current-password']"],
            "submit_btn": ["button[type='submit']", "input[type='submit']",
                           "button:has-text('Sign in')"],
        },
        "basis": "generic robust candidates from the login selector banks; refine "
                 "against a Safe Dry Run on the live form",
        "_note": "SUGGESTION ONLY. Not applied, not promoted, no debt retired. Review "
                 "in the queue and apply via the existing approved path.",
    }
    if cfg is None and site:
        suggestion["warning"] = "site not found in sites_config"
    return suggestion


def login_review_queue() -> Dict[str, Any]:
    """Read-only review queue: login templates that look like they need attention
    (missing, low confidence, drifted, or low success rate), each with a data-only
    suggestion pointer. The approve/reject workbench (state mutation) is Phase 4 —
    this is the derived list to act on. Read-only."""
    health = login_template_health()
    drift = {d["site"]: d for d in login_drift_report()["sites"]}
    items = []
    for s in health["sites"]:
        reasons = []
        if not s["template_present"]:
            reasons.append("no login template")
        if s["selector_confidence"]["band"] == "low":
            reasons.append("low selector confidence")
        d = drift.get(s["site"], {})
        if d.get("state_drift_suspected"):
            reasons.append("state drift suspected (cookie/success-rate)")
        if s["recent_success_rate"] is not None and s["recent_success_rate"] < 0.5:
            reasons.append("low recent success rate")
        if reasons:
            items.append({"site": s["site"], "reasons": reasons,
                          "has_suggestion": True})
    return {"queue": items, "count": len(items),
            "_note": "Read-only review queue derived from login health + drift. "
                     "Each item has a data-only suggested update; approve/reject "
                     "(Phase 4 workbench) is where changes are made — nothing is "
                     "applied automatically.",
            "_phase4": "approve/reject + side-by-side diffs land in the Template "
                       "Review Workbench (Phase 4)."}


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 3 — Template Drift Intelligence (v3.66.112). Read-only, recognition-only.
#
# Unifies video (Phase 1) + login (Phase 2) into one health view and adds drift
# classification + scoring. Answers: what changed? how often? which sites are
# becoming unstable? which templates can be trusted? Timelines/frequencies are
# FACTUAL event logs (not forecasts) — shown as-is, with an honest thin-data flag
# when there are too few events to read a trend. No fabrication, no projection.
# ═════════════════════════════════════════════════════════════════════════════

import datetime as _dt

# severity ordering (higher index = worse)
_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
_MIN_TREND_EVENTS = 8  # below this, frequency is shown but NOT called a trend


def classify_drift_severity(kind: str, *, template_missing: bool = False) -> str:
    """Rule-based drift severity. Pure; no guessing beyond the recorded signal."""
    if template_missing or kind in ("template_missing", "identity_change",
                                    "identity_and_rendition_change"):
        return "critical"
    if kind in ("selector_zero_match", "selector_stale", "login_failing"):
        return "high"
    if kind in ("cookie_expired", "captcha_added", "rendition_drift", "drift_verdict"):
        return "medium"
    return "low"


def _parse_ts(v: Any) -> str:
    """Normalise a timestamp/date to an ISO date prefix for sorting. Read-only."""
    if not v:
        return ""
    s = str(v)
    return s[:19] if "T" in s or ":" in s else s[:10]


def _site_drift_events(site_id: str, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect dated drift events for a site from the available sources:
    corpus drift_verdict entries, login relogin-log failures, and the download-side
    selector_drift latest-failure. Factual events only. Read-only."""
    events: List[Dict[str, Any]] = []
    # 1) corpus drift_verdict entries
    try:
        from tools.cockpit_core import _corpus, _site_of_entry
        for e in _corpus():
            if _site_of_entry(e) != site_id:
                continue
            if e.get("category") == "drift_verdict":
                sev = e.get("severity") or classify_drift_severity("drift_verdict")
                events.append({"ts": _parse_ts(e.get("date")), "kind": "drift_verdict",
                               "source": "corpus", "severity": sev,
                               "detail": _safe((e.get("subject") or "")[:80])})
    except Exception:
        pass
    # 2) login relogin-log failures
    for ev in _relogin_history(site_id, limit=25):
        out = (ev.get("outcome") or "").lower()
        if out and ("fail" in out or "error" in out):
            events.append({"ts": _parse_ts(ev.get("ts")), "kind": "login_relogin_fail",
                           "source": "relogin_log",
                           "severity": classify_drift_severity("login_failing"),
                           "detail": _safe(out)})
    # 3) download-side selector drift (latest failure, if stale)
    d = _drift_status(site_id)
    if d.get("flagged_stale") and d.get("last_failure_ts"):
        events.append({"ts": _parse_ts(d.get("last_failure_ts")),
                       "kind": "selector_stale", "source": "selector_drift",
                       "severity": classify_drift_severity("selector_stale"),
                       "detail": f"{d.get('consecutive_failures', 0)} consecutive failures"})
    events.sort(key=lambda x: x["ts"], reverse=True)
    return events


def drift_timeline() -> Dict[str, Any]:
    """Dated drift events across all sites, newest first. A factual log — honest
    even when sparse. Read-only."""
    sites = _load_sites_config()
    all_events = []
    for cfg in sites:
        sid = _site_id(cfg)
        for ev in _site_drift_events(sid, cfg):
            all_events.append({"site": _safe(sid), **ev})
    all_events.sort(key=lambda x: x["ts"], reverse=True)
    return {
        "events": all_events,
        "event_count": len(all_events),
        "sparse": len(all_events) < _MIN_TREND_EVENTS,
        "_note": "Factual drift-event log (corpus drift verdicts + login relogin "
                 "failures + stale download selectors). Newest first. With few "
                 "events this is a record, not a trend — frequency is reported but "
                 "not extrapolated. Read-only.",
    }


def drift_frequency() -> Dict[str, Any]:
    """Drift-event counts per site over recent windows. Factual counts; flagged as
    'too sparse to trend' below a threshold so no false trend is implied."""
    sites = _load_sites_config()
    today = _dt.date.today()
    rows = []
    total = 0
    for cfg in sites:
        sid = _site_id(cfg)
        evs = _site_drift_events(sid, cfg)
        total += len(evs)

        def _within(days):
            n = 0
            for e in evs:
                try:
                    d = _dt.date.fromisoformat(e["ts"][:10])
                    if (today - d).days <= days:
                        n += 1
                except Exception:
                    pass
            return n
        if evs:
            rows.append({"site": _safe(sid), "total": len(evs),
                         "last_7d": _within(7), "last_30d": _within(30),
                         "worst_severity": max((e["severity"] for e in evs),
                                               key=lambda s: _SEVERITY_RANK.get(s, 0))})
    rows.sort(key=lambda r: (-_SEVERITY_RANK.get(r["worst_severity"], 0), -r["total"]))
    return {
        "sites": rows, "total_events": total,
        "trend_reliable": total >= _MIN_TREND_EVENTS,
        "_note": f"Drift-event counts per window. Total {total} events; a trend is "
                 f"only meaningful at \u2265{_MIN_TREND_EVENTS} (trend_reliable flags "
                 "this). Counts are factual; rates are not extrapolated. Read-only.",
    }


def drift_root_causes() -> Dict[str, Any]:
    """For each site showing drift, the LIKELY cause inferred from the recorded
    signals (rule-based, not speculative). Categories needing a live DOM diff to
    pinpoint are marked accordingly. Read-only."""
    sites = _load_sites_config()
    out = []
    for cfg in sites:
        sid = _site_id(cfg)
        lb = _login_block(cfg)
        cq = _cookie_quality(sid, cfg)
        rate = _recent_login_success_rate(sid)
        dd = _drift_status(sid)
        causes = []
        if dd.get("flagged_stale"):
            causes.append({"cause": "download selector stale", "evidence":
                           f"{dd.get('consecutive_failures', 0)} consecutive zero-matches",
                           "severity": "high", "next": "re-teach download selector / dry-run"})
        if not cq.get("unavailable") and cq.get("expired"):
            causes.append({"cause": "session/cookie expired", "evidence": "cookie quality flags expired",
                           "severity": "medium", "next": "re-login via approved path"})
        if rate is not None and rate < 0.5:
            causes.append({"cause": "login failing", "evidence": f"recent success rate {round(rate, 2)}",
                           "severity": "high", "next": "Safe Dry Run to find the broken field"})
        if _captcha_in_config(cfg, lb):
            causes.append({"cause": "MFA/captcha present", "evidence": "captcha markers observed",
                           "severity": "medium", "next": "manual takeover path handles captcha"})
        if causes:
            out.append({"site": _safe(sid), "causes": causes})
    return {"sites": out, "site_count": len(out),
            "_note": "Likely drift causes from recorded signals (rule-based). "
                     "Field/form pinpointing still needs a Safe Dry Run / fresh "
                     "capture. Read-only; nothing applied."}


def template_stability_score() -> Dict[str, Any]:
    """Per-site template STABILITY (DEFINED composite, 0–100): higher = fewer recent
    drifts, clean selectors, and successful recent logins. Every input shown;
    not an objective measure. Weak on little history — sharpens as events
    accrue. Read-only."""
    sites = _load_sites_config()
    rows = []
    for cfg in sites:
        sid = _site_id(cfg)
        rate = _recent_login_success_rate(sid)
        dd = _drift_status(sid)
        evs = _site_drift_events(sid, cfg)
        fails = int(dd.get("consecutive_failures") or 0)
        download_clean = 0.0 if dd.get("flagged_stale") else round(max(0.0, 1 - fails * 0.2), 3)
        login_clean = round(rate, 3) if isinstance(rate, (int, float)) else 0.5  # unknown→neutral
        recent = sum(1 for e in evs[:30])
        drift_quiet = round(max(0.0, 1 - recent * 0.15), 3)
        comps = {"download_clean": download_clean, "login_clean": login_clean,
                 "drift_quiet": drift_quiet}
        score = round(sum(comps.values()) / len(comps) * 100)
        band = "stable" if score >= 70 else "watch" if score >= 40 else "unstable"
        rows.append({"site": _safe(sid), "score": score, "band": band,
                     "components": comps,
                     "weights": {k: round(1 / len(comps), 3) for k in comps},
                     "inputs": {"consecutive_failures": fails, "stale": bool(dd.get("flagged_stale")),
                                "recent_success_rate": (round(rate, 2) if isinstance(rate, (int, float)) else None),
                                "recent_drift_events": recent}})
    rows.sort(key=lambda r: r["score"])
    return {"sites": rows, "site_count": len(rows),
            "_note": "DEFINED stability composite: mean of download-clean + "
                     "login-clean + drift-quiet \u00d7 100. Login defaults to neutral "
                     "(0.5) when no history. Weak on little data; sharpens with "
                     "events. Every input shown. Read-only."}


@_mc_memoized
def template_maturity_score() -> Dict[str, Any]:
    """Per-site template MATURITY (DEFINED composite, 0–100): higher = more proven
    (templates present, broad selectors, observed success, low drift). Distinct from
    the framework-wide maturity score. Every input shown. Read-only."""
    sites = _load_sites_config()
    rows = []
    for cfg in sites:
        sid = _site_id(cfg)
        lb = _login_block(cfg)
        dl = _learned(cfg).get("download") if isinstance(_learned(cfg).get("download"), dict) else {}
        lc = _login_selector_counts(lb)
        has_video = bool(dl and (dl.get("row_selectors")))
        has_login = bool(lb and lc["user_field"] and lc["pass_field"])
        coverage = round((int(has_video) + int(has_login)) / 2.0, 3)
        total_sel = len([s for s in (dl.get("row_selectors") or []) if s]) + sum(lc.values())
        breadth = round(min(total_sel / 12.0, 1.0), 3)   # 12 = soft reference, adjustable
        rate = _recent_login_success_rate(sid)
        proven = round(rate, 3) if isinstance(rate, (int, float)) else 0.0
        evs = _site_drift_events(sid, cfg)
        low_drift = round(max(0.0, 1 - len(evs) * 0.1), 3)
        comps = {"template_coverage": coverage, "selector_breadth": breadth,
                 "proven_use": proven, "low_drift": low_drift}
        score = round(sum(comps.values()) / len(comps) * 100)
        band = "mature" if score >= 70 else "developing" if score >= 40 else "nascent"
        rows.append({"site": _safe(sid), "score": score, "band": band,
                     "components": comps,
                     "weights": {k: round(1 / len(comps), 3) for k in comps},
                     "inputs": {"video_template": has_video, "login_template": has_login,
                                "total_selectors": total_sel,
                                "recent_success_rate": (round(rate, 2) if isinstance(rate, (int, float)) else None),
                                "drift_events": len(evs)},
                     "trust_note": ("trusted" if band == "mature" and score >= 70
                                    else "use with review")})
    rows.sort(key=lambda r: -r["score"])
    return {"sites": rows, "site_count": len(rows),
            "_note": "DEFINED maturity composite: mean of coverage + selector-breadth "
                     "+ proven-use + low-drift \u00d7 100. Proven-use is 0 without login "
                     "history. Distinct from framework maturity. Every input shown. "
                     "Read-only."}


def unified_template_health() -> Dict[str, Any]:
    """The Phase 3 dashboard: one row per site combining video + login template
    presence, stability, maturity, and a drift summary — so 'which templates can I
    trust today?' is answerable at a glance. Read-only; recognition-only."""
    vh = {r["site"]: r for r in video_template_health()["sites"]}
    lh = {r["site"]: r for r in login_template_health()["sites"]}
    stab = {r["site"]: r for r in template_stability_score()["sites"]}
    mat = {r["site"]: r for r in template_maturity_score()["sites"]}
    freq = {r["site"]: r for r in drift_frequency()["sites"]}
    sites = sorted(set(vh) | set(lh))
    rows = []
    for s in sites:
        v = vh.get(s, {}); l = lh.get(s, {})
        st = stab.get(s, {}); mt = mat.get(s, {}); fr = freq.get(s, {})
        rows.append({
            "site": s,
            "video_template": v.get("template_present", False),
            "login_template": l.get("template_present", False),
            "video_drift": (v.get("drift", {}) or {}).get("flagged_stale", False),
            "login_success_rate": l.get("recent_success_rate"),
            "stability": {"score": st.get("score"), "band": st.get("band")},
            "maturity": {"score": mt.get("score"), "band": mt.get("band"),
                         "trust": mt.get("trust_note")},
            "drift_events": fr.get("total", 0),
            "worst_drift_severity": fr.get("worst_severity"),
        })
    # least-trustworthy first (lowest stability), then most drift
    rows.sort(key=lambda r: ((r["stability"]["score"] if r["stability"]["score"] is not None else 999),
                             -r["drift_events"]))
    trusted = sum(1 for r in rows if r["maturity"]["trust"] == "trusted")
    return {
        "sites": rows, "site_count": len(rows), "trusted_count": trusted,
        "config_present": _config_path().is_file(),
        "_note": "Unified template health: video + login presence, stability + "
                 "maturity (DEFINED composites), and drift summary per site. "
                 "Sorted least-stable first. Recognition-only; no live fetch, no "
                 "model, no replay. Scores are weak on little history and sharpen "
                 "as captures/logins accrue.",
    }


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 4 — Template Review Workbench (v3.66.113). The human review layer.
#
# Surfaces template suggestions (login + video) as review items with side-by-side
# before/after selector diffs, confidence explanation, change history, and capture
# evidence pointers. Approve/Reject is recorded via the EXISTING inert decision
# store (cockpit_core.review_decide → /api/review/decide): recording a decision
# NEVER applies it. The cockpit does not rewrite the live sites_config — applying an
# approved template stays the existing approved path. Read-only data; the only write
# is the operator's own accept/reject/defer note (the established inert pattern).
# ═════════════════════════════════════════════════════════════════════════════

def _review_decisions() -> Dict[str, Any]:
    """Read-only snapshot of recorded review decisions from the operator store."""
    try:
        from tools.cockpit_core import _store_load
        return _store_load().get("reviews", {}) or {}
    except Exception:
        return {}


def suggested_video_template_update(site: Optional[str] = None) -> Dict[str, Any]:
    """Data-only suggested video/download template update. Generic robust download
    candidates from the seed banks — NEVER applied, NEVER promoted."""
    return {
        "site": _safe(site or ""),
        "applies_automatically": False,
        "suggested": {
            "row_selectors": ["video > source[type='video/mp4']",
                              "video[class*='video-js'] source", "a[href$='.mp4']"],
            "url_attribute": "src",
            "trigger_selectors": ["button:has-text('Download')", "[data-download]",
                                  ".download-button"],
        },
        "basis": "generic robust download candidates; refine against a fresh capture / live page",
        "_note": "SUGGESTION ONLY. Not applied, not promoted, no debt retired.",
    }


def _current_login_selectors(cfg: Dict[str, Any]) -> Dict[str, Any]:
    lb = _login_block(cfg)
    out = {}
    for canon, variants in (("user_field", ("user_field", "username", "username_selector")),
                            ("pass_field", ("pass_field", "password", "password_selector")),
                            ("submit_btn", ("submit_btn", "submit", "submit_selector"))):
        for k in variants:
            if isinstance(lb.get(k), list):
                out[canon] = [_safe(s) for s in lb[k] if s]
                break
        out.setdefault(canon, [])
    return out


def _current_video_selectors(cfg: Dict[str, Any]) -> Dict[str, Any]:
    dl = _learned(cfg).get("download") if isinstance(_learned(cfg).get("download"), dict) else {}
    return {
        "row_selectors": [_safe(s) for s in (dl.get("row_selectors") or []) if s],
        "url_attribute": _safe(dl.get("url_attribute", "")),
        "trigger_selectors": [_safe(s) for s in (dl.get("trigger_selectors") or []) if s],
    }


def _selector_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """Side-by-side before/after per selector group: unchanged / added / removed."""
    groups = {}
    for key in sorted(set(before) | set(after)):
        b = before.get(key); a = after.get(key)
        if isinstance(b, list) or isinstance(a, list):
            bset = [x for x in (b or [])]; aset = [x for x in (a or [])]
            groups[key] = {
                "before": bset, "after": aset,
                "added": [x for x in aset if x not in bset],
                "removed": [x for x in bset if x not in aset],
                "unchanged": [x for x in aset if x in bset],
            }
        else:
            groups[key] = {"before": b, "after": a, "changed": b != a}
    return groups


def _site_evidence(site_id: str) -> List[Dict[str, Any]]:
    """Capture/corpus evidence pointers for a site (read-only): subject + date +
    category only, query-stripped, redacted. Captures themselves are binary and are
    never echoed."""
    out = []
    try:
        from tools.cockpit_core import _corpus, _site_of_entry
        for e in _corpus():
            if _site_of_entry(e) != site_id:
                continue
            out.append({"subject": _safe((e.get("subject") or "")[:100]),
                        "date": _parse_ts(e.get("date")),
                        "category": _safe(e.get("category", ""))})
    except Exception:
        pass
    return out[:20]


def _template_review_items() -> List[Dict[str, Any]]:
    """Derive review items: a login and/or video template suggestion per site that
    looks like it needs attention. Each carries before/after selectors, a confidence
    explanation, change history (drift events), and evidence pointers."""
    sites = _load_sites_config()
    decisions = _review_decisions()
    lh = {r["site"]: r for r in login_template_health()["sites"]}
    vh = {r["site"]: r for r in video_template_health()["sites"]}
    items = []
    for cfg in sites:
        sid = _site_id(cfg)
        drift_events = _site_drift_events(sid, cfg)
        # LOGIN item — flag if missing / low confidence / failing
        lrow = lh.get(sid, {})
        l_reasons = []
        if not lrow.get("template_present"):
            l_reasons.append("no login template")
        if (lrow.get("selector_confidence") or {}).get("band") == "low":
            l_reasons.append("low login selector confidence")
        if lrow.get("recent_success_rate") is not None and lrow["recent_success_rate"] < 0.5:
            l_reasons.append("low login success rate")
        if l_reasons:
            key = f"tpl:{sid}:login"
            before = _current_login_selectors(cfg)
            after = suggested_login_template_update(sid)["suggested"]
            items.append({
                "item_key": key, "site": _safe(sid), "kind": "login",
                "reasons": l_reasons,
                "diff": _selector_diff(before, after),
                "confidence_explanation": {
                    "selector_confidence": lrow.get("selector_confidence"),
                    "recent_success_rate": lrow.get("recent_success_rate"),
                    "why": "Suggested because the login template is " + ", ".join(l_reasons)
                           + ". Confidence/inputs shown; verify with a Safe Dry Run.",
                },
                "change_history": [e for e in drift_events
                                   if e["kind"] in ("login_relogin_fail", "drift_verdict")],
                "evidence": _site_evidence(sid),
                "decision": decisions.get(key),
                "applies_automatically": False,
            })
        # VIDEO item — flag if missing / stale / low confidence
        vrow = vh.get(sid, {})
        v_reasons = []
        if not vrow.get("template_present"):
            v_reasons.append("no video template")
        if (vrow.get("drift") or {}).get("flagged_stale"):
            v_reasons.append("download selector stale")
        if (vrow.get("selector_confidence") or {}).get("band") == "low":
            v_reasons.append("low download selector confidence")
        if v_reasons:
            key = f"tpl:{sid}:video"
            before = _current_video_selectors(cfg)
            after = suggested_video_template_update(sid)["suggested"]
            items.append({
                "item_key": key, "site": _safe(sid), "kind": "video",
                "reasons": v_reasons,
                "diff": _selector_diff(before, after),
                "confidence_explanation": {
                    "selector_confidence": vrow.get("selector_confidence"),
                    "why": "Suggested because the video template is " + ", ".join(v_reasons)
                           + ". Confidence/inputs shown; verify against a fresh capture.",
                },
                "change_history": [e for e in drift_events
                                   if e["kind"] in ("selector_stale", "drift_verdict")],
                "evidence": _site_evidence(sid),
                "decision": decisions.get(key),
                "applies_automatically": False,
            })
    return items


@_mc_memoized
def template_review_queue() -> Dict[str, Any]:
    """The Phase 4 workbench feed: template suggestions needing review, each with
    before/after selector diff, confidence explanation, change history, and capture
    evidence pointers, plus any decision already recorded. Approve/Reject is recorded
    via the existing inert decision store — it does NOT apply the change. Read-only
    apart from that established decision note."""
    items = _template_review_items()
    pending = [i for i in items if not i.get("decision")]
    return {
        "items": items,
        "count": len(items),
        "pending": len(pending),
        "config_present": _config_path().is_file(),
        "_decision_endpoint": "/api/review/decide",
        "_note": "Human review layer. Each item shows the current template (before), "
                 "the suggested template (after), why it's suggested, what has "
                 "changed (drift history), and evidence pointers. Recording "
                 "accept/reject/defer NEVER applies the change — the cockpit does not "
                 "rewrite sites_config; apply an approved template via the existing "
                 "approved path. Read-only data.",
    }


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 5 — Site Playbooks (v3.66.114). Read-only living dossier per site.
#
# Aggregates what Phases 1–4 already produce into one per-site view: login model,
# download model, selector model, drift history, known failure modes, operator
# notes, family membership, and confidence history. Pure aggregation — no live
# fetch, no model, no replay, no writes. Family membership is INFERRED from the
# stored template/config against the project's own PROVIDERS classification plus
# common player-library markers — honest ("inferred; confirm with a capture").
# ═════════════════════════════════════════════════════════════════════════════

# player-library families not covered by the CDN PROVIDERS table (Phase 6 list)
_PLAYER_FAMILY_MARKERS = {
    "jwplayer": ("jwplayer", "jw-", "data-jw", "jwpltx"),
    "video.js": ("video-js", "vjs-", "videojs"),
    "react": ("data-reactroot", "data-reactid", "__next", "_reactlisten"),
    "plyr": ("plyr", "data-plyr"),
    "flowplayer": ("flowplayer", "fp-"),
    "hls.js": ("hls.js", "hlsjs"),
    "dash.js": ("dashjs", "dash.all"),
}


def _infer_families(cfg: Dict[str, Any]) -> List[str]:
    """Infer player/provider family membership from the stored template + config
    against the project's PROVIDERS classification + player-library markers.
    Recognition-only — matches known markers; does not fetch the live page."""
    blob = json.dumps(cfg).lower()
    fams = set()
    # 1) project PROVIDERS table (CDN/provider families)
    try:
        from bulk_downloader.deep_detect import PROVIDERS
        for entry in PROVIDERS:
            name = entry[0]
            hosts = entry[1] if len(entry) > 1 else ()
            markers = entry[2] if len(entry) > 2 else ()
            for tok in tuple(hosts) + tuple(markers):
                if isinstance(tok, str) and tok and tok.lower() in blob:
                    fams.add(name)
                    break
    except Exception:
        pass
    # 2) player-library markers
    for fam, markers in _PLAYER_FAMILY_MARKERS.items():
        if any(m in blob for m in markers):
            fams.add(fam)
    return sorted(fams)


def _site_intel(site_id: str) -> Dict[str, Any]:
    try:
        from tools.cockpit_core import site_intelligence
        return site_intelligence(site_id)
    except Exception:
        return {}


def _site_notes(site_id: str) -> List[Dict[str, Any]]:
    try:
        from tools.cockpit_core import note_list
        return note_list(site_id).get("notes", []) or []
    except Exception:
        return []


def site_playbook(site: Optional[str]) -> Dict[str, Any]:
    """The living dossier for one site: login model, download model, selector model,
    drift history, known failure modes, operator notes, family membership, and
    confidence history — all read-only, aggregated from existing data."""
    if not site:
        return {"error": "site required", "site": ""}
    sites = _load_sites_config()
    cfg = next((c for c in sites if _site_id(c) == site), None)
    if cfg is None:
        return {"error": "site not found in sites_config", "site": _safe(site)}
    sid = _site_id(cfg)
    intel = _site_intel(sid)
    lh = next((r for r in login_template_health()["sites"] if r["site"] == _safe(sid)), {})
    vh = next((r for r in video_template_health()["sites"] if r["site"] == _safe(sid)), {})
    stab = next((r for r in template_stability_score()["sites"] if r["site"] == _safe(sid)), {})
    mat = next((r for r in template_maturity_score()["sites"] if r["site"] == _safe(sid)), {})
    drift_events = _site_drift_events(sid, cfg)
    # known failure modes = open concerns (from corpus) + rule-based drift causes
    rc = next((s for s in drift_root_causes()["sites"] if s["site"] == _safe(sid)), {})
    failure_modes = []
    for c in intel.get("open_concerns", []):
        failure_modes.append({"source": "corpus concern", "subject": _safe(c.get("subject", "")),
                              "outcome": _safe(c.get("outcome", ""))})
    for c in rc.get("causes", []):
        failure_modes.append({"source": "drift signal", "subject": _safe(c.get("cause", "")),
                              "outcome": _safe(c.get("evidence", "")), "next": _safe(c.get("next", ""))})
    return {
        "site": _safe(sid),
        "family": {"inferred": _infer_families(cfg),
                   "basis": "matched PROVIDERS/player-library markers in the stored "
                            "template/config; confirm against a fresh capture"},
        "login_model": {
            "template_present": lh.get("template_present", False),
            "login_url_set": lh.get("login_url_set", False),
            "selector_counts": lh.get("selector_counts"),
            "session": lh.get("session"),
            "recent_success_rate": lh.get("recent_success_rate"),
            "mfa_captcha_indicated": lh.get("mfa_captcha_indicated", False),
        },
        "download_model": {
            "template_present": vh.get("template_present", False),
            "row_selector_count": vh.get("row_selector_count"),
            "url_attribute": vh.get("url_attribute"),
            "two_step_flow": vh.get("two_step_flow", False),
            "highest_rendition_seen": vh.get("highest_rendition_seen"),
        },
        "selector_model": {
            "login": _current_login_selectors(cfg),
            "download": _current_video_selectors(cfg),
            "login_confidence": lh.get("selector_confidence"),
            "download_confidence": vh.get("selector_confidence"),
        },
        "drift_history": {
            "events": drift_events,
            "event_count": len(drift_events),
            "from_profile": intel.get("drift_history", []),
        },
        "known_failure_modes": failure_modes or None,
        "known_workarounds": "captured as operator notes (see operator_notes)",
        "operator_notes": _site_notes(sid),
        "family_confidence": {"stability": {"score": stab.get("score"), "band": stab.get("band")},
                              "maturity": {"score": mat.get("score"), "band": mat.get("band"),
                                           "trust": mat.get("trust_note")}},
        "confidence_history": intel.get("confidence_history", []),
        "known_signing_markers": intel.get("known_signing_markers", []),  # NAMES not values
        "_note": "Read-only living dossier aggregated from existing data (templates, "
                 "drift, corpus, notes, scores). No live fetch, no model, no replay, "
                 "no writes. Family membership and confidence sharpen with captures.",
    }


def site_playbook_index() -> Dict[str, Any]:
    """Directory of every site's dossier-at-a-glance: families, template presence,
    stability + maturity, open concerns, drift count. Read-only."""
    sites = _load_sites_config()
    lh = {r["site"]: r for r in login_template_health()["sites"]}
    vh = {r["site"]: r for r in video_template_health()["sites"]}
    stab = {r["site"]: r for r in template_stability_score()["sites"]}
    mat = {r["site"]: r for r in template_maturity_score()["sites"]}
    rows = []
    for cfg in sites:
        sid = _site_id(cfg); skey = _safe(sid)
        intel = _site_intel(sid)
        rows.append({
            "site": skey,
            "families": _infer_families(cfg) or None,
            "login_template": (lh.get(skey, {}) or {}).get("template_present", False),
            "video_template": (vh.get(skey, {}) or {}).get("template_present", False),
            "stability": (stab.get(skey, {}) or {}).get("band"),
            "maturity": (mat.get(skey, {}) or {}).get("band"),
            "open_concerns": len(intel.get("open_concerns", [])),
            "notes": len(_site_notes(sid)),
        })
    rows.sort(key=lambda r: (-r["open_concerns"], r["site"]))
    return {"sites": rows, "site_count": len(rows),
            "config_present": _config_path().is_file(),
            "_note": "One row per site: inferred family, template presence, stability/"
                     "maturity bands, open concerns, notes. Click a site for its full "
                     "living dossier. Read-only."}


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 6 — Family Intelligence (v3.66.115). Read-only cross-site family analysis.
#
# Groups sites by inferred family (Phase 5) and surfaces what they SHARE: selectors,
# workflow shape, drift patterns, failure modes. The cross-pollination view finds
# selectors most family members use that a given member lacks — a DATA-ONLY
# suggestion ("learning on one site can help others"); never applied. No live fetch,
# no model, no replay, no writes.
# ═════════════════════════════════════════════════════════════════════════════

def _sites_by_family() -> Dict[str, List[Dict[str, Any]]]:
    """Group site configs by inferred family. A site can belong to several."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for cfg in _load_sites_config():
        for fam in _infer_families(cfg):
            out.setdefault(fam, []).append(cfg)
    return out


def _all_download_selectors(cfg: Dict[str, Any]) -> List[str]:
    d = _current_video_selectors(cfg)
    return [s for s in (d.get("row_selectors") or []) + (d.get("trigger_selectors") or []) if s]


def _all_login_selectors(cfg: Dict[str, Any]) -> List[str]:
    d = _current_login_selectors(cfg)
    return [s for grp in d.values() for s in (grp or []) if s]


def _shared_selectors(members: List[Dict[str, Any]], getter) -> List[Dict[str, Any]]:
    """Count, across family members, how many use each selector. 'Shared' = used by
    ≥2 members. Returns selector → count + the member sites, most-shared first."""
    counts: Dict[str, set] = {}
    for cfg in members:
        sid = _safe(_site_id(cfg))
        for sel in set(getter(cfg)):
            counts.setdefault(sel, set()).add(sid)
    shared = [{"selector": _safe(sel), "used_by": len(sites), "sites": sorted(sites)}
              for sel, sites in counts.items() if len(sites) >= 2]
    shared.sort(key=lambda x: (-x["used_by"], x["selector"]))
    return shared


def _family_workflow(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Shared workflow shape across a family: two-step fraction + common url_attribute."""
    n = len(members) or 1
    two_step = 0
    attrs: Dict[str, int] = {}
    for cfg in members:
        d = _current_video_selectors(cfg)
        if d.get("trigger_selectors"):
            two_step += 1
        a = d.get("url_attribute") or ""
        if a:
            attrs[a] = attrs.get(a, 0) + 1
    common_attr = max(attrs, key=attrs.get) if attrs else None
    return {"two_step_fraction": round(two_step / n, 2),
            "two_step_members": two_step,
            "common_url_attribute": _safe(common_attr) if common_attr else None,
            "url_attribute_spread": attrs}


def _family_drift_and_failures(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Shared drift patterns + failure modes across a family (which kinds/causes
    recur across members). Read-only."""
    drift_kinds: Dict[str, int] = {}
    cause_kinds: Dict[str, int] = {}
    rc_by_site = {s["site"]: s for s in drift_root_causes()["sites"]}
    for cfg in members:
        sid = _safe(_site_id(cfg))
        for e in _site_drift_events(_site_id(cfg), cfg):
            drift_kinds[e["kind"]] = drift_kinds.get(e["kind"], 0) + 1
        for c in (rc_by_site.get(sid, {}) or {}).get("causes", []):
            cause_kinds[c["cause"]] = cause_kinds.get(c["cause"], 0) + 1
    return {
        "shared_drift_patterns": sorted(
            [{"kind": k, "members_affected": v} for k, v in drift_kinds.items()],
            key=lambda x: -x["members_affected"]),
        "shared_failure_modes": sorted(
            [{"cause": k, "members_affected": v} for k, v in cause_kinds.items()],
            key=lambda x: -x["members_affected"]),
    }


def family_intelligence() -> Dict[str, Any]:
    """Overview of every inferred family: member count, shared selectors, shared
    workflow shape, shared drift patterns, and shared failure modes. Read-only."""
    by_fam = _sites_by_family()
    fams = []
    for fam, members in sorted(by_fam.items()):
        shared_dl = _shared_selectors(members, _all_download_selectors)
        shared_login = _shared_selectors(members, _all_login_selectors)
        df = _family_drift_and_failures(members)
        fams.append({
            "family": _safe(fam),
            "member_count": len(members),
            "members": sorted(_safe(_site_id(c)) for c in members),
            "shared_download_selectors": len(shared_dl),
            "shared_login_selectors": len(shared_login),
            "workflow": _family_workflow(members),
            "shared_drift_patterns": df["shared_drift_patterns"],
            "shared_failure_modes": df["shared_failure_modes"],
        })
    fams.sort(key=lambda f: -f["member_count"])
    return {"families": fams, "family_count": len(fams),
            "config_present": _config_path().is_file(),
            "_note": "Sites grouped by inferred family; shows what members SHARE "
                     "(selectors, workflow, drift, failure modes). Single-member "
                     "families have nothing to share yet — honest. Read-only; open a "
                     "family for shared selectors + cross-pollination suggestions."}


def family_detail(family: Optional[str]) -> Dict[str, Any]:
    """Drill into one family: members, shared selectors (with which sites use them),
    and CROSS-POLLINATION — selectors most members use that a given member lacks,
    surfaced as a DATA-ONLY suggestion. Never applied. Read-only."""
    if not family:
        return {"error": "family required", "family": ""}
    by_fam = _sites_by_family()
    members = by_fam.get(family) or []
    if not members:
        return {"error": "no sites in this family", "family": _safe(family)}
    shared_dl = _shared_selectors(members, _all_download_selectors)
    shared_login = _shared_selectors(members, _all_login_selectors)
    n = len(members)
    # family-common = used by a majority (>=2 and >= half) of members
    threshold = max(2, (n + 1) // 2)
    common_dl = {s["selector"] for s in shared_dl if s["used_by"] >= threshold}
    common_login = {s["selector"] for s in shared_login if s["used_by"] >= threshold}
    cross = []
    for cfg in members:
        sid = _safe(_site_id(cfg))
        has_dl = set(_all_download_selectors(cfg))
        has_login = set(_all_login_selectors(cfg))
        missing_dl = sorted(common_dl - has_dl)
        missing_login = sorted(common_login - has_login)
        if missing_dl or missing_login:
            cross.append({
                "site": sid,
                "missing_common_download_selectors": [_safe(s) for s in missing_dl] or None,
                "missing_common_login_selectors": [_safe(s) for s in missing_login] or None,
                "suggestion": "consider adopting the family-common selectors above; "
                              "DATA-ONLY — review and apply via the existing path",
                "applies_automatically": False,
            })
    return {
        "family": _safe(family),
        "member_count": n,
        "members": sorted(_safe(_site_id(c)) for c in members),
        "shared_download_selectors": shared_dl,
        "shared_login_selectors": shared_login,
        "workflow": _family_workflow(members),
        **_family_drift_and_failures(members),
        "cross_pollination": cross,
        "_note": "Shared selectors and cross-pollination across the family. "
                 "Cross-pollination suggestions are DATA-ONLY — learning from sibling "
                 "sites is surfaced for review, never auto-applied. Read-only.",
    }


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 7 — Template Autopilot (v3.66.116). Operator-guided, NOT automation.
#
# Chains the read-only checks (detect → login template + health → video template +
# download analysis → drift → suggested updates → review queue) for one URL/site and
# ends at the human decision. "Detect site" is RECOGNITION — the URL is matched
# against stored configs, never fetched. Suggestions are data-only; the review
# decision stays the operator's separate action. No live fetch, model, replay, or
# writes — it sequences existing read-only steps and stops at the workbench.
# ═════════════════════════════════════════════════════════════════════════════

def _detect_site(target: str) -> Optional[Dict[str, Any]]:
    """Resolve a target (a site id/name, or a URL) to a configured site by
    RECOGNITION — exact id/name match, else host match against the site's id/name/
    login_url/url fields. Never fetches the URL. Returns the cfg or None."""
    if not target:
        return None
    sites = _load_sites_config()
    t = target.strip()
    # exact id / name
    for cfg in sites:
        if t == (cfg.get("id") or "") or t == (cfg.get("name") or ""):
            return cfg
    # host match
    host = ""
    try:
        if "//" in t or "." in t:
            host = (urlsplit(t if "//" in t else "//" + t).hostname or "").lower()
    except Exception:
        host = ""
    if not host:
        return None
    host_no_www = host[4:] if host.startswith("www.") else host
    for cfg in sites:
        hay = " ".join(str(cfg.get(k) or "") for k in ("id", "name", "login_url", "url", "base_url", "home_url")).lower()
        if host in hay or host_no_www in hay:
            return cfg
    return None


def template_autopilot(target: Optional[str], live_candidates: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Operator-guided run for one URL/site: detect → login template + health →
    video template + download analysis → drift → suggested updates → review queue →
    human decision. Read-only orchestration; nothing is applied. Ends pointing at the
    review workbench for the operator's approve/reject."""
    steps: List[Dict[str, Any]] = []
    if not target:
        return {"target": "", "detected_site": None,
                "steps": [{"step": "detect_site", "status": "failed",
                           "detail": "no URL or site provided"}],
                "human_decision_required": False,
                "_note": "Provide a site id or URL to run the guided checks."}

    cfg = _detect_site(target)
    if cfg is None:
        steps.append({"step": "detect_site", "status": "not_recognized",
                      "detail": "URL/site does not match any configured site (no fetch "
                                "performed); add the site to run template checks"})
        return {"target": _safe(target), "detected_site": None, "steps": steps,
                "human_decision_required": False,
                "_note": "Site not recognised. Detection is recognition-only — the URL "
                         "was not fetched. Add the site to sites_config to proceed."}

    sid = _site_id(cfg)
    skey = _safe(sid)
    steps.append({"step": "detect_site", "status": "ok",
                  "detail": f"matched configured site '{skey}'",
                  "family": _infer_families(cfg) or None})

    # login template + health
    lh = next((r for r in login_template_health()["sites"] if r["site"] == skey), {})
    steps.append({"step": "load_login_template", "status": "present" if lh.get("template_present") else "missing",
                  "result": {"selector_counts": lh.get("selector_counts"),
                             "login_url_set": lh.get("login_url_set")}})
    steps.append({"step": "login_health_check",
                  "result": {"session": lh.get("session"),
                             "recent_success_rate": lh.get("recent_success_rate"),
                             "mfa_captcha_indicated": lh.get("mfa_captcha_indicated"),
                             "selector_confidence": lh.get("selector_confidence")},
                  "dry_run": "run a Safe Dry Run on the live login page (existing "
                             "approved path) to confirm fields; never submits/echoes credentials"})

    # video template + download analysis
    vh = next((r for r in video_template_health()["sites"] if r["site"] == skey), {})
    steps.append({"step": "load_video_template", "status": "present" if vh.get("template_present") else "missing",
                  "result": {"row_selector_count": vh.get("row_selector_count"),
                             "url_attribute": vh.get("url_attribute"),
                             "two_step_flow": vh.get("two_step_flow")}})
    dexpl = download_decision_explainer(candidates=live_candidates)
    steps.append({"step": "download_analysis",
                  "result": {"is_sample": dexpl.get("is_sample"),
                             "chosen": dexpl.get("chosen"),
                             "candidate_count": len(dexpl.get("candidates", []))},
                  "_method": "pure scorer; sample candidates unless live ones are supplied — "
                             "highest rendition is chosen from live state at download time"})

    # drift
    ld = next((s for s in login_drift_report()["sites"] if s["site"] == skey), {})
    dd = (vh.get("drift") or {})
    steps.append({"step": "drift_check",
                  "result": {"login_state_drift": ld.get("state_drift_suspected"),
                             "video_selector_stale": dd.get("flagged_stale"),
                             "video_consecutive_failures": dd.get("consecutive_failures")}})

    # suggested updates (data-only)
    steps.append({"step": "generate_suggested_updates", "status": "data_only",
                  "result": {"login": suggested_login_template_update(sid)["suggested"],
                             "video": suggested_video_template_update(sid)["suggested"]},
                  "_note": "SUGGESTIONS ONLY — not applied, not promoted"})

    # review queue (derived; human decides in the workbench)
    review_items = [i for i in template_review_queue()["items"] if i["site"] == skey]
    steps.append({"step": "review_queue", "status": "ready" if review_items else "nothing_flagged",
                  "result": {"items": [{"item_key": i["item_key"], "kind": i["kind"],
                                        "reasons": i["reasons"]} for i in review_items]},
                  "next": "human decision in the Template Review Workbench "
                          "(approve/reject via the existing inert decision; never auto-applied)"})

    return {
        "target": _safe(target),
        "detected_site": skey,
        "family": _infer_families(cfg) or None,
        "steps": steps,
        "human_decision_required": bool(review_items),
        "_note": "Operator-guided run. Recognition + read-only checks + data-only "
                 "suggestions, ending at the review workbench. Detection did NOT fetch "
                 "the URL; nothing was applied. The live login/download still run via "
                 "the existing approved paths.",
    }


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 8 — Capture Intelligence (v3.66.117). Read-only, POSTURE-SAFE.
#
# Scores each capture for quality / completeness / coverage (DOM / network /
# template / drift) and lists missing evidence. METADATA ONLY — presence + counts +
# query-stripped rendition names + signing-marker NAMES (via the posture-safe
# descriptor lens). NEVER reassembles a capture, reconstructs a signed stream, reads
# signing values, or echoes raw URLs. No live fetch, no writes.
# ═════════════════════════════════════════════════════════════════════════════

_MEDIA_EXT = (".mp4", ".m4v", ".webm", ".ts", ".m3u8", ".mpd", ".mov")
_DOM_KEYS = ("dom", "html", "pages", "dom_snapshot", "document", "page_html")


def _list_captures(limit: int = 60) -> List[Any]:
    """Enumerate capture files under the captures root (read-only, guarded). Bounded.
    Returns Path objects."""
    try:
        from tools.cockpit_core import captures_root
        root = captures_root()
        if not root or not root.exists():
            return []
        out = []
        for pat in ("*.wacz", "*.json"):
            out += sorted(root.rglob(pat))
        return out[:limit]
    except Exception:
        return []


def _capture_media_count(network_log: Any) -> int:
    n = 0
    if isinstance(network_log, list):
        for ev in network_log:
            u = (ev.get("url", "") if isinstance(ev, dict) else "") or ""
            base = u.split("?", 1)[0].lower()
            if base.endswith(_MEDIA_EXT):
                n += 1
    return n


def _capture_meta(path: Any) -> Dict[str, Any]:
    """POSTURE-SAFE metadata for a capture: presence + counts + query-stripped
    rendition names + signing-marker NAMES. No raw URLs, no signing values, no
    reassembly. Read-only."""
    meta = {"name": _safe(getattr(path, "name", str(path))), "loaded": False}
    try:
        from bulk_downloader import capture_ingest as ci
        from tools.cockpit_core import _descriptors_of
        c = ci.load_capture(str(path))
        nl = c.get("network_log") or []
        meta.update({
            "loaded": True,
            "has_network": bool(nl),
            "network_events": len(nl) if isinstance(nl, list) else 0,
            "media_events": _capture_media_count(nl),
            "has_dom": any(c.get(k) for k in _DOM_KEYS),
            "has_cookies": bool(c.get("cookies")),
        })
        try:
            d = _descriptors_of(path if hasattr(path, "is_file") else __import__("pathlib").Path(path))
            meta["renditions"] = d.get("renditions", [])
            meta["signing_markers"] = d.get("signing_markers", [])  # NAMES only
        except Exception:
            meta["renditions"] = []
            meta["signing_markers"] = []
    except Exception as e:
        meta["error"] = _safe(str(e)[:120])
    return meta


def _score_capture(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Pure DEFINED capture scoring from metadata. Coverages 0–1; quality 0–100.
    Every input shown; not an objective measure. No capture content needed."""
    if not meta.get("loaded"):
        return {"quality": 0, "band": "unreadable", "completeness": 0.0,
                "coverage": {}, "missing_evidence": ["capture could not be read as a "
                "recon capture (no network_log)"], "_unreadable": True}
    media = meta.get("media_events", 0)
    netn = meta.get("network_events", 0)
    rends = meta.get("renditions") or []
    dom_coverage = 1.0 if meta.get("has_dom") else 0.0
    network_coverage = 1.0 if media > 0 else (0.5 if netn > 0 else 0.0)
    template_coverage = 1.0 if rends else (0.5 if media > 0 else 0.0)
    drift_coverage = round((min(network_coverage, 1.0) + dom_coverage) / 2.0, 3)
    coverage = {"dom": dom_coverage, "network": network_coverage,
                "template": template_coverage, "drift": drift_coverage}
    present = [k for k, v in {"network": netn > 0, "dom": meta.get("has_dom"),
                              "cookies": meta.get("has_cookies"),
                              "renditions": bool(rends)}.items() if v]
    completeness = round(len(present) / 4.0, 3)
    quality = round(sum(coverage.values()) / len(coverage) * 100)
    band = "rich" if quality >= 75 else "usable" if quality >= 40 else "thin"
    missing = []
    if not meta.get("has_network"):
        missing.append("no network log")
    if not meta.get("has_dom"):
        missing.append("no DOM/HTML snapshot")
    if not meta.get("has_cookies"):
        missing.append("no cookies/session")
    if not rends:
        missing.append("no rendition descriptors")
    return {"quality": quality, "band": band, "completeness": completeness,
            "coverage": coverage,
            "counts": {"network_events": netn, "media_events": media,
                       "renditions": len(rends)},
            "signing_markers": meta.get("signing_markers", []),  # NAMES only
            "missing_evidence": missing or None,
            "inputs": {"has_network": meta.get("has_network"), "has_dom": meta.get("has_dom"),
                       "has_cookies": meta.get("has_cookies"), "media_events": media},
            "_note": "DEFINED capture score: mean of DOM/network/template/drift "
                     "coverage \u00d7 100. Metadata only — no content, no signing "
                     "values, no reassembly. Not an objective measure."}


def capture_intelligence() -> Dict[str, Any]:
    """Per-capture quality / completeness / coverage (DOM, network, template, drift)
    + missing evidence. POSTURE-SAFE metadata only: presence, counts, query-stripped
    rendition names, signing-marker NAMES. Read-only; nothing reassembled."""
    caps = _list_captures()
    rows = []
    for p in caps:
        meta = _capture_meta(p)
        score = _score_capture(meta)
        rows.append({"capture": meta["name"], **score})
    rows.sort(key=lambda r: r["quality"])  # weakest first
    readable = [r for r in rows if not r.get("_unreadable")]
    avg_q = round(sum(r["quality"] for r in readable) / len(readable)) if readable else None
    return {
        "captures": rows,
        "capture_count": len(rows),
        "readable_count": len(readable),
        "average_quality": avg_q,
        "thin_captures": sum(1 for r in readable if r["band"] == "thin"),
        "captures_root_present": bool(_list_captures(limit=1)) or _captures_root_exists(),
        "_note": "Per-capture coverage + quality from POSTURE-SAFE metadata "
                 "(presence/counts/rendition names/signing-marker NAMES). No capture "
                 "content, no signing values, no reassembly, no live fetch. Empty "
                 "until captures exist under the captures root. Read-only.",
    }


def _captures_root_exists() -> bool:
    try:
        from tools.cockpit_core import captures_root
        r = captures_root()
        return bool(r and r.exists())
    except Exception:
        return False


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 9 — Site Readiness Score (v3.66.118). Read-only composite.
#
# One number per site — "can I trust this site today?" — from seven components:
# login health, video health, drift (inverted), evidence freshness, capture quality,
# template maturity, review debt (inverted). A DEFINED, transparent composite: every
# component + weight + the raw inputs are shown; neutral defaults where data is thin
# (flagged). No live fetch, no model, no replay, no writes.
# ═════════════════════════════════════════════════════════════════════════════

_READINESS_WEIGHTS = {
    "login_health": 0.20, "video_health": 0.20, "drift": 0.15,
    "evidence_freshness": 0.10, "capture_quality": 0.10,
    "template_maturity": 0.15, "review_debt": 0.10,
}


def _days_since(date_str: Any) -> Optional[int]:
    try:
        d = _dt.date.fromisoformat(str(date_str)[:10])
        return (_dt.date.today() - d).days
    except Exception:
        return None


def _freshness_from_days(days: Optional[int]) -> float:
    if days is None:
        return 0.5  # neutral — no dated evidence
    if days <= 14:
        return 1.0
    if days <= 30:
        return 0.8
    if days <= 90:
        return 0.5
    return 0.2


def _site_capture_quality_map() -> Dict[str, Any]:
    """Average capture quality keyed by capture name (read-only). Used to match
    captures to sites by name substring. Empty when no captures exist."""
    try:
        ci = capture_intelligence()
        return {r["capture"]: r.get("quality", 0) for r in ci.get("captures", [])
                if not r.get("_unreadable")}
    except Exception:
        return {}


def _capture_quality_for(site_id: str, cap_map: Dict[str, Any]) -> Optional[float]:
    """Average quality of captures whose name references the site (best-effort name
    match). Returns 0–1 or None if none matched."""
    if not cap_map or not site_id:
        return None
    key = site_id.lower()
    qs = [q for name, q in cap_map.items() if key and key in name.lower()]
    return round(sum(qs) / len(qs) / 100.0, 3) if qs else None


def site_readiness() -> Dict[str, Any]:
    """Per-site readiness ("can I trust this site today?") — a DEFINED composite of
    login health, video health, drift, evidence freshness, capture quality, template
    maturity, and review debt. Every component + weight + inputs shown. Read-only."""
    sites = _load_sites_config()
    lh = {r["site"]: r for r in login_template_health()["sites"]}
    vh = {r["site"]: r for r in video_template_health()["sites"]}
    mat = {r["site"]: r for r in template_maturity_score()["sites"]}
    review_pending: Dict[str, int] = {}
    for it in template_review_queue()["items"]:
        if not it.get("decision"):
            review_pending[it["site"]] = review_pending.get(it["site"], 0) + 1
    cap_map = _site_capture_quality_map()

    rows = []
    for cfg in sites:
        sid = _site_id(cfg); skey = _safe(sid)
        lrow = lh.get(skey, {}); vrow = vh.get(skey, {}); mrow = mat.get(skey, {})
        intel = _site_intel(sid)
        drift_events = _site_drift_events(sid, cfg)

        # 1) login health
        if lrow.get("template_present"):
            conf = (lrow.get("selector_confidence") or {}).get("score", 0.5)
            rate = lrow.get("recent_success_rate")
            rate = rate if isinstance(rate, (int, float)) else 0.5
            login_h = round(0.4 + 0.3 * conf + 0.3 * rate, 3)
        else:
            login_h = 0.15
        # 2) video health
        if vrow.get("template_present"):
            vconf = (vrow.get("selector_confidence") or {}).get("score", 0.5)
            stale_pen = 0.4 if (vrow.get("drift") or {}).get("flagged_stale") else 0.0
            video_h = round(max(0.0, 0.5 + 0.5 * vconf - stale_pen), 3)
        else:
            video_h = 0.15
        # 3) drift (inverted — fewer recent events = higher)
        recent = len(drift_events)
        drift_c = round(max(0.0, 1 - recent * 0.15), 3)
        # 4) evidence freshness
        dates = [e.get("date") for e in intel.get("corpus_entries", []) if e.get("date")]
        newest_days = min([d for d in (_days_since(x) for x in dates) if d is not None], default=None)
        evidence_f = _freshness_from_days(newest_days)
        # 5) capture quality (per-site name match, else neutral)
        capq = _capture_quality_for(sid, cap_map)
        capture_q = capq if capq is not None else 0.5
        # 6) template maturity
        mscore = mrow.get("score")
        maturity = round(mscore / 100.0, 3) if isinstance(mscore, (int, float)) else 0.3
        # 7) review debt (inverted)
        pend = review_pending.get(skey, 0)
        review_d = round(max(0.0, 1 - pend * 0.25), 3)

        comps = {"login_health": login_h, "video_health": video_h, "drift": drift_c,
                 "evidence_freshness": evidence_f, "capture_quality": capture_q,
                 "template_maturity": maturity, "review_debt": review_d}
        score = round(sum(comps[k] * _READINESS_WEIGHTS[k] for k in comps) * 100)
        band = "ready" if score >= 70 else "caution" if score >= 40 else "not_ready"
        rows.append({
            "site": skey, "readiness": score, "band": band,
            "components": comps,
            "weights": _READINESS_WEIGHTS,
            "inputs": {
                "login_template": lrow.get("template_present", False),
                "video_template": vrow.get("template_present", False),
                "recent_success_rate": lrow.get("recent_success_rate"),
                "video_stale": (vrow.get("drift") or {}).get("flagged_stale", False),
                "recent_drift_events": recent,
                "evidence_age_days": newest_days,
                "capture_quality_matched": capq is not None,
                "maturity_band": mrow.get("band"),
                "pending_reviews": pend,
            },
            "thin_signals": [k for k, v in {"capture_quality": capq is None,
                                            "evidence_freshness": newest_days is None,
                                            "login_success_rate": lrow.get("recent_success_rate") is None}.items() if v] or None,
        })
    rows.sort(key=lambda r: r["readiness"])  # least-ready first
    return {
        "sites": rows, "site_count": len(rows),
        "ready": sum(1 for r in rows if r["band"] == "ready"),
        "caution": sum(1 for r in rows if r["band"] == "caution"),
        "not_ready": sum(1 for r in rows if r["band"] == "not_ready"),
        "config_present": _config_path().is_file(),
        "_note": "DEFINED readiness composite (login health, video health, drift, "
                 "evidence freshness, capture quality, template maturity, review "
                 "debt) — weights and inputs shown; not an objective measure. Thin "
                 "signals use a neutral 0.5 and are flagged. Read-only.",
    }


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 10 — Operator Mission Control (v3.66.119). The one screen. Read-only.
#
# Composes Phases 1–9 + the existing ops mission_control into four zones: Needs
# Attention, Healthy, Active Work, Recommended Actions. Pure read-only roll-up — no
# live fetch, no model, no replay, no writes; nothing here acts. Recommended actions
# are SUGGESTIONS (run capture / review template / refresh evidence / investigate
# drift) tied to a site; the operator decides and acts via the existing paths.
# ═════════════════════════════════════════════════════════════════════════════

def _ops_mission() -> Dict[str, Any]:
    """The existing ops-layer mission_control (active captures, debt, recent drift),
    guarded. Read-only."""
    try:
        from tools.cockpit_core import mission_control
        return mission_control()
    except Exception:
        return {}


@_mc_scope
def operator_mission_control() -> Dict[str, Any]:
    """The single operator screen. Four zones rolled up from Phases 1–9 + ops state:
    Needs Attention, Healthy, Active Work, Recommended Actions. Read-only — nothing
    here acts; recommended actions are suggestions tied to a site."""
    ops = _ops_mission()
    readiness = site_readiness()
    lh = login_template_health()
    vh = video_template_health()
    freq = {r["site"]: r for r in drift_frequency()["sites"]}
    review = template_review_queue()
    pending_reviews = [i for i in review["items"] if not i.get("decision")]

    # ── Needs Attention ──────────────────────────────────────────────────────
    broken_login = [r["site"] for r in lh["sites"] if not r["template_present"]]
    broken_video = [r["site"] for r in vh["sites"]
                    if not r["template_present"] or (r.get("drift") or {}).get("flagged_stale")]
    high_drift = [{"site": s, "events": f["total"], "worst": f["worst_severity"]}
                  for s, f in freq.items()
                  if f.get("worst_severity") in ("high", "critical") or f.get("total", 0) >= 3]
    not_ready = [{"site": r["site"], "readiness": r["readiness"], "band": r["band"]}
                 for r in readiness["sites"] if r["band"] == "not_ready"]
    needs_attention = {
        "broken_login_templates": broken_login,
        "broken_video_templates": broken_video,
        "high_drift_sites": sorted(high_drift, key=lambda x: -x["events"]),
        "open_reviews": len(pending_reviews),
        "open_review_items": [{"site": i["site"], "kind": i["kind"]} for i in pending_reviews][:12],
        "open_debt": {"correction": (ops.get("debt") or {}).get("correction"),
                      "validation": (ops.get("debt") or {}).get("validation")},
        "not_ready_sites": not_ready,
        "count": len(broken_login) + len(broken_video) + len(high_drift) + len(not_ready) + len(pending_reviews),
    }

    # ── Healthy ──────────────────────────────────────────────────────────────
    ready_sites = [{"site": r["site"], "readiness": r["readiness"]}
                   for r in readiness["sites"] if r["band"] == "ready"]
    trusted = [r["site"] for r in template_maturity_score()["sites"]
               if r.get("trust_note") == "trusted"]
    fresh = [r["site"] for r in readiness["sites"]
             if (r["inputs"].get("evidence_age_days") is not None
                 and r["inputs"]["evidence_age_days"] <= 30)]
    healthy = {
        "ready_sites": ready_sites,
        "trusted_templates": trusted,
        "fresh_evidence": fresh,
        "count": len(ready_sites),
    }

    # ── Active Work ──────────────────────────────────────────────────────────
    recent_7d = [{"site": s, "last_7d": f["last_7d"]} for s, f in freq.items() if f.get("last_7d", 0) > 0]
    active_work = {
        "captures_running": ops.get("active_captures", 0),
        "running_tasks": ops.get("running_tasks", []),
        "review_queue": len(pending_reviews),
        "recent_drift_7d": sorted(recent_7d, key=lambda x: -x["last_7d"]),
        "recent_drift_corpus": ops.get("recent_drift", [])[:8],
    }

    # ── Recommended Actions (suggestions; operator decides) ───────────────────
    actions = []
    for s in not_ready:
        actions.append({"action": "review_template", "site": s["site"],
                        "why": f"site not ready (readiness {s['readiness']})", "priority": 1})
    for h in high_drift:
        actions.append({"action": "investigate_drift", "site": h["site"],
                        "why": f"{h['events']} drift event(s), worst {h['worst']}", "priority": 1})
    for site in broken_video:
        actions.append({"action": "review_template", "site": site,
                        "why": "video template missing or selectors stale", "priority": 2})
    for site in broken_login:
        actions.append({"action": "review_template", "site": site,
                        "why": "login template missing", "priority": 2})
    for r in readiness["sites"]:
        age = r["inputs"].get("evidence_age_days")
        if age is not None and age > 90:
            actions.append({"action": "refresh_evidence", "site": r["site"],
                            "why": f"evidence {age}d old — run a fresh capture", "priority": 3})
        elif age is None and (r["inputs"].get("video_template") or r["inputs"].get("login_template")):
            actions.append({"action": "run_capture", "site": r["site"],
                            "why": "no dated evidence on file yet", "priority": 3})
    # de-dup (site, action) keeping highest priority, then sort
    seen = {}
    for a in actions:
        k = (a["site"], a["action"])
        if k not in seen or a["priority"] < seen[k]["priority"]:
            seen[k] = a
    rec = sorted(seen.values(), key=lambda a: (a["priority"], a["site"]))

    return {
        "needs_attention": needs_attention,
        "healthy": healthy,
        "active_work": active_work,
        "recommended_actions": rec,
        "site_count": readiness["site_count"],
        "config_present": _config_path().is_file(),
        "_status": "Read-only operator overview rolled up from Phases 1–9 + ops "
                   "state. Nothing here acts. Recommended actions are suggestions "
                   "tied to a site — the operator decides and acts via the existing "
                   "paths (capture, review workbench, etc.).",
    }
