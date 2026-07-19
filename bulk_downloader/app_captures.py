"""captures API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/captures views moved onto a Flask Blueprint.
Endpoint labels gain a "captures." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_app_cfg, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import re
from flask import Blueprint, jsonify, request

captures_bp = Blueprint("captures", __name__)

def _create_site(*_a, **_k):
    """Delegate to app._create_site at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_create_site")(*_a, **_k)

def _save_app_config(*_a, **_k):
    """Delegate to app._save_app_config at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_save_app_config")(*_a, **_k)

def _save_sites_config(*_a, **_k):
    """Delegate to app._save_sites_config at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_save_sites_config")(*_a, **_k)

def _validate_path(*_a, **_k):
    """Delegate to app._validate_path at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_validate_path")(*_a, **_k)

def _app__app_cfg():
    """The live shared _app_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "_app_cfg")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@captures_bp.route("/api/captures/setup_site", methods=["POST"])
def api_setup_site():
    """GCW-3 "Setup site" wizard step: create a site and (optionally) store
    its login password as a ``@cred:`` reference, in one atomic call, returning
    the 8-hex ``site_id`` so the capture wizard auto-fills it into the
    Test/Promote steps (the operator no longer hand-types the id).

    Body: ``{name, login_url, username?, password?}``.

    F2 posture: the password is NEVER persisted in plaintext and NEVER reaches
    a template. It is stripped from the create payload BEFORE ``_create_site``
    (so it cannot land in sites_config.json or the create audit record); when a
    usable encrypted backend is unlocked it is stored via ``secrets_store`` and
    a ``@cred:bulkdl-site-<id>`` reference is written into ``cfg['password']``.
    ``resolve_password()`` resolves that reference at login-fill time.

    Atomicity: when a password is supplied but the secrets backend can't store
    it (plaintext -> 400, locked -> 401) the request is refused BEFORE the site
    is created, so no half-created site is left behind. With no password the
    site is created and the operator logs in by hand during the held-open
    capture (cookies persist). A pathological ``set()`` failure on an unlocked
    encrypted backend leaves the site created but surfaces ``cred_error`` (the
    plaintext is still never written) so the operator can retry via Secrets.

    Lives at the ROOT ``/api/captures/setup_site`` in ``app.py`` (NOT the
    deploy-excluded ``/cockpit`` blueprint in ``tools/cockpit_console.py``) so
    it overlays normally on deploy and reaches ``_create_site`` / ``s_cfg`` /
    ``secrets_store`` directly.
    """
    s_cfg = _app_s_cfg()
    from . import secrets_store as ss
    data = dict(request.json or {})
    name = (data.get("name") or "").strip()
    login_url = (data.get("login_url") or "").strip()
    if not name or not login_url:
        return jsonify({"ok": False,
                        "error": "name and login_url are required"}), 400

    # Pull the secret OUT before any create/audit so plaintext never lands in
    # sites_config.json. username is NOT a secret -> leave it for the site.
    password = data.pop("password", "") or ""
    want_cred = bool(password)

    # Atomic credential: validate the backend can store it BEFORE creating the
    # site, so a non-storable backend never leaves a half-created site behind.
    backend = None
    if want_cred:
        backend = ss.get_backend()
        if getattr(backend, "name", "") == "plaintext":
            return jsonify({"ok": False, "error":
                "active secrets backend is plaintext; switch to an encrypted "
                "backend before storing a credential, or create the site "
                "without a password and log in by hand"}), 400
        if hasattr(backend, "is_unlocked") and not backend.is_unlocked():
            return jsonify({"ok": False,
                            "error": "secrets backend is locked; unlock it first"}), 401

    actor = (request.cookies.get("bd_session", "")[:8]
             or request.remote_addr or "unknown")
    # _create_site never sees the plaintext password (popped above); the
    # non-secret username / login_url ride through normally.
    sid, err = _create_site(data, actor=actor)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    cred_stored = False
    cred_error = None
    if want_cred:
        try:
            backend.set(ss.site_password_key(sid), password)
            s_cfg[sid]["password"] = ss.make_password_reference(sid)
            _save_sites_config()
            cred_stored = True
        except Exception as e:
            # The site exists; surface the cred failure so the operator can
            # retry via Settings -> Secrets. No plaintext is ever written.
            cred_error = str(e)

    resp = {"ok": True, "id": sid, "login_url": login_url,
            "cred_stored": cred_stored}
    if cred_error:
        resp["cred_error"] = cred_error
    autopick = (s_cfg.get(sid) or {}).get("_autopick")
    if autopick:
        resp["auto_pick"] = autopick
    return jsonify(resp)


