"""app_sites.auth -- 21 @sites_bp route handlers, sub-sliced from app_sites.py (Tier M, pure motion).

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
    _app_runners,
    _app_s_cfg,
    _bd_cookie_dir,
    _check_csrf,
    _save_sites_config,
    sites_bp,
)


@sites_bp.route("/api/sites/<sid>/post_reveal_decision", methods=["POST"])
def api_post_reveal_decision(sid):
    """F12: record the operator's approve/decline choice for a two-step
    POST-reveal workflow that carried honeypot/challenge markers, so it
    isn't re-prompted on this site.

    Body: {action_url: str, decision: "approve"|"decline"}.
    Persisted under the site's learned.deep_detect.post_reveal_decisions
    block; deep_detect reads it on the next analysis and reports the
    workflow's approval_status as "approved"/"declined" instead of
    "pending"."""
    s_cfg = _app_s_cfg()
    _check_csrf()
    cfg = (s_cfg or {}).get(sid)
    if cfg is None:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    body = request.json or {}
    action_url = (body.get("action_url") or "").strip()
    decision = (body.get("decision") or "").strip().lower()
    if not action_url:
        return jsonify({"ok": False, "error": "action_url required"}), 400
    if decision not in ("approve", "decline"):
        return jsonify({"ok": False,
                        "error": "decision must be approve|decline"}), 400
    try:
        from . import learn as _learn
        _learn.record_post_reveal_decision(
            cfg, action_url, decision, site_id=sid)
        _save_sites_config()
        return jsonify({"ok": True, "decision": decision,
                        "action_url": action_url})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@sites_bp.route("/api/sites/<sid>/pending_approvals", methods=["GET"])
def api_pending_approvals(sid):
    """T11 (v3.66.264): the per-site read surface the SPA approval gate
    renders. Returns the auto-submit / post-reveal approval candidates a
    deep_detect run surfaced for this site that have NOT yet been
    decided (an approve/decline self-clears the row — see
    learn.pending_approvals).

    → {ok: True, pending: [{surface, key, kind, why, at}], count: int}
    404 on an unknown site. Read-only; carries marker LABELS only, never
    a secret value (F2 posture — same as vpn_secrets_status)."""
    s_cfg = _app_s_cfg()
    cfg = (s_cfg or {}).get(sid)
    if cfg is None:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    try:
        from . import learn as _learn
        pending = _learn.pending_approvals(cfg)
        return jsonify({"ok": True, "pending": pending,
                        "count": len(pending)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@sites_bp.route("/api/sites/<sid>/cookies/export")
def api_cookies_export(sid):
    s_cfg = _app_s_cfg()
    cfg = (s_cfg or {}).get(sid)
    if cfg is None:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    cf = (cfg.get("cookie_file") or "").strip()
    if not cf:
        return jsonify({"ok": False,
                        "error": "this site has no cookie file yet"}), 404
    path = Path(cf)
    if not path.is_file():
        return jsonify({"ok": False,
                        "error": "cookie file not found on disk"}), 404
    try:
        body = path.read_text(encoding="utf-8")
    except OSError as e:
        return jsonify({"ok": False,
                        "error": f"could not read cookie file: {e}"}), 500
    # Sanitise the site name into a safe download filename.
    raw = (cfg.get("name") or sid) or "site"
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in raw)
    fname = f"{safe or 'site'}_cookies.json"
    return Response(body, mimetype="application/json",
                    headers={"Content-Disposition":
                             f'attachment; filename="{fname}"'})


@sites_bp.route("/api/sites/<sid>/cookies/import", methods=["POST"])
def api_cookies_import(sid):
    s_cfg = _app_s_cfg()
    cfg = (s_cfg or {}).get(sid)
    if cfg is None:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    # Source the cookie text from one of: uploaded file, pasted text,
    # or an absolute file path. First non-empty wins.
    text = None
    src = None
    up = request.files.get("file") if request.files else None
    if up is not None and up.filename:
        try:
            text = up.read().decode("utf-8")
            src = "upload"
        except Exception as e:
            return jsonify({"ok": False,
                            "error": f"could not read upload: {e}"}), 400
    if text is None:
        body = request.get_json(silent=True) or {}
        pasted = (body.get("text") or "").strip()
        path_in = (body.get("path") or "").strip()
        if pasted:
            text, src = pasted, "text"
        elif path_in:
            # F-APP03-02: validate the absolute path against the reveal-safe
            # roots (never-empty: path_allowlist if set, else BD_HOME + the
            # default download dirs) BEFORE reading. Without this an
            # authenticated caller could read any file on disk (the legacy
            # path_allowlist default is empty, so plain _validate_path alone
            # would pass any absolute path). Delegated dynamically to avoid a
            # static import edge onto app.
            import importlib as _il
            _validate_reveal_path = getattr(
                _il.import_module("bulk_downloader.app"),
                "_validate_reveal_path")
            _p_ok, _p_msg = _validate_reveal_path(path_in, "cookie file path")
            if not _p_ok:
                return jsonify({"ok": False, "error": _p_msg}), 400
            p = Path(path_in)
            if not p.is_file():
                return jsonify({"ok": False,
                                "error": "no file at that path"}), 400
            try:
                text = p.read_text(encoding="utf-8")
                src = "path"
            except OSError as e:
                return jsonify({"ok": False,
                                "error": f"could not read file: {e}"}), 400
    if not text:
        return jsonify({"ok": False,
                        "error": "no cookie data provided"}), 400
    # Validate it parses as cookie JSON before writing anything.
    try:
        from . import cookies as _ck
        import tempfile as _tf
        with _tf.NamedTemporaryFile("w", suffix=".json", delete=False,
                                    encoding="utf-8") as tmp:
            tmp.write(text)
            tmp_path = tmp.name
        parsed = _ck.load_cookies_from_file(tmp_path)
        os.unlink(tmp_path)
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"not valid cookie JSON: {e}"}), 400
    if not parsed:
        return jsonify({"ok": False,
                        "error": "file parsed but contained 0 cookies"}), 400
    # Write into BD's own cookies dir and repoint the site. Atomic
    # write (.tmp + replace) per the v3.43.19 state-file invariant —
    # a mid-write crash must never leave a half-written cookie file.
    try:
        dest = _bd_cookie_dir() / f"{sid}.json"
        dest_tmp = dest.with_suffix(".json.tmp")
        dest_tmp.write_text(text, encoding="utf-8")
        dest_tmp.replace(dest)
    except OSError as e:
        return jsonify({"ok": False,
                        "error": f"could not save into BD: {e}"}), 500
    cfg["cookie_file"] = str(dest)
    _save_sites_config()
    return jsonify({"ok": True, "count": len(parsed),
                    "source": src, "cookie_file": str(dest)})


@sites_bp.route("/api/sites/<sid>/account_pool/status")
def api_account_pool_status(sid):
    """Show pool state: per-account state (available / in_use /
    cooling_down / dead), fail counts, last error, cooldown remaining.
    Replaces the rough cfg["accounts"][i]["cooldown_until"]
    introspection."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error": "Not found"}), 404
    try:
        from bulk_downloader import account_pool as _ap
        pool = _ap.get_pool(sid)
        return jsonify(pool.get_status())
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@sites_bp.route("/api/sites/<sid>/account_pool/reset/<int:account_idx>",
            methods=["POST"])
