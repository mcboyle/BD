"""interop registry API -- INTEROP-GOV-1b (v3.66.639). Operator surface over the
interop_registry keystone (v3.66.638): register an interop item + its provenance,
acknowledge its risk, enable it, and list the registry. Mirrors the app_provenance /
app_replication blueprint pattern; endpoint labels gain an "interop." prefix.

Charter unchanged (interop roadmap @601): the registry RECORDS provenance and
REQUIRES the ack + enable before an item is permitted -- it is NOT an allowlist and
BD ships nothing / keeps no catalog. These routes only let the operator populate and
consent; the actual gate is interop_registry.is_permitted, consulted at load time
(e.g. the chromium-extension gate in runner_browser).

Routes:
  GET  /api/interop/registry     -- list all registered items + the known kinds
  POST /api/interop/register     -- {kind, item_id, source?, commit?, sha256?}
  POST /api/interop/acknowledge  -- {kind, item_id}  (400 if unregistered)
  POST /api/interop/enable       -- {kind, item_id, enabled}  (400 if unregistered)

For a chromium_extension the provenance hash is computed SERVER-SIDE from the dir
(interop_registry.dir_sha256) so the operator never supplies a hash and the pin is
authoritative. All mutating routes are POST-only + go through the /api/ CSRF gate.
"""
from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

interop_bp = Blueprint("interop", __name__)


def _check_csrf(*_a, **_k):
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


def _json_body():
    """The request's JSON object, {} when absent/invalid, or None when it is
    nested past the parser's recursion limit (O1789 F11: RecursionError escaped
    get_json(silent=True) as a 500)."""
    try:
        return request.get_json(silent=True) or {}
    except RecursionError:
        return None


_BAD_BODY = ({"ok": False, "error": "request body is not a usable JSON object"}, 400)


def _extra_roots() -> list:
    """The operator's ``interop_extra_roots`` (global config only, never the
    request body): absolute dirs; anything else is ignored (O1789 F7)."""
    try:
        from . import global_config as _gc
        raw = _gc.get("interop_extra_roots", [])
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, str) and os.path.isabs(r)]


