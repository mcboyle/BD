"""app_sites.id_core -- 27 @sites_bp route handlers, sub-sliced from app_sites.py (Tier M, pure motion).

Handlers attach to the SHARED sites_bp (imported from .app_sites); the routing surface
(rule, methods, bare-name) is byte-identical -- test_route_map_invariant diffs EMPTY.
"""
from __future__ import annotations
import os as _os
import json
import os
import re
import sys
import time
import uuid
from flask import Blueprint, Response, jsonify, request
from pathlib import Path
from .constants import SCREENSHOTS_DIR
from .runner import SiteRunner
from .runner import _ts
from datetime import datetime
from .db import db_search
from .db import queue_upsert
from .app_sites import (
    _app_CFG_FIELDS,
    _app_DEFAULTS,
    _app_RATE_LIMIT_WINDOW,
    _app__SITES_BULK_ACTIONS,
    _app__watch_stops,
    _app__watch_threads,
    _app_runners,
    _app_s_cfg,
    _app_s_meta,
    _build_meta,
    _check_csrf,
    _create_site,
    _m2_age_human,
    _m2_auth_state,
    _m2_avatar_color,
    _m2_honeypot_suggestion,
    _rate_check,
    _sanitize_display_name,
    _save_sites_config,
    _site_primary_url,
    _start_session_keepers,
    _start_watch_folder_threads,
    _store_site_password_in_vault,
    _validate_config_paths,
    _vault_guard_for_password,
    sites_bp,
)


@sites_bp.route("/api/sites/<sid>/ai_reanalyze", methods=["POST"])
def api_ai_reanalyze(sid):
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    from . import aiassist
    if sid not in runners: return jsonify({"ok":False,"error":"Not found"}), 404
    body = request.json or {}
    url = (body.get("url") or "").strip()
    if not url: return jsonify({"ok":False,"error":"url required"}), 400
    runner = runners[sid]
    with runner._lock:
        job = (runner.jobs or {}).get(url)
    if not job: return jsonify({"ok":False,"error":"url not in queue"}), 404
    # Gather context. We don't read the screenshot bytes here — passing
    # paths is enough; the AI module reads them on demand and base64s.
    cfg = s_cfg.get(sid, {}) or {}
    learned = cfg.get("learned") or {}
    download_sels = (learned.get("download") or {})
    tried_selectors = []
    for role in ("row_selectors","trigger_selectors"):
        for sel in (download_sels.get(role) or []):
            tried_selectors.append({"role": role, "selector": sel})
    # Fetch recent events for this URL — gives the AI hints about what
    # the worker actually saw on the page
    try:
        events = runner.get_events(after_seq=0, limit=50, url_filter=url) or []
    except Exception:
        events = []
    event_summary = "\n".join(
        f"  [{e.get('kind','')}] {(e.get('message') or '')[:200]}"
        for e in events[-15:]
    ) or "  (no events recorded)"
    # Read the screenshot if available
    screenshot_b64 = None
    ss_path = job.get("screenshot") or ""
    if ss_path:
        try:
            import base64 as _b64
            # Screenshots are stored at /screenshots/<site>/<file> by the runner
            full_path = SCREENSHOTS_DIR / sid / Path(ss_path).name
            if full_path.exists() and full_path.stat().st_size < 2_000_000:
                screenshot_b64 = _b64.b64encode(full_path.read_bytes()).decode("ascii")
        except Exception: pass
    # Build the prompt context as a structured-ish blob; suggest_selectors
    # already takes a context_hint and dom_excerpt, so we shoehorn the
    # diagnostic context into the hint (the prompt template doesn't have
    # a dedicated "failed download" mode, but the hint steers the model).
    # Phase 76 (v3.43.16): if the user clicked on the screenshot to mark
    # where the download button should be, include the normalized coords
    # in the prompt hint. The vision model uses these to focus its
    # attention on the right region of the image.
    annotation = body.get("annotation") or {}
    annotation_text = ""
    if isinstance(annotation, dict):
        ax = annotation.get("x")
        ay = annotation.get("y")
        # AUDIT FIX (v3.43.16): bool is a subclass of int in Python; explicitly
        # reject so `{"x": true}` doesn't smuggle through as 1.0.
        if (isinstance(ax, (int, float)) and not isinstance(ax, bool)
                and isinstance(ay, (int, float)) and not isinstance(ay, bool)):
            if 0 <= ax <= 1 and 0 <= ay <= 1:
                pct_x = round(ax * 100)
                pct_y = round(ay * 100)
                annotation_text = (
                    f"USER ANNOTATION: the user clicked on the screenshot at "
                    f"x={pct_x}% from the left, y={pct_y}% from the top to "
                    f"indicate where the download button is located. "
                    f"Prioritize selectors that match elements at or near "
                    f"that screen coordinate.\n"
                )
    context_hint = (
        f"This URL was DOWNLOADED previously but moved to needs_review.\n"
        f"Failure message: {job.get('message','(none)')[:300]}\n"
        f"{annotation_text}"
        f"Recent events for this URL:\n{event_summary}\n"
        f"Selectors already tried (and they didn't work or were ambiguous): "
        f"{', '.join(s['selector'] for s in tried_selectors[:8]) or '(none)'}\n"
        f"Suggest selectors that target the highest-quality DOWNLOAD link "
        f"on the visible page, NOT the ones above that already failed."
    )
    # The DOM isn't available server-side at this point — the page lived
    # in a worker browser that has since closed. So this call is
    # screenshot-driven (vision mode) when we have one, else
    # context-only (text mode).
    fake_dom = (
        f"[No live DOM — diagnostic call based on screenshot and prior events]\n"
        f"URL: {url}\n"
    )
    result = aiassist.suggest_selectors(
        dom_excerpt=fake_dom,
        screenshot_b64=screenshot_b64,
        page_url=url,
        context_hint=context_hint,
    )
    # Add some diagnostic metadata so the UI can show what was sent
    result["had_screenshot"] = bool(screenshot_b64)
    result["tried_count"] = len(tried_selectors)
    result["event_count"] = len(events)
    return jsonify(result)


@sites_bp.route("/api/sites/<sid>/auto_submit_decision", methods=["POST"])
def api_auto_submit_decision(sid):
    """Record the operator's approve/decline choice for a login form or
    page blocker that carried bot-defense / CAPTCHA / interactive-
    challenge markers, so it isn't re-prompted on this site.

    Body: {key: str, decision: "approve"|"decline"}, where `key` is the
    `approval_key` the analysis report surfaced on the gated candidate
    or blocker. Persisted under the site's
    learned.deep_detect.auto_submit_decisions block; deep_detect reads
    it on the next analysis and reports approval_status as
    "approved"/"declined" instead of "pending". When approved, the
    do_not_auto_submit gate opens for that surface on that site."""
    s_cfg = _app_s_cfg()
    _check_csrf()
    cfg = (s_cfg or {}).get(sid)
    if cfg is None:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    body = request.json or {}
    key = (body.get("key") or "").strip()
    decision = (body.get("decision") or "").strip().lower()
    if not key:
        return jsonify({"ok": False, "error": "key required"}), 400
    if decision not in ("approve", "decline"):
        return jsonify({"ok": False,
                        "error": "decision must be approve|decline"}), 400
    try:
        from . import learn as _learn
        _learn.record_auto_submit_decision(
            cfg, key, decision, site_id=sid)
        _save_sites_config()
        return jsonify({"ok": True, "decision": decision, "key": key})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@sites_bp.route("/api/sites/<sid>/candidates/inspect", methods=["POST"])
