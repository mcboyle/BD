"""O1826 C08 (M025): a tunnel PUT whose persist failed reported success.

``vpn_tunnel_update`` mutated the live ``Tunnel`` (name/location/config),
then called ``vpn_config.update_tunnel_config``/``store_secrets`` inside a
bare ``except Exception`` that wrote one stderr line and fell through to
``_ok(...)``. The caller saw 200 and the edited tunnel; the saved config
still held the old values, so the change vanished on the next restart.

The create path already rolls back its in-memory registration and returns
500 when persist fails; the update path now mirrors it: restore the prior
name/location/config and return 500.

r2 (REFUTE E1 + router NOTE): the snapshot -> mutate -> persist -> rollback
sequence is serialized per tunnel, so a failed PUT's restore no longer
discards a concurrent PUT that persisted in between; the failure also
restores ``vpn_config._state`` and the secret store, and neither the 500
body nor the log echoes the exception text.

r3 (REFUTE E1 + E2): a per-tunnel lock lives only while a PUT holds or
waits on it, so PUT/DELETE cycles leave the lock map empty; and every
secret the PUT may overwrite is read back first -- a store or key that
cannot be read refuses the PUT (503) before anything is written.

Every value in this module is a synthetic literal; none is a credential.
"""
from __future__ import annotations

import copy
import threading

import pytest

from bulk_downloader import app_vpn_api, secrets_store, vpn, vpn_config


BD_GATE_SCOPE = "module"

_TID = "o1826c08t1"
_NAME = "o1826c08-before"
_LOCATION = "o1826c08-loc-before"
_CONFIG = {"endpoint": "o1826c08-endpoint-before", "dns": "o1826c08-dns"}
_EDIT = {
    "name": "o1826c08-after",
    "location": "o1826c08-loc-after",
    "config": {"endpoint": "o1826c08-endpoint-after"},
}
_SYNTH_SECRET = "o1826c08-SYNTHETIC-SECRET-not-a-credential"
_REAL_STORE_SECRETS = vpn_config.store_secrets


@pytest.fixture
def saves(monkeypatch):
    """Seed the saved config with the tunnel; record each save() snapshot."""
    monkeypatch.setattr(vpn_config, "_state", {
        "schema_version": vpn_config.SCHEMA_VERSION,
        "global_settings": {},
        "tunnels": [{
            "tunnel_id": _TID, "name": _NAME, "provider": "generic",
            "backend": "wireguard", "location": _LOCATION, "enabled": True,
            "config": dict(_CONFIG), "extra": {},
        }],
    })
    recorded = []
    monkeypatch.setattr(vpn_config, "save", lambda: recorded.append(
        copy.deepcopy(vpn_config._state["tunnels"])))
    return recorded


@pytest.fixture
def client(monkeypatch, saves):
    from flask import Flask

    monkeypatch.setattr(vpn, "_tunnels", {})
    vpn.register_tunnel(
        name=_NAME, provider="generic", backend="wireguard",
        location=_LOCATION, config=dict(_CONFIG), tunnel_id=_TID,
    )
    monkeypatch.setattr(vpn_config, "store_secrets", lambda tid, cfg: dict(cfg))
    monkeypatch.setattr(secrets_store, "get_backend", lambda: _MemoryBackend({}))
    app = Flask(__name__)
    app.testing = True
    assert app_vpn_api.register_routes(app) > 0
    return app.test_client()


def _snapshot():
    t = vpn.get_tunnel(_TID)
    return (t.name, t.location, dict(t.config))


def _saved(tunnels=None):
    (t,) = vpn_config._state["tunnels"] if tunnels is None else tunnels
    return (t["name"], t["location"], dict(t["config"]))


def _raise(*_a, **_kw):
    raise OSError("o1826c08 synthetic persist failure")


