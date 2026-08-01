"""app_sites.queue -- 32 @sites_bp route handlers, sub-sliced from app_sites.py (Tier M, pure motion).

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
from .runner import _ts, _ts_iso
from datetime import datetime
from .db import db_search
from .db import queue_upsert
from .app_sites import (
    _app_runners,
    _app_s_cfg,
    _do_action,
    _validate_bulk_urls,
    sites_bp,
)


@sites_bp.route("/api/sites/<sid>/jd/diagnose")
def api_jd_diagnose(sid):
    """Run JD bridge connectivity check for a site. Returns the full
    diagnose() output so the UI can show host/port, whether the API
    is enabled, and a setup hint if not. Called from the edit modal's
    'Test JD' button and from the global health pill on click."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    cfg = s_cfg.get(sid, {})
    try:
        from bulk_downloader import jd_bridge
        client = jd_bridge.get_client_for_site(cfg)
        d = client.diagnose()
        d["ok"] = bool(d.get("api_enabled"))
        d["backend"] = (cfg.get("backend") or "teach")
        return jsonify(d)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})


@sites_bp.route("/api/sites/<sid>/jd/coverage")
def api_jd_coverage(sid):
    """JD-3 (v3.66.694): report whether JDownloader covers this site's host
    (JD has a hoster plugin for it) so the operator can see coverage before
    switching a site to the jd backend. Mirrors jd/diagnose. Returns
    {ok, available, host, covered, matched, jd_host_count, backend, hint?}.

    JD2's deprecated Remote API has no cleanly-documented supported-hosts
    endpoint, so the query path is a DOCUMENTED ASSUMPTION overridable via the
    declared per-site config field `jd_supported_hosts_path` (JD-3 @702); a JD that lacks it reads
    available=False + a hint (never an error). Live-verified on-stash."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    cfg = s_cfg.get(sid, {})
    try:
        from bulk_downloader import jd_bridge
        from urllib.parse import urlsplit
        client = jd_bridge.get_client_for_site(cfg)
        try:
            path = (cfg.get("jd_supported_hosts_path") or "").strip() or None
            jd_hosts = client.supported_hosts(path)
            raw = (cfg.get("url") or cfg.get("base_url")
                   or cfg.get("host") or sid or "")
            host = urlsplit(raw if "//" in raw else "//" + raw).hostname or raw
            cov = jd_bridge.host_coverage(host, jd_hosts)
            available = bool(jd_hosts)
            out = {"ok": True, "available": available,
                   "backend": (cfg.get("backend") or "teach")}
            out.update(cov)
            if not available:
                out["hint"] = (
                    "JD returned no supported-hosts list. Verify JDownloader's "
                    "Remote API ('Deprecated API') is enabled, and set "
                    "jd_supported_hosts_path in the site config to the correct "
                    "endpoint for your JD build.")
            return jsonify(out)
        finally:
            client.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})


@sites_bp.route("/api/sites/<sid>/qb/diagnose")
def api_qb_diagnose(sid):
    """Run qB bridge connectivity check for a site. Mirrors the JD
    diagnose endpoint. Returns reachable/api_enabled/logged_in/version
    plus a hint when something's misconfigured."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    cfg = s_cfg.get(sid, {})
    try:
        from bulk_downloader import qb_bridge
        client = qb_bridge.get_client_for_site(cfg)
        try:
            d = client.diagnose()
            d["ok"] = bool(d.get("api_enabled"))
            d["backend"] = (cfg.get("backend") or "teach")
            return jsonify(d)
        finally:
            client.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})


