"""Bridge Cut A: diagnostic probes must not poison later full-suite files."""

from __future__ import annotations

from pathlib import Path


BD_GATE_SCOPE = "module"

ROOT = str(Path(__file__).resolve().parent.parent)


def test_typed_body_probe_restores_vpn_kill_switch_state(monkeypatch):
    """A mutating probe may observe the singleton, but may not retain its writes."""
    from bulk_downloader import vpn_kill_switch as ks
    from tools import body_contract as bc

    ks._reset_for_tests()
    ks.set_auto_recover(False)
    ks.kill_tunnel("preexisting", reason="fixture must survive")
    before = ks.list_kill_states()
    fired = {"probe_kills": 0}
    real_kill = ks.kill_tunnel

    def counted_kill(tunnel_id, reason=""):
        if tunnel_id == "_probe":
            fired["probe_kills"] += 1
        return real_kill(tunnel_id, reason=reason)

    monkeypatch.setattr(ks, "kill_tunnel", counted_kill)
    call = {
        "file": "tests/INJECTED.tsx",
        "fn": "apiPost",
        "path": "/api/vpn/kill_switch/${}/trigger",
        "keys": ["reason"],
        "sample": {"reason": "isolation injection"},
        "unknownType": False,
    }

    try:
        result = bc.probe_typed(ROOT, [call])
        assert result, "the injected production probe path did not return a verdict"
        assert fired["probe_kills"] == 2, (
            "both differential requests must reach the real kill endpoint; otherwise "
            "the state-restoration assertion has no mutation denominator"
        )
        assert ks.list_kill_states() == before, (
            "probe_typed leaked its synthetic _probe kill into the process singleton"
        )
        assert ks.get_auto_recover() is False
    finally:
        ks._reset_for_tests()


def test_literal_body_probe_restores_vpn_kill_switch_state(monkeypatch):
    """The older literal-body production probe has the same isolation duty."""
    from bulk_downloader import vpn_kill_switch as ks
    from tools import body_contract as bc

    ks._reset_for_tests()
    ks.set_auto_recover(False)
    ks.kill_tunnel("preexisting", reason="fixture must survive")
    before = ks.list_kill_states()
    fired = {"probe_kills": 0}
    real_kill = ks.kill_tunnel

    def counted_kill(tunnel_id, reason=""):
        if tunnel_id == "_probe":
            fired["probe_kills"] += 1
        return real_kill(tunnel_id, reason=reason)

    monkeypatch.setattr(ks, "kill_tunnel", counted_kill)
    call = {
        "file": "tests/INJECTED.tsx",
        "fn": "apiPost",
        "path": "/api/vpn/kill_switch/${}/trigger",
        "keys": ["reason"],
        "shape": "{reason}",
    }

    try:
        result = bc.probe(ROOT, [call])
        assert result, "the injected production probe path did not return a verdict"
        assert fired["probe_kills"] == 1, (
            "the literal-body probe must reach the real kill endpoint exactly once; "
            "otherwise the state-restoration assertion has no mutation denominator"
        )
        assert ks.list_kill_states() == before, (
            "probe leaked its synthetic _probe kill into the process singleton"
        )
        assert ks.get_auto_recover() is False
    finally:
        ks._reset_for_tests()
