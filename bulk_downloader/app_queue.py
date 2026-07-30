"""queue API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/queue views moved onto a Flask Blueprint.
Endpoint labels gain a "queue." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (RATE_LIMIT_WINDOW, runners, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

queue_bp = Blueprint("queue", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _chk(*_a, **_k):
    """Delegate to app._chk at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_chk")(*_a, **_k)

def _diff_collect_one(*_a, **_k):
    """Delegate to app._diff_collect_one at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_diff_collect_one")(*_a, **_k)

def _diff_lines_for(*_a, **_k):
    """Delegate to app._diff_lines_for at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_diff_lines_for")(*_a, **_k)

def _m2_avatar_color(*_a, **_k):
    """Delegate to app._m2_avatar_color at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_m2_avatar_color")(*_a, **_k)

def _m2_site_drain_eta(*_a, **_k):
    """Delegate to app._m2_site_drain_eta at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_m2_site_drain_eta")(*_a, **_k)

def _oi_default_download_dir(*_a, **_k):
    """Delegate to app._oi_default_download_dir at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_oi_default_download_dir")(*_a, **_k)

def _oi_dir_writable(*_a, **_k):
    """Delegate to app._oi_dir_writable at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_oi_dir_writable")(*_a, **_k)

def _oi_flagged(*_a, **_k):
    """Delegate to app._oi_flagged at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_oi_flagged")(*_a, **_k)

def _rate_check(*_a, **_k):
    """Delegate to app._rate_check at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_rate_check")(*_a, **_k)

def _app_RATE_LIMIT_WINDOW():
    """The live shared RATE_LIMIT_WINDOW from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "RATE_LIMIT_WINDOW")

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@queue_bp.route("/api/queue/preflight", methods=["GET"])
def api_queue_preflight():
    """Read-only go/no-go strip for the queue (Cut 4). Aggregates existing
    signals (auth_health, daily_budget, selector_drift, runner status, review
    backlog) plus two new checks (download-dir writable, dupe estimate). Writes
    nothing."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    checks = []
    # auth health
    try:
        from . import cookie_health as _ch
        bad = _oi_flagged(_ch.status_all(), flag_keys=("expired", "unhealthy"))
        checks.append(_chk("auth_health", "Auth health",
                           "fail" if bad else "ok",
                           f"{bad} site(s) need re-login" if bad else "all sites healthy"))
    except Exception as e:
        checks.append(_chk("auth_health", "Auth health", "warn", f"unavailable: {e}"[:90]))
    # daily budget
    try:
        from . import daily_budget as _dbud
        over = _oi_flagged(_dbud.status_all(s_cfg), flag_keys=("over", "exceeded", "over_budget"))
        checks.append(_chk("daily_budget", "Daily budget",
                           "warn" if over else "ok",
                           f"{over} site(s) over budget" if over else "within budget"))
    except Exception as e:
        checks.append(_chk("daily_budget", "Daily budget", "warn", f"unavailable: {e}"[:90]))
    # selector drift
    try:
        from . import selector_drift as _sd
        stale = _oi_flagged(_sd.status_all(), flag_keys=("stale", "drifted"))
        checks.append(_chk("selector_drift", "Selector drift",
                           "warn" if stale else "ok",
                           f"{stale} site(s) drift-stale" if stale else "no drift flagged"))
    except Exception as e:
        checks.append(_chk("selector_drift", "Selector drift", "warn", f"unavailable: {e}"[:90]))
    # runners (sum failed across runners)
    failed = needs_review = 0
    try:
        for rn in runners.values():
            st = rn.get_status(light=True) or {}
            c = st.get("counts", {}) if isinstance(st, dict) else {}
            failed += int(c.get("failed", 0) or 0)
            needs_review += int(c.get("needs_review", 0) or 0)
    except Exception:
        pass
    checks.append(_chk("runners", "Active runners",
                       "warn" if failed else "ok",
                       f"{failed} failed job(s) in flight" if failed else "no failed jobs in flight"))
    # review backlog
    checks.append(_chk("review_backlog", "Review backlog",
                       "warn" if needs_review else "ok",
                       f"{needs_review} job(s) awaiting review" if needs_review
                       else "nothing to review"))
    # NEW: download dir writable
    dl = _oi_default_download_dir()
    if not dl:
        checks.append(_chk("download_dir", "Download directory", "warn",
                           "no default download directory configured"))
    else:
        exists, writable = _oi_dir_writable(dl)
        if exists and writable:
            checks.append(_chk("download_dir", "Download directory", "ok",
                               f"{dl} is writable"))
        else:
            checks.append(_chk("download_dir", "Download directory", "fail",
                               f"{dl} {'is not writable' if exists else 'does not exist'}"))
    # NEW: dupe estimate (pending URLs already in history) — best-effort, cheap
    dupes = 0
    try:
        from . import db as _db
        for rn in runners.values():
            st = rn.get_status(light=False) or {}
            jobs = st.get("jobs", {}) if isinstance(st, dict) else {}
            for u, j in jobs.items():
                if (j or {}).get("status") == "pending":
                    try:
                        if _db.db_find_url_in_history(u):
                            dupes += 1
                    except Exception:
                        pass
    except Exception:
        pass
    checks.append(_chk("dupe_estimate", "Duplicate estimate", "ok",
                       f"{dupes} queued URL(s) already in history"))

    ready = not any(ch["status"] == "fail" for ch in checks)
    return jsonify({"ok": True, "ready": ready, "checks": checks})