@sites_bp.route("/api/sites/<sid>/events")
def api_events(sid):
    """Live event log. Polled by the UI's event drawer.

    Query params:
      after  — return events with seq > this value (incremental polling)
      limit  — max events to return (default 200, hard cap 500)
      url    — filter to a single URL
      kind   — filter to a single event kind (state / js_error / network /
               login / download / takeover / retry)

    Response:
      { ok: true, events: [...], last_seq: N }
    where last_seq is the highest seq in the BUFFER (not just the response)
    so the UI knows whether to expect more."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"ok":False,"error":"Not found"}),404
    after = int(request.args.get("after", 0) or 0)
    limit = min(int(request.args.get("limit", 200) or 200), 500)
    url_filter = request.args.get("url") or None
    kind_filter = request.args.get("kind") or None
    runner = runners[sid]
    events = runner.get_events(after_seq=after, limit=limit,
                                url_filter=url_filter, kind_filter=kind_filter)
    last_seq = runner._event_seq
    return jsonify({"ok": True, "events": events, "last_seq": last_seq})


@sites_bp.route("/api/sites/<sid>/timeline")
def api_timeline(sid):
    """Per-URL event timeline. Returns ALL events for one URL (not capped
    by `after`), so the UI can render the full history of state transitions
    + screenshots for a single URL when the user clicks a row."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"ok":False,"error":"Not found"}),404
    url = request.args.get("url","")
    if not url: return jsonify({"ok":False,"error":"url required"}),400
    runner = runners[sid]
    events = runner.get_events(after_seq=0, limit=500, url_filter=url)
    return jsonify({"ok": True, "events": events, "url": url})


@sites_bp.route("/api/sites/<sid>/load_urls",methods=["POST"])
def api_load_urls(sid):
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    content=(request.files["file"].read().decode("utf-8","ignore") if "file" in request.files
             else (request.json or {}).get("text",""))
    urls=[u.strip() for u in content.splitlines() if u.strip().startswith("http")]
    if not urls: return jsonify({"error":"No valid URLs"}),400
    # Phase 7.4: folder_scan flag — pre-mark URLs whose filename appears
    # to already exist on disk. Sent via querystring or form field.
    folder_scan=(request.args.get("folder_scan")=="1"
                 or (request.form.get("folder_scan") if request.form else "")=="1")
    result=runners[sid].load_urls(urls,folder_scan=folder_scan)
    if len(result)==3:
        added,dupes,skipped=result
        return jsonify({"ok":True,"added":added,"dupes_skipped":dupes,"already_on_disk":skipped})
    added,dupes=result
    return jsonify({"ok":True,"added":added,"dupes_skipped":dupes})


@sites_bp.route("/api/sites/<sid>/queue_url", methods=["POST"])
def api_site_queue_url(sid):
    """Force a URL into a specific site's queue, bypassing the
    routing logic. Used by the extension's 'Send to <site>...'
    submenu override.

    Body: {"url": "..."} OR {"urls": [...]}

    Returns: {ok, added, dupes, total}"""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners:
        return jsonify({"ok": False, "error": "Not found"}), 404
    body = request.get_json(silent=True) or {}
    urls = body.get("urls")
    if not urls:
        single = body.get("url")
        urls = [single] if single else []
    urls = [str(u).strip() for u in (urls or [])
              if str(u).strip().startswith("http")]
    if not urls:
        return jsonify({"ok": False, "error": "no http URLs provided"}), 400
    added, dupes, *_ = runners[sid].load_urls(urls)
    return jsonify({
        "ok": True,
        "added": added,
        "dupes": dupes,
        "total": len(urls),
        "site_id": sid,
        "site_name": s_cfg.get(sid, {}).get("name") or sid,
    })


@sites_bp.route("/api/sites/<sid>/reorder",methods=["POST"])
def api_reorder(sid):
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    runners[sid].reorder_urls(request.json or []); return jsonify({"ok":True})


@sites_bp.route("/api/sites/<sid>/priority",methods=["POST"])
def api_priority(sid):
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    data=request.json or {}; runners[sid].set_priority(data.get("url",""),data.get("priority","normal"))
    return jsonify({"ok":True})


@sites_bp.route("/api/sites/<sid>/bulk_priority",methods=["POST"])
def api_bulk_priority(sid):
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    data=request.json or {}
    urls=data.get("urls",[]) or []
    pri=data.get("priority","normal")
    if pri not in ("normal","high"): return jsonify({"error":"bad priority"}),400
    n=runners[sid].bulk_priority(urls,pri)
    return jsonify({"ok":True,"count":n})


