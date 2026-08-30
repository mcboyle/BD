"""config API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/config views moved onto a Flask Blueprint.
Endpoint labels gain a "config." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (CFG_FIELDS, DEFAULTS, runners, s_cfg, s_meta) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid
from flask import Blueprint, Response, current_app, jsonify, request
from pathlib import Path
from .runner import (
    CAPTCHA_EGRESS_ACK_FIELD,
    SiteRunner,
    captcha_egress_disclosure_error,
)

config_bp = Blueprint("config", __name__)

def _build_meta(*_a, **_k):
    """Delegate to app._build_meta at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_build_meta")(*_a, **_k)

def _app_CFG_FIELDS():
    """The live shared CFG_FIELDS from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "CFG_FIELDS")

def _app_DEFAULTS():
    """The live shared DEFAULTS from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "DEFAULTS")

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")

def _app_s_meta():
    """The live shared s_meta from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_meta")


def _save_sites_config():
    """Delegate to the one atomic sites-config writer at call time."""
    import importlib
    return getattr(
        importlib.import_module("bulk_downloader.app"),
        "_save_sites_config",
    )()


@contextlib.contextmanager
def _all_site_lifecycle_transaction():
    """Own every fixed site stripe in its process-stable order.

    Config import is the one site mutation whose target population is not
    represented by a ``<sid>`` route argument.  Taking the fixed stripe tuple
    lets it parse first, then revalidate and mutate the complete live
    population atomically against per-site routes and DELETE.

    Lock order is always site stripe(s) -> sites-config save lock.  In
    particular this helper never takes the save lock itself, so a route that
    already owns one stripe and is finishing a save cannot form an inversion
    with an importer waiting to acquire that stripe.
    """
    from . import app_state

    locks = tuple(app_state._site_lifecycle_locks)
    acquired = []
    try:
        for lock in locks:
            lock.acquire()
            acquired.append(lock)
        yield
    finally:
        while acquired:
            acquired.pop().release()


_IMPORT_CONSTRUCTION_ROLLBACK_TIMEOUT_S = 2.0
_IMPORT_REPLACE_RETIRE_TIMEOUT_S = 2.0


def _construct_import_runner(site_id: str, cfg: dict):
    """Construct while retaining a partially initialized object on failure."""
    factory = SiteRunner
    if not isinstance(factory, type):
        try:
            return factory(site_id, cfg), None
        except BaseException as exc:  # pragma: no cover - callable test seams
            return None, exc

    try:
        candidate = factory.__new__(factory)
    except BaseException as exc:
        return None, exc
    try:
        factory.__init__(candidate, site_id, cfg)
    except BaseException as exc:
        # __init__ can start scheduler/auto-retry generations before a later
        # initialization step raises.  Keep the object so rollback can retain
        # and prove those handles instead of losing the sole owner reference.
        return candidate, exc
    return candidate, None


def _retire_import_candidates(candidates):
    """Boundedly retire staged runtimes; return identities not proven dead."""
    deadline = time.monotonic() + _IMPORT_CONSTRUCTION_ROLLBACK_TIMEOUT_S
    survivors = []
    for site_id, cfg, runner in candidates:
        if runner is None:
            continue
        proven = True
        for method_name in (
                "retire_scheduler", "retire_auto_retry", "retire_workers"):
            method = getattr(runner, method_name, None)
            if not callable(method):
                proven = False
                continue
            try:
                verdict = method(timeout=max(0.0, deadline - time.monotonic()))
            except BaseException:
                verdict = False
            if verdict is not True:
                proven = False
        if not proven:
            survivors.append((site_id, cfg, runner))
    return survivors


def _retain_import_rollback_survivors(survivors, runners, s_cfg, s_meta):
    """Keep the only teardown handles reachable for a later delete retry."""
    for site_id, cfg, runner in survivors:
        runners.setdefault(site_id, runner)
        s_cfg.setdefault(site_id, cfg)
        s_meta.setdefault(site_id, _build_meta(cfg))


def _canonical_delete_response(site_id: str):
    from .app_sites_id_core import _api_delete_transaction

    return current_app.make_response(_api_delete_transaction(site_id))


def _preflight_replace_owner_population(site_ids, runners):
    """Retire/prove every old writer before any durable old state is reaped.

    Runtime retirement is intentionally irreversible.  On a later survivor we
    therefore leave every runner/config/queue identity published and return a
    failure; an earlier proven runner may be retired, but it remains reachable
    for inspection and a later delete retry.  Only a fully green population
    may advance to canonical deletion.
    """
    from . import app_sites_id_core as site_core
    from . import session_keeper

    watch_stops = site_core._app__watch_stops()
    watch_threads = site_core._app__watch_threads()
    watch_lock = site_core._app__watch_registry_lock()
    deadline = time.monotonic() + _IMPORT_REPLACE_RETIRE_TIMEOUT_S
    current = threading.current_thread()

    def remaining():
        return max(0.0, deadline - time.monotonic())

    for site_id in sorted(site_ids):
        with watch_lock:
            stop_event = watch_stops.get(site_id)
            watch_thread = watch_threads.get(site_id)
            if stop_event is not None:
                try:
                    stop_event.set()
                except Exception:
                    return site_id, "watch worker could not be signalled"
        if watch_thread is not None and watch_thread is not current:
            try:
                watch_thread.join(timeout=remaining())
            except Exception:
                pass
        if watch_thread is not None:
            try:
                watch_alive = (watch_thread is current
                               or watch_thread.is_alive())
            except Exception:
                watch_alive = True
            if watch_alive:
                return site_id, "watch worker did not stop"

        try:
            keepers_quiescent = session_keeper.stop_site_keepers(
                site_id, timeout=remaining()) is True
        except Exception:
            keepers_quiescent = False
        if not keepers_quiescent:
            return site_id, "session keeper did not stop"

        runner = runners.get(site_id)
        if runner is None:
            continue
        failed_owner = None
        for owner_name, method_name in (
            ("scheduler worker", "retire_scheduler"),
            ("auto-retry worker", "retire_auto_retry"),
            ("runner worker", "retire_workers"),
        ):
            method = getattr(runner, method_name, None)
            if not callable(method):
                verdict = False
            else:
                try:
                    verdict = method(timeout=remaining())
                except Exception:
                    verdict = False
            if verdict is not True and failed_owner is None:
                failed_owner = owner_name
        if failed_owner is not None:
            return site_id, f"{failed_owner} did not stop"
    return None


def _replace_site_identity_absent(site_id, runners, s_cfg, s_meta) -> bool:
    """Revalidate that a preflighted watch-only owner finalized itself."""
    from . import app_sites_id_core as site_core

    watch_threads = site_core._app__watch_threads()
    watch_stops = site_core._app__watch_stops()
    watch_lock = site_core._app__watch_registry_lock()
    with watch_lock:
        return (site_id not in runners
                and site_id not in s_cfg
                and site_id not in s_meta
                and site_id not in watch_threads
                and site_id not in watch_stops)


def _sync_imported_runtime_dependencies(site_id: str, cfg: dict) -> None:
    """Bring independent keeper/account owners onto a merged config."""
    try:
        from . import session_keeper
        for keeper in session_keeper.get_status():
            if keeper.get("site_id") == site_id:
                session_keeper.update_config(
                    site_id, keeper.get("account_idx", 0), cfg)
    except Exception:
        pass
    from . import account_pool
    accounts = cfg.get("accounts") or []
    if accounts:
        account_pool.configure_pool(
            site_id,
            accounts,
            cooldown_seconds=int(cfg.get(
                "account_cooldown_seconds",
                account_pool.DEFAULT_COOLDOWN_S,
            )),
        )
    else:
        account_pool.remove_pool(site_id)


def _start_imported_runtime_dependencies(site_ids) -> None:
    """Start required new-site keepers/watchers through canonical owners.

    The canonical helpers intentionally no-op when keepalive is disabled, a
    site lacks credentials/watch configuration, or process runtime retirement
    is active.  Actual launch failures propagate; a committed import must not
    silently claim that required background owners started.
    """
    import importlib

    app_module = importlib.import_module("bulk_downloader.app")
    for site_id in site_ids:
        app_module._start_session_keepers(site_id=site_id)
    app_module._start_watch_folder_threads()


@config_bp.route("/api/config/export")
def api_config_export():
    """Export full site configs as JSON. Passwords are stripped by default
    (set ?include_passwords=1 to include them — only do this for offline backup)."""
    s_cfg = _app_s_cfg()
    include_pw=(request.args.get("include_passwords","0")=="1")
    if not include_pw:
        # Redact the FULL secret-field set (not just 'password'), using the
        # authoritative SoT so a default export can never leak plex_token /
        # *_api_key / auth_token etc. Same SoT the marketplace export uses.
        from .site_editor import SECRET_FIELDS  # lazy: no static import edge
    payload=[]
    for sid,cfg in s_cfg.items():
        c=dict(cfg)
        if not include_pw:
            for _sk in SECRET_FIELDS:
                if _sk in c: c[_sk]=""
        c["_id"]=sid  # for round-trip merging
        payload.append(c)
    body=json.dumps({"version":"2.1.1","sites":payload},indent=2)
    fn="bulk_downloader_config.json"
    return Response(body,mimetype="application/json",
                    headers={"Content-Disposition":f"attachment;filename={fn}"})

@config_bp.route("/api/config/import",methods=["POST"])
def api_config_import():
    """Import site configs. mode='merge' (default) keeps existing sites and
    adds new ones (matched by name); mode='replace' wipes existing sites
    first. Passwords from the imported file are used; if blank, the existing
    password for a matching site is preserved."""
    CFG_FIELDS = _app_CFG_FIELDS()
    DEFAULTS = _app_DEFAULTS()
    if "file" in request.files:
        try: data=json.loads(request.files["file"].read().decode("utf-8"))
        except Exception as e: return jsonify({"error":f"bad JSON: {e}"}),400
    else:
        data=request.json or {}
    # Request-only acknowledgement for any paid captcha solver transition in
    # this bulk import.  It is read from the envelope and never copied into a
    # normalized site config (CAPTCHA_EGRESS_ACK_FIELD is not in CFG_FIELDS).
    _captcha_import_ack = (
        data.get(CAPTCHA_EGRESS_ACK_FIELD)
        if isinstance(data, dict) else None
    )
    mode=request.args.get("mode") or (data.get("mode") if isinstance(data,dict) else None) or "merge"
    sites=data.get("sites",[]) if isinstance(data,dict) else (data if isinstance(data,list) else [])
    if not sites: return jsonify({"error":"no sites in payload"}),400
    if mode not in ("merge", "replace"):
        return jsonify({"error": "mode must be 'merge' or 'replace'"}), 400

    # Parse the complete payload before taking the all-site transaction.  No
    # live identity is observed here, so malformed input never stalls normal
    # site routes.  Matching and secret preservation are deliberately deferred
    # until after every stripe is owned and the live dictionaries are
    # revalidated.
    normalized = []
    for raw in sites:
        if not isinstance(raw,dict):
            continue
        _n = raw.get("name")
        name = (_n if isinstance(_n, str)
                else str(_n) if _n is not None else "").strip()
        cfg = {k: raw.get(k, "") for k in CFG_FIELDS}
        cfg["name"] = name or f"Imported {len(normalized) + 1}"
        for key, default in DEFAULTS.items():
            if cfg.get(key) in ("", None):
                cfg[key] = default
        normalized.append(cfg)
    if not normalized:
        return jsonify({"error": "no valid sites in payload"}), 400

    # Phase 41 preflight: defensive coercion against non-string names that
    # may exist in s_cfg from before the input validator was added.
    def _name_of(c):
        v = c.get("name") if isinstance(c, dict) else None
        return (v if isinstance(v, str) else str(v) if v is not None else "").strip().lower()

    with _all_site_lifecycle_transaction():
        runners = _app_runners()
        s_cfg = _app_s_cfg()
        s_meta = _app_s_meta()
        name_to_sid = {_name_of(c): sid for sid, c in s_cfg.items()}
        occupied_ids = set(runners) | set(s_cfg) | set(s_meta)
        update_plan = []
        create_plan = []

        for cfg in normalized:
            existing_sid = (name_to_sid.get(cfg["name"].lower())
                            if mode == "merge" else None)
            # Preserve the historical orphan behavior: a config without a
            # runner cannot be updated safely, so import a fresh live site
            # rather than pretending the orphan's runtime changed.
            if existing_sid and existing_sid not in runners:
                existing_sid = None
            if existing_sid:
                from .site_editor import SECRET_FIELDS
                old_cfg = s_cfg[existing_sid]
                for secret_key in SECRET_FIELDS:
                    if not cfg.get(secret_key) and old_cfg.get(secret_key):
                        cfg[secret_key] = old_cfg[secret_key]
                _captcha_gate_input = dict(cfg)
                _captcha_gate_input[CAPTCHA_EGRESS_ACK_FIELD] = (
                    _captcha_import_ack)
                _captcha_egress_error = captcha_egress_disclosure_error(
                    _captcha_gate_input, old_cfg)
                if _captcha_egress_error:
                    return jsonify({"error": _captcha_egress_error}), 400
                update_plan.append(
                    (existing_sid, cfg, dict(old_cfg), runners[existing_sid]))
                continue

            site_id = uuid.uuid4().hex[:8]
            while site_id in occupied_ids:
                site_id = uuid.uuid4().hex[:8]
            occupied_ids.add(site_id)
            _captcha_gate_input = dict(cfg)
            _captcha_gate_input[CAPTCHA_EGRESS_ACK_FIELD] = (
                _captcha_import_ack)
            _captcha_egress_error = captcha_egress_disclosure_error(
                _captcha_gate_input)
            if _captcha_egress_error:
                return jsonify({"error": _captcha_egress_error}), 400
            create_plan.append((site_id, cfg))

        # Constructors start scheduler and auto-retry owners.  Stage every
        # runtime off-registry first; a later constructor failure can then
        # retire the complete staged population without exposing a half import
        # to routing or deletion.
        staged = []
        construction_error = None
        for site_id, cfg in create_plan:
            candidate, error = _construct_import_runner(site_id, cfg)
            if candidate is not None:
                staged.append((site_id, cfg, candidate))
            if error is None and cfg.get("cookie_file"):
                cookie_path = Path(cfg["cookie_file"])
                if cookie_path.exists():
                    try:
                        candidate.set_cookies_from_file(cookie_path)
                    except BaseException as exc:
                        error = exc
            if error is not None:
                construction_error = error
                break

        if construction_error is not None:
            survivors = _retire_import_candidates(staged)
            _retain_import_rollback_survivors(
                survivors, runners, s_cfg, s_meta)
            if survivors:
                try:
                    _save_sites_config()
                except Exception:
                    pass
            return jsonify({
                "ok": False,
                "error": "site runtime construction failed",
                "detail": (f"{type(construction_error).__name__}: "
                           f"{construction_error}"),
                "rollback_complete": not survivors,
            }), 503

        if mode == "replace":
            # Revalidate the owner population only after all stripes are held.
            # Include watch registries so a configured site's independently
            # owned watcher generation cannot be omitted from teardown.
            old_site_ids = set(runners) | set(s_cfg) | set(s_meta)
            try:
                from . import app_sites_id_core as site_core
                old_site_ids.update(site_core._app__watch_threads())
                old_site_ids.update(site_core._app__watch_stops())
            except Exception:
                pass
            preflight_failure = _preflight_replace_owner_population(
                old_site_ids, runners)
            if preflight_failure is not None:
                failed_site_id, failure_detail = preflight_failure
                survivors = _retire_import_candidates(staged)
                _retain_import_rollback_survivors(
                    survivors, runners, s_cfg, s_meta)
                if survivors:
                    try:
                        _save_sites_config()
                    except Exception:
                        pass
                return jsonify({
                    "ok": False,
                    "error": "replace teardown failed",
                    "site_id": failed_site_id,
                    "detail": failure_detail,
                    # Old runtime retirement is irreversible.  Even when the
                    # staged replacement rolled back cleanly, one or more old
                    # owners may already be stopped while their durable state
                    # remains intentionally reachable.
                    "rollback_complete": False,
                    "teardown_partial": True,
                }), 503
            for site_id in sorted(old_site_ids):
                delete_response = _canonical_delete_response(site_id)
                if delete_response.status_code < 400:
                    continue
                # A real watcher target removes its own thread/stop identities
                # in ``finally``.  Preflight joins that target before this
                # commit loop, so a watch-only orphan can legitimately become
                # totally absent before canonical DELETE observes it.  Treat
                # only that proven post-preflight absence as already reaped;
                # ordinary DELETE of a truly unknown ID remains a 404.
                if (delete_response.status_code == 404
                        and _replace_site_identity_absent(
                            site_id, runners, s_cfg, s_meta)):
                    continue
                survivors = _retire_import_candidates(staged)
                _retain_import_rollback_survivors(
                    survivors, runners, s_cfg, s_meta)
                if survivors:
                    try:
                        _save_sites_config()
                    except Exception:
                        pass
                detail = delete_response.get_json(silent=True) or {}
                return jsonify({
                    "ok": False,
                    "error": "replace teardown failed",
                    "site_id": site_id,
                    "detail": detail.get("error", "site did not retire"),
                    "rollback_complete": False,
                    "teardown_partial": True,
                }), delete_response.status_code

        # Existing runners remain the same identity in merge mode.  Apply all
        # runtime updates before publishing config dictionaries; if one raises,
        # best-effort restore every runner already touched and keep the old
        # persisted identities visible.
        applied_updates = []
        update_error = None
        for site_id, cfg, old_cfg, runner in update_plan:
            try:
                runner.update_config(cfg)
                applied_updates.append((site_id, old_cfg, runner))
            except BaseException as exc:
                update_error = exc
                applied_updates.append((site_id, old_cfg, runner))
                break
        if update_error is not None:
            rollback_complete = True
            for _site_id, old_cfg, runner in reversed(applied_updates):
                try:
                    runner.update_config(old_cfg)
                except BaseException:
                    rollback_complete = False
            survivors = _retire_import_candidates(staged)
            _retain_import_rollback_survivors(
                survivors, runners, s_cfg, s_meta)
            if survivors:
                rollback_complete = False
            return jsonify({
                "ok": False,
                "error": "site runtime update failed",
                "detail": f"{type(update_error).__name__}: {update_error}",
                "rollback_complete": rollback_complete,
            }), 503

        for site_id, cfg, _old_cfg, _runner in update_plan:
            s_cfg[site_id] = cfg
            s_meta[site_id] = _build_meta(cfg)
        for site_id, cfg, runner in staged:
            s_cfg[site_id] = cfg
            s_meta[site_id] = _build_meta(cfg)
            runners[site_id] = runner

        try:
            persisted = _save_sites_config()
        except Exception as exc:
            # The in-memory transaction has committed, but acknowledging it as
            # durable would be false.  Leave identities reachable and fail
            # loudly so the operator can retry persistence.
            return jsonify({
                "ok": False,
                "error": "site config persistence failed",
                "detail": f"{type(exc).__name__}: {exc}",
            }), 503
        if persisted is not True:
            return jsonify({
                "ok": False,
                "error": "site config persistence failed",
                "detail": "atomic sites-config writer did not confirm replace",
            }), 503

        try:
            for site_id, cfg, _old_cfg, _runner in update_plan:
                _sync_imported_runtime_dependencies(site_id, cfg)
            for site_id, cfg, _runner in staged:
                _sync_imported_runtime_dependencies(site_id, cfg)
            dependency_site_ids = list(dict.fromkeys([
                *[site_id for site_id, _cfg, _old_cfg, _runner in update_plan],
                *[site_id for site_id, _cfg, _runner in staged],
            ]))
            _start_imported_runtime_dependencies(dependency_site_ids)
        except Exception as exc:
            return jsonify({
                "ok": False,
                "error": "site runtime dependency startup failed",
                "detail": f"{type(exc).__name__}: {exc}",
            }), 503

        return jsonify({
            "ok": True,
            "imported": len(staged),
            "updated": len(update_plan),
            "mode": mode,
        })

def register_routes(app) -> int:
    app.register_blueprint(config_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("config."))