@pytest.mark.parametrize("seam", ["update_tunnel_config", "store_secrets"])
def test_failed_persist_returns_500_and_restores_tunnel(client, monkeypatch, seam):
    monkeypatch.setattr(vpn_config, seam, _raise)
    before = _snapshot()

    resp = client.put(f"/api/vpn/tunnels/{_TID}", json=_EDIT)

    after = _snapshot()
    assert (resp.status_code, after) == (500, before), (
        f"O1826-C08 UNPERSISTED-PUT-REPORTED-OK: {seam} raised but PUT "
        f"returned {resp.status_code} and the live tunnel moved "
        f"{before!r} -> {after!r}"
    )
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error"] == "persist failed; the tunnel was not changed"


def test_successful_persist_returns_200_and_applies_edit(client, monkeypatch):
    calls = []

    def record(tunnel_id, **fields):
        calls.append((tunnel_id, fields))
        return {"tunnel_id": tunnel_id, **fields}

    monkeypatch.setattr(vpn_config, "update_tunnel_config", record)

    resp = client.put(f"/api/vpn/tunnels/{_TID}", json=_EDIT)

    assert resp.status_code == 200
    merged = {**_CONFIG, **_EDIT["config"]}
    assert _snapshot() == (_EDIT["name"], _EDIT["location"], merged)
    assert calls == [(_TID, {
        "name": _EDIT["name"], "location": _EDIT["location"], "config": merged,
    })]
    assert resp.get_json()["tunnel"]["name"] == _EDIT["name"]


def test_failed_put_does_not_discard_concurrent_successful_put(client, monkeypatch, saves):
    """E1: PUT1's persist fails while PUT2 is in flight; PUT2 must survive."""
    edits = {
        n: {"name": f"o1826c08-{n}", "location": f"o1826c08-loc-{n}",
            "config": {"endpoint": f"o1826c08-endpoint-{n}"}}
        for n in ("first", "second")
    }
    first_saving = threading.Event()
    release_first = threading.Event()
    second_storing = threading.Event()

    def store(tid, cfg):
        if cfg.get("endpoint") == edits["second"]["config"]["endpoint"]:
            second_storing.set()
        return dict(cfg)

    def save():
        if vpn_config._state["tunnels"][0]["name"] == edits["first"]["name"]:
            first_saving.set()
            assert release_first.wait(10)
            raise OSError("o1826c08 synthetic first save refusal")
        saves.append(copy.deepcopy(vpn_config._state["tunnels"]))

    monkeypatch.setattr(vpn_config, "store_secrets", store)
    monkeypatch.setattr(vpn_config, "save", save)
    app = client.application
    codes = {}

    def put(n):
        codes[n] = app.test_client().put(
            f"/api/vpn/tunnels/{_TID}", json=edits[n]).status_code

    first = threading.Thread(target=put, args=("first",))
    second = threading.Thread(target=put, args=("second",))
    first.start()
    try:
        assert first_saving.wait(10)
        second.start()
        # Unfixed, PUT2 reaches its persist while PUT1 is still failing; fixed,
        # it waits for PUT1's rollback. Either way PUT1 is released next.
        second_storing.wait(1)
    finally:
        release_first.set()
        first.join(10)
        second.join(10)
    assert not first.is_alive() and not second.is_alive()

    want = (edits["second"]["name"], edits["second"]["location"],
            {**_CONFIG, **edits["second"]["config"]})
    got = {"codes": codes, "live": _snapshot(), "state": _saved(),
           "persisted": _saved(saves[-1]) if saves else None}
    assert got == {"codes": {"first": 500, "second": 200}, "live": want,
                   "state": want, "persisted": want}, (
        f"O1826-C08 FAILED-PUT-ROLLBACK-DISCARDED-CONCURRENT-PUT: {got!r}")


def test_save_failure_restores_saved_config_state(client, monkeypatch):
    before = copy.deepcopy(vpn_config._state)
    monkeypatch.setattr(vpn_config, "save", _raise)

    resp = client.put(f"/api/vpn/tunnels/{_TID}", json=_EDIT)

    assert resp.status_code == 500
    assert _snapshot() == (_NAME, _LOCATION, _CONFIG)
    assert vpn_config._state == before, (
        f"O1826-C08 FAILED-EDIT-LEFT-IN-CONFIG-STATE: {_saved()!r}")