@sites_bp.route("/api/sites/<sid>/bulk_delete",methods=["POST"])
def api_bulk_delete(sid):
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    urls=(request.json or {}).get("urls",[]) or []
    n=runners[sid].bulk_delete(urls)
    return jsonify({"ok":True,"removed":n})


@sites_bp.route("/api/sites/<sid>/bulk_approve",methods=["POST"])
def api_bulk_approve(sid):
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    urls=(request.json or {}).get("urls",[]) or []
    n=runners[sid].bulk_approve(urls)
    return jsonify({"ok":True,"approved":n})


@sites_bp.route("/api/sites/<sid>/bulk_pause", methods=["POST"])
def api_bulk_pause(sid):
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}), 404
    urls = (request.json or {}).get("urls", []) or []
    n = runners[sid].bulk_pause(urls)
    return jsonify({"ok": True, "paused": n})


@sites_bp.route("/api/sites/<sid>/bulk_resume", methods=["POST"])
def api_bulk_resume(sid):
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}), 404
    urls = (request.json or {}).get("urls", []) or []
    n = runners[sid].bulk_resume(urls)
    return jsonify({"ok": True, "resumed": n})


@sites_bp.route("/api/sites/<sid>/bulk_retry", methods=["POST"])
def api_bulk_retry(sid):
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}), 404
    urls = (request.json or {}).get("urls", []) or []
    n = runners[sid].bulk_retry(urls)
    return jsonify({"ok": True, "retried": n})


# v3.66.726: /api/sites/<sid>/bulk_reorder REMOVED -- it was a SHADOW.
#
# It took {"order": [url, ...]} and duplicated /api/sites/<sid>/jobs/reorder, which takes
# {"ordering": {url: ord}} and is what SortableQueueGroup actually calls. Two endpoints
# doing one job, and the frontend used the other one: bulk_reorder was called by NOTHING
# but its own route. Wiring it (724 considered it) would have added a second, worse path
# to the same behaviour and scored as a reachability WIN. A second way to do one thing is
# not reachability, it is debt -- the /api/sched_exports precedent from 716.
#
# runner_queue.bulk_reorder() stays: it is part of the runner contract (pinned by
# test_v3_49_phase2) and removing it is a separate blast radius.


@sites_bp.route("/api/sites/<sid>/queue/search")
def api_queue_search(sid):
    from .db import queue_search as _qs
    q = (request.args.get("q") or "").strip() or None
    status = (request.args.get("status") or "").strip() or None
    priority = (request.args.get("priority") or "").strip() or None
    try:
        after = (int(request.args.get("after_ord"))
                 if request.args.get("after_ord") not in (None, "") else None)
    except ValueError:
        after = None
    try:
        limit = max(1, min(1000, int(request.args.get("limit", 200))))
    except ValueError:
        limit = 200
    rows, next_cursor = _qs(site_id=sid, query=q, status=status,
                            priority=priority, after_ord=after, limit=limit)
    return jsonify({
        "ok": True,
        "rows": rows,
        "count": len(rows),
        "next_cursor": next_cursor,
        "site_id": sid,
    })


@sites_bp.route("/api/sites/<sid>/queue/counts")
def api_queue_counts(sid):
    """Aggregate by-status counts for a single site's queue. Cheaper than
    /api/status when only the summary chips need updating."""
    from .db import queue_count_by_status
    counts = queue_count_by_status(sid)
    return jsonify({"ok": True, "counts": counts, "site_id": sid})


@sites_bp.route("/api/sites/<sid>/queue/grouped")
def api_queue_grouped(sid):
    """v3.49 (#57): bucket the queue by host/path/status/priority for the
    collapsible group view. Returns {group_key: [rows...]} sorted by
    group size descending so the busiest groups render first."""
    from .db import queue_group_by
    group_by = (request.args.get("by") or "host").strip().lower()
    try:
        limit = max(1, min(5000, int(request.args.get("limit", 2000))))
    except ValueError:
        limit = 2000
    groups = queue_group_by(sid, group_by=group_by, limit=limit)
    # Sort by group size, then by name for stable ordering.
    out = sorted(
        ({"key": k, "count": len(v), "rows": v} for k, v in groups.items()),
        key=lambda g: (-g["count"], g["key"])
    )
    return jsonify({
        "ok": True,
        "site_id": sid,
        "group_by": group_by,
        "groups": out,
        "total_rows": sum(g["count"] for g in out),
    })


