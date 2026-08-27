"""v3.43.60: Flask routes for the VPN feature.

23 endpoints split across:
  - Tunnels:        list / create / update / delete / start / stop / cycle
  - Providers:      list / test creds / list locations
  - Leak tests:     run / history / WebRTC result posting
  - Kill switch:    state / clear / trigger
  - System KS:      plan / apply / commit / revert / availability
  - Settings:       get / update global settings
  - Diagnostics:    backend availability

Wired into the main Flask app with:

    from .app_vpn_api import register_routes
    register_routes(app)

# Auth / CSRF

This module assumes app.py's existing CSRF mechanism is enforced upstream.
POST/PUT/DELETE handlers don't re-validate the CSRF token; that's the
main app's responsibility (Flask request hook).

# Response shape

Every endpoint returns JSON via jsonify(). Success: 200 with the requested
resource. Failure: 4xx/5xx with {"ok": False, "error": "<message>"}.
"""
from __future__ import annotations

import sys

try:
    from flask import Blueprint, request, jsonify
except ImportError:  # pragma: no cover — Flask must be installed in production
    Blueprint = None  # type: ignore
    request = None  # type: ignore
    jsonify = None  # type: ignore


# Single blueprint so we can register everything at once.
vpn_bp = Blueprint("vpn_api", __name__) if Blueprint else None


# ─── Helpers ────────────────────────────────────────────────────────

def _err(msg: str, status: int = 400):
    return jsonify({"ok": False, "error": msg}), status


def _ok(payload=None):
    if payload is None:
        return jsonify({"ok": True})
    if isinstance(payload, dict):
        return jsonify({"ok": True, **payload})
    return jsonify({"ok": True, "data": payload})


# ─── Tunnel routes ──────────────────────────────────────────────────

