"""app_store_raw_editor.py — Bucket 3b (GUI-config parity): the raw vpn/widgets
store-metadata editor.

vpn.* and widgets.* metadata (schema_version, tunnel_id, _saved_at) live in
SEPARATE stores (tunnels.json / widgets.json), NOT global_config — and neither
store exposes a per-key meta API. So the GUI path is a raw JSON store-file
editor: read the current file text, let the operator edit it, validate + write
it back atomically, and invalidate the in-memory cache so the live state
reflects the edit without a restart.

A DEDICATED blueprint (same precedent as Bucket 2's app_envfile_editor) so the
Settings Center stays read-only — this never touches global_config.

Endpoints:
  GET  /api/settings/store-raw?store=vpn|widgets  -> {store, path, text}
  POST /api/settings/store-raw  {store, text}     -> validate + atomic write
  POST /api/settings/store-raw/rekey {old_id,new_id} -> atomic tunnel rename

R1 (tunnel_id guard): a raw POST that changes/removes a tunnel_id which still
has secrets in secrets_store is REJECTED (400, file byte-identical) — a naive
edit would orphan the @cred:{tunnel_id}:* secrets. Rename via the rekey action.
_saved_at stays display-only (it is auto-stamped by each store's save()).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Blueprint, jsonify, request

store_raw_editor_bp = Blueprint("store_raw_editor", __name__)

_STORES = ("vpn", "widgets")


def _store_modules():
    from bulk_downloader import vpn_config, widgets_config
    return {"vpn": vpn_config, "widgets": widgets_config}


def _path_for(store: str) -> Path:
    mods = _store_modules()
    return mods[store]._config_path()


def _read_text(store: str) -> str:
    """Current file contents, pretty-printed. If the file doesn't exist yet,
    materialize the module's current in-memory state via save() so the editor
    always has a real, round-trippable document."""
    mods = _store_modules()
    mod = mods[store]
    path = _path_for(store)
    if not path.exists():
        mod.load()
        mod.save()
    try:
        raw = path.read_text(encoding="utf-8")
        # normalize to pretty 2-space for the editor
        return json.dumps(json.loads(raw), indent=2)
    except Exception:
        return path.read_text(encoding="utf-8")


def _invalidate(store: str) -> None:
    mods = _store_modules()
    mod = mods[store]
    mod._loaded = False
    mod.load()


def _validate_shape(store: str, data) -> str | None:
    """Return an error string, or None when the shape is acceptable."""
    if not isinstance(data, dict):
        return "top-level value must be a JSON object"
    sv = data.get("schema_version")
    if sv is not None and (not isinstance(sv, int) or isinstance(sv, bool) or sv < 1):
        return "schema_version must be a positive integer"
    if store == "vpn":
        tunnels = data.get("tunnels", [])
        if not isinstance(tunnels, list) or not all(isinstance(t, dict) for t in tunnels):
            return "vpn: 'tunnels' must be a list of objects"
        for t in tunnels:
            if "tunnel_id" not in t or not isinstance(t.get("tunnel_id"), str):
                return "vpn: every tunnel needs a string tunnel_id"
    if store == "widgets":
        g = data.get("global")
        if g is not None and not isinstance(g, list):
            return "widgets: 'global' must be a list"
        ps = data.get("per_site")
        if ps is not None and not isinstance(ps, dict):
            return "widgets: 'per_site' must be an object"
    return None


def _tunnel_id_guard(data) -> str | None:
    """R1: reject an incoming vpn payload whose tunnel set drops/renames a
    tunnel_id that still has secrets (would orphan @cred:{id}:* refs)."""
    from bulk_downloader import vpn_config
    with_secrets = vpn_config.tunnel_ids_with_secrets()
    if not with_secrets:
        return None
    incoming = {t.get("tunnel_id") for t in data.get("tunnels", [])
                if isinstance(t, dict)}
    orphaned = sorted(with_secrets - incoming)
    if orphaned:
        return ("tunnel_id change/removal blocked: {} still own stored secrets. "
                "Rename via the rekey action (POST /api/settings/store-raw/rekey) "
                "or delete+recreate the tunnel.".format(", ".join(orphaned)))
    return None


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


@store_raw_editor_bp.route("/api/settings/store-raw", methods=["GET"])
def api_store_raw_get():
    store = request.args.get("store", "")
    if store not in _STORES:
        return jsonify({"ok": False, "error": f"unknown store {store!r}; "
                        f"allowed: {list(_STORES)}"}), 400
    try:
        return jsonify({"ok": True, "store": store,
                        "path": str(_path_for(store)),
                        "text": _read_text(store)})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@store_raw_editor_bp.route("/api/settings/store-raw", methods=["POST"])
def api_store_raw_post():
    body = request.get_json(silent=True) or {}
    store = body.get("store", "")
    text = body.get("text", "")
    if store not in _STORES:
        return jsonify({"ok": False, "error": f"unknown store {store!r}"}), 400
    if not isinstance(text, str):
        return jsonify({"ok": False, "error": "'text' must be a string"}), 400
    # (a) JSON validity
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        return jsonify({"ok": False, "error": f"invalid JSON: {str(e)[:160]}"}), 400
    # (b) shape
    shape_err = _validate_shape(store, data)
    if shape_err:
        return jsonify({"ok": False, "error": shape_err}), 400
    # (c) R1 tunnel_id guard (vpn only)
    if store == "vpn":
        guard_err = _tunnel_id_guard(data)
        if guard_err:
            return jsonify({"ok": False, "error": guard_err,
                            "note": "no write performed"}), 400
    # (d) atomic write of the normalized JSON, then (e) cache-invalidate
    try:
        _atomic_write(_path_for(store), json.dumps(data, indent=2))
        _invalidate(store)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"write failed: {str(e)[:160]}"}), 500
    return jsonify({"ok": True, "store": store, "path": str(_path_for(store)),
                    "note": "saved + reloaded"})


@store_raw_editor_bp.route("/api/settings/store-raw/rekey", methods=["POST"])
def api_store_raw_rekey():
    body = request.get_json(silent=True) or {}
    old_id = body.get("old_id", "")
    new_id = body.get("new_id", "")
    if not isinstance(old_id, str) or not isinstance(new_id, str) \
            or not old_id or not new_id:
        return jsonify({"ok": False, "error": "old_id and new_id required"}), 400
    if old_id == new_id:
        return jsonify({"ok": False, "error": "old_id == new_id"}), 400
    from bulk_downloader import vpn_config
    try:
        moved = vpn_config.rekey_tunnel(old_id, new_id)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)[:160]}), 400
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"rekey failed: {str(e)[:160]}"}), 500
    if not moved:
        return jsonify({"ok": False, "error": f"no tunnel named {old_id!r}"}), 404
    return jsonify({"ok": True, "old_id": old_id, "new_id": new_id,
                    "note": "secrets moved + tunnel renamed atomically"})


def register_routes(app):
    """Register the raw store-editor blueprint. Returns route count added."""
    before = len(list(app.url_map.iter_rules()))
    app.register_blueprint(store_raw_editor_bp)
    return len(list(app.url_map.iter_rules())) - before