@sites_bp.route("/api/sites/<sid>/queue/export")
def api_queue_export(sid):
    """v3.49 (#62): dump the live queue to a JSON snapshot. Used by the
    "Export queue" button + included automatically in any /api/backup."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error": "Not found"}), 404
    from .db import queue_load
    rows = queue_load(sid)
    return jsonify({
        "ok": True,
        "format_version": 1,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "site_id": sid,
        "count": len(rows),
        "rows": [
            {"url": r["url"], "priority": r.get("priority", ""),
             "status": r.get("status", "pending"),
             "force_download": bool(r.get("force_download")),
             "message": r.get("message", "")}
            for r in rows
        ],
    })


@sites_bp.route("/api/sites/<sid>/queue/import", methods=["POST"])
def api_queue_import(sid):
    """v3.49 (#63): import a queue snapshot. Body: {"rows": [...], "mode":
    "append" | "replace"}. Default mode is "append" — non-destructive,
    new URLs are added, existing ones left alone. "replace" wipes the
    current queue first."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error": "Not found"}), 404
    body = request.json or {}
    rows = body.get("rows") or []
    mode = (body.get("mode") or "append").lower()
    if mode not in ("append", "replace"):
        return jsonify({"ok": False, "error":
                        f"unknown mode: {mode}"}), 400
    runner = runners[sid]
    if mode == "replace":
        with runner._lock:
            from .db import queue_delete_site
            queue_delete_site(sid)
            runner.jobs.clear()
            runner.urls.clear()
    added = 0
    new_urls = []
    for row in rows:
        url = (row.get("url") or "").strip()
        if not url: continue
        if url in runner.jobs: continue  # already queued — skip
        new_urls.append(url)
    if new_urls:
        result = runner.load_urls(new_urls, folder_scan=False)
        # load_urls returns (added, dupes) or (added, dupes, skipped)
        added = result[0] if isinstance(result, tuple) else len(new_urls)
        # Apply priority + force_download after load (load_urls is generic)
        with runner._lock:
            for row in rows:
                url = (row.get("url") or "").strip()
                if url not in runner.jobs: continue
                if row.get("priority"):
                    runner.jobs[url]["priority"] = row["priority"]
                if row.get("force_download"):
                    runner.jobs[url]["force_download"] = True
    return jsonify({"ok": True, "added": added,
                    "mode": mode, "site_id": sid})