def api_account_pool_reset(sid, account_idx):
    """Reset one account to 'available' — clears dead state, cooldown,
    fail_count. Used after the user has fixed something (updated
    password, completed manual captcha, etc.)."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error": "Not found"}), 404
    try:
        from bulk_downloader import account_pool as _ap
        pool = _ap.get_pool(sid)
        pool.reset(account_idx)
        # Persist immediately so it survives restart
        runners[sid]._persist_pool_state()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@sites_bp.route("/api/sites/<sid>/login",methods=["POST"])
def api_login(sid):
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    runners[sid].login_async(); return jsonify({"ok":True})


@sites_bp.route("/api/sites/<sid>/accounts")
def api_accounts_get(sid):
    """List accounts with their state. Passwords masked; cookie_file
    shown but not its contents. Returns the active index too so the UI
    can highlight which one is currently in use."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    cfg = s_cfg.get(sid, {})
    accounts = cfg.get("accounts") or []
    now = time.time()
    out = []
    for i, a in enumerate(accounts):
        cd = float(a.get("cooldown_until", 0) or 0)
        out.append({
            "index": i,
            "label": a.get("label", "") or f"Account {i+1}",
            "username": a.get("username", ""),
            "has_password": bool(a.get("password")),
            "cookie_file": a.get("cookie_file", ""),
            "cooldown_until": cd,
            "cooling_down": cd > now,
            "cooldown_remaining": max(0, int(cd - now)),
            "last_failure": (a.get("last_failure") or "")[:200],
        })
    return jsonify({
        "ok": True,
        "accounts": out,
        "active_index": getattr(runners[sid], "_active_account_idx", 0),
        "mode": cfg.get("accounts_mode", "failover"),
        "rotate_every": int(cfg.get("accounts_rotate_every", 50) or 50),
    })


