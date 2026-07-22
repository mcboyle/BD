"""L30 must verify that stored VPN configs are registered live."""

import live_tests.checks as checks  # noqa: F401
import live_tests.harness as h


def _l30():
    return next(test for test in h.registry() if test.id == "L30")


class _Context:
    def __init__(self, body):
        self.body = body

    def get(self, path, timeout=15):
        assert path == "/api/dev/vpn_config"
        return True, 200, self.body, 1.0

    def log(self, _message):
        pass


def test_l30_fails_when_configured_tunnel_is_not_registered_live():
    level, detail = _l30().fn(_Context({
        "ok": True,
        "tunnels": [{"tunnel_id": "tun-a", "registered_live": False}],
        "total_tunnels": 1,
        "live_tunnels": 0,
    }))
    assert level == h.FAIL
    assert "not registered live" in detail
