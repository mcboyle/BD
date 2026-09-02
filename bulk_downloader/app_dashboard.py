"""dashboard API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/dashboard views moved onto a Flask Blueprint.
Endpoint labels gain a "dashboard." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request


def _runners_generation(mapping):
    """A stable (sid, runner) list; locked when `mapping` is the live registry.

    Row 634: walking ``app_state.runners`` bare raises ``RuntimeError:
    dictionary changed size during iteration`` the instant a site create or
    delete lands mid-walk, AFTER the loop body has already acted on a prefix of
    the fleet.  Imported lazily (importlib, per call) for the same reason the
    other shared-state accessors here are: no new static import edge.
    """
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"),
                   "runners_generation")(mapping)


dashboard_bp = Blueprint("dashboard", __name__)

def _m2_attention_for_site(*_a, **_k):
    """Delegate to app._m2_attention_for_site at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_m2_attention_for_site")(*_a, **_k)

def _m2_avatar_color(*_a, **_k):
    """Delegate to app._m2_avatar_color at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_m2_avatar_color")(*_a, **_k)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@dashboard_bp.route("/api/dashboard")
def api_dashboard():
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    import time as _t
    from .cookies import cookies_expiry_info
    totals = {"running":0, "pending":0, "done":0, "failed":0,
              "needs_review":0, "stopped":0}
    active_workers = 0
    today_done = today_failed = today_review = 0
    today_iso = _t.strftime("%Y-%m-%d")
    expiring_cookies_sites = []
    rate_limited_sites = []
    low_disk_sites = []
    disk_aggregate = []   # [(path, free_gb, total_gb)] for the sparkline
    # Throughput: bytes/sec rate per site over the last 60s (computed
    # from the rolling totals attached to each runner). Sum across sites.
    bytes_per_sec_total = 0.0

    for sid, runner in _runners_generation(runners):
        if not runner: continue
        cfg = s_cfg.get(sid, {}) or {}
        st_state = runner.state()
        # Per-status counts across this site's jobs
        with runner._lock:
            for url, j in runner.jobs.items():
                s = j.get("status","")
                if s in totals: totals[s] += 1
                # Today's done / failed / review
                ts = j.get("ts_iso","") or ""
                if ts.startswith(today_iso):
                    if s == "done": today_done += 1
                    elif s == "failed": today_failed += 1
                    elif s == "needs_review": today_review += 1
            active_workers += 1 if st_state == "running" else 0
        # Disk free for this site's primary download dir
        dl_dir = cfg.get("download_dir","") or ""
        if dl_dir:
            try:
                import shutil as _shutil
                u = _shutil.disk_usage(dl_dir)
                disk_aggregate.append({
                    "site": cfg.get("name","") or sid,
                    "path": dl_dir,
                    "free_gb": round(u.free/(1024**3), 2),
                    "total_gb": round(u.total/(1024**3), 2),
                    "free_pct": round((u.free/u.total)*100, 1),
                })
            except Exception: pass
        # Expiring cookies (within 1h)
        try:
            ei = cookies_expiry_info(runner.cookies or [])
            earliest = ei.get("earliest")
            if earliest:
                hours = (earliest - _t.time()) / 3600.0
                if 0 < hours < 1:
                    expiring_cookies_sites.append({
                        "site_id": sid, "name": cfg.get("name","") or sid,
                        "expires_in_minutes": round(hours*60, 1),
                    })
        except Exception: pass
        # Rate limited
        try:
            if runner.is_rate_limited():
                rate_limited_sites.append({
                    "site_id": sid, "name": cfg.get("name","") or sid,
                    "until": getattr(runner, "_rl_until", 0) or 0,
                })
        except Exception: pass
        # Low disk
        if st_state == "low_disk":
            low_disk_sites.append({"site_id": sid, "name": cfg.get("name","") or sid})
        # Throughput rate
        try:
            bytes_per_sec_total += float(runner._current_throughput_bps() or 0)
        except Exception: pass

    # Queue ETA: jobs/minute over the last 60s applied to remaining pending.
    # We compute it from a counter the runner already tracks (_recent_completions
    # — falls through gracefully if the runner doesn't have one). This is
    # simpler and more accurate at-the-moment than a bytes-based estimate
    # when downloads vary widely in size.
    pending_total = totals["pending"] + totals["running"]
    eta_seconds = None
    recent_completions_per_min = 0.0
    for _sid, runner in _runners_generation(runners):
        try:
            recent_completions_per_min += float(getattr(runner, "_recent_per_min", 0) or 0)
        except Exception: pass
    if pending_total > 0 and recent_completions_per_min > 0:
        eta_seconds = int((pending_total / recent_completions_per_min) * 60)
    return jsonify({
        "ok": True,
        "totals": totals,
        "active_workers": active_workers,
        "today": {"done": today_done, "failed": today_failed,
                  "needs_review": today_review},
        "throughput_bps": bytes_per_sec_total,
        "eta_seconds": eta_seconds,
        "disk": disk_aggregate,
        "expiring_cookies": expiring_cookies_sites,
        "rate_limited": rate_limited_sites,
        "low_disk": low_disk_sites,
    })
@dashboard_bp.route("/api/dashboard/widgets")
def api_dashboard_widgets():
    """Return just the widget metrics + their internal state. Used
    by the dashboard tile component (lighter than /api/dashboard
    which builds the full snapshot) AND as a debug endpoint to
    see what the rolling window looks like."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    try:
        from bulk_downloader import dashboard_widgets as _dw
        snap = _dw.snapshot(runners_dict=runners, s_cfg=s_cfg)
        debug = _dw.get_widgets().get_debug_info()
        return jsonify({
            "ok": True,
            "widgets": snap,
            "debug": debug,
            "formatted": {
                "rate": _dw.format_rate(snap.get("bytes_per_sec", 0)),
                "eta": _dw.format_eta(snap.get("eta_seconds")),
            },
        })
    except Exception as e:
        return jsonify({"ok": False,
                          "error": f"{type(e).__name__}: {e}"}), 500