@sites_bp.route("/api/sites/<sid>/accounts/reset_cooldown", methods=["POST"])
def api_accounts_reset_cooldown(sid):
    """Force-clear the cooldown on one account (or all). Body:
    {account_index: N} for one, {all: true} for all. Useful when you
    know the underlying issue is resolved and want to retry now rather
    than wait 24h."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    body = request.json or {}
    cfg = s_cfg.get(sid, {})
    accounts = cfg.get("accounts") or []
    cleared = []
    if body.get("all"):
        for i, a in enumerate(accounts):
            if float(a.get("cooldown_until", 0) or 0) > 0:
                a["cooldown_until"] = 0
                a["last_failure"] = ""
                cleared.append(i)
    else:
        try: idx = int(body.get("account_index", -1))
        except Exception: idx = -1
        if not (0 <= idx < len(accounts)):
            return jsonify({"error": "account_index out of range"}), 400
        accounts[idx]["cooldown_until"] = 0
        accounts[idx]["last_failure"] = ""
        cleared.append(idx)
    s_cfg[sid] = cfg
    runners[sid].update_config(cfg)
    _save_sites_config()
    return jsonify({"ok": True, "cleared_indices": cleared})


@sites_bp.route("/api/sites/<sid>/accounts/rotate", methods=["POST"])
def api_accounts_rotate(sid):
    """Force a rotation to the next available account. Useful when the
    user notices issues with the current account but it hasn't tripped
    auto-rotation yet. Returns the new active index."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    ok = runners[sid]._rotate_account_if_available(reason="manual rotation")
    if not ok:
        return jsonify({"ok": False,
                        "error": "no other account available (need 2+, current must not be the only non-cooled one)"}), 400
    return jsonify({"ok": True,
                    "active_index": runners[sid]._active_account_idx})


