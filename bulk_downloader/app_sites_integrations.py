"""app_sites.integrations -- 12 @sites_bp route handlers, sub-sliced from app_sites.py (Tier M, pure motion).

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
    _app_s_meta,
    sites_bp,
)


@sites_bp.route("/api/sites/<sid>/export")
def api_sites_export(sid):
    """Export one site's config as a portable JSON envelope.

    `?include_secrets=1` includes credentials (for a personal backup);
    default strips them. The response is sent with a Content-Disposition
    so the browser saves it as a file."""
    s_cfg = _app_s_cfg()
    from . import site_editor as _se
    if sid not in s_cfg:
        return jsonify({"error": "Not found"}), 404
    include = request.args.get("include_secrets", "") \
        .lower() in ("1", "true", "yes")
    envelope = _se.export_config(s_cfg[sid], include_secrets=include)
    # Build a filename from the site name, sanitized
    raw_name = (s_cfg[sid].get("name") or sid)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(raw_name))[:60]
    resp = jsonify(envelope)
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="bd-site-{safe_name}.json"')
    return resp


@sites_bp.route("/api/sites/<sid>/jellyfin/diagnose")
def api_jellyfin_diagnose(sid):
    """Probe Jellyfin connectivity + auth for a site."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error": "Not found"}), 404
    cfg = s_cfg.get(sid, {})
    try:
        from bulk_downloader import jellyfin_deep
        client = jellyfin_deep.get_client_for_site(cfg)
        return jsonify(client.diagnose())
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})


@sites_bp.route("/api/sites/<sid>/jellyfin/libraries")
def api_jellyfin_libraries(sid):
    """List the Jellyfin libraries. UI uses this to confirm setup."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error": "Not found"}), 404
    cfg = s_cfg.get(sid, {})
    try:
        from bulk_downloader import jellyfin_deep
        client = jellyfin_deep.get_client_for_site(cfg)
        if not client.configured:
            return jsonify({"ok": False,
                             "error": "Jellyfin URL or API key not set"})
        libs = client.list_libraries()
        return jsonify({"ok": True, "libraries": libs})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})


@sites_bp.route("/api/sites/<sid>/plex/diagnose")
def api_plex_diagnose(sid):
    """Probe Plex connectivity + auth for a site. Same structured
    response shape as the Stash/JD/qB diagnose endpoints."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error": "Not found"}), 404
    cfg = s_cfg.get(sid, {})
    try:
        from bulk_downloader import plex_deep
        client = plex_deep.get_client_for_site(cfg)
        return jsonify(client.diagnose())
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})


@sites_bp.route("/api/sites/<sid>/plex/sections")
def api_plex_sections(sid):
    """List the Plex libraries available on the configured server.
    UI uses this to populate a section dropdown so the user doesn't
    have to manually look up section IDs."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error": "Not found"}), 404
    cfg = s_cfg.get(sid, {})
    try:
        from bulk_downloader import plex_deep
        client = plex_deep.get_client_for_site(cfg)
        if not client.configured:
            return jsonify({"ok": False,
                             "error": "Plex URL or token not configured"})
        sections = client.list_sections()
        return jsonify({"ok": True, "sections": sections})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})


@sites_bp.route("/api/sites/<sid>/stash/diagnose")
def api_stash_diagnose(sid):
    """Probe Stash connectivity + auth for a site. Returns the same
    structured shape as JD/qB diagnose endpoints so the UI can render
    them with the same component."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error": "Not found"}), 404
    cfg = s_cfg.get(sid, {})
    try:
        from bulk_downloader import stash_deep
        client = stash_deep.get_client_for_site(cfg)
        return jsonify(client.diagnose())
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})


