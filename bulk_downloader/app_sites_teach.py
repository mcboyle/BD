"""app_sites.teach -- 11 @sites_bp route handlers, sub-sliced from app_sites.py (Tier M, pure motion).

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
    _apply_login_template_by_id,
    _check_csrf,
    _save_sites_config,
    _site_primary_url,
    _teach_cors_response,
    sites_bp,
)


# B1: a conservative age backstop for an in-flight capture marker, used only when
# there is no usable pid. >= 40 min sits comfortably above the 25-min capture
# auto-save, so a long-but-live capture is never cleared by age.
_CAPTURE_AGE_LIMIT_SECONDS = 40 * 60
_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")


def _capture_process_identity(pid: int) -> dict | None:
    """Read the non-reusable Linux identity for one capture process."""
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text(
            encoding="ascii", errors="replace"
        )
        tail = raw.rsplit(") ", 1)
        fields = tail[1].split() if len(tail) == 2 else []
        pid_start = fields[19] if len(fields) > 19 else ""
        boot_id = _BOOT_ID_PATH.read_text(encoding="ascii").strip()
    except (OSError, TypeError, ValueError):
        return None
    if not pid_start or not boot_id or boot_id.lower() == "unknown":
        return None
    return {"pid_start": pid_start, "boot_id": boot_id}


def _capture_marker_stale(cap: dict) -> bool:
    """B1 self-heal: decide whether an in-flight capture marker is stale (the
    capture died WITHOUT producing a draft). The caller has already confirmed no
    draft exists.

    Errs SAFE in every direction:
      * matching process identity -> NOT stale, even for a long capture;
      * recycled pid identity -> use the conservative age backstop;
      * dead pid -> stale (ProcessLookupError);
      * unavailable identity evidence -> decline to clear;
      * legacy/no usable pid identity -> use the age backstop on started_at;
      * neither a usable pid nor started_at -> NOT stale (the pre-B1 marker
        shape; treat as in-flight).
    """
    if not isinstance(cap, dict):
        return False
    try:
        pid = int(cap.get("pid"))
    except (TypeError, ValueError):
        pid = 0
    if pid > 0:
        recorded_start = cap.get("pid_start")
        recorded_boot = cap.get("boot_id")
        has_recorded_identity = (
            isinstance(recorded_start, str) and bool(recorded_start)
            and isinstance(recorded_boot, str) and bool(recorded_boot)
        )
        if has_recorded_identity:
            current = _capture_process_identity(pid)
            if current is not None:
                if (current["pid_start"] == recorded_start
                        and current["boot_id"] == recorded_boot):
                    return False
                # The numeric PID now names a different process. It carries no
                # authority over this marker; let age decide below.
            else:
                # Distinguish a dead owner from unavailable identity evidence.
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return True
                except (PermissionError, OSError):
                    return False
                return False
        else:
            # Legacy markers have no process-start receipt. A dead PID is still
            # decisive, while a live numeric PID alone cannot defeat age
            # recovery because it may have been recycled.
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                pass
            except OSError:
                return False
    # Age backstop for no identity, a recycled identity, or no usable PID.
    try:
        started = float(cap.get("started_at"))
    except (TypeError, ValueError):
        return False
    if started <= 0:
        return False
    return (time.time() - started) > _CAPTURE_AGE_LIMIT_SECONDS


@sites_bp.route("/api/sites/<sid>/template_status")
def api_template_status(sid):
    """Read-only reviewed-template status for the site's page.

    Returns the enabled reviewed template (if any) for the site's host plus
    the onboarding state (`approved_template_found` / `capture_required`,
    written by the onboarding tool/endpoint). Reads selectors/patterns only —
    never cookies, tokens, or signed URLs.
    """
    s_cfg = _app_s_cfg()
    cfg = (s_cfg or {}).get(sid)
    if cfg is None:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    try:
        from .template_assist import template_summary
        from .template_registry import find_template_for_url
        from .selector_lint import lint_template, has_blocking_issues
        url = _site_primary_url(cfg)
        tmpl_obj = find_template_for_url(url) if url else None
        summary = template_summary(tmpl_obj)
        # 3c: the run/download leg targets the site's CONTENT host (start_url),
        # which can differ from the LOGIN host that _site_primary_url prefers.
        # Resolve any ENABLED host-level template for the content/job host and
        # surface it when it isn't already what the primary status reports, so
        # the card never says "No reviewed template" while a host-level template
        # silently drives the run. Additive + read-only (selectors/host only).
        download_template = None
        job_url = ""
        for _k in ("start_url", "base_url", "url", "homepage",
                   "member_url", "site_url", "login_url"):
            _v = (cfg.get(_k) or "").strip()
            if _v.startswith(("http://", "https://")):
                job_url = _v
                break
        if job_url and job_url != url:
            job_sum = template_summary(find_template_for_url(job_url))
            if job_sum.get("enabled") and job_sum.get("host") != summary.get("host"):
                download_template = job_sum
        lint_issues = lint_template(tmpl_obj or {})
        onboarding = cfg.get("template_onboarding")
        if summary.get("enabled"):
            label = f"Reviewed Template: {summary.get('host')} enabled"
        elif onboarding == "capture_required":
            label = "Capture required — no reviewed template yet"
        else:
            label = "No reviewed template"
        # B1 self-heal: cfg["template_capture"] is set when onboarding launches
        # and otherwise cleared ONLY by the finish/cancel routes. If the capture
        # pipeline already finished out-of-band -- the 25-min auto-save, a
        # SIGTERM, or a second-shell `touch <wacz>.FINISH` -- the marker would
        # stick forever and the SPA would show a phantom in-flight control. When
        # the pipeline's draft has been written, the capture is done: clear the
        # marker so the control resolves. Reconciles against reality rather than
        # trusting the marker alone.
        cap = cfg.get("template_capture")
        capture_in_flight = bool(cap)
        if capture_in_flight and isinstance(cap, dict):
            _draft = (cap.get("draft") or "").strip()
            _draft_exists = bool(_draft) and Path(_draft).exists()
            # B1: a capture that DIED before a draft was built (SIGTERM, crash,
            # killed shell) leaves the marker stuck forever -> a phantom
            # Finish/Cancel control. When there is no draft, reconcile against
            # the capture process: clear only when it is provably gone (dead pid)
            # or the marker is older than the conservative age backstop. A live
            # capture is NEVER cleared. NOT wacz-exists (the wacz exists for the
            # whole legitimate in-flight window).
            if _draft_exists or (not _draft_exists and _capture_marker_stale(cap)):
                cfg.pop("template_capture", None)
                try:
                    _save_sites_config()
                except Exception:
                    pass
                capture_in_flight = False
        return jsonify({
            "ok": True,
            "site": sid,
            "url": url,
            "template": summary,
            "onboarding": onboarding,
            "auto_teach_first_run": cfg.get("auto_teach_first_run", True),
            "template_auto_detect_mode": cfg.get("template_auto_detect_mode"),
            "label": label,
            # 3c: the enabled host-level template that applies at download time
            # when it differs from the primary (login-host) resolution; null
            # otherwise. Read-only summary (host/selectors), no secrets.
            "download_template": download_template,
            # CAP-CANCEL: a truthy template_capture marker means an onboarding
            # capture subprocess is in flight; the SPA shows Finish & Cancel
            # controls. Boolean only — never the wacz/profile paths (F2 posture).
            "capture_in_flight": capture_in_flight,
            "lint": [i.to_dict() for i in lint_issues],
            "has_blocking_lint": has_blocking_issues(lint_issues),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@sites_bp.route("/api/sites/<sid>/session/reuse_onboarding", methods=["POST"])
def api_session_reuse_onboarding(sid):
    """Reuse the authenticated onboarding session for downloads (3e/C1).

    The onboarding/teach capture writes the authenticated session (login +
    cf_clearance) into profiles/<sid>-<host>-cloak, but the download worker
    opens profiles/<sid>/main and never reads it — so a host behind a hand-
    solved challenge stalls at download time even though the operator already
    established a session. This copies the login-continuity browser state from
    the most recent onboarding capture profile into the runtime download
    profiles (main + any existing workers/keepalive).

    Session REUSE, not challenge-solving: no challenge system is defeated; only
    local browser state the operator already produced is copied. Same-machine,
    local-only. Returns a value-free summary — copied item NAMES, counts, and
    the source host — never cookie/token/storage VALUES or filesystem paths
    (F2 posture). Body: {"ensure"?: [profile names]} (default ["main"]).
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
        summary = ps.sync_onboarding_to_runtime(sid, ensure=ensure)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
    synced = summary.get("synced") or {}
    reused = bool(synced)
    # Value-free contract: NAMES + counts + source host only — no paths/values.
    seeded = [{"profile": name, "items": its, "count": len(its)}
              for name, its in synced.items()]
    resp = {
        "ok": True,
        "site": sid,
        "reused": reused,
        "host": summary.get("host"),
        "seeded": seeded,
        "skipped_reason": summary.get("skipped_reason"),
    }
    return jsonify(resp)