@sites_bp.route("/api/sites/<sid>/captcha/stats")
def api_captcha_stats(sid):
    """Per-site solver metrics — submissions, solves, failures, timeouts,
    average solve time. Plus current config (provider, has_key).

    v3.43.39: now includes a `by_type` breakdown showing per-(type,
    provider) success rates. Lets the user see which captcha types
    their provider handles well and which are failing — useful for
    deciding whether to switch providers."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    runner = runners[sid]
    cfg = s_cfg.get(sid, {})
    stats = dict(getattr(runner, "_captcha_stats", {}) or {})
    total = stats.get("submitted", 0)
    solved = stats.get("solved", 0)
    avg_ms = int(stats.get("total_solve_time_ms", 0) / max(1, solved)) if solved else 0
    # v3.43.39: per-type breakdown from the new captcha_resolver tracker
    by_type = {}
    resolver_stats = getattr(runner, "_captcha_resolver_stats", None)
    if resolver_stats is not None:
        try:
            by_type = resolver_stats.snapshot()
        except Exception:
            by_type = {}
    return jsonify({
        "ok": True,
        "provider": cfg.get("captcha_provider", "2captcha"),
        "has_key": bool((cfg.get("captcha_api_key") or "").strip()),
        "submitted": total,
        "solved": solved,
        "failed": stats.get("failed", 0),
        "timeouts": stats.get("timeouts", 0),
        "success_rate": round((solved / total) * 100, 1) if total else 0,
        "avg_solve_time_ms": avg_ms,
        "last_solve_time_ms": stats.get("last_solve_time_ms", 0),
        "last_failure": stats.get("last_failure", ""),
        "last_failure_at": stats.get("last_failure_at", 0),
        "last_success_at": stats.get("last_success_at", 0),
        "by_type": by_type,
    })


@sites_bp.route("/api/sites/<sid>/captcha/test", methods=["POST"])
def api_captcha_test(sid):
    """Verify the configured API key works + report balance. Hits the
    provider's balance endpoint, doesn't actually submit a challenge.
    Returns {ok, provider, balance_usd, message}."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    cfg = s_cfg.get(sid, {})
    api_key = (cfg.get("captcha_api_key") or "").strip()
    provider = (cfg.get("captcha_provider") or "2captcha").strip().lower()
    if not api_key:
        return jsonify({"ok": False, "error": "no API key configured for this site"})
    try:
        import httpx as _httpx
        if provider == "2captcha":
            # 2captcha balance: GET /res.php?key=KEY&action=getbalance
            r = _httpx.get("https://2captcha.com/res.php",
                params={"key": api_key, "action": "getbalance", "json": "1"},
                timeout=10)
            d = r.json()
            if d.get("status") == 1:
                bal = float(d.get("request", 0))
                return jsonify({"ok": True, "provider": provider,
                                "balance_usd": bal,
                                "message": f"${bal:.2f} available"})
            else:
                return jsonify({"ok": False, "provider": provider,
                                "error": f"API rejected key: {d.get('request','unknown')}"})
        elif provider == "capsolver":
            # CapSolver balance: POST /getBalance with clientKey
            r = _httpx.post("https://api.capsolver.com/getBalance",
                json={"clientKey": api_key}, timeout=10)
            d = r.json()
            if d.get("errorId") == 0:
                bal = float(d.get("balance", 0))
                return jsonify({"ok": True, "provider": provider,
                                "balance_usd": bal,
                                "message": f"${bal:.2f} available"})
            else:
                return jsonify({"ok": False, "provider": provider,
                                "error": f"API rejected key: {d.get('errorDescription','unknown')}"})
        else:
            return jsonify({"ok": False,
                            "error": f"unknown provider: {provider}"})
    except Exception as e:
        return jsonify({"ok": False, "error": f"request failed: {str(e)[:200]}"})


@sites_bp.route("/api/sites/<sid>/manual_login",methods=["POST"])
def api_manual_login(sid):
    """Phase 19: skip auto-login and open the browser straight to the
    login URL with the recorder + manual banner active. The user logs
    in by hand; their clicks teach the app selectors for next time."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    ok,msg=runners[sid].start_manual_login()
    return jsonify({"ok":ok,"message":msg})


@sites_bp.route("/api/sites/<sid>/login_manual_done",methods=["POST"])
def api_login_manual_done(sid):
    """Phase 4.4: user finished a manual login. Capture cookies from the
    still-live Playwright context, save them, close the browser."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    ok,msg=runners[sid].finish_manual_login()
    return jsonify({"ok":ok,"message":msg})


