"""replication API -- Cut v3.66.635 (status) + v3.66.636 (controls) / C5.
A thin Flask blueprint over the ``db_replication`` engine (continuous SQLite
replication via Litestream). Mirrors the ``app_semantic_search`` / ``app_backup``
blueprint pattern; endpoint labels gain a ``replication.`` prefix.

Why this exists: ``db_replication`` (Cut 622) shipped complete + unit-tested but
as an ISLAND -- no route reached it, so the durability layer that "strengthens the
A0 gold-backup the automation program gates L2 autonomy on" was unreachable and
``start_replication`` was uncallable at runtime.

Routes:
  GET  /api/replication/status   -- durability snapshot (safe binary-or-not) [635]
  POST /api/replication/start    -- start the Litestream sidecar (fail-closed)  [636]
  POST /api/replication/stop     -- stop the sidecar (idempotent)               [636]
  POST /api/replication/restore  -- restore a CONFIGURED store from its replica  [636]

Safety: every entry point delegates to the fail-closed db_replication primitives
(charter default-OFF; ok=False when disabled or the litestream binary is absent).
``restore`` requires ``db_name`` to be one of the configured stores (rejects an
arbitrary replica name) and restores to a SERVER-CHOSEN staging path -- the caller
never supplies a filesystem destination (the dual of the F-APP03-02 file-read fix:
no file-write-anywhere). Live WAL shipping is validated on-stash (needs the binary).
"""
from __future__ import annotations

import os
from flask import Blueprint, jsonify, request

replication_bp = Blueprint("replication", __name__)


def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@replication_bp.route("/api/replication/status", methods=["GET"])
def api_replication_status():
    """Read-only durability snapshot. Wraps ``db_replication.replication_status``
    (which never raises) as ``{ok: True, ...}``. Falls back to ``ok: False`` +
    500 only if the module import itself fails."""
    try:
        from . import db_replication as _repl
        return jsonify({"ok": True, **_repl.replication_status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@replication_bp.route("/api/replication/start", methods=["POST"])
def api_replication_start():
    """Start the Litestream replicate sidecar. Fail-closed: returns
    ``{ok: False, reason}`` when replication is disabled in config, the binary is
    absent, or there is nothing to replicate (never a fabricated success)."""
    _check_csrf()
    try:
        from . import db_replication as _repl
        return jsonify(_repl.start_replication())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@replication_bp.route("/api/replication/stop", methods=["POST"])
def api_replication_stop():
    """Stop the running sidecar. Idempotent: ``{ok: True, stopped: bool}``."""
    _check_csrf()
    try:
        from . import db_replication as _repl
        return jsonify(_repl.stop_replication())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@replication_bp.route("/api/replication/restore", methods=["POST"])
def api_replication_restore():
    """Restore a CONFIGURED store from its file replica into a server-chosen
    staging path, then verify. Body: ``{db_name}``.

    ``db_name`` must be one of the configured stores -- an arbitrary name is
    refused (400) before any filesystem access, so a caller can neither read an
    off-tree replica nor choose the write destination. The destination is derived
    server-side under ``<replica_root>/restored/`` (never operator-supplied), and
    the underlying primitive is fail-closed when the litestream binary is absent."""
    _check_csrf()
    body = request.get_json(silent=True) or {}
    db_name = (body.get("db_name") or "").strip()
    if not db_name:
        return jsonify({"ok": False, "error": "db_name required"}), 400
    try:
        from . import db_replication as _repl
        stores = {p.name for p in _repl.replication_stores()}
        if db_name not in stores:
            return jsonify({"ok": False,
                            "error": f"unknown store: {db_name}"}), 400
        # Server-chosen staging destination -- the caller never supplies a path.
        replica_root = _repl.replication_status().get("replica_root") or "."
        staging = os.path.join(replica_root, "restored")
        os.makedirs(staging, exist_ok=True)
        dest = os.path.join(staging, os.path.basename(db_name))
        return jsonify(_repl.restore_store(db_name, dest))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def register_routes(app) -> int:
    app.register_blueprint(replication_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("replication."))