@queue_bp.route("/api/queue/v2")
def api_queue_v2():
    """SPA-shaped queue snapshot. Three buckets:
      - running: currently downloading, with progress + ETA
      - waiting: pending, with priority (lower = sooner)
      - done_today_count: integer
    Each running/waiting entry has site_id + avatar color so the SPA
    can render the colored per-site chip without a separate sites
    lookup."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    import time as _t
    today_iso = _t.strftime("%Y-%m-%d")
    try:
        running = []
        waiting = []
        per_site_acc = []
        done_today = 0
        for sid, runner in runners.items():
            if not runner:
                continue
            cfg = s_cfg.get(sid, {}) or {}
            name = cfg.get("name") or sid
            color = _m2_avatar_color(name)
            try:
                site_per_min = float(getattr(runner, "_recent_per_min", 0) or 0)
            except (TypeError, ValueError):
                site_per_min = 0.0
            site_running = 0
            site_waiting = 0
            try:
                with runner._lock:
                    for url, j in runner.jobs.items():
                        s = j.get("status", "")
                        if s == "running":
                            site_running += 1
                            running.append({
                                "site_id": sid, "site_name": name,
                                "avatar_color": color,
                                "url": url,
                                "filename": j.get("filename", ""),
                                "progress": j.get("progress", 0),
                                "bytes_done": j.get("bytes_done", 0),
                                "bytes_total": j.get("bytes_total", 0),
                                "eta_seconds": j.get("eta_seconds"),
                                "rate_human": j.get("rate_human", ""),
                            })
                        elif s == "pending":
                            site_waiting += 1
                            waiting.append({
                                "site_id": sid, "site_name": name,
                                "avatar_color": color,
                                "url": url,
                                "filename": j.get("filename", ""),
                                "priority": j.get("priority", 0),
                                "queued_ts": j.get("queued_ts", 0),
                            })
                        elif s == "done":
                            ts = j.get("ts_iso", "") or ""
                            if ts.startswith(today_iso):
                                done_today += 1
            except Exception:
                continue
            if site_running or site_waiting:
                per_site_acc.append({
                    "site_id": sid, "site_name": name, "avatar_color": color,
                    "waiting_count": site_waiting,
                    "running_count": site_running,
                    "drain_eta_seconds": _m2_site_drain_eta(
                        site_running + site_waiting, site_per_min),
                })
        # Sort waiting by (priority asc, queued_ts asc) — same rule the
        # runner uses internally. SPA shows them in dispatch order.
        waiting.sort(key=lambda e: (e["priority"], e["queued_ts"]))
        # Cap waiting list at 200 for payload size; SPA paginates.
        truncated = max(0, len(waiting) - 200)
        # Per-site drain summary (F1.6): longest-draining site first; a None
        # eta (no rate yet) sorts last so populated estimates lead.
        per_site_acc.sort(
            key=lambda e: (e["drain_eta_seconds"] is None,
                           -(e["drain_eta_seconds"] or 0)))
        return jsonify({
            "ok": True,
            "running": running,
            "waiting": waiting[:200],
            "waiting_truncated_count": truncated,
            "done_today_count": done_today,
            "per_site": per_site_acc,
            "ts": int(_t.time()),
        })
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 503
@queue_bp.route("/api/queue/v2/cancel", methods=["POST"])
def api_queue_v2_cancel():
    """Cancel one URL. Body: {site_id, url}. Marks the job as stopped
    via the runner's _update_job path (same code the site-level /stop
    endpoint uses, scoped to one URL)."""
    runners = _app_runners()
    body = request.get_json(silent=True) or {}
    sid = (body.get("site_id") or "").strip()
    url = (body.get("url") or "").strip()
    if not sid or sid not in runners:
        return jsonify({"ok": False, "error": "unknown site_id"}), 400
    if not url:
        return jsonify({"ok": False, "error": "missing url"}), 400
    runner = runners[sid]
    with runner._lock:
        job = runner.jobs.get(url)
        if not job:
            return jsonify({"ok": False, "error": "url not in queue"}), 404
        current_status = job.get("status") or ""
    # Cancel is meaningful from pending/running/needs_review. Done is
    # a no-op (don't second-guess); failed/stopped are already terminal.
    if current_status not in ("pending", "running", "needs_review"):
        return jsonify({
            "ok": False,
            "error": f"can't cancel from status {current_status!r}",
        }), 400
    runner._update_job(url, "stopped", "Cancelled by user")
    return jsonify({"ok": True, "previous_status": current_status})
@queue_bp.route("/api/queue/v2/job_log")
def api_queue_v2_job_log():
    """Last N events for one URL. Query: site_id, url, limit (default
    50, max 200). Used by the Queue tab's error modal — operator taps
    a row and sees the recent log lines that led to the current state.

    Returns {ok, events: [{ts, kind, message}, ...]}.
    """
    runners = _app_runners()
    sid = (request.args.get("site_id") or "").strip()
    url = (request.args.get("url") or "").strip()
    try:
        limit = max(1, min(200, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    if not sid or sid not in runners:
        return jsonify({"ok": False, "error": "unknown site_id"}), 400
    if not url:
        return jsonify({"ok": False, "error": "missing url"}), 400
    runner = runners[sid]
    ev_log = list(getattr(runner, "_event_log", None) or [])
    # Filter to this URL and take the last `limit`. The event log is
    # already in chronological order (append-only with cap), so the
    # tail is the most recent.
    matched = [ev for ev in ev_log if ev.get("url") == url]
    matched = matched[-limit:]
    out = [
        {
            "ts": ev.get("ts", 0),
            "kind": ev.get("kind", ""),
            "message": ev.get("message", "")[:500],
        }
        for ev in matched
    ]
    # Also include the current job's stored message so the modal has
    # the most recent terminal state even if it predates the event log
    # window.
    with runner._lock:
        job = runner.jobs.get(url) or {}
        current = {
            "status": job.get("status", ""),
            "message": (job.get("message") or "")[:500],
            "filename": job.get("filename", ""),
        }
    return jsonify({
        "ok": True,
        "events": out,
        "current": current,
        "truncated": len(matched) < len([
            ev for ev in ev_log if ev.get("url") == url
        ]),
    })
@queue_bp.route("/api/queue/v2/job_log_diff")
def api_queue_v2_job_log_diff():
    """Return two job logs + a precomputed unified diff.

    Query: a=<site_id>:<url>, b=<site_id>:<url>, limit (default 50,
    max 200). Used by the Queue tab's Compare flow: operator opens
    one job's error modal, taps Compare, picks a second job; the
    SPA navigates to /m2/logs/diff?a=...&b=... which calls this.

    Returns {ok, a: <one-side>, b: <one-side>, diff: [<unified diff lines>]}.
    Each side has {site_id, url, events, current, ok, error}. The
    top-level ok is True if the response is well-formed (even when
    one side has ok=false — partial results are still useful).
    """
    from difflib import unified_diff
    a_spec = (request.args.get("a") or "").strip()
    b_spec = (request.args.get("b") or "").strip()
    if not a_spec or not b_spec:
        return jsonify({
            "ok": False, "error": "missing 'a' or 'b' query parameter",
        }), 400
    try:
        limit = max(1, min(200, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    a = _diff_collect_one(a_spec, limit)
    b = _diff_collect_one(b_spec, limit)
    a_lines = _diff_lines_for(a["events"])
    b_lines = _diff_lines_for(b["events"])
    # n=3 is the default context; tweak if event lines get longer.
    diff_lines = list(unified_diff(
        a_lines, b_lines,
        fromfile=f"{a['site_id']}:{a['url']}"[:120],
        tofile=f"{b['site_id']}:{b['url']}"[:120],
        n=3,
        lineterm="",
    ))
    return jsonify({
        "ok": True,
        "a": a,
        "b": b,
        "diff": diff_lines,
    })
@queue_bp.route("/api/queue/v2/bulk_cancel", methods=["POST"])
def api_queue_v2_bulk_cancel():
    """Cancel many URLs across one or more sites.

    Body: {"jobs": [{"site_id": "...", "url": "..."}, ...]}

    Cancels each job via the same _update_job path as the single
    /api/queue/v2/cancel endpoint. Per-job failures (unknown site,
    URL not in queue, non-cancellable status) are collected, not
    raised. Returns 200 with the aggregate.
    """
    RATE_LIMIT_WINDOW = _app_RATE_LIMIT_WINDOW()
    runners = _app_runners()
    _check_csrf()
    body = request.get_json(silent=True) or {}
    jobs = body.get("jobs") or []
    if not isinstance(jobs, list) or not jobs:
        return jsonify({
            "ok": False,
            "error": "jobs must be a non-empty list",
        }), 400
    if not _rate_check("queue_bulk_cancel"):
        return jsonify({"ok": False, "error": "rate limited",
                        "retry_after": RATE_LIMIT_WINDOW}), 429
    results = {"cancelled": 0, "errors": []}
    # De-dup on (site_id, url) so a duplicate entry in the request
    # doesn't cancel the same job twice (the second would 404).
    seen = set()
    for j in jobs:
        if not isinstance(j, dict):
            continue
        sid = (j.get("site_id") or "").strip()
        url = (j.get("url") or "").strip()
        key = (sid, url)
        if not sid or not url:
            results["errors"].append({
                "site_id": sid, "url": url, "error": "missing site_id or url",
            })
            continue
        if key in seen:
            continue
        seen.add(key)
        if sid not in runners:
            results["errors"].append({
                "site_id": sid, "url": url, "error": "unknown site_id",
            })
            continue
        runner = runners[sid]
        try:
            with runner._lock:
                job = runner.jobs.get(url)
                if not job:
                    results["errors"].append({
                        "site_id": sid, "url": url, "error": "url not in queue",
                    })
                    continue
                current_status = job.get("status") or ""
            if current_status not in ("pending", "running", "needs_review"):
                results["errors"].append({
                    "site_id": sid, "url": url,
                    "error": f"can't cancel from status {current_status!r}",
                })
                continue
            runner._update_job(url, "stopped", "Cancelled by user")
            results["cancelled"] += 1
        except Exception as e:
            results["errors"].append({
                "site_id": sid, "url": url, "error": str(e)[:200],
            })
    return jsonify({
        "ok": True,
        "cancelled": results["cancelled"],
        "total": len(seen),
        "errors": results["errors"],
    })
@queue_bp.route("/api/queue/v2/add_url", methods=["POST"])
def api_queue_v2_add_url():
    """v3.66.8 — enqueue a single URL on a configured site.

    The m2 Add-URL wizard composes this with /api/dev/deep_detect:
    detect → ResolutionPicker → confirm → this endpoint.

    Body: {
        "site_id":        "<configured site id>",  # required
        "url":            "https://...",            # required
        "click_selector": "...",                    # optional, picker-provided
        "resolution":     "1080p",                  # optional, picker-provided
        "codec":          "h264",                   # optional, picker-provided
        "fps":            30,                       # optional, picker-provided
        "source_type":    "resolution_download_card", # optional
    }

    Calls runners[site_id].load_urls([url]) — the same per-site
    enqueue path the legacy UI uses. The picker-provided hints
    (resolution/codec/fps/click_selector/source_type) are accepted
    for forward compat with the planned per-job hint passthrough
    but not consumed by load_urls today (Phase 68's per-URL
    header mechanism is the closest existing analog). Storing
    them is a v3.66.9 candidate once the runner grows a hint slot.

    Returns: {ok, site_id, url, added, dupes, skipped}.

    On unknown site_id: 400. On missing url: 400. On load_urls
    raising: 500 with the exception type + truncated message.
    """
    runners = _app_runners()
    _check_csrf()
    body = request.get_json(silent=True) or {}
    sid = (body.get("site_id") or "").strip()
    url = (body.get("url") or "").strip()
    if not sid:
        return jsonify({"ok": False, "error": "site_id required"}), 400
    if not url:
        return jsonify({"ok": False, "error": "url required"}), 400
    if sid not in runners or not runners[sid]:
        return jsonify({
            "ok": False,
            "error": f"unknown site_id {sid!r}",
        }), 400
    runner = runners[sid]
    try:
        added, dupes, skipped = runner.load_urls([url])
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }), 500
    return jsonify({
        "ok": True,
        "site_id": sid,
        "url": url,
        "added": int(added),
        "dupes": int(dupes),
        "skipped": int(skipped),
    })

@queue_bp.route("/api/queue/dead_letter")
def api_queue_dead_letter_list():
    """Phase 2 Cut 2.1: list dead-lettered jobs (terminal, retry-exhausted or
    dependency-blocked). Value-free: url + status + message + retries, never a
    filesystem path or secret. Optional ?site_id= scopes to one site."""
    from . import db as _db
    sid = (request.args.get("site_id") or "").strip() or None
    rows, _cursor = _db.queue_search(site_id=sid, status="dead_letter", limit=500)
    jobs = [{
        "site_id": r.get("site_id"),
        "url": r.get("url"),
        "status": r.get("status"),
        "message": r.get("message", ""),
        "retries": r.get("retries", 0),
        "lane": r.get("lane", "default"),
        "depends_on": r.get("depends_on", ""),
    } for r in rows]
    return jsonify({"ok": True, "jobs": jobs, "total": len(jobs)})


@queue_bp.route("/api/queue/dead_letter/requeue", methods=["POST"])
def api_queue_dead_letter_requeue():
    """Phase 2 Cut 2.1: requeue one dead-lettered job back to pending (retry
    counters cleared). Body: {site_id, url}. Only acts on a currently
    dead-lettered row. CSRF via the global before_request."""
    _check_csrf()
    from . import db as _db
    body = request.get_json(silent=True) or {}
    sid = (body.get("site_id") or "").strip()
    url = (body.get("url") or "").strip()
    if not sid or not url:
        return jsonify({"ok": False, "error": "site_id and url are required"}), 400
    ok = _db.db_queue_requeue_dead_letter(sid, url)
    if not ok:
        return jsonify({"ok": False, "error": "no dead-lettered job for that site_id/url"}), 404
    return jsonify({"ok": True, "site_id": sid, "url": url})


def register_routes(app) -> int:
    app.register_blueprint(queue_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("queue."))

