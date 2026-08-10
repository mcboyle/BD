"""v3.43.60: VPN UI integration tests.

Backend-only — verifies the contracts that vpn_ui.js expects from the server.

NOTE: This file uses class helper methods instead of pytest.fixture-decorated
methods, for portability across the project's three test runners (real
pytest, run_min_pytest, run_tests.py).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ════════════════════════════════════════════════════════════════════
#  HTML fragments
# ════════════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════════════
#  app.js integration
# ════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════
#  vpn_ui.js sanity
# ════════════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════════════
#  vpn_ui.css sanity
# ════════════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════════════
#  Endpoint contract — vpn_ui.js calls these, app_vpn_api must serve them
# ════════════════════════════════════════════════════════════════════

class TestEndpointContract:
    def test_all_referenced_endpoints_exist_in_blueprint(self):
        try:
            import flask
        except ImportError:
            pytest.skip("flask not installed")
        from bulk_downloader import app_vpn_api
        app = flask.Flask("test")
        app_vpn_api.register_routes(app)
        registered = {r.rule for r in app.url_map.iter_rules()}

        needed = [
            "/api/vpn/status",
            "/api/vpn/tunnels",
            "/api/vpn/providers",
            "/api/vpn/settings",
            "/api/vpn/tunnels/<tunnel_id>",
            "/api/vpn/tunnels/<tunnel_id>/start",
            "/api/vpn/tunnels/<tunnel_id>/stop",
            "/api/vpn/tunnels/<tunnel_id>/cycle",
            "/api/vpn/tunnels/<tunnel_id>/leak_test/run",
            "/api/vpn/tunnels/<tunnel_id>/leak_test/history",
            "/api/vpn/kill_switch/<tunnel_id>/clear",
            "/api/vpn/providers/<provider_id>/test_credentials",
            "/api/vpn/providers/<provider_id>/locations",
        ]
        for ep in needed:
            assert ep in registered, f"endpoint {ep!r} not registered (vpn_ui.js calls it)"


# ════════════════════════════════════════════════════════════════════
#  Site config schema — vpn field round-trip
# ════════════════════════════════════════════════════════════════════

class TestSiteConfigVpnField:
    def test_vpn_runtime_reads_site_vpn_field(self, monkeypatch):
        """sites_config.json site entries can have a `vpn` field; vpn_runtime.init
        must parse it correctly into _site_to_tunnel and _site_required.

        The two env writes go through `monkeypatch` so they are RESTORED. The
        raw `os.environ[...] = ""` this replaced was measured escaping the
        session: `BD_DISABLE_VPN_RUNTIME` was still set after the file finished,
        so whichever file xdist scheduled next on the worker inherited it.
        `tests/conftest.py`'s autouse guard could not catch it -- its denominator
        is five named keys and this is not one of them. `monkeypatch.setenv` is
        shimmed by the fallback runner (`run_tests_core.py:445`), so the
        module docstring's portability note still holds.
        """
        import os
        if "BD_DISABLE_KEEPALIVE" not in os.environ:
            monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
        monkeypatch.setenv("BD_DISABLE_VPN_RUNTIME", "")
        from bulk_downloader import vpn_runtime
        vpn_runtime._reset_for_tests()
        import importlib
        importlib.reload(vpn_runtime)

        result = vpn_runtime.init({
            "sites": [
                {"site_id": "wowgirls", "vpn": {"tunnel_id": "tun-x", "required": True}},
                {"site_id": "filthykings", "vpn": {"tunnel_id": "tun-y"}},
                {"site_id": "ultrafilms"},
            ],
            "global_vpn": {"tunnel_id": "tun-global"},
        }, start_monitors=False)

        assert result["ok"]
        assert result["site_to_tunnel"] == {"wowgirls": "tun-x", "filthykings": "tun-y"}
        assert result["global_tunnel_id"] == "tun-global"
        assert vpn_runtime.is_vpn_required_for_site("wowgirls") is True
        assert vpn_runtime.is_vpn_required_for_site("filthykings") is False
        assert vpn_runtime.is_vpn_required_for_site("ultrafilms") is False
        assert vpn_runtime.get_tunnel_for_site("ultrafilms") == "tun-global"

        vpn_runtime._reset_for_tests()