def test_failed_persist_does_not_echo_secret(client, monkeypatch, capsys):
    def leak(*_a, **_kw):
        raise OSError(f"o1826c08 cannot save password={_SYNTH_SECRET}")

    monkeypatch.setattr(vpn_config, "update_tunnel_config", leak)

    resp = client.put(f"/api/vpn/tunnels/{_TID}",
                      json={"config": {"password": _SYNTH_SECRET}})

    err = capsys.readouterr().err
    assert resp.status_code == 500
    assert "[vpn-api] update persist failed" in err
    assert _SYNTH_SECRET not in resp.get_data(as_text=True), (
        "O1826-C08 PERSIST-ERROR-BODY-ECHOES-SECRET")
    assert _SYNTH_SECRET not in err, "O1826-C08 PERSIST-ERROR-LOG-ECHOES-SECRET"


class _MemoryBackend:
    name = "o1826c08-memory"

    def __init__(self, vault):
        self.vault = vault

    def set(self, key, password):
        self.vault[key] = password

    def get(self, key):
        return self.vault.get(key)

    def delete(self, key):
        return self.vault.pop(key, None) is not None

    def list_keys(self):
        return list(self.vault)


@pytest.mark.parametrize("seam", ["update_tunnel_config", "save"])
def test_failed_persist_leaves_no_backend_write(client, monkeypatch, seam):
    vault = {f"{_TID}:password": "o1826c08-old-synthetic"}
    before = dict(vault)
    monkeypatch.setattr(secrets_store, "get_backend", lambda: _MemoryBackend(vault))
    monkeypatch.setattr(vpn_config, "store_secrets", _REAL_STORE_SECRETS)
    monkeypatch.setattr(vpn_config, seam, _raise)

    resp = client.put(f"/api/vpn/tunnels/{_TID}", json={"config": {
        "password": _SYNTH_SECRET, "private_key": _SYNTH_SECRET}})

    assert resp.status_code == 500
    assert vault == before, f"O1826-C08 SECRET-WRITE-SURVIVES-FAILED-PUT: {vault!r}"


def test_successful_persist_keeps_backend_write(client, monkeypatch):
    vault = {}
    monkeypatch.setattr(secrets_store, "get_backend", lambda: _MemoryBackend(vault))
    monkeypatch.setattr(vpn_config, "store_secrets", _REAL_STORE_SECRETS)

    resp = client.put(f"/api/vpn/tunnels/{_TID}",
                      json={"config": {"password": _SYNTH_SECRET}})

    assert resp.status_code == 200
    assert vault == {f"{_TID}:password": _SYNTH_SECRET}
    assert _saved()[2]["password"] == f"@cred:{_TID}:password"


def test_put_delete_cycles_leave_no_lock_entries(client):
    """r3 E1: the per-tunnel lock map must not grow with every id edited."""
    grown = []
    for i in range(24):
        tid = f"o1826c08cyc{i}"
        vpn.register_tunnel(
            name=_NAME, provider="generic", backend="wireguard",
            location=_LOCATION, config=dict(_CONFIG), tunnel_id=tid,
        )
        vpn_config._state["tunnels"].append({
            "tunnel_id": tid, "name": _NAME, "provider": "generic",
            "backend": "wireguard", "location": _LOCATION, "enabled": True,
            "config": dict(_CONFIG), "extra": {},
        })
        codes = (client.put(f"/api/vpn/tunnels/{tid}", json=_EDIT).status_code,
                 client.delete(f"/api/vpn/tunnels/{tid}").status_code)
        assert codes == (200, 200)
        assert vpn.get_tunnel(tid) is None
        if app_vpn_api._tunnel_update_locks:
            grown.append(sorted(app_vpn_api._tunnel_update_locks))
    assert not grown, f"O1826-C08 LOCK-MAP-GROWS: {grown[-1]!r} after {len(grown)} cycles"


def _wait_for(pred):
    done = threading.Event()
    for _ in range(1000):
        if pred():
            return True
        done.wait(0.01)
    return pred()


