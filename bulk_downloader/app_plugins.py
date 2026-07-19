"""plugins API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/plugins views moved onto a Flask Blueprint.
Endpoint labels gain a "plugins." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

plugins_bp = Blueprint("plugins", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@plugins_bp.route("/api/plugins/status")
def api_plugins_status():
    try:
        from . import plugins as _pl
        return jsonify(_pl.status())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@plugins_bp.route("/api/plugins/reload", methods=["POST"])
def api_plugins_reload():
    """Scan and (re)load all plugins from the plugin dir."""
    _check_csrf()
    try:
        from . import plugins as _pl
        _pl.reset()
        return jsonify(_pl.load_all())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@plugins_bp.route("/api/plugins/events")
def api_plugins_events():
    """Documentation for plugin authors: known hook events + payloads."""
    try:
        from . import plugins as _pl
        return jsonify({"events": _pl.known_events()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@plugins_bp.route("/api/plugins/config", methods=["GET", "POST"])
def api_plugins_config():
    """GET: current plugin-load config (enabled/disabled/order/allow_full_access)
    + discovered plugin files. POST: write plugins.json (CSRF-gated) then reload,
    so the operator controls plugin enable + the full-access gate from the GUI
    (the CLI->GUI parity for BD_PLUGINS_ENABLE / BD_PLUGINS_ALLOW_FULL_ACCESS)."""
    from . import plugins as _pl
    if request.method == "POST":
        _check_csrf()
        try:
            body = request.get_json(force=True, silent=True) or {}
            cfg = _pl.write_config(body)
            _pl.reset()
            _pl.load_all()
            return jsonify({"ok": True, "config": cfg,
                            "full_access_enabled": _pl.full_access_enabled()})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500
    try:
        return jsonify({"ok": True, "config": _pl.read_config(),
                        "discovered": _pl.discovered_plugins(),
                        "schemas": _pl.plugin_config_schemas(),
                        "full_access_enabled": _pl.full_access_enabled()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@plugins_bp.route("/api/plugins/installed")
def api_plugins_installed():
    """GUI-facing managed-install registry + the no-sandbox disclaimer + the
    persisted at-your-own-risk ack state, so the Install panel can render what
    has been installed and gate the upload behind an explicit acknowledgment.
    (Hand-dropped files are intentionally absent from the registry; they surface
    via /api/plugins/config `discovered`.)"""
    try:
        from . import plugins as _pl
        return jsonify({
            "ok": True,
            "installed": _pl.installed_registry(),
            "risk_acknowledged": bool(_pl.read_config().get("risk_acknowledged", False)),
            "disclaimer": _pl.disclaimer(),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


@plugins_bp.route("/api/plugins/install", methods=["POST"])
def api_plugins_install():
    """Install an uploaded plugin file via the managed install path -- the GUI
    equivalent of `tools/plugin_install.py install`. The upload is staged to a
    temp file and handed to plugins.install_plugin(), which: ast-reads the
    PLUGIN manifest (NEVER exec/imports it), gates on the api version-range,
    requires an at-your-own-risk acknowledgment, stages atomically, and records
    the install in the registry. No plugin code runs at any step, and install
    does NOT enable/load -- the operator hits Reload + the load toggle after.

    Multipart form:
      file        (required) the plugin source (.py / .js / .mjs / exec script)
      ack=1       one-shot acknowledgment for THIS install
      persist_ack=1  also write risk_acknowledged into plugins.json (sticky ack)
      force=1     overwrite an existing un-registered file of the same name

    Returns install_plugin()'s dict: 200 on {installed:true}, 400 on any refusal
    (risk-not-acked -> includes `disclaimer`; api-incompatible; exists-unmanaged;
    unreadable/binary upload), so the SPA can surface the reason inline."""
    _check_csrf()
    from . import plugins as _pl
    import os
    import tempfile

    f = request.files.get("file")
    if f is None or not (f.filename or "").strip():
        return jsonify({"installed": False, "reason": "no file uploaded"}), 400

    ack = _truthy(request.form.get("ack"))
    force = _truthy(request.form.get("force"))
    if _truthy(request.form.get("persist_ack")):
        _pl.write_config({"risk_acknowledged": True})
        ack = True

    # basename() strips any path components from the client filename (traversal
    # guard); install_plugin's _resolve_source also keys on Path(...).name, so
    # the on-disk plugin name matches what the operator chose.
    safe = os.path.basename((f.filename or "").strip()) or "plugin.py"
    tmpdir = tempfile.mkdtemp(prefix="bd_plugin_upload_")
    tmp = os.path.join(tmpdir, safe)
    try:
        f.save(tmp)
        res = _pl.install_plugin(tmp, ack=ack, force=force)
    except Exception as e:  # noqa: BLE001
        return jsonify({"installed": False,
                        "reason": f"upload failed: {str(e)[:160]}"}), 400
    finally:
        try:
            os.remove(tmp)
            os.rmdir(tmpdir)
        except OSError:
            pass
    return jsonify(res), (200 if res.get("installed") else 400)


@plugins_bp.route("/api/plugins/uninstall", methods=["POST"])
def api_plugins_uninstall():
    """Remove a managed-installed plugin (destructive; the GUI gates this behind
    a Tier-A confirm). Body: {"file": "<name>.py", "ack": true}. Refuses a
    path-escape, an un-acked call, and a non-registry-managed (hand-dropped)
    file. Returns the value-free {uninstalled, file, reason?}."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import plugins as _pl
        res = _pl.uninstall_plugin(body.get("file") or "",
                                   ack=_truthy(body.get("ack")))
        return jsonify(res), (200 if res.get("uninstalled") else 400)
    except Exception as e:
        return jsonify({"uninstalled": False, "error": str(e)[:200]}), 500


def register_routes(app) -> int:
    app.register_blueprint(plugins_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("plugins."))