@sites_bp.route("/api/sites/<sid>/stash/preview_url", methods=["POST"])
def api_stash_preview_url(sid):
    """Pre-download scrape preview. Surfaces what Stash would extract
    from `url` without committing to a download. Body: {url}. Returns
    the scrape result OR {ok:False, reason} when nothing useful was
    found."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    url = data.get("url") or ""
    if not url:
        return jsonify({"ok": False, "error": "missing url"}), 400
    runner = runners[sid]
    try:
        result = runner._stash_scrape_preview(url)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})
    if not result:
        return jsonify({"ok": False, "reason":
                         "no scrape result (URL not supported by any "
                         "configured Stash scraper, or deep features "
                         "not enabled for this site)"})
    from bulk_downloader.stash_deep import summarize_scrape
    return jsonify({"ok": True, "result": result,
                     "summary": summarize_scrape(result)})


@sites_bp.route("/api/sites/<sid>/hooks/test", methods=["POST"])
def api_hooks_test(sid):
    """Test one specific hook sink. Body: {"kind": "post_cmd"|"webhook"|
    "stash"|"plex"|"jellyfin"|"ha", "url": "..."(optional override)}.

    All tests use a synthetic vars dict so the operator sees a realistic
    payload even without a recent download. Failure messages include the
    actual HTTP status / stderr so config issues are obvious."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    body = request.json or {}
    kind = body.get("kind", "")
    cfg = s_cfg.get(sid, {}) or {}
    cfg = dict(cfg); cfg["_site_id"] = sid
    from .hooks import (build_vars, run_command_hook, send_webhook,
                        stash_trigger_scan, plex_refresh, jellyfin_refresh,
                        home_assistant_notify, _render_template)
    job = {"url": "https://example.com/test", "filename": "test.mp4",
           "path": "/tmp/test.mp4", "file_size": 12345678,
           "resolution": "4K", "hash": "abc123",
           "message": "Bulk Downloader hook test"}
    vars = build_vars(cfg, "completed", job)
    try:
        if kind == "post_cmd":
            cmd = (body.get("override") or cfg.get("post_download_cmd","")).strip()
            if not cmd: return jsonify({"ok":False,"message":"No command configured"})
            ok, msg = run_command_hook(cmd, vars, timeout=30)
            return jsonify({"ok":ok,"message":msg[:1000]})
        if kind == "webhook":
            url = body.get("override") or ""
            if not url:
                urls = [u for u in (cfg.get("webhook_urls","") or "").splitlines() if u.strip()]
                url = urls[0] if urls else ""
            if not url: return jsonify({"ok":False,"message":"No webhook URL configured"})
            payload = dict(vars); payload["job"] = job
            ok, msg = send_webhook(_render_template(url, vars), payload)
            return jsonify({"ok":ok,"message":msg})
        if kind == "stash":
            ok, msg = stash_trigger_scan(
                body.get("override") or cfg.get("stash_url",""),
                cfg.get("stash_api_key",""),
                paths=[],  # full library scan as a test
            )
            return jsonify({"ok":ok,"message":msg})
        if kind == "plex":
            ok, msg = plex_refresh(
                body.get("override") or cfg.get("plex_url",""),
                cfg.get("plex_token",""),
                section_id=cfg.get("plex_section_id") or None,
            )
            return jsonify({"ok":ok,"message":msg})
        if kind == "jellyfin":
            ok, msg = jellyfin_refresh(
                body.get("override") or cfg.get("jellyfin_url",""),
                cfg.get("jellyfin_api_key",""),
            )
            return jsonify({"ok":ok,"message":msg})
        if kind == "ha":
            ok, msg = home_assistant_notify(
                body.get("override") or cfg.get("ha_url",""),
                cfg.get("ha_token",""),
                cfg.get("ha_service","notify"),
                _render_template(cfg.get("ha_message_template","[{site}] test"), vars),
                title="Bulk Downloader hook test",
            )
            return jsonify({"ok":ok,"message":msg})
        return jsonify({"ok":False,"message":f"Unknown hook kind: {kind}"}),400
    except Exception as e:
        return jsonify({"ok":False,"message":f"{type(e).__name__}: {str(e)[:200]}"}),500