@captures_bp.route("/api/captures/validate_download_dir")
def api_captures_validate_download_dir():
    """GCW guided-mode READ-ONLY download-root check. Query: ``?path=<abs>``.

    Returns ``{ok, error?}``. Wraps ``_validate_path(path, "root")`` -- the exact
    allowlist semantics already enforced at every download -- so the guided Setup
    step can flag a download folder that isn't under an allowed root INLINE (the
    moment the field blurs), instead of letting it fail silently at Test. Read-only:
    no FS write, no state change; it only reports what the runtime enforces.
    """
    path = request.args.get("path", "").strip()
    ok, msg = _validate_path(path, "root")
    return jsonify({"ok": True} if ok else {"ok": False, "error": msg})


@captures_bp.route("/api/captures/allowlist_add", methods=["POST"])
def api_captures_allowlist_add():
    """GCW guided-mode: add a root to the path allowlist -- the CONFIRM-GATED,
    AUDITED one-click that removes the Settings round-trip when a Setup download
    folder isn't yet under an allowed root.
    Body: ``{"path": "<abs>", "confirm": true}``.

    Widening the allowlist is security-relevant, so this is the single WRITE in
    the guided backend surface and is fully gated: CSRF (global before_request),
    an explicit ``confirm: true`` (absent/false -> 400, so it can never fire by
    accident), an absolute + non-traversing path check, dedup, persist via
    ``_save_app_config``, and an explicit AUDIT record of the widening
    (before/after). It deliberately does NOT require the path to already be under
    the allowlist -- that membership is exactly what it is adding.
    """
    _app_cfg = _app__app_cfg()
    body = request.json or {}
    if body.get("confirm") is not True:
        return jsonify({"ok": False,
                        "error": "confirm:true required to widen the path "
                                 "allowlist"}), 400
    raw = body.get("path") or ""
    p = raw.strip() if isinstance(raw, str) else ""
    if not p:
        return jsonify({"ok": False, "error": "path required"}), 400
    # Abs + non-traversing sanity -- the SAME rules as _validate_path's first
    # half, MINUS the allowlist-membership check (membership is what we are
    # widening, so requiring it here would reject every path this route adds).
    import os as _os
    if ".." in re.split(r"[\\/]+", p):
        return jsonify({"ok": False,
                        "error": "path cannot contain '..' segments"}), 400
    is_abs = (p.startswith("/") or p.startswith("\\\\")
              or (len(p) >= 2 and p[1] == ":") or _os.path.isabs(p))
    if not is_abs:
        return jsonify({"ok": False,
                        "error": "path must be an absolute path"}), 400
    before = list(_app_cfg.get("path_allowlist") or [])
    if p in before:
        return jsonify({"ok": True, "allowlist": before})  # idempotent
    after = before + [p]
    _app_cfg["path_allowlist"] = after
    _save_app_config()
    actor = (request.cookies.get("bd_session", "")[:8]
             or request.remote_addr or "unknown")
    try:
        from . import audit as _audit
        _audit.audit_log(source="api", action="add", target="path_allowlist",
                         before=before, after=after, actor=actor)
    except Exception:
        pass  # audit failures must never block the actual write
    return jsonify({"ok": True, "allowlist": after})


@captures_bp.route("/api/captures/suggest_rows", methods=["POST"])
def api_captures_suggest_rows():
    """Auto-detect repeating row groups on a running interactive capture.

    Body: ``{task_id, action: arm|poll}``. Delegates to
    ``cockpit_core.suggest_rows_capture``, which drives the AUTO_ROW_REQUEST /
    AUTO_ROW_RESULT sentinels the capture services on its tick. Lives at the ROOT
    ``/api/captures/suggest_rows`` (NOT the deploy-excluded ``/cockpit``
    blueprint) so it overlays normally on deploy -- same rationale as
    ``/api/captures/setup_site``. Recommendation only: the ranked candidates
    pre-fill the wizard's row_selectors field; the operator confirms.
    """
    from tools import cockpit_core as _cc
    body = request.get_json(silent=True) or {}
    tid = str(body.get("task_id", ""))
    action = str(body.get("action", "arm"))
    try:
        res = _cc.suggest_rows_capture(tid, action=action)
    except _cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(res)

_SCAN_CACHE = {"rows": [], "summary": None, "built_at": None}