@interop_bp.route("/api/interop/registry", methods=["GET"])
def api_interop_registry():
    """Read-only: every registered item (kind, item_id, provenance, flags) + the
    known interop kinds for the register form."""
    try:
        from . import interop_registry as _ir
        extra = _extra_roots()
        items = [{"kind": k, "item_id": i, **rec,
                  "outside_roots": _ir.outside_roots(k, i, extra)}
                 for k, i, rec in _ir.list_all()]
        return jsonify({"ok": True, "items": items, "kinds": list(_ir.KINDS)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@interop_bp.route("/api/interop/register", methods=["POST"])
def api_interop_register():
    """Record (or update) an item's provenance. Off by default -- registering does
    not acknowledge or enable. For a chromium_extension the dir is hashed
    server-side; other kinds may pass an explicit sha256/commit."""
    refused = _check_csrf()
    if refused is not None:
        return refused
    body = _json_body()
    if body is None:
        return jsonify(_BAD_BODY[0]), _BAD_BODY[1]
    try:
        from . import interop_registry as _ir
        kind = (body.get("kind") or "").strip()
        item_id = (body.get("item_id") or "").strip()
        if kind not in _ir.KINDS:
            return jsonify({"ok": False, "error": f"unknown kind: {kind}"}), 400
        if not item_id:
            return jsonify({"ok": False, "error": "item_id required"}), 400
        # O1789 F7/F6/F14 (PM ruling ANSWER-pm-B-F7-0858Z): a path item_id must
        # resolve strictly inside a root of its own kind BEFORE anything is read,
        # hashed or unpacked. Refused paths are never opened.
        _ir.ensure_roots()
        if kind in _ir.PATH_KINDS:
            try:
                _ir.contain(kind, item_id, _extra_roots())
            except _ir.OutsideRoots as e:
                return jsonify({"ok": False, "error": str(e)}), 400
        # INTEROP-EXT-4 (v3.66.690): a chromium_extension may be supplied as a
        # packed .crx. Validate the manifest FIRST, then unpack it to
        # <BD_HOME>/interop/chromium_extension/<sha256 of the .crx> (F7 rule 5),
        # then register the UNPACKED dir so the existing dir_sha256 provenance +
        # is_permitted gate apply unchanged. MV3 service-worker caveats (and any
        # manifest warnings) ride back in the response; a manifest that fails
        # validation is rejected 400 before anything is registered. A non-.crx
        # item_id (an unpacked dir) is byte-identical to the prior path.
        crx_warnings: list = []
        if kind == "chromium_extension" and item_id.lower().endswith(".crx"):
            from . import extension_crx as _crx
            try:
                # unpack only into the app-managed root, never next to the .crx
                info = _crx.crx_info(item_id, dest_root=_ir.default_root(kind))
            except _crx.CrxError as e:
                return jsonify({"ok": False, "error": f"CRX unpack failed: {e}"}), 400
            if not info["ok"]:
                return jsonify({"ok": False,
                                "error": "invalid CRX manifest: " + "; ".join(info["errors"])}), 400
            item_id = info["dir"]          # register the unpacked dir
            crx_warnings = info["warnings"]
        source = (body.get("source") or "").strip()
        commit = (body.get("commit") or "").strip()
        sha256 = (body.get("sha256") or "").strip()
        if kind == "chromium_extension" and not sha256:
            try:
                sha256 = _ir.dir_sha256(item_id, strict=True)
            except _ir.TreeTooLarge as e:
                return jsonify({"ok": False, "error": str(e)}), 400
        rec = _ir.register(kind, item_id, source=source, sha256=sha256, commit=commit)
        return jsonify({"ok": True,
                        "record": {"kind": kind, "item_id": item_id, **rec},
                        "crx_warnings": crx_warnings})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@interop_bp.route("/api/interop/acknowledge", methods=["POST"])
def api_interop_acknowledge():
    """Set risk_acknowledged for a REGISTERED item. 400 if it was never registered
    (an ack cannot conjure a permitted phantom)."""
    refused = _check_csrf()
    if refused is not None:
        return refused
    body = _json_body()
    if body is None:
        return jsonify(_BAD_BODY[0]), _BAD_BODY[1]
    try:
        from . import interop_registry as _ir
        kind = (body.get("kind") or "").strip()
        item_id = (body.get("item_id") or "").strip()
        if not _ir.acknowledge(kind, item_id):
            return jsonify({"ok": False, "error": "unknown item (register first)"}), 400
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@interop_bp.route("/api/interop/enable", methods=["POST"])
def api_interop_enable():
    """Set the enabled flag for a REGISTERED item. 400 if unregistered."""
    refused = _check_csrf()
    if refused is not None:
        return refused
    body = _json_body()
    if body is None:
        return jsonify(_BAD_BODY[0]), _BAD_BODY[1]
    try:
        from . import interop_registry as _ir
        kind = (body.get("kind") or "").strip()
        item_id = (body.get("item_id") or "").strip()
        enabled = body.get("enabled", False)
        if not isinstance(enabled, bool):   # O1789 F12: bool("false") is True
            return jsonify({"ok": False, "error": "enabled must be a JSON boolean"}), 400
        # F7 rule 7: a legacy record outside the roots stays, but cannot be enabled
        if enabled and _ir.outside_roots(kind, item_id, _extra_roots()):
            return jsonify({"ok": False, "error": _ir.OUTSIDE_ROOTS}), 400
        if not _ir.set_enabled(kind, item_id, enabled):
            return jsonify({"ok": False, "error": "unknown item (register first)"}), 400
        return jsonify({"ok": True, "enabled": enabled})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def register_routes(app) -> int:
    app.register_blueprint(interop_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("interop."))