@dashboard_bp.route("/api/dashboard/v2")
def api_dashboard_v2():
    """SPA-shaped dashboard snapshot. Composed of:
      - attention: sorted list of sites with active issues
      - by_site:   queue depth per site for the mockup's queue chart
      - today:     done / running / failed counts
      - sparkline: last 60 throughput samples (separate endpoint
                   `/api/dashboard/v2/sparkline` for high-cadence polling)
      - sites_count: total loaded sites
    """
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    import time as _t
    try:
        attention = []
        by_site = []
        running = 0
        today_done = today_failed = 0
        today_iso = _t.strftime("%Y-%m-%d")
        active_workers = 0
        for sid, runner in _runners_generation(runners):
            if not runner:
                continue
            cfg = s_cfg.get(sid, {}) or {}
            name = cfg.get("name") or sid
            entry = _m2_attention_for_site(sid, runner, cfg)
            if entry:
                attention.append(entry)
            # Per-site queue depth + per-site today counts
            site_queued = site_running = site_today_done = 0
            try:
                with runner._lock:
                    for j in runner.jobs.values():
                        s = j.get("status", "")
                        if s == "pending":
                            site_queued += 1
                        elif s == "running":
                            site_running += 1
                        ts = j.get("ts_iso", "") or ""
                        if ts.startswith(today_iso):
                            if s == "done":
                                site_today_done += 1
                                today_done += 1
                            elif s == "failed":
                                today_failed += 1
                running += site_running
                if runner.state() == "running":
                    active_workers += 1
            except Exception:
                pass
            by_site.append({
                "site_id": sid,
                "name": name,
                "avatar_color": _m2_avatar_color(name),
                "queued": site_queued,
                "running": site_running,
                "today_done": site_today_done,
            })
        # Stable sort for attention: captcha first, login_expired next,
        # rate_limited last; within a class, by name. Predictable order
        # matters because the SPA renders the banner without flicker.
        _kind_order = {"captcha_pending": 0, "login_expired": 1,
                       "rate_limited": 2}
        attention.sort(key=lambda e: (_kind_order.get(e["kind"], 9),
                                       (e.get("name") or "").lower()))
        # by_site sorted by queue depth desc, then name — busiest first.
        by_site.sort(key=lambda e: (-e["queued"] - e["running"],
                                      (e.get("name") or "").lower()))
        # v3.65.x V2.1: compute aggregate worker counts via the shared
        # helper in app_widgets_api. Two new fields:
        #   workers_active = sum of active_worker_count() across runners
        #                    (concurrent download workers running NOW)
        #   workers_total  = sum of max_concurrent; None on empty fleet
        # `active_workers` (existing field) is "sites with running state"
        # — a different metric, kept for back-compat. Don't conflate
        # the two; the SPA's display-mode status line uses workers_active
        # / workers_total to render "{N} of {M} workers".
        try:
            from . import app_widgets_api
            _wc = app_widgets_api.compute_worker_counts(runners)
            workers_active = _wc["workers_active"]
            workers_total = _wc["workers_total"]
        except Exception:
            workers_active = 0
            workers_total = None
        return jsonify({
            "ok": True,
            "attention": attention,
            "by_site": by_site,
            "today": {
                "done": today_done,
                "running": running,
                "failed": today_failed,
            },
            "active_workers": active_workers,
            "workers_active": workers_active,
            "workers_total": workers_total,
            "sites_count": len(runners),
            "ts": int(_t.time()),
        })
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 503
@dashboard_bp.route("/api/dashboard/v2/sparkline")
def api_dashboard_v2_sparkline():
    """High-cadence throughput sparkline. Returns the last 60 samples
    of total bytes/sec. Polled separately at 1-2s cadence by the SPA;
    the main /api/dashboard/v2 is polled at 5-10s. Splitting the
    sparkline out keeps the main endpoint cheap to refresh."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    import time as _t
    try:
        # Reuse the dashboard_widgets snapshot for the current rate;
        # build a 60-point history by reading the widgets module's
        # rolling buffer if available, falling back to a single point.
        from bulk_downloader import dashboard_widgets as _dw
        snap = _dw.snapshot(runners_dict=runners, s_cfg=s_cfg)
        history = _dw.get_widgets().get_history("bytes_per_sec") \
            if hasattr(_dw.get_widgets(), "get_history") else None
        if not history:
            # Fall back to a single-point sparkline. The SPA renders
            # this as a flat line and will fill in on subsequent polls.
            history = [{"ts": int(_t.time()),
                        "value": snap.get("bytes_per_sec", 0)}]
        return jsonify({
            "ok": True,
            "current": snap.get("bytes_per_sec", 0),
            "history": history,
            "ts": int(_t.time()),
        })
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 503
@dashboard_bp.route("/api/dashboard/v2/resolve", methods=["POST"])
def api_dashboard_v2_resolve():
    runners = _app_runners()
    body = request.get_json(silent=True) or {}
    sid = (body.get("site_id") or "").strip()
    kind = (body.get("kind") or "").strip()
    if not sid or sid not in runners:
        return jsonify({"ok": False, "error": "unknown site_id"}), 400
    valid_kinds = {"captcha_pending", "login_expired", "rate_limited"}
    if kind not in valid_kinds:
        return jsonify({"ok": False,
                        "error": f"invalid kind: {kind!r} "
                                 f"(want one of {sorted(valid_kinds)})"}), 400
    runner = runners[sid]
    try:
        if kind == "captcha_pending":
            # Trigger the manual-login Playwright window. The runner
            # handles all the actual login flow; we just kick it.
            from . import login as _login  # noqa: F401  (import sanity)
            # Call the runner method directly rather than HTTP-loopback
            # back to /api/sites/<sid>/manual_login — saves a request,
            # avoids CSRF re-check, identical effect.
            try:
                runner.start_manual_login()
                return jsonify({
                    "ok": True,
                    "action": "manual_login_started",
                    "detail": "Login window opened — solve the captcha there.",
                })
            except AttributeError:
                # Older runner without start_manual_login — fall back to
                # the HTTP loopback so we work on the current zip.
                return jsonify({
                    "ok": True,
                    "action": "manual_login_url",
                    "detail": f"POST /api/sites/{sid}/manual_login",
                    "url": f"/api/sites/{sid}/manual_login",
                })
        elif kind == "login_expired":
            # Trigger re-login. login_async() is the runner's
            # background-login entry point — same method the v1
            # /api/sites/<sid>/login endpoint uses internally.
            try:
                runner.login_async()
                return jsonify({
                    "ok": True,
                    "action": "relogin_started",
                    "detail": "Re-login triggered.",
                })
            except AttributeError:
                return jsonify({
                    "ok": True,
                    "action": "relogin_url",
                    "detail": f"POST /api/sites/{sid}/login",
                    "url": f"/api/sites/{sid}/login",
                })
        else:  # rate_limited
            # Visibility only — no action available server-side. The
            # cool-down is set by the runner when it sees HTTP 429 etc.
            # and clears itself.
            until = getattr(runner, "_rl_until", 0) or 0
            return jsonify({
                "ok": True,
                "action": "noop",
                "detail": "Rate-limit is automatic; nothing to do here.",
                "until_ts": until,
            })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        }), 500

def register_routes(app) -> int:
    app.register_blueprint(dashboard_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("dashboard."))