@sites_bp.route("/api/sites/<sid>/login_manual_cancel",methods=["POST"])
def api_login_manual_cancel(sid):
    """User abandoned the manual login. Close the browser, no cookies."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    ok,msg=runners[sid].cancel_manual_login_pending()
    return jsonify({"ok":ok,"message":msg})


@sites_bp.route("/api/sites/<sid>/login_verify", methods=["POST"])
def api_login_verify(sid):
    """v3.43.51: run the post-wizard headless verification. Replays
    the login against the same persistent profile the manual flow
    used; optionally probes a member-only URL.

    Body: {"member_url": "https://example.com/member-area"}  (optional)

    Returns the structured verify result. Synchronous — typical
    runtime is 5-20 seconds depending on the site. The wizard UI
    shows a spinner while this is running and may poll
    /api/sites/<sid>/login_verify_status as a fallback if the POST
    itself times out at the proxy layer.
    """
    runners = _app_runners()
    if sid not in runners:
        return jsonify({"ok": False, "error": "site not found"}), 404
    body = request.get_json(silent=True) or {}
    member_url = (body.get("member_url") or "").strip()
    try:
        result = runners[sid].verify_login_after_wizard(
            member_url=member_url)
    except Exception as e:
        return jsonify({"ok": False,
                          "error": f"verify failed: "
                                   f"{type(e).__name__}: {e}"}), 500
    return jsonify({"ok": True, **result})


@sites_bp.route("/api/sites/<sid>/login_verify_status", methods=["GET"])
def api_login_verify_status(sid):
    """v3.43.51: read the last verify result. Used by the wizard's
    polling fallback in case the synchronous POST got cut off by a
    reverse proxy timeout."""
    runners = _app_runners()
    if sid not in runners:
        return jsonify({"ok": False, "error": "site not found"}), 404
    result = runners[sid].get_last_verify_result()
    if result is None:
        return jsonify({"ok": False,
                          "error": "no verification has run for this site"}), 404
    return jsonify({"ok": True, **result})


@sites_bp.route("/api/sites/<sid>/take_over_url",methods=["POST"])
def api_take_over_url(sid):
    """Open a non-headless Chromium at the given URL with the site's
    cookies and click recorder installed. User clicks through the
    download flow manually; subsequent take_over_done captures the
    learned trigger/row selectors and url_attribute."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    target_url=(request.json or {}).get("url","").strip()
    ok,msg=runners[sid].start_manual_download(target_url)
    return jsonify({"ok":ok,"message":msg})


@sites_bp.route("/api/sites/<sid>/take_over_done",methods=["POST"])
def api_take_over_done(sid):
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    ok,msg=runners[sid].finish_manual_download()
    # Phase 52 (v3.37.x): include a summary of what was just learned so
    # the UI can show a verification card before running on the rest of
    # the queue. Read from the stored config — finish_manual_download
    # has already persisted the captured selectors.
    summary = {}
    if ok:
        cfg = s_cfg.get(sid, {})
        for key in ("row_selector", "trigger_selector", "dl_selector",
                    "dismiss_selectors"):
            v = cfg.get(key)
            if v: summary[key] = v
    return jsonify({"ok":ok,"message":msg,"learned_summary":summary})


@sites_bp.route("/api/sites/<sid>/take_over_cancel",methods=["POST"])
def api_take_over_cancel(sid):
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    ok,msg=runners[sid].cancel_manual_download()
    return jsonify({"ok":ok,"message":msg})


@sites_bp.route("/api/sites/<sid>/load_cookies",methods=["POST"])
def api_load_cookies(sid):
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    if "file" not in request.files: return jsonify({"error":"No file"}),400
    try:
        raw=json.loads(request.files["file"].read().decode("utf-8"))
        items=raw if isinstance(raw,list) else sum(raw.values(),[]) if isinstance(raw,dict) else []
        cookies=[]
        for c in items:
            ss=c.get("sameSite","None")
            if ss not in ("Strict","Lax","None"): ss="None"
            e={"name":c.get("name",""),"value":c.get("value",""),"domain":c.get("domain",""),
               "path":c.get("path","/"),"sameSite":ss,"secure":bool(c.get("secure")),"httpOnly":bool(c.get("httpOnly"))}
            if c.get("expirationDate"): e["expires"]=int(c["expirationDate"])
            cookies.append(e)
        runners[sid].set_cookies(cookies); return jsonify({"ok":True,"count":len(cookies)})
    except Exception as e: return jsonify({"error":str(e)}),400
