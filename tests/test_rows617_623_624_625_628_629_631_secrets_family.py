"""Rows 617/623/624/625/628/629/631: secrets failures keep exact state.

All paths and persisted stores are isolated below pytest's temporary root.
Synthetic password literals in this file are documented zero-entropy fixtures.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
from pathlib import Path

import pytest
from flask import Flask, jsonify

from bulk_downloader import app_secrets
from bulk_downloader import app_state
from bulk_downloader import auth_throttle as at
from bulk_downloader import extension_vault as ev
from bulk_downloader import secrets_store as ss


BD_GATE_SCOPE = "module"

_MASTER = "rows617-631-synthetic-master"
_VALUE = "rows617-631-synthetic-value"
_OTHER_VALUE = "rows617-631-synthetic-other-value"
_SITE = "rows617631site"
_KEY = ss.site_password_key(_SITE)
_OTHER_KEY = "bulkdl-site-rows617631-other"
_TOKEN = "rows617-631-synthetic-token"


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    install = tmp_path / "install"
    home = tmp_path / "home"
    temp = tmp_path / "tmp"
    for directory in (install, home, temp):
        directory.mkdir()
    monkeypatch.chdir(install)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(temp))
    monkeypatch.setenv("BD_HOME", str(install))
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)

    vault = install / "secrets.json"
    meta = install / "secrets_meta.json"
    sites = install / "sites_config.json"
    monkeypatch.setattr(ss, "SECRETS_FILE", vault)
    monkeypatch.setattr(ss, "SECRETS_META_FILE", meta)
    monkeypatch.setattr(ss, "_backend", None)
    monkeypatch.setattr(ss, "_backend_pref", None)
    monkeypatch.setattr(ss, "_audited_cache", None)
    monkeypatch.setattr(ev, "VAULT_TOKENS_FILE", install / "vault_tokens.json")
    monkeypatch.setattr(ev, "VAULT_AUDIT_LOG", install / "vault_access.log")

    import bulk_downloader.app as bd_app
    monkeypatch.setattr(bd_app, "SITES_FILE", sites)

    live = app_state.s_cfg
    assert bd_app.s_cfg is live
    snapshot = dict(live)
    live.clear()
    at.reset()
    yield install, vault, meta, sites, live
    live.clear()
    live.update(snapshot)
    at.reset()


def _backend(keys: dict[str, str] | None = None) -> ss.MasterPasswordBackend:
    assert ss._CRYPTO_AVAILABLE is True
    backend = ss.MasterPasswordBackend()
    backend._data["iterations"] = 1_000
    assert backend.unlock(_MASTER) is True
    for key, value in (keys or {}).items():
        backend.set(key, value)
    assert backend.is_unlocked() is True
    assert set(backend.list_keys()) == set(keys or {})
    ss._backend = backend
    ss._backend_pref = "master_password"
    return backend


def _client() -> object:
    flask_app = Flask("rows617-631-secrets")
    flask_app.config["PROPAGATE_EXCEPTIONS"] = False
    flask_app.register_blueprint(app_secrets.secrets_bp)

    @flask_app.errorhandler(500)
    def _opaque_500(_error):
        return jsonify({"ok": False, "error": "internal server error"}), 500

    return flask_app.test_client()


def _seed_token(root: Path) -> None:
    payload = {
        "redeemed": {
            _TOKEN: {
                "label": "rows617-631-extension",
                "issued_at": 0,
                "entry_cooldowns": {},
                "recent_fetches": [],
            }
        }
    }
    (root / "vault_tokens.json").write_text(json.dumps(payload), encoding="utf-8")


def _ledger(root: Path) -> dict:
    payload = json.loads((root / "vault_tokens.json").read_text(encoding="utf-8"))
    return payload["redeemed"][_TOKEN]


def _audit(root: Path) -> list[str]:
    path = root / "vault_access.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _extension_client(monkeypatch, sites: dict):
    monkeypatch.setattr(app_secrets, "_app_s_cfg", lambda: sites)
    monkeypatch.setattr(
        app_secrets,
        "_require_vault_token",
        lambda *_a, **_k: (_TOKEN, {"label": "rows617-631-extension"}, None),
    )
    return _client()


def test_transform_control_imports_without_exercising_secrets_behaviour():
    """The mutation transform is valid even when no behavioral seam runs."""
    assert ss.__name__ == "bulk_downloader.secrets_store"


# Row 617 -------------------------------------------------------------------


def test_row617_one_stat_oserror_is_reprobed_without_wiping_the_key(
    isolated, monkeypatch
):
    _root, vault, _meta, _sites, _cfg = isolated
    backend = _backend({_KEY: _VALUE})
    before = vault.read_bytes()
    real_stat = Path.stat
    probe_calls = 0

    def one_failure(path: Path, *args, **kwargs):
        nonlocal probe_calls
        if path == vault:
            probe_calls += 1
            if probe_calls == 1:
                raise OSError("row617 synthetic transient EIO")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", one_failure)
    assert backend._save() is True

    assert probe_calls == 3, (
        "one failed stat, one successful retry, and one post-publish identity "
        "probe must fire"
    )
    assert backend.is_unlocked() is True
    assert backend._load_error is None
    assert backend.get(_KEY) == _VALUE
    assert vault.read_bytes() == before


def test_row617_persistent_stat_oserror_still_latches_and_refuses(
    isolated, monkeypatch
):
    _root, vault, _meta, _sites, _cfg = isolated
    backend = _backend({_KEY: _VALUE})
    probe_calls = 0
    real_stat = Path.stat

    def persistent_failure(path: Path, *args, **kwargs):
        nonlocal probe_calls
        if path == vault:
            probe_calls += 1
            raise OSError("row617 synthetic persistent EIO")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", persistent_failure)
    with pytest.raises(ss.SecretsUnreadableError, match="cannot be measured"):
        backend._save()

    assert probe_calls == 2, "persistent classification requires two failed probes"
    assert backend.is_unlocked() is False
    assert backend._load_error_kind == "unmeasured"
    with pytest.raises(ss.SecretsUnreadableError, match="could not be measured"):
        backend.list_keys()


# Row 623 -------------------------------------------------------------------


class _MembershipBackend:
    name = "master_password"

    def __init__(self):
        self.delete_calls = 0

    def delete(self, key):
        self.delete_calls += 1
        return key in {"present": True}


@pytest.mark.parametrize("bad_key", [{"object": True}, ["list"], 0, ""])
def test_row623_delete_rejects_every_non_string_or_empty_key_before_membership(
    isolated, monkeypatch, bad_key
):
    backend = _MembershipBackend()
    monkeypatch.setattr(ss, "get_backend", lambda: backend)

    response = _client().post("/api/secrets/delete", json={"key": bad_key})
    body = response.get_json() or {}

    assert response.status_code == 400, body
    assert body == {"ok": False, "error": "key or site_id must be a non-empty string"}
    assert backend.delete_calls == 0, "invalid input reached backend membership"


def test_row623_string_key_keeps_the_existing_present_and_absent_results(
    isolated, monkeypatch
):
    backend = _MembershipBackend()
    monkeypatch.setattr(ss, "get_backend", lambda: backend)
    client = _client()

    present = client.post("/api/secrets/delete", json={"key": "present"})
    absent = client.post("/api/secrets/delete", json={"key": "absent"})

    assert present.status_code == 200 and present.get_json()["removed"] is True
    assert absent.status_code == 200 and absent.get_json()["removed"] is False
    assert backend.delete_calls == 2


# Row 624 -------------------------------------------------------------------


def _damaged_backend(vault: Path) -> ss.MasterPasswordBackend:
    document = {
        "version": 1,
        "kdf": "pbkdf2-sha256",
        "iterations": 1_000,
        "salt": base64.b64encode(b"row624-salt-value").decode(),
        "ciphertexts": [],
    }
    vault.write_text(json.dumps(document), encoding="utf-8")
    backend = ss.MasterPasswordBackend()
    assert backend._load_error is None, "the damaged vault must still be readable"
    with pytest.raises(ss.SecretsIntegrityError) as error:
        backend.list_keys()
    assert type(error.value) is ss.SecretsIntegrityError
    return backend


def test_row624_delete_and_status_agree_on_readable_integrity_damage(
    isolated, monkeypatch
):
    _root, vault, _meta, _sites, _cfg = isolated
    backend = _damaged_backend(vault)
    monkeypatch.setattr(ss, "get_backend", lambda: backend)
    client = _client()

    status = client.get("/api/secrets/status")
    deleted = client.post("/api/secrets/delete", json={"key": _KEY})
    status_body = status.get_json() or {}
    delete_body = deleted.get_json() or {}

    assert status.status_code == 409 and status_body["state"] == "integrity_error"
    assert deleted.status_code == 409, delete_body
    assert delete_body["state"] == "integrity_error"
    assert delete_body.get("requires_restart") is not True


def test_row624_unreadable_vault_keeps_the_restart_refusal(isolated, monkeypatch):
    _root, vault, _meta, _sites, _cfg = isolated
    vault.write_text("{not valid json", encoding="utf-8")
    backend = ss.MasterPasswordBackend()
    assert backend._load_error is not None
    assert backend._load_error_kind == "unreadable"
    monkeypatch.setattr(ss, "get_backend", lambda: backend)

    response = _client().post("/api/secrets/delete", json={"key": _KEY})
    body = response.get_json() or {}

    assert response.status_code == 409, body
    assert body["state"] == "unreadable"
    assert body["requires_restart"] is True


# Row 625 -------------------------------------------------------------------


def test_row625_delete_restores_the_entry_when_save_raises(isolated, monkeypatch):
    backend = _backend({_KEY: _VALUE, _OTHER_KEY: _OTHER_VALUE})
    before = dict(backend._data["ciphertexts"])

    def raising_save():
        assert _KEY not in backend._data["ciphertexts"], (
            "precondition: delete did not pop the target before save"
        )
        raise ss.SecretsUnreadableError("row625 synthetic save refusal")

    monkeypatch.setattr(backend, "_save", raising_save)
    with pytest.raises(ss.SecretsUnreadableError, match="row625 synthetic"):
        backend.delete(_KEY)

    assert backend._data["ciphertexts"] == before
    assert backend.get(_KEY) == _VALUE


def test_row625_false_save_and_success_keep_their_existing_results(
    isolated, monkeypatch
):
    install, vault, meta, _sites, _cfg = isolated
    backend = _backend({_KEY: _VALUE, _OTHER_KEY: _OTHER_VALUE})
    before = dict(backend._data["ciphertexts"])
    monkeypatch.setattr(backend, "_save", lambda: False)
    with pytest.raises(ss.SecretsPersistError, match="failed to persist deletion"):
        backend.delete(_KEY)
    assert backend._data["ciphertexts"] == before

    assert Path.cwd() == install, "the row625 test escaped its isolated cwd"
    assert ss.SECRETS_FILE == vault
    assert ss.SECRETS_META_FILE == meta
    backend = _backend({_KEY: _VALUE, _OTHER_KEY: _OTHER_VALUE})
    assert backend.delete(_KEY) is True
    assert _KEY not in backend.list_keys()
    assert backend.get(_OTHER_KEY) == _OTHER_VALUE


# Row 628 -------------------------------------------------------------------


class _DeleteBackend:
    name = "master_password"

    def delete(self, _key):
        return True


def test_row628_clear_save_restore_is_one_sites_config_transaction(
    isolated, monkeypatch
):
    _root, _vault, _meta, sites_path, live = isolated
    import bulk_downloader.app as bd_app

    reference = ss.make_password_reference(_SITE)
    live[_SITE] = {"password": reference, "cookie_file": "already-set"}
    sites_path.write_text(json.dumps(live), encoding="utf-8")
    assert json.loads(sites_path.read_text())[_SITE]["password"] == reference
    monkeypatch.setattr(ss, "get_backend", lambda: _DeleteBackend())

    first_save_entered = threading.Event()
    second_save_started = threading.Event()
    second_save_finished = threading.Event()
    route_result: list[tuple[int, dict]] = []
    second_result: list[bool] = []
    route_owned_lock: list[bool] = []
    first_calls = 0

    def fail_first_save():
        nonlocal first_calls
        first_calls += 1
        assert live[_SITE]["password"] == "", "route never published the clear"
        owns_lock = app_state._sites_config_save_lock._is_owned()
        route_owned_lock.append(owns_lock)
        first_save_entered.set()
        assert second_save_started.wait(5), "concurrent writer never started"
        if not owns_lock:
            assert second_save_finished.wait(5), (
                "the unlocked concurrent writer did not persist the temporary clear"
            )
        return False

    monkeypatch.setattr(app_secrets, "_save_sites_config", fail_first_save)

    def route_delete():
        response = _client().post("/api/secrets/delete", json={"site_id": _SITE})
        route_result.append((response.status_code, response.get_json() or {}))

    def concurrent_save():
        second_save_started.set()
        try:
            second_result.append(bd_app._save_sites_config())
        finally:
            second_save_finished.set()

    route_thread = threading.Thread(target=route_delete)
    route_thread.start()
    assert first_save_entered.wait(5), "the route never reached its injected save"
    save_thread = threading.Thread(target=concurrent_save)
    save_thread.start()
    assert second_save_started.wait(5), "the concurrent writer never attempted a save"
    route_thread.join(5)
    save_thread.join(5)

    assert not route_thread.is_alive() and not save_thread.is_alive()
    assert first_calls == 1
    assert route_owned_lock == [True], (
        "the route's clear-save-restore sequence did not own the writer lock"
    )
    assert second_result == [True], "the concurrent real save did not complete exactly once"
    assert route_result[0][0] == 500
    assert route_result[0][1]["state"] == "config_write_failed"
    assert live[_SITE]["password"] == reference
    assert json.loads(sites_path.read_text())[_SITE]["password"] == reference, (
        "a concurrent writer persisted the temporary clear outside the route's "
        "clear-save-restore transaction"
    )


# Row 629 -------------------------------------------------------------------


class _KeyringDouble:
    def __init__(self, values: dict[str, str], *, fail_delete: bool = False):
        self.values = values
        self.fail_delete = fail_delete
        self.delete_calls = 0

    def get_password(self, _service, key):
        return self.values.get(key)

    def delete_password(self, _service, key):
        self.delete_calls += 1
        if self.fail_delete:
            raise RuntimeError("row629 synthetic keyring refusal")
        self.values.pop(key, None)


def _windows_backend(monkeypatch, *, fail_index: bool, fail_keyring: bool):
    keyring = _KeyringDouble({_KEY: _VALUE}, fail_delete=fail_keyring)
    index = [_KEY]
    backend = object.__new__(ss.WindowsCredentialBackend)

    def save_index(keys):
        if fail_index:
            return False
        index[:] = keys
        return True

    monkeypatch.setattr(ss, "keyring", keyring)
    monkeypatch.setattr(backend, "_load_index", lambda: list(index))
    monkeypatch.setattr(backend, "_save_index", save_index)
    monkeypatch.setattr(ss, "_unstamp_rotation", lambda _key: None)
    monkeypatch.setattr(ss, "get_backend", lambda: backend)
    return backend, keyring, index


def test_row629_index_failure_message_names_keyring_deleted_index_stale(
    isolated, monkeypatch
):
    backend, keyring, index = _windows_backend(
        monkeypatch, fail_index=True, fail_keyring=False
    )
    response = _client().post("/api/secrets/delete", json={"key": _KEY})
    body = response.get_json() or {}

    assert response.status_code == 500, body
    assert keyring.delete_calls == 1
    assert _KEY not in keyring.values, "Credential Manager deletion did not happen"
    assert backend.list_keys() == index == [_KEY], "the index did not remain stale"
    assert "index may be out of sync with Credential Manager" in body["error"]
    assert "rolled back and still holds" not in body["error"]
    assert "secrets.json" not in body["error"]


def test_row629_keyring_failure_message_names_index_updated_credential_present(
    isolated, monkeypatch
):
    backend, keyring, index = _windows_backend(
        monkeypatch, fail_index=False, fail_keyring=True
    )
    response = _client().post("/api/secrets/delete", json={"key": _KEY})
    body = response.get_json() or {}

    assert response.status_code == 500, body
    assert keyring.delete_calls == 1
    assert keyring.values[_KEY] == _VALUE, "the credential unexpectedly disappeared"
    assert backend.list_keys() == index == [], "the index update did not land"
    assert "index updated but credential may still be in Credential Manager" in body["error"]
    assert "rolled back and still holds" not in body["error"]
    assert "secrets.json" not in body["error"]


# Row 631 -------------------------------------------------------------------


def _fetch(client):
    return client.post(
        "/api/secrets/extension/fetch_one",
        json={"id": f"site:{_SITE}", "origin": "https://example.test/"},
    )


def test_row631_corrupt_listed_entry_is_refused_before_rate_accounting(
    isolated, monkeypatch
):
    root, _vault, _meta, _sites_path, _live = isolated
    backend = _backend({_KEY: _VALUE})
    entry = backend._data["ciphertexts"][_KEY]
    ciphertext = bytearray(base64.b64decode(entry["ct"]))
    ciphertext[-1] ^= 1
    entry["ct"] = base64.b64encode(bytes(ciphertext)).decode()
    assert backend.list_keys() == [_KEY], "precondition: exactly one listed entry"
    assert backend.is_unlocked() is True
    assert backend.store_state() == "unlocked"
    assert backend.get(_KEY) is None, "the corruption did not reach AES-GCM decrypt"
    value, state = ss.resolve_password_state(f"@cred:{_KEY}")
    assert value is None and state == "unavailable"
    _seed_token(root)
    sites = {_SITE: {"username": "u", "password": f"@cred:{_KEY}"}}

    response = _fetch(_extension_client(monkeypatch, sites))
    body = response.get_json() or {}

    assert response.status_code == 409, body
    assert body["state"] == "entry_unreadable"
    assert body["requires_vault_repair"] is True
    assert "repair" in body["error"].lower() or "replace" in body["error"].lower()
    lines = _audit(root)
    assert len(lines) == 1
    assert "entry_unreadable" in lines[0]
    assert "no_password_stored" not in lines[0]
    ledger = _ledger(root)
    assert ledger["entry_cooldowns"] == {}
    assert ledger["recent_fetches"] == []


def test_row631_absent_password_still_audits_missing_after_one_rate_charge(
    isolated, monkeypatch
):
    root, _vault, _meta, _sites_path, _live = isolated
    backend = _backend()
    assert backend.list_keys() == []
    assert backend.is_unlocked() is True
    _seed_token(root)
    sites = {_SITE: {"username": "u", "password": f"@cred:{_KEY}"}}

    response = _fetch(_extension_client(monkeypatch, sites))

    assert response.status_code == 403
    assert response.get_json() == {"ok": False, "error": "denied"}
    lines = _audit(root)
    assert len(lines) == 1 and "no_password_stored" in lines[0]
    ledger = _ledger(root)
    assert list(ledger["entry_cooldowns"]) == [_KEY]
    assert len(ledger["recent_fetches"]) == 1