@captures_bp.route("/api/captures/scan", methods=["POST"])
def api_captures_scan():
    """Item 3: explicit operator-triggered recursive capture scan. Walks the
    capture roots (incl. nested onboarding subdirs that the flat list misses),
    (re)builds the inventory, caches it, and returns a value-free summary
    ``{ok, total, by_host, new_since_last, took_ms}``. Opens NO wacz zips. This
    is the expensive call -- the GUI runs it on demand, never on a poll. CSRF is
    enforced by the global before_request."""
    import time as _time
    from . import dom_analyzer as _da
    from . import db as _db  # Cut 1.2: durable capture index (lazy; edge re-frozen)
    prev = {r["rel_path"] for r in (_SCAN_CACHE.get("rows") or [])}
    summ = _da.scan_captures_summary()
    rows = summ.pop("rows")
    new_since_last = (
        sum(1 for r in rows if r["rel_path"] not in prev) if prev else len(rows)
    )
    # Cut 1.2: the walk is the reconcile PRODUCER; persist it into the db index so
    # the inventory survives a restart. Upsert the walked rows, then prune rows for
    # captures that no longer exist on disk. The in-memory cache is kept only as a
    # warm summary; the durable source of truth is now the table.
    try:
        _db.db_captures_upsert(rows)
        _db.db_captures_prune_missing({r["rel_path"] for r in rows})
    except Exception:
        pass  # index persistence is best-effort; the walk result is still returned
    _SCAN_CACHE["rows"] = rows
    _SCAN_CACHE["summary"] = summ
    _SCAN_CACHE["built_at"] = _time.time()
    return jsonify({"ok": True, "new_since_last": new_since_last, **summ})


@captures_bp.route("/api/captures")
def api_captures_list():
    """Item 3: serve the cached recursive capture inventory -- paginated +
    filterable by host/kind/redacted, newest first. Fast: opens no zips, just
    reads the cache built by POST /api/captures/scan. Value-free (relative
    subpath tokens + cheap metadata only, never an absolute path -- F2)."""
    from . import db as _db  # Cut 1.2: durable capture index (lazy; edge re-frozen)
    host = (request.args.get("host") or "").strip()
    kind = (request.args.get("kind") or "").strip()
    redacted_arg = request.args.get("redacted")
    redacted = None
    if redacted_arg in ("true", "false"):
        redacted = (redacted_arg == "true")
    try:
        page = max(1, int(request.args.get("page", 1)))
        per = min(200, max(1, int(request.args.get("per_page", 50))))
    except (TypeError, ValueError):
        page, per = 1, 50
    # Cut 1.2: served from the DURABLE db `captures` index so the inventory
    # survives a restart without a re-walk (was the restart-ephemeral _SCAN_CACHE).
    try:
        rows = _db.db_captures_all(
            host=host or None,
            kind=kind if kind in ("wacz", "json") else None,
            redacted=redacted,
        )
    except Exception:
        rows = list(_SCAN_CACHE.get("rows") or [])  # defensive fallback
    total = len(rows)
    start = (page - 1) * per
    # "scanned" now means the durable index has ever been populated -- either a
    # prior scan this process or a persisted one from before a restart.
    scanned = total > 0 or (_SCAN_CACHE.get("built_at") is not None)
    return jsonify({
        "ok": True,
        "scanned": scanned,
        "built_at": _SCAN_CACHE.get("built_at"),
        "total": total,
        "page": page,
        "per_page": per,
        "captures": rows[start:start + per],
        "summary": _SCAN_CACHE.get("summary"),
    })


@captures_bp.route("/api/captures/build_draft", methods=["POST"])
def api_captures_build_draft():
    """Item 3 follow-on: build a REVIEW-ONLY template draft from one stored
    capture (resolved by its subpath ``token``). Delegates to
    ``dom_analyzer.build_draft_from_token`` -- writes only into the drafts
    review queue, never promotes/enables. 400 on an unresolvable token or a
    build failure. CSRF via the global before_request."""
    from . import dom_analyzer as _da
    body = request.get_json(silent=True) or {}
    res = _da.build_draft_from_token((body.get("token") or "").strip())
    return jsonify(res), (200 if res.get("ok") else 400)


@captures_bp.route("/api/captures/scrub", methods=["POST"])
def api_captures_scrub():
    """Item 3 follow-on: scrub a raw ``.wacz`` capture (resolved by ``token``)
    to its share-ready ``.redacted.wacz`` twin via the fail-soft capture-scrub
    hook. Delegates to ``dom_analyzer.scrub_capture_token``; the raw capture is
    never touched. 400 only on an unresolvable token. CSRF via the global
    before_request."""
    from . import dom_analyzer as _da
    res = _da.scrub_capture_token(((request.get_json(silent=True) or {}).get("token") or "").strip())
    return jsonify(res), (200 if res.get("ok") else 400)


def register_routes(app) -> int:
    app.register_blueprint(captures_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("captures."))