@sites_bp.route("/api/sites/<sid>/template_onboard", methods=["POST"])
def api_template_onboard(sid):
    """Run template onboarding for the site (manual trigger from the UI).

    Classifies the site via the onboarding tool's pure planner and persists
    the resulting config keys. Body: {"run": bool} (default true). When a
    capture is required and `run` is true, the capture flow is launched as a
    detached subprocess (it opens the capture browser via the configured
    DISPLAY); approved sites never launch anything.
    """
    s_cfg = _app_s_cfg()
    _check_csrf()
    cfg = (s_cfg or {}).get(sid)
    if cfg is None:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    body = request.json or {}
    run = bool(body.get("run", True))
    try:
        from tools.onboard_site_template import (
            plan_site, build_capture_command, run_capture_flow,
        )
        plan = plan_site(cfg)
        if not plan:
            return jsonify({"ok": False,
                            "error": "site has no usable URL"}), 400
        cfg.update(plan)
        _save_sites_config()
        result = {"ok": True, "site": sid, **plan, "launched": False}
        if run and plan.get("template_onboarding") == "capture_required":
            display = _os.environ.get("DISPLAY", ":99")
            info = build_capture_command(sid, _site_primary_url(cfg), display)
            # B1: record started_at + the capture-wrapper pid so a marker left
            # behind by a DIED capture (no draft) can be self-healed in
            # /template_status. Launch first to obtain the pid, then persist the
            # marker once.
            _started_at = float(time.time())
            _cap_pid = run_capture_flow(info, run=True)  # detached Popen
            cfg["template_capture"] = {
                k: info[k] for k in ("profile_dir", "wacz", "draft", "display")
            }
            cfg["template_capture"]["started_at"] = _started_at
            if _cap_pid:
                cfg["template_capture"]["pid"] = int(_cap_pid)
                _identity = _capture_process_identity(int(_cap_pid))
                if _identity is not None:
                    cfg["template_capture"].update(_identity)
            _save_sites_config()
            result["launched"] = True
            result["capture"] = cfg["template_capture"]
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@sites_bp.route("/api/sites/<sid>/template_capture_cancel", methods=["POST"])
def api_template_capture_cancel(sid):
    """Cancel an in-flight onboarding capture (CAP-CANCEL).

    The onboarding launch (api_template_onboard, capture_required) starts
    capture_session.py as a DETACHED subprocess with
    ``--finish-file <wacz>.FINISH``; it polls for the ``<wacz>.CANCEL`` sibling
    and discards the session (no WACZ written) when it appears. That subprocess
    has no cockpit task_id, so /cockpit/api/captures/finish cannot reach it —
    here we drop the per-capture CANCEL sentinel directly from the persisted
    cfg["template_capture"]["wacz"] handle, then clear the marker so the SPA
    control disappears. No filesystem paths are returned (F2 posture). The
    .CANCEL watch in capture_session.py (a release guard) is unchanged.
    """
    s_cfg = _app_s_cfg()
    _check_csrf()
    cfg = (s_cfg or {}).get(sid)
    if cfg is None:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    cap = cfg.get("template_capture") or {}
    wacz = (cap.get("wacz") or "").strip()
    if not wacz:
        # nothing in flight — idempotent, not an error
        return jsonify({"ok": True, "site": sid, "cancelled": False,
                        "reason": "no capture in flight"})
    try:
        Path(wacz).with_suffix(".CANCEL").touch()
        cfg.pop("template_capture", None)
        _save_sites_config()
        return jsonify({"ok": True, "site": sid, "cancelled": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@sites_bp.route("/api/sites/<sid>/template_capture_finish", methods=["POST"])
def api_template_capture_finish(sid):
    """Finish & SAVE an in-flight onboarding capture (CAP-FINISH).

    Mirror of ``template_capture_cancel`` but drops the ``<wacz>.FINISH``
    sentinel (SAVE) instead of ``<wacz>.CANCEL`` (DISCARD). The onboarding launch
    starts ``capture_session.py`` detached with ``--finish-file <wacz>.FINISH``;
    it is blocked in its held-open loop waiting for exactly that sentinel, then
    writes the WACZ and the onboarding shell script builds the draft. Before this
    route the ONLY ways to finish were a second SSH shell (``touch
    <wacz>.FINISH``) or the 25-minute auto-save -- there was no GUI finish, so
    the whole onboarding pipeline (including the draft build) stalled behind an
    un-GUI-able step. Clears the in-flight marker so the SPA control resolves
    immediately. No filesystem paths are returned (F2 posture). The ``.FINISH``
    watch in ``capture_session.py`` is unchanged.
    """
    s_cfg = _app_s_cfg()
    _check_csrf()
    cfg = (s_cfg or {}).get(sid)
    if cfg is None:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    cap = cfg.get("template_capture") or {}
    wacz = (cap.get("wacz") or "").strip()
    if not wacz:
        # nothing in flight — idempotent, not an error
        return jsonify({"ok": True, "site": sid, "finished": False,
                        "reason": "no capture in flight"})
    try:
        Path(wacz).with_suffix(".FINISH").touch()
        cfg.pop("template_capture", None)
        _save_sites_config()
        return jsonify({"ok": True, "site": sid, "finished": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@sites_bp.route("/api/sites/<sid>/template/dry_run", methods=["POST"])
def api_template_dry_run(sid):
    """#5 — Static template test runner. Body: {"html"?: "...", "url"?: "..."}.

    Reports whether a reviewed template matches the site URL, its selector
    groups / resolutions / redacted network patterns, lint warnings, static
    selector hit-counts against any supplied HTML, the candidate
    classification, and whether a final safe candidate would be selected.
    Never fetches a page, never downloads.
    """
    s_cfg = _app_s_cfg()
    _check_csrf()
    cfg = (s_cfg or {}).get(sid)
    if cfg is None:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    body = request.json or {}
    html = body.get("html") or ""
    try:
        from .dry_run import template_dry_run
        url = body.get("url") or _site_primary_url(cfg) or ""
        return jsonify(template_dry_run(url, html=html))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@sites_bp.route("/api/sites/<sid>/teach_verify",methods=["POST","OPTIONS"])
def api_teach_verify(sid):
    runners = _app_runners()
    if request.method == "OPTIONS": return _teach_cors_response({})
    if sid not in runners: return _teach_cors_response({"ok":False,"error":"Not found"},404)
    data = request.json or {}
    picks = data.get("selectors") or {}
    ok, detail = runners[sid].teach_verify(picks)
    payload = {"ok": ok}; payload.update(detail or {})
    return _teach_cors_response(payload)


@sites_bp.route("/api/sites/<sid>/teach_test_download",methods=["POST","OPTIONS"])
def api_teach_test_download(sid):
    """v3.43.16: dry-run the picks against the live page AND pull ~2 MB
    of the resulting URL to confirm it's real video bytes. Used by the
    teach panel to gate commit on a successful test fetch.

    Same CORS-permissive flow as teach_verify since it's called from
    the in-page teach overlay running in the takeover browser context.
    """
    runners = _app_runners()
    if request.method == "OPTIONS": return _teach_cors_response({})
    if sid not in runners: return _teach_cors_response({"ok":False,"error":"Not found"},404)
    data = request.json or {}
    picks = data.get("selectors") or {}
    ok, detail = runners[sid].teach_test_download(picks)
    payload = {"ok": ok}; payload.update(detail or {})
    return _teach_cors_response(payload)


@sites_bp.route("/api/sites/<sid>/teach_commit",methods=["POST","OPTIONS"])
def api_teach_commit(sid):
    runners = _app_runners()
    if request.method == "OPTIONS": return _teach_cors_response({})
    if sid not in runners: return _teach_cors_response({"ok":False,"error":"Not found"},404)
    data = request.json or {}
    picks = data.get("selectors") or {}
    raw_events = data.get("events") or []
    ok, msg = runners[sid].teach_commit(picks, raw_events)
    return _teach_cors_response({"ok":ok,"message":msg})


@sites_bp.route("/api/sites/<sid>/teach_cancel",methods=["POST","OPTIONS"])
def api_teach_cancel(sid):
    runners = _app_runners()
    if request.method == "OPTIONS": return _teach_cors_response({})
    if sid not in runners: return _teach_cors_response({"ok":False,"error":"Not found"},404)
    ok, msg = runners[sid].teach_cancel()
    return _teach_cors_response({"ok":ok,"message":msg})


@sites_bp.route("/api/sites/<sid>/teach_save_template",methods=["POST","OPTIONS"])
def api_teach_save_template(sid):
    """v3.43.16: save the current teach picks as a reusable user template.

    Called from the teach panel's Save Template dialog. Goes through
    _teach_cors_response (no CSRF, no auth) like the other teach_*
    endpoints because the takeover browser is on a different origin
    and can't carry session cookies.

    Body matches the /api/user_templates POST schema:
      {name, description, patterns, learned, config_defaults?, source?}

    The sid is included in the route for symmetry with the rest of the
    teach API and so we can validate the runner still exists, but the
    saved template itself is global (not site-scoped) — the same
    template applies to any future site matching its URL pattern."""
    runners = _app_runners()
    if request.method == "OPTIONS": return _teach_cors_response({})
    if sid not in runners: return _teach_cors_response({"ok":False,"error":"Not found"},404)
    from . import user_templates as _ut
    data = request.json or {}
    ok, result = _ut.save_user_template(
        name=str(data.get("name", "")),
        description=str(data.get("description", "")),
        patterns=list(data.get("patterns") or []),
        learned=data.get("learned") or {},
        config_defaults=data.get("config_defaults") or None,
        source=str(data.get("source", "user_teach")),
        tid=data.get("id") or None,
    )
    if not ok:
        return _teach_cors_response({"ok": False, "error": result}, 400)
    return _teach_cors_response({"ok": True, "template": result})


@sites_bp.route("/api/sites/<sid>/login_template/apply", methods=["POST"])
def api_login_template_apply(sid):
    """Apply a login template to a site. Body: {login_template: "id"}.
    Writes the template's selectors into the site's learned.login so
    the first-run manual-login teach is skipped."""
    body = request.get_json(silent=True) or {}
    tid = (body.get("login_template") or "").strip()
    if not tid:
        return jsonify({"ok": False,
                          "error": "login_template id required"}), 400
    ok, msg = _apply_login_template_by_id(sid, tid)
    return jsonify({"ok": ok, "message": msg}) if ok \
        else (jsonify({"ok": False, "error": msg}), 400)


@sites_bp.route("/api/sites/<sid>/templates/apply", methods=["POST"])
def api_template_apply(sid):
    """Merge a template's learned block into the site config. Existing
    learned selectors are PRESERVED (the template's are appended). User
    can then teach to refine; the merge logic is the same one used by
    real teach commits.

    v3.43.16: templates can also ship a `config_defaults` block — top-level
    site settings (quality_preference, min_resolution, etc.) that the
    template recommends. These are only applied to fields where the
    current value is empty/None/0 — never overwrite settings the user
    has already configured."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"ok":False,"error":"Not found"}),404
    body = request.json or {}
    tpl_id = (body.get("template_id") or "").strip()
    from . import templates as _tpls
    from .learn import merge_learned
    tpl = _tpls.get(tpl_id)
    if not tpl: return jsonify({"ok":False,"error":f"unknown template: {tpl_id}"}),400
    cfg = s_cfg.get(sid, {})
    download = (tpl.get("learned") or {}).get("download") or {}
    if download:
        merge_learned(cfg, download, kind="download")
    # v3.43.16: apply config_defaults non-destructively
    applied_defaults = []
    for key, val in (tpl.get("config_defaults") or {}).items():
        existing = cfg.get(key)
        # Only apply if the user hasn't set this. "Unset" = missing,
        # None, empty string, or 0 for numerics.
        if existing in (None, "", 0, 0.0) or key not in cfg:
            cfg[key] = val
            applied_defaults.append(key)
    # v3.62.2: record the applied template so the runner's auto-teach
    # preflight treats this site as ready-to-run (teach only on failure).
    cfg["applied_template"] = tpl_id
    s_cfg[sid] = cfg
    runners[sid].update_config(cfg)
    _save_sites_config()
    return jsonify({"ok": True, "template": tpl["name"],
                    "added_roles": list(download.keys()),
                    "applied_defaults": applied_defaults})
