"""End-to-end: the seeded tunnel must turn L30's WARN into a real PASS.

The FakeClient tests in tests/test_live_seed.py pin what the seeder SENDS.
This file pins what BD DOES with it, against the real vpn / vpn_config /
app_vpn_api / dev_suite modules and a real Flask test client -- because a
seeder that validates only against a fake it also wrote is the vacuous-pass
failure one level up. A relative cookie_file path once shipped for months and
400'd on every real run while the FakeClient suite stayed green.

What is proven here:

  * seeding through the HTTP API leaves the stored config and the live
    registry in agreement, so L30 has a real 1:1 pair to verify;
  * L30 returns PASS for the right reason -- and still FAILs when the two
    sides are made to diverge, so the PASS is not structural;
  * the tunnel is INERT: state stays "down" and no SOCKS port is allocated;
  * L29's verdict is unchanged by the tunnel's existence;
  * teardown removes it from BOTH stores and leaves the operator's tunnel;
  * seeding refuses, and changes nothing, when the stored config is broken.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

import live_tests.checks as checks  # noqa: F401  (registers the checks)
import live_tests.harness as h


def _check(test_id):
    return next(t for t in h.registry() if t.id == test_id)


class _AppContext:
    """A live-test Context backed by a real Flask test client."""

    def __init__(self, client):
        self._client = client
        self.logs = []

    def get(self, path, timeout=15):
        resp = self._client.get(path)
        try:
            body = resp.get_json()
        except Exception:
            body = None
        return resp.status_code == 200, resp.status_code, body, 0.01

    def log(self, message):
        self.logs.append(message)


class TestSeededTunnelEndToEnd:

    def setup_method(self):
        self._saved_env = {k: os.environ.get(k)
                           for k in ("BD_VPN_CONFIG_PATH", "BD_DEV_MODE",
                                     "BD_DISABLE_KEEPALIVE")}
        self._tmp = Path(tempfile.mkdtemp(prefix="l30-seed-"))
        os.environ["BD_VPN_CONFIG_PATH"] = str(self._tmp / "tunnels.json")
        os.environ["BD_DEV_MODE"] = "1"
        os.environ["BD_DISABLE_KEEPALIVE"] = "1"
        from bulk_downloader import vpn, vpn_config
        vpn._reset_for_tests()
        vpn_config._reset_for_tests()
        vpn_config.load()

    def teardown_method(self):
        from bulk_downloader import vpn, vpn_config
        vpn._reset_for_tests()
        vpn_config._reset_for_tests()
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── plumbing ────────────────────────────────────────────────────────────

    def _app(self):
        import flask
        from bulk_downloader import app_vpn_api
        app = flask.Flask(__name__)
        app_vpn_api.register_routes(app)

        # The dev blueprint carries app-wide imports; mount just the one route
        # this check reads, calling the same dev_suite renderer it does.
        @app.route("/api/dev/vpn_config")
        def _dev_vpn_config():
            from bulk_downloader import dev_suite as ds
            return flask.jsonify(ds.vpn_config_render())

        # teardown() also sweeps the queue and marked sites. Those halves are
        # pinned by the FakeClient tests in tests/test_live_seed.py; here they
        # are stubbed EMPTY so the tunnel half is what this file measures.
        # They must still answer in the real shape -- _queue_snapshot refuses
        # an unreadable queue, which is correct behaviour and would otherwise
        # mask what these tests are about.
        @app.route("/api/queue/v2")
        def _queue():
            return flask.jsonify({"ok": True, "running": [], "waiting": [],
                                  "done_today_count": 0})

        @app.route("/api/status")
        def _status():
            return flask.jsonify({})

        return app

    def _seed_module(self):
        import importlib.machinery
        import importlib.util
        path = Path(__file__).resolve().parent.parent / "tools" / "live_seed.py"
        loader = importlib.machinery.SourceFileLoader("bd_live_seed_e2e", str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod

    class _HttpClient:
        """The seeder's Client interface, backed by a Flask test client."""

        def __init__(self, flask_client):
            self._c = flask_client

        def get(self, path):
            return self._c.get(path).get_json()

        def post(self, path, payload):
            return self._c.post(path, json=payload).get_json()

        def delete(self, path):
            return self._c.delete(path).get_json()

    # ── the tests ───────────────────────────────────────────────────────────

    def test_l30_warns_before_seeding_and_passes_after(self):
        seed = self._seed_module()
        app = self._app()
        ctx = _AppContext(app.test_client())

        level, detail = _check("L30").fn(ctx)
        assert level == h.WARN, (level, detail)
        assert "no VPN tunnels configured" in detail

        seed.seed_vpn_tunnel(self._HttpClient(app.test_client()))

        level, detail = _check("L30").fn(ctx)
        assert level == h.PASS, (level, detail)
        assert "1 VPN tunnel(s) configured" in detail, detail

    def test_the_pass_is_earned_not_structural(self):
        """Break the config/state agreement and L30 must FAIL.

        Without this, a PASS could mean "L30 cannot see a divergence" rather
        than "there is none" -- the gate-that-cannot-see-its-subject failure.
        """
        from bulk_downloader import vpn
        seed = self._seed_module()
        app = self._app()
        ctx = _AppContext(app.test_client())
        seed.seed_vpn_tunnel(self._HttpClient(app.test_client()))
        assert _check("L30").fn(ctx)[0] == h.PASS

        # Drop the tunnel from the LIVE registry only, leaving the stored
        # config in place: exactly the partial-restart divergence L30 exists
        # to catch.
        for t in list(vpn.list_tunnels()):
            vpn.unregister_tunnel(t.tunnel_id)

        level, detail = _check("L30").fn(ctx)
        assert level == h.FAIL, (level, detail)
        assert "not registered live" in detail

    def test_the_seeded_tunnel_survives_a_service_restart(self):
        """L30 must still PASS after the service comes back up.

        Found by mutation testing: flipping the seeded tunnel's `enabled` to
        False changed nothing in any other test, because within one process
        POST /api/vpn/tunnels registers the tunnel live regardless of the
        flag. The divergence only appears on the NEXT boot, when
        vpn_config.register_loaded_tunnels() skips disabled tunnels -- leaving
        the tunnel in the stored config but absent from the live registry,
        which is exactly the registered_live=False state L30 FAILs on.

        That matters operationally: the capture restarts the service (L28
        checks queue survival across precisely that), so a seeded tunnel that
        only agrees with itself before the first restart would turn L30 red
        halfway through a run.
        """
        from bulk_downloader import vpn, vpn_config
        seed = self._seed_module()
        app = self._app()
        seed.seed_vpn_tunnel(self._HttpClient(app.test_client()))
        assert _check("L30").fn(_AppContext(app.test_client()))[0] == h.PASS

        # Restart: drop all in-process state, re-read from disk, re-register.
        vpn._reset_for_tests()
        vpn_config._reset_for_tests()
        vpn_config.load()
        count, errors = vpn_config.register_loaded_tunnels()
        assert errors == [], errors
        assert count == 1, (
            f"the seeded tunnel did not re-register after restart "
            f"(count={count}); it is stored but not live, which is the "
            f"divergence L30 FAILs on"
        )

        level, detail = _check("L30").fn(_AppContext(app.test_client()))
        assert level == h.PASS, (level, detail)

    def test_the_seeded_tunnel_is_inert(self):
        """It must hold no port and never leave the 'down' state."""
        from bulk_downloader import vpn
        seed = self._seed_module()
        app = self._app()
        seed.seed_vpn_tunnel(self._HttpClient(app.test_client()))

        tunnels = vpn.list_tunnels()
        assert len(tunnels) == 1
        t = tunnels[0]
        assert t.state == "down", f"seeded tunnel is in state {t.state!r}"
        assert t.socks_port == 0, f"seeded tunnel holds SOCKS port {t.socks_port}"
        assert vpn._allocated_ports_snapshot() == set(), (
            "seeding allocated a SOCKS port; the tunnel is not inert"
        )

    def test_the_seeded_tunnel_is_not_routable_by_any_site(self):
        """No site maps to it, so no download's traffic can reach it."""
        from bulk_downloader import vpn_runtime
        seed = self._seed_module()
        app = self._app()
        seed.seed_vpn_tunnel(self._HttpClient(app.test_client()))

        vpn_runtime._reset_for_tests()
        vpn_runtime.init({"sites": [{"site_id": "real-site"}]},
                         start_monitors=False)
        assert vpn_runtime.get_tunnel_for_site("real-site") is None, (
            "a site resolved to the synthetic tunnel; seeding can divert "
            "real traffic"
        )
        assert vpn_runtime.get_socks_url_for_site("real-site") is None

    def test_l29_verdict_is_unchanged_by_the_seeded_tunnel(self):
        """L29 reads kill-switch state, which registration must not populate."""
        seed = self._seed_module()
        app = self._app()
        ctx = _AppContext(app.test_client())

        before_level, _ = _check("L29").fn(ctx)
        seed.seed_vpn_tunnel(self._HttpClient(app.test_client()))
        after_level, after_detail = _check("L29").fn(ctx)

        assert before_level == after_level == h.PASS, (
            f"L29 moved from {before_level} to {after_level}: {after_detail}"
        )
        assert "0 active kills" in after_detail

    def test_teardown_removes_the_tunnel_from_both_stores(self):
        from bulk_downloader import vpn, vpn_config
        seed = self._seed_module()
        app = self._app()
        client = self._HttpClient(app.test_client())
        seed.seed_vpn_tunnel(client)
        assert len(vpn_config.list_tunnel_configs()) == 1
        assert len(vpn.list_tunnels()) == 1

        plan = seed.teardown(client)

        assert vpn.list_tunnels() == [], "tunnel survived in the live registry"
        assert vpn_config.list_tunnel_configs() == [], (
            "tunnel survived in the stored config -- teardown did not reach it"
        )
        on_disk = json.loads(
            Path(os.environ["BD_VPN_CONFIG_PATH"]).read_text(encoding="utf-8"))
        assert on_disk["tunnels"] == [], on_disk
        assert plan["removed_tunnels"], plan

    def test_teardown_leaves_the_operators_tunnel_alone(self):
        from bulk_downloader import vpn, vpn_config
        seed = self._seed_module()
        app = self._app()
        client = self._HttpClient(app.test_client())
        # The operator's own tunnel, created the same way the GUI creates one.
        client.post("/api/vpn/tunnels", {"name": "Mullvad NYC",
                                         "provider": "mullvad",
                                         "backend": "wireguard"})
        seed.seed_vpn_tunnel(client)
        assert len(vpn.list_tunnels()) == 2

        seed.teardown(client)

        surviving = [t.name for t in vpn.list_tunnels()]
        assert surviving == ["Mullvad NYC"], surviving
        assert [t["name"] for t in vpn_config.list_tunnel_configs()] == ["Mullvad NYC"]

    def test_seeding_refuses_and_changes_nothing_on_a_broken_config(self):
        """The data-destruction guard, end to end.

        Before the quarantine fix this exact sequence rewrote tunnels.json
        with only the synthetic tunnel and deleted both operator records.
        """
        from bulk_downloader import vpn_config
        path = Path(os.environ["BD_VPN_CONFIG_PATH"])
        path.write_text(json.dumps({
            "schema_version": 1, "global_settings": {},
            "tunnels": [
                {"tunnel_id": "tun-real", "name": "Mullvad NYC",
                 "provider": "mullvad", "backend": "wireguard", "config": {}},
                {"tunnel_id": "tun-broken", "provider": "mullvad",
                 "backend": "wireguard", "config": {}},
            ],
        }, indent=2), encoding="utf-8")
        vpn_config._reset_for_tests()
        vpn_config.load()
        before = path.read_bytes()

        seed = self._seed_module()
        app = self._app()
        with pytest.raises(Exception) as excinfo:
            seed.seed_vpn_tunnel(self._HttpClient(app.test_client()))
        assert "tun-broken" in str(excinfo.value), str(excinfo.value)
        assert path.read_bytes() == before, (
            "the refused seed still modified tunnels.json"
        )

    def test_a_valid_tunnel_survives_a_broken_sibling_end_to_end(self):
        """The operator's good tunnel must still register and be visible."""
        from bulk_downloader import vpn_config
        path = Path(os.environ["BD_VPN_CONFIG_PATH"])
        path.write_text(json.dumps({
            "schema_version": 1, "global_settings": {},
            "tunnels": [
                {"tunnel_id": "tun-real", "name": "Mullvad NYC",
                 "provider": "mullvad", "backend": "wireguard", "config": {}},
                {"tunnel_id": "tun-broken", "provider": "mullvad",
                 "backend": "wireguard", "config": {}},
            ],
        }, indent=2), encoding="utf-8")
        vpn_config._reset_for_tests()
        vpn_config.load()
        vpn_config.register_loaded_tunnels()

        app = self._app()
        body = app.test_client().get("/api/vpn/status").get_json()
        assert [t["tunnel_id"] for t in body["tunnels"]] == ["tun-real"]
        assert [e["tunnel_id"] for e in body["config_load_errors"]] == ["tun-broken"]