@sites_bp.route("/api/sites/<sid>/hooks/spillover_check", methods=["GET"])
def api_hooks_spillover_check(sid):
    """Show which download dir would be chosen RIGHT NOW based on the
    site's spillover config and current free-space readings. Lets the
    operator verify their spillover ordering before a download runs."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    from .hooks import resolve_download_dir, _split_lines
    cfg = s_cfg.get(sid, {}) or {}
    chosen, reason = resolve_download_dir(
        cfg,
        free_threshold_pct=float(cfg.get("spillover_threshold_pct", 5.0) or 5.0),
    )
    import shutil as _shutil
    candidates = []
    for d in [cfg.get("download_dir","")] + _split_lines(cfg.get("spillover_dirs","")):
        if not d: continue
        try:
            u = _shutil.disk_usage(d)
            candidates.append({
                "path": d, "exists": True,
                "free_gb": round(u.free/(1024**3), 2),
                "total_gb": round(u.total/(1024**3), 2),
                "free_pct": round((u.free/u.total)*100, 1),
            })
        except Exception as e:
            candidates.append({"path": d, "exists": False, "error": str(e)[:120]})
    return jsonify({"ok":True, "chosen": chosen, "reason": reason,
                    "candidates": candidates})


@sites_bp.route("/api/sites/<sid>/storage_tier/status")
def api_storage_tier_status(sid):
    """Per-site storage tier status — feature enabled, dest config,
    when last sweep ran and what it migrated."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners:
        return jsonify({"error": "Not found"}), 404
    cfg = s_cfg.get(sid, {})
    try:
        from . import storage_tier as _st
        sched = _st.get_scheduler()
        full = sched.get_status()
        per_site = (full.get("per_site") or {}).get(sid, {})
        return jsonify({
            "ok": True,
            "enabled": bool(cfg.get("storage_tier_enabled")),
            "dest_dir": cfg.get("storage_tier_dir") or "",
            "age_days": cfg.get("storage_tier_age_days", 30),
            "min_size_mb": cfg.get("storage_tier_min_size_mb", 0),
            "mode": cfg.get("storage_tier_mode", "move"),
            "scheduler_running": full.get("running"),
            "last_run_ts": per_site.get("last_run_ts"),
            "last_summary": per_site.get("summary"),
        })
    except Exception as e:
        return jsonify({"ok": False,
                          "error": f"{type(e).__name__}: {e}"}), 500


@sites_bp.route("/api/sites/<sid>/storage_tier/run_now", methods=["POST"])
def api_storage_tier_run_now(sid):
    """Trigger a migration pass immediately, out-of-cycle. Useful
    after the user changes config (e.g. lowered age_days) without
    waiting for the next hourly tick.

    Runs synchronously — if the migration is huge, the HTTP request
    blocks until it completes. UI should show a spinner."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners:
        return jsonify({"error": "Not found"}), 404
    cfg = s_cfg.get(sid, {})
    dl_dir = (cfg.get("download_dir") or "").strip()
    if not dl_dir:
        return jsonify({"ok": False,
                          "error": "download_dir not set"}), 400
    try:
        from . import storage_tier as _st
        summary = _st.run_site_migration(sid, cfg, dl_dir)
        return jsonify({"ok": True, "summary": summary})
    except Exception as e:
        return jsonify({"ok": False,
                          "error": f"{type(e).__name__}: {e}"}), 500


@sites_bp.route("/api/sites/<sid>/export_watchlist")
def api_export_watchlist(sid):
    """Phase 8.2: export URLs in failed and/or needs_review status as a
    plain text file (one URL per line). Useful for moving a curated batch
    out of the live queue for separate review without losing track.

    Args (querystring):
      status: comma-separated list of statuses to include (default
              'failed,needs_review'). Pass 'all' to include every status.
      include_message: '1' to prepend a `# message` comment line before
              each URL, useful for triage.

    Returns text/plain so the browser downloads it as a file."""
    runners = _app_runners()
    s_meta = _app_s_meta()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    statuses=(request.args.get("status") or "failed,needs_review").split(",")
    statuses=[s.strip() for s in statuses if s.strip()]
    include_msg=request.args.get("include_message")=="1"
    runner=runners[sid]; site_name=s_meta.get(sid,{}).get("name",sid)
    lines=[f"# Watchlist export for site '{site_name}' ({sid})",
           f"# Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
           f"# Statuses: {', '.join(statuses)}", ""]
    count=0
    with runner._lock:
        for u in runner.urls:
            j=runner.jobs.get(u,{})
            if "all" in statuses or j.get("status") in statuses:
                if include_msg and j.get("message"):
                    lines.append(f"# [{j.get('status','?')}] {j['message']}")
                lines.append(u); count+=1
    lines.insert(3, f"# Total URLs: {count}")
    body="\n".join(lines)+"\n"
    fname=f"watchlist_{site_name}_{datetime.now().strftime('%Y-%m-%d')}.txt"
    # Sanitize filename for Content-Disposition
    fname=re.sub(r"[^\w.\-]","_",fname)
    return Response(body, mimetype="text/plain",
                    headers={"Content-Disposition":f'attachment; filename="{fname}"'})
