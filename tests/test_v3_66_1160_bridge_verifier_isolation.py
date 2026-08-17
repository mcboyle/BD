"""Bridge Cut A: diagnostic probes must not poison later full-suite files."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

ROOT = str(Path(__file__).resolve().parent.parent)


@pytest.fixture(autouse=True)
def _isolate_kill_switch_without_erasing_callbacks():
    """Give each case clean state, then restore every singleton it observed."""
    from bulk_downloader import vpn_kill_switch as ks

    with ks._state_lock:
        states = copy.deepcopy(ks._states)
        auto_recover = ks._auto_recover_enabled
        ks._states.clear()
        ks._auto_recover_enabled = True
    try:
        yield
    finally:
        with ks._state_lock:
            ks._states.clear()
            ks._states.update(states)
            ks._auto_recover_enabled = auto_recover


def test_typed_body_probe_restores_vpn_kill_switch_state(monkeypatch, request):
    """A mutating probe may observe the singleton, but may not retain its writes."""
    from bulk_downloader import vpn_kill_switch as ks
    from tools import body_contract as bc

    ks.set_auto_recover(False)
    ks.kill_tunnel("preexisting", reason="fixture must survive")
    before = ks.list_kill_states()
    fired = {"probe_kills": 0}
    real_kill = ks.kill_tunnel
    callback_events = []

    def callback(tunnel_id, state):
        callback_events.append((tunnel_id, state))

    ks.register_kill_callback(callback)
    request.addfinalizer(lambda: ks.unregister_kill_callback(callback))

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
    assert callback_events == [("_probe", "killed")]
    with ks._callbacks_lock:
        assert callback in ks._callbacks


def test_literal_body_probe_restores_vpn_kill_switch_state(monkeypatch):
    """The older literal-body production probe has the same isolation duty."""
    from bulk_downloader import vpn_kill_switch as ks
    from tools import body_contract as bc

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


def test_fixture_body_probe_restores_vpn_kill_switch_state(monkeypatch):
    """The fixture-backed production replay must not leak its tunnel kill."""
    from bulk_downloader import vpn_kill_switch as ks
    from tools import body_contract as bc

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

    result = bc.probe_fixtures(ROOT, [call])
    assert result, "the fixture-backed production probe returned no verdict"
    assert fired["fixture_kills"] == 2, (
        "both fixture differential requests must reach the real kill endpoint; "
        "otherwise the restoration assertion has no mutation denominator"
    )
    assert ks.list_kill_states() == before
    assert ks.get_auto_recover() is False


def test_probe_serializes_restore_against_concurrent_real_state(monkeypatch):
    """A legitimate concurrent kill must occur after restore, never be erased by it."""
    from bulk_downloader import vpn_kill_switch as ks
    from tools import body_contract as bc

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
    bc.probe(ROOT, [])
    holder["thread"].join(2)
    assert completed.is_set(), "the serialized legitimate mutation never resumed"
    assert [row["tunnel_id"] for row in ks.list_kill_states()] == ["concurrent"]


def test_probe_serializes_restore_against_concurrent_auto_recover_change(monkeypatch):
    """A legitimate concurrent policy change must survive probe restoration."""
    from bulk_downloader import vpn_kill_switch as ks
    from tools import body_contract as bc

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
    bc.probe(ROOT, [])
    holder["thread"].join(2)
    assert completed.is_set(), "the serialized auto-recover mutation never resumed"
    assert ks.get_auto_recover() is True


def test_fixture_probe_snapshots_kill_switch_before_cold_app_import(tmp_path):
    """App initialization is part of the probe mutation window, not its baseline."""
    script = """
import json
import sys
from bulk_downloader import vpn_kill_switch as ks
from tools import body_contract as bc

assert "bulk_downloader.app" not in sys.modules
ks.set_auto_recover(False)
before = ks.get_auto_recover()
result = bc.probe_fixtures(sys.argv[1], [])
print(json.dumps({"before": before, "after": ks.get_auto_recover(), "result": result}))
"""
    env = os.environ.copy()
    env["BD_DISABLE_KEEPALIVE"] = "1"
    env["BD_HOME"] = str(tmp_path / "home")
    env["PYTHONPATH"] = ROOT
    proc = subprocess.run(
        [sys.executable, "-c", script, ROOT],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.splitlines()[-1])
    assert payload["before"] is False
    assert payload["result"] == []
    assert payload["after"] is False