@vpn_bp.route("/api/vpn/status", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_status():
    """Overall VPN status: tunnels, kill switch state, registered providers.

    `config_load_errors` reports tunnel records that vpn_config quarantined
    at load time. Without it, a tunnels.json that failed to parse is
    indistinguishable over the API from one with no tunnels in it: both show
    an empty `tunnels` list. That ambiguity is why a broken VPN config read as
    "nothing configured" everywhere outside the process, and it is why any
    client that writes tunnels must check this first -- vpn_config.save()
    serialises the in-memory list, so writing while records are quarantined is
    safe only because save() re-emits them.
    """
    from . import (vpn, vpn_config, vpn_kill_switch, vpn_kill_switch_system,
                   vpn_providers)
    tunnels = [t.to_dict() for t in vpn.list_tunnels()]
    kill_states = vpn_kill_switch.list_kill_states()
    sys_ok, sys_why = vpn_kill_switch_system.available()
    return jsonify({
        "ok": True,
        "tunnels": tunnels,
        "kill_states": kill_states,
        "config_load_errors": vpn_config.load_errors(),
        "providers": vpn_providers.list_providers(),
        "system_killswitch_available": sys_ok,
        "system_killswitch_reason": sys_why,
        "system_killswitch_active": vpn_kill_switch_system.list_active(),
    })


@vpn_bp.route("/api/vpn/tunnels", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_tunnels_list():
    from . import vpn
    return _ok({"tunnels": [t.to_dict() for t in vpn.list_tunnels()]})


@vpn_bp.route("/api/vpn/tunnels", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_tunnels_create():
    from . import vpn, vpn_config
    data = request.get_json(silent=True) or {}
    required = ("name", "provider", "backend")
    missing = [k for k in required if not data.get(k)]
    if missing:
        return _err(f"missing fields: {missing}")
    try:
        tid = vpn.register_tunnel(
            name=data["name"], provider=data["provider"], backend=data["backend"],
            location=data.get("location"), config=data.get("config", {}),
            tunnel_id=data.get("tunnel_id"),
        )
    except ValueError as e:
        return _err(str(e))
    # Persist to disk via vpn_config.
    # Audit 2026-05 (Phase 5): roll back the in-memory registration if
    # disk persist fails. Previously, register succeeded + persist failed
    # left the tunnel only in memory; the API reported success but the
    # tunnel vanished on next restart. Now we unregister to keep the
    # two state stores consistent.
    try:
        vpn_config.add_tunnel_config({
            "tunnel_id": tid,
            "name": data["name"],
            "provider": data["provider"],
            "backend": data["backend"],
            "location": data.get("location", ""),
            "enabled": data.get("enabled", True),
            "config": vpn_config.store_secrets(tid, data.get("config", {})),
            "extra": data.get("extra", {}),
        })
    except Exception as e:
        sys.stderr.write(f"[vpn-api] persist failed; rolling back register: {e}\n")
        try:
            vpn.unregister_tunnel(tid)
        except Exception as e2:
            sys.stderr.write(f"[vpn-api] rollback unregister failed: {e2}\n")
        return _err(f"persist failed: {e}", 500)
    return _ok({"tunnel_id": tid, "tunnel": vpn.get_tunnel(tid).to_dict()})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_tunnel_get(tunnel_id):
    from . import vpn
    t = vpn.get_tunnel(tunnel_id)
    if t is None:
        return _err("no such tunnel", 404)
    return _ok({"tunnel": t.to_dict()})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>", methods=["PUT", "PATCH"]) if vpn_bp else (lambda f: f)
def vpn_tunnel_update(tunnel_id):
    from . import vpn, vpn_config
    t = vpn.get_tunnel(tunnel_id)
    if t is None:
        return _err("no such tunnel", 404)
    data = request.get_json(silent=True) or {}
    for f in ("name", "location"):
        if f in data:
            setattr(t, f, data[f])
    if "config" in data and isinstance(data["config"], dict):
        # Merge into existing config (so PATCH-style partial updates work).
        t.config = {**(t.config or {}), **data["config"]}
    try:
        vpn_config.update_tunnel_config(tunnel_id,
            name=t.name, location=t.location,
            config=vpn_config.store_secrets(tunnel_id, t.config),
        )
    except Exception as e:
        sys.stderr.write(f"[vpn-api] update persist failed: {e}\n")
    return _ok({"tunnel": t.to_dict()})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>", methods=["DELETE"]) if vpn_bp else (lambda f: f)
def vpn_tunnel_delete(tunnel_id):
    from . import vpn, vpn_config
    if vpn.get_tunnel(tunnel_id) is None:
        return _err("no such tunnel", 404)
    try:
        vpn.stop_tunnel(tunnel_id)
    except Exception:
        pass
    vpn.unregister_tunnel(tunnel_id) if hasattr(vpn, "unregister_tunnel") else None
    vpn_config.remove_tunnel_config(tunnel_id)
    return _ok({"removed": tunnel_id})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>/start", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_tunnel_start(tunnel_id):
    from . import vpn
    try:
        ok = vpn.start_tunnel(tunnel_id)
    except Exception as e:
        return _err(str(e), 500)
    t = vpn.get_tunnel(tunnel_id)
    return _ok({"started": ok, "tunnel": t.to_dict() if t else None})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>/stop", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_tunnel_stop(tunnel_id):
    from . import vpn
    ok = vpn.stop_tunnel(tunnel_id)
    return _ok({"stopped": ok})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>/cycle", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_tunnel_cycle(tunnel_id):
    from . import vpn
    ok = vpn.cycle_tunnel(tunnel_id)
    return _ok({"cycled": ok, "tunnel": vpn.get_tunnel(tunnel_id).to_dict() if vpn.get_tunnel(tunnel_id) else None})


# ─── Provider routes ────────────────────────────────────────────────

@vpn_bp.route("/api/vpn/providers", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_providers_list():
    from . import vpn_providers
    return _ok({"providers": vpn_providers.list_providers()})


@vpn_bp.route("/api/vpn/providers/<provider_id>/test_credentials", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_provider_test_creds(provider_id):
    from . import vpn_providers
    mod = vpn_providers.get_provider(provider_id)
    if mod is None:
        return _err(f"unknown provider: {provider_id}", 404)
    data = request.get_json(silent=True) or {}
    try:
        ok, msg = mod.test_credentials(data)
    except Exception as e:
        return _err(f"provider raised: {e}", 500)
    return jsonify({"ok": True, "valid": ok, "message": msg})


@vpn_bp.route("/api/vpn/providers/<provider_id>/locations", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_provider_locations(provider_id):
    """POST not GET — credentials are required to list locations on some providers."""
    from . import vpn_providers
    mod = vpn_providers.get_provider(provider_id)
    if mod is None:
        return _err(f"unknown provider: {provider_id}", 404)
    data = request.get_json(silent=True) or {}
    try:
        locs = mod.list_locations(data)
    except Exception as e:
        return _err(f"provider raised: {e}", 500)
    return _ok({"locations": locs})


# ─── Leak test routes ───────────────────────────────────────────────

@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>/leak_test/run", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_leak_test_run(tunnel_id):
    from . import vpn_leak_tests, vpn
    if vpn.get_tunnel(tunnel_id) is None:
        return _err("no such tunnel", 404)
    data = request.get_json(silent=True) or {}
    expected_country = data.get("expected_country")
    expected_exit_ip = data.get("expected_exit_ip")
    try:
        agg = vpn_leak_tests.run_all_probes(
            tunnel_id,
            expected_country=expected_country,
            expected_exit_ip=expected_exit_ip,
        )
    except Exception as e:
        return _err(f"probe run raised: {e}", 500)
    return _ok({"result": agg.to_dict()})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>/leak_test/history", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_leak_test_history(tunnel_id):
    from . import vpn_leak_tests
    limit = int(request.args.get("limit", "20"))
    return _ok({"history": vpn_leak_tests.get_history(tunnel_id, limit=limit)})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>/leak_test/latest", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_leak_test_latest(tunnel_id):
    from . import vpn_leak_tests
    return _ok({"result": vpn_leak_tests.get_latest(tunnel_id)})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>/webrtc_result", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_webrtc_post(tunnel_id):
    """Workers POST WebRTC probe results captured in browser contexts here."""
    from . import vpn_leak_tests
    data = request.get_json(silent=True) or {}
    vpn_leak_tests.record_external_probe_result(tunnel_id, "webrtc_leak", data)
    return _ok()


@vpn_bp.route("/api/vpn/webrtc_js", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_webrtc_js():
    """Return the JS snippet that workers inject into their Playwright contexts.
    Workers eval the snippet and POST the result to /api/vpn/tunnels/<id>/webrtc_result."""
    from . import vpn_leak_tests
    return vpn_leak_tests.WEBRTC_PROBE_JS, 200, {"Content-Type": "application/javascript"}


# ─── Kill switch routes ─────────────────────────────────────────────

@vpn_bp.route("/api/vpn/kill_switch/state", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_kill_state():
    from . import vpn_kill_switch
    return _ok({
        "states": vpn_kill_switch.list_kill_states(),
        "auto_recover": vpn_kill_switch.get_auto_recover(),
    })


@vpn_bp.route("/api/vpn/kill_switch/<tunnel_id>/clear", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_kill_clear(tunnel_id):
    from . import vpn_kill_switch
    vpn_kill_switch.clear_kill(tunnel_id)
    return _ok()


@vpn_bp.route("/api/vpn/kill_switch/<tunnel_id>/trigger", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_kill_trigger(tunnel_id):
    from . import vpn_kill_switch
    data = request.get_json(silent=True) or {}
    vpn_kill_switch.kill_tunnel(tunnel_id, reason=data.get("reason", "manual trigger via API"))
    return _ok()


@vpn_bp.route("/api/vpn/kill_switch/auto_recover", methods=["PUT"]) if vpn_bp else (lambda f: f)
def vpn_kill_auto_recover():
    from . import vpn_kill_switch
    data = request.get_json(silent=True) or {}
    if "enabled" not in data:
        return _err("missing 'enabled' boolean")
    vpn_kill_switch.set_auto_recover(bool(data["enabled"]))
    return _ok({"auto_recover": vpn_kill_switch.get_auto_recover()})


# ─── System kill switch routes ──────────────────────────────────────

@vpn_bp.route("/api/vpn/system_killswitch/available", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_sysks_available():
    from . import vpn_kill_switch_system
    ok, why = vpn_kill_switch_system.available()
    return _ok({"available": ok, "reason": why})


@vpn_bp.route("/api/vpn/system_killswitch/<tunnel_id>/plan", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_sysks_plan(tunnel_id):
    from . import vpn_kill_switch_system
    data = request.get_json(silent=True) or {}
    endpoint = data.get("vpn_endpoint", "")
    if not endpoint:
        return _err("vpn_endpoint required")
    allow = data.get("allow_ports")  # dict | None
    cmds = vpn_kill_switch_system.plan(tunnel_id, endpoint, allow_ports=allow)
    return _ok({"commands": cmds, "count": len(cmds)})


@vpn_bp.route("/api/vpn/system_killswitch/<tunnel_id>/apply", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_sysks_apply(tunnel_id):
    from . import vpn_kill_switch_system
    data = request.get_json(silent=True) or {}
    endpoint = data.get("vpn_endpoint", "")
    if not endpoint:
        return _err("vpn_endpoint required")
    dry_run = bool(data.get("dry_run", True))
    allow = data.get("allow_ports")
    result = vpn_kill_switch_system.apply(tunnel_id, endpoint, dry_run=dry_run, allow_ports=allow)
    return jsonify({**result, "ok": result.get("ok", False)})


@vpn_bp.route("/api/vpn/system_killswitch/<tunnel_id>/commit", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_sysks_commit(tunnel_id):
    from . import vpn_kill_switch_system
    ok = vpn_kill_switch_system.commit(tunnel_id)
    return _ok({"committed": ok})


@vpn_bp.route("/api/vpn/system_killswitch/<tunnel_id>/revert", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_sysks_revert(tunnel_id):
    from . import vpn_kill_switch_system
    result = vpn_kill_switch_system.revert(tunnel_id)
    return jsonify({**result, "ok": result.get("ok", True)})


# ─── Settings routes ────────────────────────────────────────────────

@vpn_bp.route("/api/vpn/settings", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_settings_get():
    from . import vpn_config
    return _ok({"settings": vpn_config.get_global_settings()})


@vpn_bp.route("/api/vpn/settings", methods=["PUT"]) if vpn_bp else (lambda f: f)
def vpn_settings_update():
    from . import vpn_config
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return _err("body must be JSON object")
    out = vpn_config.update_global_settings(**data)
    return _ok({"settings": out})


# ─── Backend availability ───────────────────────────────────────────

@vpn_bp.route("/api/vpn/backends/availability", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_backends_availability():
    from . import vpn_wireguard, vpn_openvpn
    wg_ok, wg_why = vpn_wireguard.is_available()
    ov_ok, ov_why = vpn_openvpn.is_available()
    return _ok({
        "wireguard": {"available": wg_ok, "reason": wg_why},
        "openvpn": {"available": ov_ok, "reason": ov_why},
    })


# ─── Registration helper ────────────────────────────────────────────

def register_routes(app) -> int:
    """Call from app.py: register_routes(app). Returns route count."""
    if vpn_bp is None:
        sys.stderr.write("[vpn-api] Flask not installed; routes not registered\n")
        return 0
    try:
        app.register_blueprint(vpn_bp)
    except (ValueError, AssertionError) as e:
        # Already registered (hot-reload) — ignore.
        sys.stderr.write(f"[vpn-api] blueprint already registered: {e}\n")
        return 0
    # Count routes that match our prefix.
    n = sum(1 for r in app.url_map.iter_rules() if r.rule.startswith("/api/vpn/"))
    sys.stderr.write(f"[vpn-api] registered {n} VPN routes\n")
    return n


__all__ = ["register_routes", "vpn_bp"]