def api_candidates_inspect(sid):
    """#1 — Dry-run candidate inspector. Body: {"html": "...", "url"?: "..."}.

    Classifies every detection candidate found in the supplied page source
    (selector, text, url variants, score, size, host, signals, verdict +
    rejection reason) and returns the winner that WOULD be selected. Never
    fetches a page, never downloads, never reads cookies/tokens/storage.
    """
    s_cfg = _app_s_cfg()
    _check_csrf()
    cfg = (s_cfg or {}).get(sid)
    if cfg is None:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    body = request.json or {}
    html = body.get("html") or ""
    try:
        from .dry_run import inspect_candidates
        url = _site_primary_url(cfg) or body.get("url") or ""
        return jsonify(inspect_candidates(html, page_url=url))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@sites_bp.route("/api/sites/<sid>/profile/seed", methods=["POST"])
def api_profile_seed(sid):
    """#7 — One-click LOCAL worker-profile seed. Copies the manual-login
    session into the site's runtime profiles (main / w* / keepalive_*) via
    profile_sync (LOCK skipped, keepers guarded, backup-before-overwrite).
    Local-only, same-machine. Returns copied item NAMES, counts, timestamps,
    and backup info — never cookie/token/storage VALUES. Body: {"ensure"?: [..]}.
    """
    s_cfg = _app_s_cfg()
    _check_csrf()
    cfg = (s_cfg or {}).get(sid)
    if cfg is None:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    body = request.json or {}
    ensure = body.get("ensure") or ["main"]
    try:
        from . import profile_sync as ps
        summary = ps.sync_manual_to_runtime(sid, ensure=ensure)
        status = ps.handoff_status(sid)
        backups = {}
        for s in status.get("sites", []):
            if s.get("site") == sid:
                for r in s.get("runtime_profiles", []):
                    backups[r["profile"]] = {
                        "backup_count": r.get("backup_count"),
                        "last_backup": r.get("last_backup"),
                    }
        seeded = [{"profile": name, "items": items, "count": len(items),
                   **backups.get(name, {})}
                  for name, items in (summary.get("synced") or {}).items()]
        return jsonify({
            "ok": True, "site": sid, "source": summary.get("source"),
            "seeded": seeded,
            "skipped": summary.get("skipped"),
            "errors": summary.get("errors"),
            "skipped_reason": summary.get("skipped_reason"),
            "note": ("local-only, same-machine; item names/counts/backups "
                     "only — no cookie/token/storage values"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@sites_bp.route("/api/sites/<sid>/profile/status")
def api_profile_status(sid):
    """#8 — Per-site browser-profile storage status. For the manual / main /
    worker / keeper profiles, reports per storage item (Cookies, Local Storage,
    Session Storage, IndexedDB, WebStorage, ...) existence + byte size + mtime
    only. Read-only; never returns stored VALUES.
    """
    s_cfg = _app_s_cfg()
    cfg = (s_cfg or {}).get(sid)
    if cfg is None:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    try:
        from . import profile_sync as ps
        return jsonify({"ok": True, "site": sid,
                        **ps.profile_storage_status(sid)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@sites_bp.route("/api/sites/v2")
def api_sites_v2():
    """SPA-shaped per-site state for the Sites tab. Each entry has
    the fields the mockup row needs: avatar color, name, auth/captcha
    state, downloaded total, active workers, last-event human age."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    import time as _t
    try:
        out = []
        now = _t.time()
        for sid, runner in runners.items():
            if not runner:
                continue
            cfg = s_cfg.get(sid, {}) or {}
            name = cfg.get("name") or sid
            auth = _m2_auth_state(runner, cfg)
            captcha = bool(getattr(runner, "_captcha_pending", False))
            # Quick counts off the runner without acquiring a heavy lock
            # — get_status(light=True) is the cheap path.
            try:
                st = runner.get_status(light=True)
                counts = st.get("counts") or {}
                downloaded_total = int(counts.get("done") or 0)
                active = int(st.get("active") or 0)
            except Exception:
                downloaded_total = 0
                active = 0
            # Last-event timestamp for "active 2h ago" UI affordance
            last_event_ts = 0
            try:
                ev_log = getattr(runner, "_event_log", None) or []
                if ev_log:
                    last_event_ts = max(
                        (ev.get("ts", 0) or 0) for ev in ev_log
                    )
            except Exception:
                pass
            # F3.4 advisory: surface a learned per-site honeypot drop
            # threshold (None until enough trap evidence). Never changes
            # behaviour; fail-soft already inside the helper.
            hp_suggested, hp_samples = _m2_honeypot_suggestion(sid)
            out.append({
                "site_id": sid,
                "name": name,
                "avatar_color": _m2_avatar_color(name),
                "state": runner.state(),
                "auth_state": auth,
                "captcha_pending": captcha,
                "downloaded_total": downloaded_total,
                "active_workers": active,
                "last_event_ts": last_event_ts,
                "last_event_age": (_m2_age_human(now - last_event_ts)
                                    if last_event_ts > 0 else ""),
                "honeypot_threshold_suggested": hp_suggested,
                "honeypot_threshold_samples": hp_samples,
            })
        # Sort: issues first (captcha, expired), then active, then
        # alphabetical. Matches mockup's Sites tab ordering.
        def _sort_key(e):
            issue = 0 if e["captcha_pending"] else \
                    1 if e["auth_state"] == "expired" else \
                    2 if e["state"] == "running" else 3
            return (issue, (e["name"] or "").lower())
        out.sort(key=_sort_key)
        return jsonify({
            "ok": True,
            "sites": out,
            "count": len(out),
            "ts": int(now),
        })
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 503


@sites_bp.route("/api/sites/v2/bulk", methods=["POST"])
def api_sites_v2_bulk():
    """Apply one action to a list of sites.

    Body: {"action": "pause"|"resume"|"start"|"delete",
           "site_ids": ["sid1", "sid2", ...]}

    Returns 200 with an aggregate result on every non-malformed call.
    Per-site failures are collected, not raised. Delete uses the same
    code path as DELETE /api/sites/<sid> (full teardown: runner stop,
    keepers, watch threads, account pool, queue rows, config save).
    """
    RATE_LIMIT_WINDOW = _app_RATE_LIMIT_WINDOW()
    _SITES_BULK_ACTIONS = _app__SITES_BULK_ACTIONS()
    _watch_stops = _app__watch_stops()
    _watch_threads = _app__watch_threads()
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    s_meta = _app_s_meta()
    _check_csrf()
    body = request.get_json(silent=True) or {}
    action = (body.get("action") or "").strip().lower()
    site_ids = body.get("site_ids") or []
    if action not in _SITES_BULK_ACTIONS:
        return jsonify({
            "ok": False,
            "error": f"unknown action: {action!r}",
            "allowed": list(_SITES_BULK_ACTIONS),
        }), 400
    if not isinstance(site_ids, list) or not site_ids:
        return jsonify({
            "ok": False,
            "error": "site_ids must be a non-empty list",
        }), 400
    if not _rate_check(f"sites_bulk_{action}"):
        return jsonify({"ok": False, "error": "rate limited",
                        "retry_after": RATE_LIMIT_WINDOW}), 429
    # De-dup while preserving order so duplicate sids in the request
    # don't double-fire actions.
    seen = set()
    ordered = []
    for sid in site_ids:
        if not isinstance(sid, str):
            continue
        if sid in seen:
            continue
        seen.add(sid)
        ordered.append(sid)
    results = {"ok": 0, "errors": []}
    for sid in ordered:
        if sid not in runners:
            results["errors"].append({"site_id": sid, "error": "unknown site_id"})
            continue
        try:
            if action == "delete":
                # Inline the per-site delete teardown so the aggregate
                # endpoint goes through the same code paths as
                # api_delete. Kept in sync with api_delete at L10757.
                runner = runners[sid]
                runner.stop()
                try: runner._stop_auto_retry()
                except Exception: pass
                try:
                    from . import session_keeper as _sk
                    for k in _sk.get_status():
                        if k["site_id"] == sid:
                            _sk.stop_keeper(sid, k["account_idx"])
                except Exception: pass
                try:
                    stop_ev = _watch_stops.pop(sid, None)
                    if stop_ev is not None:
                        stop_ev.set()
                    _watch_threads.pop(sid, None)
                except Exception: pass
                try:
                    from . import account_pool as _ap
                    _ap.remove_pool(sid)
                except Exception: pass
                del runners[sid]
                s_meta.pop(sid, None)
                s_cfg.pop(sid, None)
                try:
                    from .db import queue_delete_site
                    queue_delete_site(sid)
                except Exception: pass
                # v3.66.820 (#36): same reap as api_delete -- this branch
                # is a second, inlined copy of the whole teardown, so a
                # reap in only one of them is a half-fix.
                try:
                    from .cookie_health import forget_site
                    forget_site(sid)
                except Exception as _reap_err:
                    try:
                        from .log import get_logger
                        get_logger(__name__).warning(
                            "auth_health reap failed for site %s: %s",
                            sid, _reap_err)
                    except Exception: pass
            else:
                method = getattr(runners[sid], action, None)
                if method is None or not callable(method):
                    results["errors"].append({
                        "site_id": sid,
                        "error": f"runner has no method {action!r}",
                    })
                    continue
                method()
        except Exception as e:
            results["errors"].append({"site_id": sid, "error": str(e)[:200]})
            continue
        results["ok"] += 1
    if action == "delete" and results["ok"] > 0:
        # One config save at the end, not one per delete — matches the
        # per-site api_delete's _save_sites_config() call.
        try: _save_sites_config()
        except Exception as e:
            results["errors"].append({
                "site_id": "*",
                "error": f"config save failed: {str(e)[:200]}",
            })
    return jsonify({
        "ok": True,
        "action": action,
        "applied_to": results["ok"],
        "total": len(ordered),
        "errors": results["errors"],
    })


@sites_bp.route("/api/sites",methods=["POST"])
def api_add():
    s_cfg = _app_s_cfg()
    s_meta = _app_s_meta()
    data = dict(request.json or {})
    actor = (request.cookies.get("bd_session", "")[:8]
             or request.remote_addr or "unknown")
    # v3.66.326: route the login password to the encrypted secrets vault as a
    # @cred: reference. Pop it BEFORE _create_site so plaintext can NEVER reach
    # sites_config.json. The SITE is always created and {id} is always returned
    # (the long-standing create contract is preserved); only the CREDENTIAL is
    # contingent on an unlocked encrypted backend. If the vault is locked or
    # plaintext we still create the site, store NO password (never plaintext),
    # and flag the reason so the SPA can prompt to unlock and store it via PUT.
    password = data.pop("password", "") or ""
    sid, err = _create_site(data, actor=actor)
    if err:
        return jsonify({"error": err}), 400
    resp = {"id": sid}
    if password:
        allowed, _status, errbody = _vault_guard_for_password()
        if allowed:
            ok, cred_err = _store_site_password_in_vault(sid, password)
            if ok:
                _save_sites_config()
                # Refresh the cached meta: the password was popped before
                # _create_site, so the meta was built with has_password=False.
                # Now that the @cred ref is on s_cfg, rebuild so /api/status
                # reports has_password=True (the wizard's "saved" hint).
                s_meta[sid] = _build_meta(s_cfg[sid])
                resp["cred_stored"] = True
            else:
                resp["cred_stored"] = False
                resp["cred_error"] = cred_err
        else:
            resp["cred_stored"] = False
            if errbody.get("secrets_locked"):
                resp["secrets_locked"] = True
            if errbody.get("secrets_plaintext"):
                resp["secrets_plaintext"] = True
    # v3.65.2: surface auto-picked template info so the UI can show
    # the user what was applied.
    autopick = (s_cfg.get(sid) or {}).get("_autopick")
    if autopick:
        resp["auto_pick"] = autopick
    # A4 auto-onboard (prep only; default-OFF -> byte-identical). Classify
    # onboarding and stage a draft intent on the new site; never enables, never
    # launches live capture here. Gated by the auto_onboard toggle.
    try:
        from . import auto_onboard as _ao
        _ob = _ao.auto_onboard_on_site_change(s_cfg.get(sid) or {})
        if _ob.get("staged") or _ob.get("onboarding"):
            _save_sites_config()
            resp["auto_onboard"] = {"onboarding": _ob.get("onboarding"),
                                    "staged": bool(_ob.get("staged"))}
    except Exception:
        pass
    return jsonify(resp)


@sites_bp.route("/api/sites/validate", methods=["POST"])
def api_sites_validate():
    """Dry-run validate a config without saving. Body: the config dict.
    Returns {ok, errors, warnings}. The editor calls this on field
    changes to show inline feedback."""
    from . import site_editor as _se
    cfg = request.get_json(silent=True) or {}
    result = _se.validate_config(cfg)
    return jsonify(result)


@sites_bp.route("/api/sites/<sid>/diff", methods=["POST"])
def api_sites_diff(sid):
    """Preview what a proposed config change would alter. Body: the
    proposed config dict. Returns {ok, diff: [...]} comparing it
    field-by-field against the site's current saved config."""
    s_cfg = _app_s_cfg()
    from . import site_editor as _se
    if sid not in s_cfg:
        return jsonify({"error": "Not found"}), 404
    proposed = request.get_json(silent=True) or {}
    diff = _se.diff_config(s_cfg[sid], proposed)
    return jsonify({"ok": True, "diff": diff, "change_count": len(diff)})


@sites_bp.route("/api/sites/schema")
def api_sites_schema():
    """#58 — JSON Schema describing sites_config.json."""
    CFG_FIELDS = _app_CFG_FIELDS()
    from . import site_editor as _se
    try:
        schema = _se.generate_json_schema(CFG_FIELDS)
        return jsonify(schema)
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 500


@sites_bp.route("/api/sites/selector_health")
def api_sites_selector_health():
    """#103 — per-site selector drift status.

    Returns each site's consecutive-failure count and stale flag, most-
    degraded first. Read-only."""
    s_cfg = _app_s_cfg()
    try:
        from . import selector_drift as _drift
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"selector_drift unavailable: {e}"}), 503
    try:
        rows = _drift.status_all()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 500
    # Attach the site name and sort worst-first
    for r in rows:
        sid = r.get("site_id", "")
        r["site_name"] = (s_cfg.get(sid, {}) or {}).get("name") or sid
    rows.sort(key=lambda r: (not r.get("flagged_stale", False),
                             -(r.get("consecutive_failures", 0) or 0)))
    stale = sum(1 for r in rows if r.get("flagged_stale"))
    return jsonify({"ok": True, "sites": rows, "count": len(rows),
                    "stale_count": stale})


@sites_bp.route("/api/sites/<sid>/clone",methods=["POST"])
def api_clone_site(sid):
    DEFAULTS = _app_DEFAULTS()
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    s_meta = _app_s_meta()
    if sid not in s_cfg: return jsonify({"error":"Not found"}),404
    src = s_cfg[sid]
    new_sid = uuid.uuid4().hex[:8]
    # Fields to STRIP from clone (must be unique per site)
    strip = {"username","password","cookie_file","accounts","learned",
             "fingerprint","captcha_api_key",
             # Phase 20: site-specific secrets that shouldn't carry over
             "stash_api_key","plex_token","jellyfin_api_key","ha_token"}
    cfg = {k: v for k, v in src.items() if k not in strip}
    # Mark the clone with a " (copy)" suffix on the name; numbered if needed
    base_name = (src.get("name") or "Site").rstrip()
    candidate = f"{base_name} (copy)"
    existing_names = {(s.get("name") or "").lower() for s in s_cfg.values()}
    if candidate.lower() in existing_names:
        n = 2
        while f"{base_name} (copy {n})".lower() in existing_names: n += 1
        candidate = f"{base_name} (copy {n})"
    cfg["name"] = candidate
    # Apply defaults to the stripped fields
    for k, d in DEFAULTS.items():
        if cfg.get(k) in ("", None): cfg[k] = d
    # Fresh fingerprint for the new site (don't share with parent — they'd
    # both look like the same browser to anti-bot services)
    from .constants import make_fingerprint
    cfg["fingerprint"] = make_fingerprint()
    # Wipe credentials explicitly (some may have been set as empty strings)
    for f in ("username", "password", "cookie_file", "captcha_api_key",
              # Phase 20: also redact webhook/integration secrets
              "stash_api_key", "plex_token", "jellyfin_api_key", "ha_token"):
        cfg[f] = ""
    s_cfg[new_sid] = cfg
    s_meta[new_sid] = _build_meta(cfg)
    runners[new_sid] = SiteRunner(new_sid, cfg)
    _save_sites_config()
    return jsonify({"ok": True, "id": new_sid, "name": cfg["name"]})


@sites_bp.route("/api/sites/<sid>/randomize_fingerprint",methods=["POST"])
def api_randomize_fingerprint(sid):
    """Phase 7.1: regenerate a random fingerprint for this site. Useful
    if the current one is getting flagged or you want to rotate after
    a long run."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    s_meta = _app_s_meta()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    from .constants import make_fingerprint
    fp=make_fingerprint()
    s_cfg[sid]["fingerprint"]=fp
    s_meta[sid]["fingerprint"]=fp
    runners[sid].update_config(s_cfg[sid])
    _save_sites_config()
    return jsonify({"ok":True,"fingerprint":fp})


@sites_bp.route("/api/sites/<sid>",methods=["PUT"])
def api_update(sid):
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    s_meta = _app_s_meta()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    data=request.json or {}
    # v3.48 (#25): snapshot the BEFORE state for audit logging. Copy
    # because s_cfg[sid] is going to mutate below.
    _audit_old = dict(s_cfg.get(sid) or {})
    # v3.47.8 (#81): normalize display name on update too, so renames
    # that contain bidi/control chars get cleaned before persist.
    if "name" in data:
        data["name"] = _sanitize_display_name(data["name"])
    # Phase 26.6: path validation. We check only the fields actually
    # being updated — if the request doesn't touch download_dir, the
    # existing value isn't re-validated (avoids breaking sites whose
    # paths were legal under an older policy).
    path_check = {k: data[k] for k in ("download_dir","cookie_file","spillover_dirs") if k in data}
    if path_check:
        ok, err = _validate_config_paths(path_check)
        if not ok: return jsonify({"error": err}), 400
    # Phase 41 preflight: type-validate string fields
    for str_field in ("name","login_url","username","password","cookie_file",
                      "download_dir","trigger_selector","dl_selector",
                      "user_field","pass_field","submit_btn","success_url",
                      "filename_template","sched_time","min_resolution"):
        if str_field in data:
            v = data[str_field]
            if v is None: data[str_field] = ""
            elif isinstance(v, (list, dict)):
                return jsonify({"error": f"{str_field} must be a string"}), 400
            elif not isinstance(v, str): data[str_field] = str(v)
    # numeric-range backstop (see SETTINGS_CENTER_PUT_RANGE_BACKSTOP_FINDING):
    # site_editor.validate_config already
    # range-checks, and the Settings Center dry-run gate rejects out-of-range values,
    # but a DIRECT PUT must not be able to persist one either. Shared source of truth =
    # site_editor.NUMERIC_RANGES; only numeric fields actually present are checked, so
    # valid PUTs, blank (preserve-on-blank) values, and non-numeric fields are
    # unaffected. Fail-open to prior behavior only if the validator is unavailable.
    try:
        from . import site_editor as _se_num
        _num_errs = _se_num.validate_numeric_updates(data)
    except Exception as _num_err:  # noqa: BLE001
        sys.stderr.write(f"[app] numeric backstop unavailable, skipped: {_num_err}\n")
        _num_errs = {}
    if _num_errs:
        return jsonify({"error": " ".join(_num_errs[k] for k in sorted(_num_errs))}), 400
    # Phase 20: secret fields that should be preserved when sent blank
    # from the edit form. The frontend never sees the stored values (they're
    # stripped in api_status), so an unmodified edit submits empty strings;
    # without this guard, every save would wipe all stored credentials.
    #
    # v3.43.16: ALSO preserve username (and a few other "sticky" fields)
    # when the user submits the form blank. Username DOES round-trip to
    # the UI (it's not a secret), so blank submission usually means the
    # user simply cleared the field by accident. The intentional-clear
    # case is rare; preserving on blank is the safer default. Users
    # who genuinely want to clear can delete-and-recreate the site, or
    # we could later add a "Clear credentials" button.
    PRESERVE_IF_BLANK = {"password", "cookie_file", "captcha_api_key",
                         "stash_api_key", "plex_token",
                         "jellyfin_api_key", "ha_token",
                         # v3.43.16 additions
                         "username", "login_url",
                         # v3.43.26: qB Web UI password is a stored secret.
                         # Blank-on-edit means "no change", same convention
                         # as the main site password. Without this, opening
                         # the edit modal and saving without re-typing the
                         # qB password would silently wipe it.
                         "qb_password",
                         # v3.43.81 Phase 161: TPDB API key (v3.43.80
                         # module flags). Same secret-preservation rule.
                         "tpdb_api_key"}
    # v3.66.326: a newly-typed top-level password is routed to the encrypted
    # vault as a @cred: reference, not stored plaintext. Pop it here so the
    # field-merge loop below can never write plaintext into s_cfg; a blank
    # value falls through to preserve-on-blank (keep the existing @cred ref).
    # If the backend is locked/plaintext we DON'T refuse the whole update and
    # DON'T write plaintext — we apply the rest, skip storing the password, and
    # flag the reason so the UI can prompt to unlock (existing ref untouched).
    _new_password = data.pop("password", None)
    if _new_password in (None, ""):
        _new_password = None
    _cred_flags = {}
    if _new_password is not None:
        _allowed, _pw_status, _pw_errbody = _vault_guard_for_password()
        if not _allowed:
            if _pw_errbody.get("secrets_locked"):
                _cred_flags["secrets_locked"] = True
            if _pw_errbody.get("secrets_plaintext"):
                _cred_flags["secrets_plaintext"] = True
            _new_password = None
    # Phase 31: per-account password preservation. The UI sends accounts
    # with `password` only when the user actually typed one; blank means
    # "keep existing". Merge by index (the index is implicit in array
    # position; same order as displayed).
    if "accounts" in data and isinstance(data.get("accounts"), list):
        existing = s_cfg.get(sid, {}).get("accounts") or []
        merged = []
        for i, new_acct in enumerate(data["accounts"]):
            if not isinstance(new_acct, dict): continue
            old = existing[i] if i < len(existing) else {}
            # Match by username when index doesn't line up (user added/removed rows)
            if new_acct.get("username") and not new_acct.get("password"):
                # Try to find the existing account with the same username
                m = next((e for e in existing if e.get("username") == new_acct["username"]), None)
                if m: old = m
            merged.append({
                "label": new_acct.get("label", "") or "",
                "username": new_acct.get("username", ""),
                "password": new_acct.get("password") or (old.get("password","") if old else ""),
                "cookie_file": new_acct.get("cookie_file", ""),
                "cooldown_until": new_acct.get("cooldown_until") or (old.get("cooldown_until", 0) if old else 0),
                "last_failure": new_acct.get("last_failure") or (old.get("last_failure", "") if old else ""),
            })
        data["accounts"] = merged
    for k,v in data.items():
        if k in PRESERVE_IF_BLANK and v in ("", None):
            # Skip — keep existing value
            continue
        s_cfg[sid][k]=v
        if k not in PRESERVE_IF_BLANK or v not in ("", None):
            # Mirror to meta only if it's not a secret; the meta dict is
            # what /api/status returns, and we don't want secrets there.
            if k not in ("password","cookie_file","captcha_api_key",
                         "stash_api_key","plex_token","jellyfin_api_key","ha_token",
                         # v3.43.81 Phase 161: keep TPDB API key out of
                         # meta — UI uses `has_tpdb_api_key` boolean instead.
                         "tpdb_api_key"):
                s_meta[sid][k]=v
    # Phase 31: rebuild meta wholesale to ensure accounts.password
    # mirror is scrubbed. The per-key mirror above misses nested fields
    # since `accounts` itself is allowed in meta — we just need each
    # element's password scrubbed.
    s_meta[sid] = _build_meta(s_cfg[sid])
    # v3.66.326: persist a newly-typed password to the vault as a @cred ref
    # (guard already passed above). Done after the field merge / before the
    # config save so the @cred ref — never the plaintext — lands on disk and
    # in the runner config.
    if _new_password is not None:
        _ok, _cred_err = _store_site_password_in_vault(sid, _new_password)
        if _ok:
            # The @cred ref landed on s_cfg AFTER the meta rebuild above, so
            # rebuild once more to refresh has_password (parity with api_add).
            s_meta[sid] = _build_meta(s_cfg[sid])
        else:
            sys.stderr.write(f"  vault store on update {sid} failed: {_cred_err}\n")
    runners[sid].update_config(s_cfg[sid])
    _save_sites_config()
    # v3.43.16: sync session keeper to the updated config. If the user
    # toggled keep_alive_enabled or changed credentials, we update the
    # keeper's config in place (it picks up the change on next check)
    # or stop/restart it.
    try:
        from . import session_keeper as _sk
        keepalive_now = s_cfg[sid].get("keep_alive_enabled", True)
        active = [k for k in _sk.get_status() if k["site_id"] == sid]
        if keepalive_now and s_cfg[sid].get("password"):
            if active:
                for k in active:
                    _sk.update_config(sid, k["account_idx"], s_cfg[sid])
            else:
                _start_session_keepers()
        else:
            for k in active:
                _sk.stop_keeper(sid, k["account_idx"])
    except Exception as e:
        sys.stderr.write(f"  keepalive sync on update {sid} failed: {e}\n")
    # v3.43.30: spawn watch-folder thread for newly-added or newly-
    # enabled sites. Idempotent — _start_watch_folder_threads skips
    # sites that already have a thread.
    try:
        _start_watch_folder_threads()
    except Exception as e:
        sys.stderr.write(f"  watch_folder spawn on update {sid} failed: {e}\n")
    # v3.43.35: reconfigure the account pool if accounts changed.
    # AccountPool.configure() preserves health state for unchanged
    # accounts and resets only added/removed ones.
    try:
        from . import account_pool as _ap
        accounts = s_cfg[sid].get("accounts") or []
        if accounts:
            _ap.configure_pool(sid, accounts,
                cooldown_seconds=int(s_cfg[sid].get(
                    "account_cooldown_seconds", _ap.DEFAULT_COOLDOWN_S)))
        else:
            # Accounts removed entirely — drop the pool
            _ap.remove_pool(sid)
    except Exception as e:
        sys.stderr.write(f"  account_pool reconfigure on update {sid} failed: {e}\n")
    # v3.48 (#25): audit log
    try:
        from . import audit as _audit
        _audit.audit_log(
            source="api", action="update",
            target=f"sites_config:{sid}",
            before=_audit_old,
            after=dict(s_cfg.get(sid) or {}),
            actor=(request.cookies.get("bd_session", "")[:8]
                   or request.remote_addr or "unknown"))
    except Exception:
        pass
    return jsonify({"ok": True, **_cred_flags})


@sites_bp.route("/api/sites/<sid>",methods=["DELETE"])
def api_delete(sid):
    _watch_stops = _app__watch_stops()
    _watch_threads = _app__watch_threads()
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    s_meta = _app_s_meta()
    # v3.48 (#25): capture before-state for audit
    _audit_old = dict(s_cfg.get(sid) or {})
    # BUG-1: a truly-absent id (in neither runners nor config) is a 404 -- so a
    # successful delete is distinguishable from deleting nothing.
    if sid not in runners and sid not in s_cfg and sid not in s_meta:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    if sid in runners:
        runners[sid].stop()
        # Phase 30: also stop the auto-retry scanner so its daemon thread
        # exits cleanly rather than running until process death.
        try: runners[sid]._stop_auto_retry()
        except Exception: pass
        # v3.43.16: stop any session keepers for this site
        try:
            from . import session_keeper as _sk
            for k in _sk.get_status():
                if k["site_id"] == sid:
                    _sk.stop_keeper(sid, k["account_idx"])
        except Exception: pass
        # v3.43.30: stop the watch-folder thread for this site
        try:
            stop_ev = _watch_stops.pop(sid, None)
            if stop_ev is not None:
                stop_ev.set()
            _watch_threads.pop(sid, None)
        except Exception: pass
        # v3.43.35: drop the account pool entirely — health state
        # for this site is gone with the site itself
        try:
            from . import account_pool as _ap
            _ap.remove_pool(sid)
        except Exception: pass
        del runners[sid]
    # BUG-1: config/meta removal + queue cleanup + save are UNCONDITIONAL, so an
    # idle (never-started, runner-less) site is actually deleted rather than
    # silently left in place behind a success toast.
    s_meta.pop(sid, None)
    s_cfg.pop(sid, None)
    try:
        from .db import queue_delete_site
        queue_delete_site(sid)
    except Exception:
        pass
    # v3.66.820 (#36): reap the auth_health row. It is last-known-STATE
    # read as a CURRENT signal, so a survivor is a permanent phantom site
    # in /api/data/site_health -- not a retained record like history,
    # which dev_suite.db_tools.orphan_rows (D-7) keeps by design.
    try:
        from .cookie_health import forget_site
        forget_site(sid)
    except Exception as _reap_err:
        # forget_site does not swallow, so the failure lands here. Log it
        # rather than pass: silence makes "the reap did not happen"
        # indistinguishable from "there was nothing to reap". The delete
        # itself still succeeds and still returns 200.
        try:
            from .log import get_logger
            get_logger(__name__).warning(
                "auth_health reap failed for site %s: %s", sid, _reap_err)
        except Exception:
            pass
    _save_sites_config()
    # v3.48 (#25): audit log — record even if the site was already absent
    # (idempotent delete) so the audit trail captures the intent
    try:
        from . import audit as _audit
        _audit.audit_log(
            source="api", action="delete",
            target=f"sites_config:{sid}",
            before=_audit_old if _audit_old else None,
            after=None,
            actor=(request.cookies.get("bd_session", "")[:8]
                   or request.remote_addr or "unknown"))
    except Exception:
        pass
    return jsonify({"ok":True})


@sites_bp.route("/api/sites/<sid>/ai/detect_login", methods=["POST"])
def api_ai_detect_login(sid):
    """Run AI login-form detection against a snapshot from the live
    page. Body: {dom_excerpt, screenshot_b64?, page_url?,
    context_hint?}.

    Surfaces the proposal so the user can see what the AI would
    pick BEFORE actually trying to log in with it. Used by the
    🪄 button in the manual-login UI to validate AI setup.

    Returns the same shape as ai_login.detect_login_form."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    dom_excerpt = data.get("dom_excerpt") or ""
    screenshot_b64 = data.get("screenshot_b64") or None
    page_url = data.get("page_url") or s_cfg.get(sid, {}).get("login_url", "")
    context_hint = data.get("context_hint") or ""
    try:
        from bulk_downloader import ai_login
        result = ai_login.detect_login_form(
            dom_excerpt=dom_excerpt,
            screenshot_b64=screenshot_b64,
            page_url=page_url,
            context_hint=context_hint,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})


@sites_bp.route("/api/sites/<sid>/reset_learned",methods=["POST"])
def api_reset_learned(sid):
    """Phase 5: clear any learned selectors for this site. Useful if the
    site changed its UI and the learned patterns are now stale (the auto
    drift-recovery in Phase 5.8 handles this automatically, but a manual
    reset is still useful for debugging)."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    s_meta = _app_s_meta()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    cfg=s_cfg.get(sid,{})
    if "learned" in cfg:
        del cfg["learned"]
        s_meta[sid].pop("learned",None)
        runners[sid].update_config(cfg)
        _save_sites_config()
        return jsonify({"ok":True,"message":"Learned selectors cleared"})
    return jsonify({"ok":True,"message":"Nothing to clear"})


@sites_bp.route("/api/sites/<sid>/selector_stats")
def api_selector_stats(sid):
    """Phase 13.3: hit-rate dashboard data. Returns each learned selector
    with its hit/miss counts so the user can see which patterns are
    working and which have gone stale."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"ok":False,"error":"Not found"}),404
    cfg = s_cfg.get(sid, {})
    learned = cfg.get("learned") or {}
    out = {"login": [], "download": []}
    for kind in ("login", "download"):
        block = learned.get(kind) or {}
        per_sel = block.get("_per_selector") or {}
        for role, selectors in block.items():
            if role.startswith("_") or not isinstance(selectors, list): continue
            for sel in selectors:
                stats = per_sel.get(sel) or {}
                out[kind].append({
                    "role": role, "selector": sel,
                    "hits": int(stats.get("hits", 0)),
                    "misses": int(stats.get("misses", 0)),
                })
    return jsonify({"ok": True, "stats": out, "site_id": sid})


@sites_bp.route("/api/sites/<sid>/prune_selectors", methods=["POST"])
def api_prune_selectors(sid):
    """Remove selectors with a miss/total ratio above the threshold.
    Body: {"min_attempts": 5, "max_miss_ratio": 0.8, "dry_run": true}.
    Returns a list of which selectors would be / were removed."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"ok":False,"error":"Not found"}),404
    body = request.json or {}
    min_attempts = int(body.get("min_attempts", 5))
    max_miss_ratio = float(body.get("max_miss_ratio", 0.8))
    dry_run = bool(body.get("dry_run", False))
    cfg = s_cfg.get(sid, {})
    learned = cfg.get("learned") or {}
    pruned = []
    for kind in ("login", "download"):
        block = learned.get(kind) or {}
        per_sel = block.get("_per_selector") or {}
        for role, selectors in list(block.items()):
            if role.startswith("_") or not isinstance(selectors, list): continue
            keep = []
            for sel in selectors:
                stats = per_sel.get(sel) or {}
                hits = int(stats.get("hits", 0))
                misses = int(stats.get("misses", 0))
                total = hits + misses
                if total >= min_attempts and (misses / total) >= max_miss_ratio:
                    pruned.append({"kind": kind, "role": role, "selector": sel,
                                   "hits": hits, "misses": misses,
                                   "miss_ratio": round(misses/total, 2)})
                    continue
                keep.append(sel)
            if not dry_run and keep != selectors:
                block[role] = keep
    if not dry_run and pruned:
        # Persist + propagate to the live runner
        _save_sites_config()
        runners[sid].update_config(cfg)
    return jsonify({"ok": True, "pruned": pruned, "dry_run": dry_run,
                    "count": len(pruned)})


@sites_bp.route("/api/sites/<sid>/learned/apply_repairs", methods=["POST"])
def api_learned_apply_repairs(sid):
    """Phase 29 commit: apply operator-verified diff_repair proposals to a
    site's learned DOWNLOAD selectors. Body:
      {"repairs": [{"old_selector","new_selector","role"}, ...],
       "removed": ["selector", ...],   # optional
       "dry_run": false}               # optional; preview without writing
    Each repair REPLACES an existing selector in learned.download[role]
    (role in row_selectors|trigger_selectors); old_selector MUST already be
    present (no blind append). 'removed' deletes matching selectors. On a
    real (non-dry_run) change we persist + propagate to the live runner,
    mirroring prune_selectors. Returns {ok, applied, removed, rejected,
    dry_run, count}. The operator verifies each repair in the AI-teach panel
    before this call — that review is the manual handoff at the write boundary;
    nothing is auto-applied straight from the model."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"ok":False,"error":"Not found"}),404
    body = request.json or {}
    repairs = body.get("repairs") or []
    removed_in = body.get("removed") or []
    dry_run = bool(body.get("dry_run", False))
    if not isinstance(repairs, list) or not isinstance(removed_in, list):
        return jsonify({"ok":False,"error":"repairs and removed must be lists"}),400
    VALID_ROLES = ("row_selectors", "trigger_selectors")
    cfg = s_cfg.get(sid, {})
    learned = cfg.get("learned") or {}
    download = learned.get("download") or {}
    applied, rejected, removed_done = [], [], []
    changed = False
    for r in repairs:
        if not isinstance(r, dict):
            rejected.append({"reason": "not an object", "repair": r}); continue
        old = (r.get("old_selector") or "").strip()
        new = (r.get("new_selector") or "").strip()[:300]
        role = r.get("role", "row_selectors")
        if role not in VALID_ROLES:
            rejected.append({"reason": f"invalid role {role!r}", "old_selector": old}); continue
        if not old or not new:
            rejected.append({"reason": "empty old/new selector", "role": role}); continue
        lst = download.get(role)
        if not isinstance(lst, list) or old not in lst:
            rejected.append({"reason": "old_selector not present in learned",
                             "role": role, "old_selector": old}); continue
        if not dry_run:
            idx = lst.index(old)
            if new in lst:
                lst.remove(old)          # new already present → just drop the broken one
            else:
                lst[idx] = new           # in-place replace, preserves ordering
            download[role] = lst
            changed = True
        applied.append({"role": role, "old_selector": old, "new_selector": new})
    for sel in removed_in:
        if not isinstance(sel, str): continue
        sel = sel.strip()
        if not sel: continue
        for role in VALID_ROLES:
            lst = download.get(role)
            if isinstance(lst, list) and sel in lst:
                if not dry_run:
                    download[role] = [s for s in lst if s != sel]
                    changed = True
                removed_done.append({"role": role, "selector": sel})
    if not dry_run and changed:
        learned["download"] = download
        cfg["learned"] = learned
        _save_sites_config()
        runners[sid].update_config(cfg)
    return jsonify({"ok": True, "applied": applied, "removed": removed_done,
                    "rejected": rejected, "dry_run": dry_run,
                    "count": len(applied)})


@sites_bp.route("/api/sites/<sid>/learned/export")
def api_learned_export(sid):
    """Return the site's learned block as a downloadable JSON file."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"ok":False,"error":"Not found"}),404
    cfg = s_cfg.get(sid, {})
    name = cfg.get("name", sid)
    payload = {
        "schema": "bulk_downloader.learned/1",
        "exported_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "site_name": name,
        "site_id": sid,
        "learned": cfg.get("learned") or {},
    }
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", name)[:40] or sid
    return Response(
        json.dumps(payload, indent=2, default=str),
        mimetype="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="bd_learned_{safe_name}.json"'})


@sites_bp.route("/api/sites/<sid>/learned/import", methods=["POST"])
def api_learned_import(sid):
    """Accept a previously-exported (or hand-written) learned block.
    Body: {"learned": {...}} OR the entire exported file shape.
    Validates the structure minimally and writes it to the site config."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    s_meta = _app_s_meta()
    if sid not in runners: return jsonify({"ok":False,"error":"Not found"}),404
    body = request.json or {}
    learned = body.get("learned") if "learned" in body else body
    if not isinstance(learned, dict):
        return jsonify({"ok":False,"error":"learned must be an object"}),400
    # Minimal validation: only allow expected top-level keys
    allowed_top = {"login","download","stats","fingerprint"}
    cleaned = {k: v for k, v in learned.items() if k in allowed_top}
    if not cleaned:
        return jsonify({"ok":False,"error":"no recognized blocks found"}),400
    cfg = s_cfg.get(sid, {})
    cfg["learned"] = cleaned
    s_cfg[sid] = cfg
    s_meta[sid] = _build_meta(cfg)
    runners[sid].update_config(cfg)
    _save_sites_config()
    role_summary = {}
    for kind, block in cleaned.items():
        if isinstance(block, dict):
            for role, val in block.items():
                if role.startswith("_") or not isinstance(val, list): continue
                role_summary[f"{kind}.{role}"] = len(val)
    return jsonify({"ok": True, "imported": role_summary})


@sites_bp.route("/api/sites/<sid>/heuristic/fingerprint", methods=["GET", "DELETE"])
def api_heuristic_fingerprint(sid):
    """Per-site URL pattern fingerprint accumulated from successful
    downloads. Exposed for inspection + so the upcoming paste-HTML
    template extractor can use the same self-tuning signal.

    GET — returns current state
    DELETE — wipes the fingerprint (useful when a site's CDN moves
    and the prior fingerprint is now stale)"""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners:
        return jsonify({"error": "Not found"}), 404
    cfg = s_cfg.get(sid, {})
    if request.method == "DELETE":
        cfg["url_fingerprint"] = {}
        _save_sites_config()
        return jsonify({"ok": True, "wiped": True})
    fp = cfg.get("url_fingerprint") or {}
    return jsonify({
        "ok": True,
        "known_hosts": fp.get("known_hosts") or [],
        "known_path_prefixes": fp.get("known_path_prefixes") or [],
    })


@sites_bp.route("/api/sites/<sid>/window/status")
def api_window_status(sid):
    """Per-site download-window introspection: is this site
    currently in window? what's the configured spec? when does
    state next flip?

    Used by the edit modal to show a live "currently in window"
    badge and seconds-until-flip countdown. Also useful as a
    smoke test for the user's window_active_hours regex."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners:
        return jsonify({"error": "Not found"}), 404
    cfg = s_cfg.get(sid, {})
    try:
        from . import download_window as _dw
        in_win = _dw.site_in_window(cfg)
        next_t = _dw.next_transition_seconds(cfg)
        windows_parsed = _dw.parse_windows(cfg.get("window_active_hours") or "")
        return jsonify({
            "ok": True,
            "enabled": bool(cfg.get("window_enabled")),
            "active_hours": cfg.get("window_active_hours") or "",
            "action_outside": cfg.get("window_action_outside") or "paused",
            "currently_in_window": in_win,
            "next_transition_seconds": next_t,
            "windows_parsed": [
                {"start_min": s, "end_min": e}
                for s, e in windows_parsed
            ],
        })
    except Exception as e:
        return jsonify({"ok": False,
                          "error": f"{type(e).__name__}: {e}"}), 500


# ReDoS guard for the operator-supplied regex in api_bulk_url_transform below.
# That endpoint runs rgx.subn() over every queued URL, and Python's re has no
# match timeout, so a nested-quantifier pattern ((a+)+ / (.*)*) causes
# catastrophic backtracking and hangs the worker. We reject two shapes BEFORE
# compiling or matching: an absurd length, and an outer unbounded quantifier
# (*, +, {n,}) applied to a group that itself contains a + or * -- the classic
# exponential signature. The detector is intentionally conservative on the
# dangerous side: a rare benign pattern with a mandatory delimiter (e.g.
# (a+b)* ) may be rejected with a clear message and can be rewritten. That is
# the safe direction for a DoS guard with no runtime timeout, and the failure
# is loud (400), not a hung worker.
_REDOS_NESTED_QUANT = re.compile(r"\([^()]*[+*][^()]*\)(?:[*+]|\{\d*,\})")


def _regex_redos_risk(pattern):
    """Return a human-readable reason string if `pattern` is prone to
    catastrophic backtracking (ReDoS), else '' (safe to compile and run)."""
    if len(pattern) > 1000:
        return "pattern too long (>1000 chars)"
    if _REDOS_NESTED_QUANT.search(pattern):
        return ("nested unbounded quantifier (e.g. (a+)+ or (.*)* ) can cause "
                "catastrophic backtracking")
    return ""


@sites_bp.route("/api/sites/<sid>/bulk_url_transform",methods=["POST"])
def api_bulk_url_transform(sid):
    """Apply a regex find/replace across every URL in this site's queue.

    Body: {"pattern": "...", "replacement": "...", "dry_run": true|false}
    Returns: {ok, matched, changed, sample: [{from, to}, ...], errors}

    Useful for bulk URL fixes:
      - swap subdomain:  pattern=`//cdn1\\.`  replacement=`//cdn2.`
      - strip query params: pattern=`\\?.*$`  replacement=``
      - trailing-slash dedup: pattern=`/$`    replacement=``

    Dry-run by default — returns what WOULD change without applying. Set
    `dry_run: false` to commit. Always returns up to 8 sample
    transformations so the user can verify the regex before committing."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    data = request.json or {}
    pattern = data.get("pattern") or ""
    replacement = data.get("replacement", "")
    dry_run = bool(data.get("dry_run", True))
    if not pattern:
        return jsonify({"ok": False, "error": "pattern required"}), 400
    _redos = _regex_redos_risk(pattern)
    if _redos:
        return jsonify({"ok": False, "error": f"unsafe regex: {_redos}"}), 400
    try:
        rgx = re.compile(pattern)
    except re.error as e:
        return jsonify({"ok": False, "error": f"invalid regex: {e}"}), 400
    # Translate JS-style $1, $2, ... backreferences to Python-style \1, \2.
    # Most users come from JavaScript/sed-land and write $1 expecting capture
    # groups. Python's re.sub uses \1 instead. We convert when there's no
    # already-escaped \1 in the string (avoid double-translating).
    if "$" in replacement and "\\1" not in replacement:
        replacement = re.sub(r"\$(\d)", r"\\\1", replacement)

    runner = runners[sid]
    matched = 0; changed = 0; samples = []
    transforms = []  # (old_url, new_url) pairs to apply
    for url in list(runner.urls):  # snapshot — list mutated below
        new_url, n = rgx.subn(replacement, url)
        if n > 0:
            matched += 1
            if new_url != url:
                changed += 1
                if len(samples) < 8:
                    samples.append({"from": url, "to": new_url})
                transforms.append((url, new_url))
    if not dry_run and transforms:
        try:
            n_committed = runner.bulk_url_transform(transforms)
        except Exception as e:
            return jsonify({"ok": False, "error": f"commit failed: {e}",
                            "matched": matched, "changed": changed}), 500
    else:
        n_committed = 0
    return jsonify({"ok": True, "matched": matched, "changed": changed,
                    "committed": n_committed, "sample": samples,
                    "dry_run": dry_run})


@sites_bp.route("/api/sites/preview_filename", methods=["POST"])
def api_sites_preview_filename():
    """#15 — filename templating live preview.

    Body: {template: '{site}/{title} [{resolution}]{ext}', context: {...}}.
    Renders the template against the supplied (or a built-in sample)
    context and returns the resulting filename. The editor calls this
    on every keystroke so the operator sees what files will be named
    before committing the template.

    Pure — never touches disk or the queue."""
    from . import fname as _fname
    body = request.get_json(silent=True) or {}
    template = body.get("template", "")
    if not isinstance(template, str):
        return jsonify({"ok": False,
                        "error": "template must be a string"}), 400
    # A representative sample context so the preview is meaningful even
    # before the operator supplies real metadata.
    sample = {
        "site": "examplesite", "title": "Sample Scene Title",
        "performer": "Jane Doe", "studio": "Example Studio",
        "date": "2026-05-15", "year": "2026",
        "resolution": "1080p", "quality": "1080p",
        "ext": ".mp4", "filename": "sample_scene",
        "id": "abc123",
    }
    # Caller-supplied context overrides the sample, key by key
    ctx = dict(sample)
    supplied = body.get("context")
    if isinstance(supplied, dict):
        ctx.update({k: v for k, v in supplied.items()})
    try:
        rendered = _fname.resolve_filename_template(template, ctx)
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"template error: {type(e).__name__}"}), 400
    # Surface which placeholders the template actually used + which it
    # references but the context can't fill (likely typos).
    import re as _re
    used = set(_re.findall(r"\{([A-Za-z_][A-Za-z_0-9]*)\}", template))
    unknown = sorted(used - set(ctx.keys()))
    return jsonify({"ok": True, "preview": rendered,
                    "used_placeholders": sorted(used & set(ctx.keys())),
                    "unknown_placeholders": unknown})
