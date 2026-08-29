"""discovery API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/discovery views moved onto a Flask Blueprint.
Endpoint labels gain a "discovery." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from urllib.parse import urlsplit

discovery_bp = Blueprint("discovery", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


def _bounded_int(value, default, low, high):
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


def _bounded_float(value, default, low, high):
    try:
        return max(low, min(float(value), high))
    except (TypeError, ValueError):
        return default


@discovery_bp.route("/api/discovery/run", methods=["POST"])
def api_discovery_run():
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    _check_csrf()
    body = request.json or {}
    sid = body.get("site_id")
    # Local enqueue: append URL to the site's pending queue
    def enqueue(site_id, urls):
        if site_id not in s_cfg or site_id not in runners:
            return 0
        n = 0
        for url in urls[:1000]:  # safety cap
            try:
                runners[site_id].load_urls([url])  # type: ignore  # SiteRunner enqueue is load_urls, not add_url (v3.66.245)
                n += 1
            except Exception:
                pass
        return n
    try:
        from . import discovery as _disc
        if sid:
            cfg = (s_cfg or {}).get(sid, {})
            dcfg = (cfg or {}).get("discovery") or {}
            return jsonify(_disc.discover_one(sid, dcfg, enqueue_fn=enqueue))
        return jsonify({"runs": _disc.discover_all(
            s_cfg, enqueue_fn=enqueue)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@discovery_bp.route("/api/discovery/history")
def api_discovery_history():
    try:
        from . import discovery as _disc
        return jsonify({"runs": _disc.recent_runs(
            limit=int(request.args.get("limit", 50)))})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@discovery_bp.route("/api/discovery/disco/run", methods=["POST"])
def api_discovery_disco_run():
    """Operator manual trigger for A-DISCO (cut 4b): a run-now. It FORCES a pass
    past the ``auto_disco`` DAILY toggle -- an attended, explicit operator action,
    matching the app's other run-now controls (e.g. drift_repair). The MASTER
    off-switch still dominates (default off_switch_fn = automation_controller's kill
    path -> the per-site pass is inert when engaged), per-site ``disco.enabled``
    still gates which sites run, and the bounded budget + AR4 enqueue cap still
    apply. It does NOT flip the daily toggle; it only runs one pass now."""
    _check_csrf()
    try:
        from . import disco_runner as _dr
        return jsonify(_dr.scheduled_disco(
            s_cfg=_app_s_cfg(), runners=_app_runners(),
            enabled_fn=lambda: True))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@discovery_bp.route("/api/discovery/disco/runs")
def api_discovery_disco_runs():
    """The persisted A-DISCO run history (disco_runner.recent_runs), so the operator
    can see what a manual trigger (or the daily task) did."""
    try:
        from . import disco_runner as _dr
        return jsonify({"runs": _dr.recent_runs(
            limit=int(request.args.get("limit", 50)))})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@discovery_bp.route("/api/discovery/scenes/start", methods=["POST"])
def api_discovery_scenes_start():
    """Start one bounded authenticated library crawl from the existing GUI."""
    _check_csrf()
    body = request.get_json(silent=True) or {}
    sid = str(body.get("site_id") or "").strip()
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if not sid:
        return jsonify({"ok": False, "error": "site_id required"}), 400
    if sid not in runners or not runners[sid] or sid not in s_cfg:
        return jsonify({"ok": False, "error": f"unknown site_id {sid!r}"}), 400
    cfg = dict((s_cfg or {}).get(sid) or {})
    listing_url = str(
        body.get("listing_url") or cfg.get("crawler_listing_url") or ""
    ).strip()
    parsed = urlsplit(listing_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return jsonify({
            "ok": False,
            "error": "listing_url must be an absolute http(s) URL",
        }), 400

    newest_default = _bounded_int(cfg.get("crawler_newest_n"), 50, 0, 10000)
    pages_default = _bounded_int(cfg.get("crawler_max_pages"), 5, 1, 500)
    scrolls_default = _bounded_int(cfg.get("crawler_max_scrolls"), 8, 0, 50)
    delay_default = _bounded_float(cfg.get("crawler_delay_s"), 1.0, 0.1, 30.0)
    title_default = _bounded_int(
        cfg.get("crawler_title_fetch_limit"), 50, 0, 1000
    )
    newest_n = _bounded_int(body.get("newest_n"), newest_default, 0, 10000)
    max_pages = _bounded_int(body.get("max_pages"), pages_default, 1, 500)
    max_scrolls = _bounded_int(body.get("max_scrolls"), scrolls_default, 0, 50)
    delay_s = _bounded_float(body.get("delay_s"), delay_default, 0.1, 30.0)
    title_limit = _bounded_int(
        body.get("title_fetch_limit"),
        title_default,
        0,
        1000,
    )

    from . import app_queue as _queue
    from . import scene_crawler as _crawler
    try:
        result = _crawler.start_background_crawl(
            site_id=sid,
            listing_url=listing_url,
            site_config=cfg,
            runner=runners[sid],
            newest_n=newest_n,
            max_pages=max_pages,
            max_scrolls=max_scrolls,
            delay_s=delay_s,
            title_fetch_limit=title_limit,
            enqueue_fn=lambda site_id, url: _queue.enqueue_one_url(
                site_id, url, runners=runners
            ),
        )
    except _crawler.CrawlAlreadyRunning as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "run_id": e.run_id,
            "state": "RUNNING",
        }), 409
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }), 500

    try:
        from . import audit as _audit
        _audit.audit_log(
            source="api",
            action="scene_crawl_start",
            target=f"site:{sid}",
            after={
                "listing_url": listing_url,
                "newest_n": newest_n,
                "max_pages": max_pages,
                "max_scrolls": max_scrolls,
                "delay_s": delay_s,
                "title_fetch_limit": title_limit,
            },
            actor=(request.cookies.get("bd_session", "")[:8]
                   or request.remote_addr or "operator"),
        )
    except Exception:
        pass
    return jsonify(result), 202


@discovery_bp.route("/api/discovery/scenes/status")
def api_discovery_scenes_status():
    sid = str(request.args.get("site_id") or "").strip()
    if not sid:
        return jsonify({"ok": False, "error": "site_id required"}), 400
    cfg = dict((_app_s_cfg() or {}).get(sid) or {})
    if not cfg:
        return jsonify({"ok": False, "error": f"unknown site_id {sid!r}"}), 400
    try:
        from . import scene_crawler as _crawler
        status = _crawler.crawl_status(
            site_id=sid,
            run_id=str(request.args.get("run_id") or "").strip() or None,
        )
        status["defaults"] = {
            "listing_url": str(cfg.get("crawler_listing_url") or ""),
            "newest_n": _bounded_int(cfg.get("crawler_newest_n"), 50, 0, 10000),
            "max_pages": _bounded_int(cfg.get("crawler_max_pages"), 5, 1, 500),
            "max_scrolls": _bounded_int(cfg.get("crawler_max_scrolls"), 8, 0, 50),
            "delay_s": _bounded_float(cfg.get("crawler_delay_s"), 1.0, 0.1, 30.0),
            "title_fetch_limit": _bounded_int(
                cfg.get("crawler_title_fetch_limit"), 50, 0, 1000
            ),
        }
        status["history"] = _crawler.discovery_history(sid, limit=20)
        return jsonify(status)
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }), 500

def register_routes(app) -> int:
    app.register_blueprint(discovery_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("discovery."))