@sites_bp.route("/api/sites/<sid>/queue/save_template", methods=["POST"])
def api_queue_save_template(sid):
    """Convenience: save THIS site's current queue as a template.
    Body: {"name": "...", "note": "..."}. Returns the new template id."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}), 404
    body = request.json or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    runner = runners[sid]
    from . import queue_templates as _qt
    # Snapshot current state. We use the canonical urls list (not the
    # jobs dict iteration order) so the template preserves the order
    # the operator sees in the UI.
    with runner._lock:
        urls = list(runner.urls)
        prio_map = {u: runner.jobs[u].get("priority", "")
                    for u in urls
                    if runner.jobs.get(u, {}).get("priority")}
        force_set = [u for u in urls
                     if runner.jobs.get(u, {}).get("force_download")]
    tid = _qt.create(
        name=name, origin_site_id=sid, urls=urls,
        priority_map=prio_map, force_set=force_set,
        note=body.get("note", ""))
    return jsonify({"ok": True, "id": tid,
                    "saved": len(urls), "name": name})


@sites_bp.route("/api/sites/<sid>/history/export")
def api_export(sid):
    runners = _app_runners()
    # v3.62.1: moved off /api/sites/<sid>/export. That URL is also
    # claimed by api_sites_export() (config-envelope export), which
    # registers first - so this history exporter was dead, unreachable
    # code. It exports a site's *download history* (CSV or URL list),
    # a genuinely different feature from config export, so rather than
    # delete it we gave it its own path. Old callers hitting the bare
    # /export got the config exporter, not this; any client wanting
    # history export must now use /history/export.
    if sid not in runners: return jsonify({"error":"Not found"}),404
    status=request.args.get("status"); fmt=request.args.get("fmt","txt")
    if fmt=="csv":
        rows=db_search(site_id=sid,status=status)
        def gen():
            yield "url,status,filename,file_size,message,ts\n"
            for r in rows: yield f"{r['url']},{r['status']},{r['filename']},{r['file_size']},{r['message']},{r['ts']}\n"
        return Response(gen(),mimetype="text/csv",headers={"Content-Disposition":f"attachment;filename={sid}_history.csv"})
    return Response(runners[sid].export_urls(status),mimetype="text/plain",
                    headers={"Content-Disposition":f"attachment;filename={sid}_{status or 'all'}.txt"})


@sites_bp.route("/api/sites/<sid>/clear",  methods=["POST"])
def api_clear(sid):  return _do_action(sid, "clear")


@sites_bp.route("/api/sites/<sid>/retry",  methods=["POST"])
def api_retry(sid):  return _do_action(sid, "retry")


@sites_bp.route("/api/sites/<sid>/retry_one", methods=["POST"])
def api_retry_one(sid):
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}), 404
    data = request.get_json(silent=True) or {}
    url = data.get("url") or ""
    if not url: return jsonify({"ok": False, "error": "missing url"}), 400
    runner = runners[sid]
    with runner._lock:
        job = runner.jobs.get(url)
        if not job:
            return jsonify({"ok": False, "error": "url not in queue"}), 404
        # Only meaningful states for retry: failed, needs_review, stopped.
        # Pending/running are no-ops, done is intentional (don't second-
        # guess the user — they'd use 'reset' for that).
        if job.get("status") not in ("failed", "needs_review", "stopped"):
            return jsonify({"ok": False, "error": f"can't retry from status {job.get('status')!r}"}), 400
    # Drive through the normal _update_job path so the state transition
    # is logged and persisted. Clears retries so the worker pool doesn't
    # immediately requeue with the same backoff timer.
    runner._update_job(url, "pending", "Retry queued by user",
                       retries=0, retry_after=0)
    return jsonify({"ok": True})


@sites_bp.route("/api/sites/<sid>/jobs/mark", methods=["POST"])
def api_jobs_mark(sid):
    """Phase 51 (v3.37.7): mark a single URL's job status. Used by the
    Review panel to handle "Mark failed" — moves a needs_review URL to
    failed so the auto-retry scanner stops bumping it back to pending.

    Body: {url, status, message?}
    Allowed statuses: failed, needs_review, pending, done. Anything else
    is rejected — we don't allow setting `running` (the worker owns that)
    or arbitrary strings."""
    runners = _app_runners()
    if sid not in runners:
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    status = (body.get("status") or "").strip()
    message = (body.get("message") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "missing url"}), 400
    allowed = {"failed", "needs_review", "pending", "done"}
    if status not in allowed:
        return jsonify({"ok": False, "error": f"status must be one of {sorted(allowed)}"}), 400
    runner = runners[sid]
    with runner._lock:
        if url not in runner.jobs:
            return jsonify({"ok": False, "error": "url not in queue"}), 404
        j = runner.jobs[url]
        prev = j.get("status", "")
        j["status"] = status
        if message:
            j["message"] = message
        j["ts"] = _ts()
        # CUT #41: the date-comparable sibling. Without it a manually
        # marked job is counted by NO day-window consumer.
        j["ts_iso"] = _ts_iso()
        # When marking failed/done, freeze auto-retry so the scanner doesn't
        # keep re-queueing.
        if status in ("failed", "done"):
            j["next_auto_retry_at"] = 0
            j["auto_retry_count"] = j.get("auto_retry_count", 0)
    # Persist + emit event outside the lock (Phase 42 pattern)
    try:
        queue_upsert(sid, url, status=status, message=message or j.get("message", ""))
    except Exception:
        pass
    runner.log_event("manual_mark", f"Marked {prev}{status} via API", url=url)
    return jsonify({"ok": True, "status": status, "url": url})


@sites_bp.route("/api/sites/<sid>/jobs/bulk_mark", methods=["POST"])
def api_jobs_bulk_mark(sid):
    """Mark N URLs at once. Body: {urls: [...], status: str, message?: str}"""
    runners = _app_runners()
    if sid not in runners:
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(silent=True) or {}
    ok, urls_or_err = _validate_bulk_urls(body)
    if not ok:
        return jsonify(urls_or_err), 400
    urls = urls_or_err
    status = (body.get("status") or "").strip()
    message = (body.get("message") or "").strip()
    allowed = {"pending", "failed", "needs_review", "done"}
    if status not in allowed:
        return jsonify({"ok": False,
                        "error": f"status must be one of {sorted(allowed)}"}), 400
    runner = runners[sid]
    # Update in-memory job state under the runner's lock — this is what
    # the live UI reads from. Persist to SQLite outside the lock.
    affected = 0
    with runner._lock:
        for u in urls:
            if u not in runner.jobs:
                continue
            j = runner.jobs[u]
            j["status"] = status
            if message:
                j["message"] = message
            j["ts"] = _ts()
            j["ts_iso"] = _ts_iso()   # CUT #41, as above
            if status in ("failed", "done"):
                j["next_auto_retry_at"] = 0
            affected += 1
    # DB-side bulk update — same urls list, single transaction
    try:
        from .db import queue_bulk_mark
        queue_bulk_mark(sid, urls, status, message=message)
    except Exception as e:
        sys.stderr.write(f"  bulk_mark DB persist failed: {e}\n")
    runner.log_event(
        "manual_mark",
        f"Bulk marked {affected}/{len(urls)} URLs → {status}",
        url=urls[0] if urls else "")
    # SSE nudge — UI should re-render the queue
    try:
        from . import sse_broker as _sse
        _sse.publish("queue_change", {
            "site_id": sid, "op": "bulk_mark",
            "status": status, "count": affected,
        })
    except Exception:
        pass
    return jsonify({"ok": True, "affected": affected,
                    "requested": len(urls)})


@sites_bp.route("/api/sites/<sid>/jobs/bulk_delete", methods=["POST"])
def api_jobs_bulk_delete(sid):
    """Delete N URLs from the queue. Body: {urls: [...]}.

    Does NOT delete already-downloaded files — only removes the queue
    entries. Use force_cleanup=true to also call the file-cleanup hook
    for partial downloads (.bdseg.json + .part files)."""
    runners = _app_runners()
    if sid not in runners:
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(silent=True) or {}
    ok, urls_or_err = _validate_bulk_urls(body)
    if not ok:
        return jsonify(urls_or_err), 400
    urls = urls_or_err
    force_cleanup = bool(body.get("force_cleanup", False))
    runner = runners[sid]
    affected = 0
    cleanup_count = 0
    # Drop from in-memory queue
    with runner._lock:
        for u in urls:
            if u in runner.jobs:
                if force_cleanup:
                    # Attempt to remove the partial-download sidecar +
                    # .part file. Best-effort — file may not exist if
                    # the download never started, which is fine.
                    fn = runner.jobs[u].get("filename", "")
                    if fn:
                        try:
                            from . import resume as _resume
                            _resume.cleanup(fn)
                            cleanup_count += 1
                        except Exception:
                            pass
                del runner.jobs[u]
                affected += 1
    # DB-side bulk delete
    try:
        from .db import queue_bulk_delete
        queue_bulk_delete(sid, urls)
    except Exception as e:
        sys.stderr.write(f"  bulk_delete DB failed: {e}\n")
    runner.log_event(
        "queue_op",
        f"Bulk deleted {affected}/{len(urls)} URLs"
        + (f" (+ {cleanup_count} partial-file cleanups)"
           if cleanup_count else ""))
    try:
        from . import sse_broker as _sse
        _sse.publish("queue_change", {
            "site_id": sid, "op": "bulk_delete", "count": affected,
        })
    except Exception:
        pass
    return jsonify({"ok": True, "affected": affected,
                    "cleanup_count": cleanup_count,
                    "requested": len(urls)})


@sites_bp.route("/api/sites/<sid>/jobs/bulk_priority", methods=["POST"])
def api_jobs_bulk_priority(sid):
    """Set priority label on N URLs. Body: {urls: [...], priority: str}.

    Priority is a freeform short tag. Runner consults it when picking the
    next pending URL. Recognized: 'high', 'normal', 'low', '' (clear).
    Other values are accepted but treated as 'normal'."""
    runners = _app_runners()
    if sid not in runners:
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(silent=True) or {}
    ok, urls_or_err = _validate_bulk_urls(body)
    if not ok:
        return jsonify(urls_or_err), 400
    urls = urls_or_err
    priority = (body.get("priority") or "").strip()[:20]
    runner = runners[sid]
    affected = 0
    with runner._lock:
        for u in urls:
            if u in runner.jobs:
                runner.jobs[u]["priority"] = priority
                affected += 1
    try:
        from .db import queue_set_priority
        queue_set_priority(sid, urls, priority)
    except Exception as e:
        sys.stderr.write(f"  bulk_priority DB failed: {e}\n")
    runner.log_event(
        "queue_op",
        f"Set priority='{priority}' on {affected}/{len(urls)} URLs")
    try:
        from . import sse_broker as _sse
        _sse.publish("queue_change", {
            "site_id": sid, "op": "bulk_priority",
            "priority": priority, "count": affected,
        })
    except Exception:
        pass
    return jsonify({"ok": True, "affected": affected,
                    "priority": priority})


@sites_bp.route("/api/sites/<sid>/jobs/reorder", methods=["POST"])
def api_jobs_reorder(sid):
    """Drag-to-reorder. Body: {ordering: {url: ord_int, ...}}.

    The full new ordering is sent in a single call. The client computes
    the ord values (typically as 10, 20, 30, ... with gaps for future
    inserts). Server validates the URLs exist in the queue and updates
    the ord column for all of them atomically."""
    runners = _app_runners()
    if sid not in runners:
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(silent=True) or {}
    ordering = body.get("ordering")
    if not isinstance(ordering, dict) or not ordering:
        return jsonify({"ok": False,
                        "error": "ordering must be a non-empty dict"}), 400
    if len(ordering) > 5000:
        return jsonify({"ok": False,
                        "error": "ordering too large (max 5000)"}), 400
    # Validate ord values are coercible to int
    try:
        coerced = {str(u): int(o) for u, o in ordering.items()}
    except (TypeError, ValueError):
        return jsonify({"ok": False,
                        "error": "ord values must be integers"}), 400
    runner = runners[sid]
    affected = 0
    with runner._lock:
        for u, ord_val in coerced.items():
            if u in runner.jobs:
                runner.jobs[u]["ord"] = ord_val
                affected += 1
    # v3.64.x D3 follow-up U2: also rebuild runner.urls so the
    # dispatch order in the running session reflects the reorder.
    # The pre-existing endpoint only persisted `ord` to DB + the
    # per-job dict, which meant a reorder in a running session
    # didn't actually change which URL got dequeued next — the
    # dispatch path reads runner.urls. That latent was harmless
    # before drag-to-reorder shipped because nothing drove this
    # endpoint at scale. Now it matters; route through
    # runner.reorder_urls() which is the canonical "the order
    # has changed" entry point (it updates self.urls AND persists
    # via queue_reorder, so we skip the explicit queue_reorder
    # call below to avoid a second redundant DB transaction).
    try:
        ordered_urls = sorted(coerced.keys(), key=lambda u: coerced[u])
        runner.reorder_urls(ordered_urls)
    except Exception as e:
        sys.stderr.write(f"  reorder_urls failed: {e}\n")
        # Fallback: at least persist the raw ord values so a
        # subsequent restart picks them up.
        try:
            from .db import queue_reorder
            queue_reorder(sid, coerced)
        except Exception as e2:
            sys.stderr.write(f"  reorder DB fallback failed: {e2}\n")
    runner.log_event(
        "queue_op",
        f"Reordered {affected}/{len(coerced)} URLs")
    try:
        from . import sse_broker as _sse
        _sse.publish("queue_change", {
            "site_id": sid, "op": "reorder", "count": affected,
        })
    except Exception:
        pass
    return jsonify({"ok": True, "affected": affected})


@sites_bp.route("/api/sites/<sid>/jobs/detail")
def api_jobs_detail(sid):
    """GET /api/sites/<sid>/jobs/detail?url=<url> → full job state.

    The URL is in the query string, not path, because URLs themselves
    contain slashes/colons/etc. and would clash with Flask routing."""
    runners = _app_runners()
    if sid not in runners:
        return jsonify({"error": "Not found"}), 404
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"ok": False, "error": "url required"}), 400
    runner = runners[sid]
    with runner._lock:
        j = runner.jobs.get(url)
        if j is None:
            return jsonify({"ok": False, "error": "url not in queue"}), 404
        # Defensive copy — we're about to drop the lock and the runner
        # may mutate j concurrently.
        job_snapshot = dict(j)
    # Pull prior history from the history table
    try:
        from .db import db_search
        history = db_search(site_id=sid, limit=20)
        # Filter to just THIS url
        history = [h for h in history if h.get("url") == url]
    except Exception as e:
        history = []
        sys.stderr.write(f"  job_detail history fetch failed: {e}\n")
    # Compute elapsed_s for running jobs
    elapsed_s = None
    if job_snapshot.get("status") == "running":
        started_ts = job_snapshot.get("ts_started")
        if started_ts:
            try:
                import time as _t
                elapsed_s = round(_t.time() - float(started_ts), 1)
            except (TypeError, ValueError):
                pass
    return jsonify({
        "ok": True,
        "site_id": sid,
        "url": url,
        "job": job_snapshot,
        "history": history,
        "history_count": len(history),
        "elapsed_s": elapsed_s,
    })


@sites_bp.route("/api/sites/<sid>/queue")
def api_queue(sid):
    """Phase 4.5/4.6: paginated server-side query of the queue table.

    Args (all query params, all optional):
      offset:  starting row index (default 0)
      limit:   max rows returned (default 500, capped at 5000)
      status:  filter by status ("pending"|"running"|"done"|"failed"|...)
      q:       substring search in URL
      since:   ISO timestamp; if given, return ONLY rows updated since
               that time (delta polling). When `since` is set, offset/limit
               still apply but the result is naturally bounded.

    Always returns a `total` field with the unfiltered total so the UI
    can size its scrollbar/pagination correctly."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}),404
    from .db import queue_paginate, queue_changed_since, queue_count
    offset = max(0, int(request.args.get("offset",0)))
    limit = max(1, min(5000, int(request.args.get("limit",500))))
    status = request.args.get("status") or None
    q = request.args.get("q") or None
    since = request.args.get("since") or None
    if since:
        rows = queue_changed_since(sid, since, limit=limit)
    else:
        rows = queue_paginate(sid, status_filter=status, search=q,
                              offset=offset, limit=limit)
    total = queue_count(sid, status if status and status != "all" else None)
    # Frontend wants the same field shapes as the old in-memory job dict
    out_rows = []
    for r in rows:
        out_rows.append({
            "url": r["url"],
            "status": r["status"],
            "message": r.get("message",""),
            "filename": r.get("filename",""),
            "file_size": r.get("file_size",0),
            "screenshot": r.get("screenshot",""),
            "priority": r.get("priority","") or "normal",
            "force_download": bool(r.get("force_download")),
            # RAW UTC on purpose -- do NOT localize this to match the
            # in-memory job shape's `ts` (runner_util._ts(), local HH:MM:SS).
            # This field doubles as the `since` query param's delta-poll
            # cursor a few lines up, and queue_changed_since (db.py:1792)
            # compares it directly against the UTC `ts_updated` column with
            # a bare `>`. Converting it to local time gives the cursor a
            # different clock than the column it filters: verified to break
            # the round trip in BOTH TZ directions (over-returns west of
            # UTC, silently drops rows east of UTC). See
            # tests/test_queue_ts_since_cursor_pin.py.
            "ts": r.get("ts_updated","") or r.get("ts_added",""),
        })
    return jsonify({"rows": out_rows, "total": total, "offset": offset, "limit": limit})