def test_lock_entry_survives_while_a_waiter_holds_it():
    """r3 E1: retiring an entry a waiter still holds would let a third PUT
    take a fresh lock and run beside it."""
    tid = "o1826c08wait"
    locks = app_vpn_api._tunnel_update_locks
    first = app_vpn_api._tunnel_update_lock(tid)
    first.__enter__()
    entered, release = threading.Event(), threading.Event()

    def waiter():
        with app_vpn_api._tunnel_update_lock(tid):
            entered.set()
            release.wait(10)

    th = threading.Thread(target=waiter)
    th.start()
    try:
        assert _wait_for(lambda: tid in locks and locks[tid][1] == 2)
        first.__exit__(None, None, None)
        assert entered.wait(10)
        held = tid in locks and locks[tid][0].locked()
        assert held, f"O1826-C08 LOCK-RETIRED-WHILE-HELD: {locks.get(tid)!r}"
    finally:
        release.set()
        th.join(10)
    assert not th.is_alive()
    assert tid not in locks


class _ReadRefusingBackend(_MemoryBackend):
    """Writes land; reading ``refuse`` raises (a read-refusing vault)."""

    def __init__(self, vault, refuse):
        super().__init__(vault)
        self.refuse = refuse

    def get(self, key):
        if key == self.refuse:
            raise OSError("o1826c08 synthetic vault read refusal")
        return super().get(key)


@pytest.mark.parametrize("refuse", ["password", "private_key"])
def test_unreadable_prior_secret_refuses_put_before_any_write(
        client, monkeypatch, saves, refuse):
    """r3 E2: a secret that cannot be snapshotted cannot be rolled back, so
    the PUT is refused before store_secrets writes anything."""
    vault = {f"{_TID}:password": "o1826c08-old-synthetic",
             f"{_TID}:private_key": "o1826c08-old-synthetic-key"}
    before = dict(vault)
    state_before = copy.deepcopy(vpn_config._state)
    monkeypatch.setattr(secrets_store, "get_backend",
                        lambda: _ReadRefusingBackend(vault, f"{_TID}:{refuse}"))
    monkeypatch.setattr(vpn_config, "store_secrets", _REAL_STORE_SECRETS)

    resp = client.put(f"/api/vpn/tunnels/{_TID}", json={
        "name": "o1826c08-after", "config": {
            "password": _SYNTH_SECRET, "private_key": _SYNTH_SECRET}})

    got = {"code": resp.status_code, "vault": vault, "live": _snapshot(),
           "state": vpn_config._state, "saves": len(saves)}
    assert got == {"code": 503, "vault": before,
                   "live": (_NAME, _LOCATION, _CONFIG),
                   "state": state_before, "saves": 0}, (
        f"O1826-C08 UNREADABLE-SECRET-OVERWRITTEN: {got!r}")
    assert _SYNTH_SECRET not in resp.get_data(as_text=True)


def test_unavailable_secret_store_refuses_put_with_secret(client, monkeypatch, saves):
    def unavailable():
        raise OSError("o1826c08 synthetic store unavailable")

    monkeypatch.setattr(secrets_store, "get_backend", unavailable)
    monkeypatch.setattr(vpn_config, "store_secrets", _REAL_STORE_SECRETS)

    resp = client.put(f"/api/vpn/tunnels/{_TID}",
                      json={"config": {"password": _SYNTH_SECRET}})

    got = (resp.status_code, _snapshot(), len(saves))
    assert got == (503, (_NAME, _LOCATION, _CONFIG), 0), (
        f"O1826-C08 UNSNAPSHOTTED-SECRET-PUT-PROCEEDED: {got!r}")


def test_plaintext_backend_put_is_not_refused(client, monkeypatch):
    """Control: store_secrets writes nothing through the plaintext backend,
    so there is nothing to snapshot and the PUT persists."""
    backend = _ReadRefusingBackend({}, f"{_TID}:password")
    backend.name = "plaintext"
    monkeypatch.setattr(secrets_store, "get_backend", lambda: backend)
    monkeypatch.setattr(vpn_config, "store_secrets", _REAL_STORE_SECRETS)

    resp = client.put(f"/api/vpn/tunnels/{_TID}",
                      json={"config": {"password": _SYNTH_SECRET}})

    assert resp.status_code == 200
    assert backend.vault == {}
