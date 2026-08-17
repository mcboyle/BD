"""Bridge Cut A: diagnostic probes must not poison later full-suite files."""

from __future__ import annotations

import threading
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

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


def test_fixture_body_probe_restores_vpn_kill_switch_state(monkeypatch):
    """The fixture-backed production replay must not leak its tunnel kill."""
    from bulk_downloader import vpn_kill_switch as ks
    from tools import body_contract as bc

    ks._reset_for_tests()
    ks.set_auto_recover(False)
    ks.kill_tunnel("preexisting", reason="fixture must survive")
    before = ks.list_kill_states()
    fired = {"fixture_kills": 0}
    real_kill = ks.kill_tunnel

    def counted_kill(tunnel_id, reason=""):
        if tunnel_id != "preexisting":
            fired["fixture_kills"] += 1
        return real_kill(tunnel_id, reason=reason)

    monkeypatch.setattr(ks, "kill_tunnel", counted_kill)
    call = {
        "file": "tests/INJECTED.tsx",
        "fn": "apiPost",
        "path": "/api/vpn/kill_switch/${}/trigger",
        "keys": ["reason"],
        "sample": {"reason": "fixture isolation injection"},
        "unknownType": False,
    }

    try:
        result = bc.probe_fixtures(ROOT, [call])
        assert result, "the fixture-backed production probe returned no verdict"
        assert fired["fixture_kills"] == 2, (
            "both fixture differential requests must reach the real kill endpoint; "
            "otherwise the restoration assertion has no mutation denominator"
        )
        assert ks.list_kill_states() == before
        assert ks.get_auto_recover() is False
    finally:
        ks._reset_for_tests()


def test_probe_serializes_restore_against_concurrent_real_state(monkeypatch):
    """A legitimate concurrent kill must occur after restore, never be erased by it."""
    from bulk_downloader import vpn_kill_switch as ks
    from tools import body_contract as bc

    ks._reset_for_tests()
    ks.set_auto_recover(False)
    holder = {}
    attempted = threading.Event()
    completed = threading.Event()

    def concurrent_kill():
        attempted.set()
        ks.kill_tunnel("concurrent", reason="must survive probe restoration")
        completed.set()

    def inner(_work, _calls):
        thread = threading.Thread(target=concurrent_kill, daemon=True)
        holder["thread"] = thread
        thread.start()
        assert attempted.wait(1), "the concurrent mutation seam never fired"
        assert not completed.wait(0.1), (
            "concurrent state mutation was not serialized behind probe restoration"
        )
        return []

    monkeypatch.setattr(bc, "_probe_inner", inner)
    try:
        bc.probe(ROOT, [])
        holder["thread"].join(2)
        assert completed.is_set(), "the serialized legitimate mutation never resumed"
        assert [row["tunnel_id"] for row in ks.list_kill_states()] == ["concurrent"]
    finally:
        ks._reset_for_tests()


def test_probe_serializes_restore_against_concurrent_auto_recover_change(monkeypatch):
    """A legitimate concurrent policy change must survive probe restoration."""
    from bulk_downloader import vpn_kill_switch as ks
    from tools import body_contract as bc

    ks._reset_for_tests()
    ks.set_auto_recover(False)
    holder = {}
    attempted = threading.Event()
    completed = threading.Event()

    def concurrent_change():
        attempted.set()
        ks.set_auto_recover(True)
        completed.set()

    def inner(_work, _calls):
        thread = threading.Thread(target=concurrent_change, daemon=True)
        holder["thread"] = thread
        thread.start()
        assert attempted.wait(1), "the concurrent auto-recover seam never fired"
        assert not completed.wait(0.1), (
            "concurrent auto-recover mutation was not serialized behind restoration"
        )
        return []

    monkeypatch.setattr(bc, "_probe_inner", inner)
    try:
        bc.probe(ROOT, [])
        holder["thread"].join(2)
        assert completed.is_set(), "the serialized auto-recover mutation never resumed"
        assert ks.get_auto_recover() is True
    finally:
        ks._reset_for_tests()
