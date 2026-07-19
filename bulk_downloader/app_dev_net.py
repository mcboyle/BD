"""app_dev.net -- 7 @dev_bp route handlers, sub-sliced from app_dev.py (Tier M, pure motion).

Handlers attach to the SHARED dev_bp (imported from .app_dev); the routing surface
(rule, methods, bare-name) is byte-identical -- test_route_map_invariant diffs EMPTY.
"""
from __future__ import annotations
from flask import Blueprint, jsonify, request
from .app_dev import (
    _app_s_cfg,
    _dev_mode_guard,
    dev_bp,
)


@dev_bp.route("/api/dev/vpn_config")
def api_dev_vpn_config():
    """T13/D-41 — render every configured VPN tunnel with secrets
    redacted, paired with the live runtime state (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.vpn_config_render())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/vpn_rotation")
def api_dev_vpn_rotation():
    """T13/D-43 — group configured tunnels by provider and report
    state, health, and which tunnel per provider would be the next
    cycle target (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.vpn_provider_rotation_view())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/vpn_probe")
def api_dev_vpn_probe():
    """T14/D-42 — call vpn.check_health() per registered tunnel and
    report per-tunnel connectivity + SOCKS URL (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.vpn_connectivity_probe())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/egress_ip")
def api_dev_egress_ip():
    """T14/D-49 — walk registered tunnels, report each tunnel's last
    known public IP, freshness of last health check, and shared-IP
    findings (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.egress_ip_monitor(
            stale_after_sec=request.args.get("stale_after_sec", 300)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/flaresolverr_health")
def api_dev_flaresolverr_health():
    """T15/D-45 — FlareSolverr endpoint config, live ping
    (fail-open), and cumulative solver stats (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.flaresolverr_health())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/window_simulate", methods=["POST"])
def api_dev_window_simulate():
    """T41/D-16 — simulate site_in_window() against a spec + sample
    timestamps. POST for body. Read-only, stateless."""
    guard = _dev_mode_guard()
    if guard: return guard
    body = request.json or {}
    spec = body.get("window_spec", "")
    samples = body.get("samples")
    window_enabled = body.get("window_enabled", True)
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.window_simulate(
            window_spec=spec, samples=samples,
            window_enabled=bool(window_enabled)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/tls_check", methods=["POST"])
def api_dev_tls_check():
    """T43/D-47 — TLS cert inspection. POST + opt-in: caller must
    pass hosts=[...] explicitly, or omit and accept derivation from
    configured site_configs (HTTPS URLs only). Each host gets a
    bounded socket timeout. Outbound network calls — NOT auto-run."""
    s_cfg = _app_s_cfg()
    guard = _dev_mode_guard()
    if guard: return guard
    body = request.json or {}
    hosts = body.get("hosts")
    use_sites = bool(body.get("derive_from_sites", False))
    try:
        timeout = float(body.get("timeout", 5.0))
    except (TypeError, ValueError):
        timeout = 5.0
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.tls_cert_check(
            hosts=hosts,
            site_configs=(s_cfg if use_sites else None),
            timeout=timeout))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
