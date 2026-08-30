"""Row 402: an empty master-password vault has a durable commitment.

The first unlock is the product's first-use setup path.  It must persist an
AES-GCM verifier before reporting success, so locking, restarting, deleting the
last credential, or rotating a zero-secret vault cannot turn it back into an
accept-any-password store.  Health must report the resulting states without
laundering an uninitialised vault into a missing-credential diagnosis.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from flask import Flask
import pytest

from bulk_downloader import app_health
from bulk_downloader import app_secrets
from bulk_downloader import auth_throttle as at
from bulk_downloader import secrets_store as ss


BD_GATE_SCOPE = "module"

_MASTER = "row402-synthetic-master-password"
_NEW_MASTER = "row402-synthetic-new-master-password"
_WRONG = "row402-synthetic-wrong-password"
_KEY = "bulkdl-site-row402"
_VALUE = "row402-synthetic-value"
_REF = f"@cred:{_KEY}"
_ITERATIONS = 1_000


def _new_backend(monkeypatch, root: Path):
    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not available")
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)
    monkeypatch.setattr(ss, "SECRETS_FILE", root / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", root / "secrets_meta.json")
    backend = ss.MasterPasswordBackend()
    backend._data["iterations"] = _ITERATIONS
    monkeypatch.setattr(ss, "_backend", backend)
    monkeypatch.setattr(ss, "_backend_pref", "master_password")
    monkeypatch.setattr(ss, "_audited_cache", None)
    at.reset()
    return backend


def _reopen_backend(monkeypatch, root: Path):
    backend = ss.MasterPasswordBackend()
    monkeypatch.setattr(ss, "_backend", backend)
    monkeypatch.setattr(ss, "_backend_pref", "master_password")
    monkeypatch.setattr(ss, "_audited_cache", None)
    return backend


@pytest.fixture
def fresh_backend(monkeypatch, tmp_path):
    backend = _new_backend(monkeypatch, tmp_path)
    path = tmp_path / "secrets.json"
    assert path.exists() is False
    assert backend.list_keys() == []
    assert backend.is_initialized() is False
    assert backend.is_unlocked() is False
    yield backend, path
    at.reset()


def _secrets_client():
    flask_app = Flask("row402-secrets")
    flask_app.register_blueprint(app_secrets.secrets_bp)
    return flask_app.test_client()


@contextmanager
def _memory_db():
    connection = sqlite3.connect(":memory:")
    try:
        yield connection
    finally:
        connection.close()


def _health_client(monkeypatch, sites):
    monkeypatch.setattr(app_health, "db_conn", _memory_db)
    monkeypatch.setattr(app_health, "_app_runners", lambda: {})
    monkeypatch.setattr(app_health, "_app_s_cfg", lambda: sites)
    monkeypatch.setattr(app_health, "_app__app_boot_time", lambda: 0.0)
    monkeypatch.setattr(app_health, "app_test_mode", lambda: False)
    monkeypatch.setattr(
        app_health,
        "build_identity",
        lambda _install_dir: {
            "sha": None,
            "built_at": None,
            "source": "unknown",
        },
    )
    flask_app = Flask("row402-health")
    flask_app.register_blueprint(app_health.health_bp)
    return flask_app.test_client()


def test_first_unlock_persists_commitment_across_lock_and_restart(
    fresh_backend, monkeypatch, tmp_path
):
    backend, path = fresh_backend

    assert backend.unlock(_MASTER) is True
    assert backend.is_initialized() is True
    assert backend.is_unlocked() is True
    assert backend.list_keys() == []

    blob = json.loads(path.read_text(encoding="utf-8"))
    assert blob["ciphertexts"] == {}
    assert sorted(blob["verifier"]) == ["ct", "nonce"]

    backend.lock()
    assert backend.is_initialized() is True
    assert backend.is_unlocked() is False

    reopened = _reopen_backend(monkeypatch, tmp_path)
    assert reopened.is_initialized() is True
    assert reopened.is_unlocked() is False
    assert reopened.list_keys() == []
    assert reopened.unlock(_WRONG) is False
    assert reopened.is_unlocked() is False
    assert reopened.unlock(_MASTER) is True
    assert reopened.is_unlocked() is True


def test_first_unlock_persist_failure_raises_and_rolls_back(
    fresh_backend, monkeypatch
):
    backend, path = fresh_backend
    tmp = path.with_suffix(".json.tmp")
    replace_calls = []
    real_replace = Path.replace

    def fail_publish(self, target):
        if self == tmp:
            replace_calls.append((self, Path(target)))
            raise OSError("synthetic verifier publish failure")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_publish)
    with pytest.raises(ss.SecretsPersistError, match="initial"):
        backend.unlock(_MASTER)

    assert len(replace_calls) == 1
    assert backend.is_unlocked() is False
    assert backend.is_initialized() is False
    assert "verifier" not in backend._data
    assert path.exists() is False
    assert tmp.exists() is False


def test_unlock_endpoint_maps_initialization_persist_failure_to_distinct_500(
    fresh_backend, monkeypatch
):
    backend, path = fresh_backend
    tmp = path.with_suffix(".json.tmp")
    real_replace = Path.replace

    def fail_publish(self, target):
        if self == tmp:
            raise OSError("synthetic verifier publish failure")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_publish)
    response = _secrets_client().post(
        "/api/secrets/unlock", json={"password": _MASTER}
    )
    body = response.get_json()

    assert response.status_code == 500, body
    assert body["ok"] is False
    assert body["state"] == "uninitialized"
    assert body["is_initialized"] is False
    assert body["is_unlocked"] is False
    assert "not committed" in body["error"]
    assert backend.is_initialized() is False
    assert backend.is_unlocked() is False
    assert path.exists() is False
    assert tmp.exists() is False


def test_unlock_endpoint_distinguishes_first_initialization_from_later_unlock(
    fresh_backend,
):
    backend, _path = fresh_backend
    client = _secrets_client()

    first = client.post("/api/secrets/unlock", json={"password": _MASTER})
    first_body = first.get_json()
    assert first.status_code == 200, first_body
    assert first_body == {
        "ok": True,
        "state": "initialized",
        "initialized_now": True,
        "is_initialized": True,
        "is_unlocked": True,
    }

    backend.lock()
    at.reset()
    later = client.post("/api/secrets/unlock", json={"password": _MASTER})
    later_body = later.get_json()
    assert later.status_code == 200, later_body
    assert later_body == {
        "ok": True,
        "state": "unlocked",
        "initialized_now": False,
        "is_initialized": True,
        "is_unlocked": True,
    }


def test_ciphertext_only_legacy_vault_still_unlocks(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    seed._data.pop("verifier", None)
    assert seed._save() is True
    seed.lock()

    legacy_blob = json.loads(
        (tmp_path / "secrets.json").read_text(encoding="utf-8")
    )
    assert "verifier" not in legacy_blob
    assert list(legacy_blob["ciphertexts"]) == [_KEY]

    reopened = _reopen_backend(monkeypatch, tmp_path)
    assert reopened.is_initialized() is True
    assert reopened.is_unlocked() is False
    assert reopened.unlock(_WRONG) is False
    assert reopened.unlock(_MASTER) is True
    assert reopened.get(_KEY) == _VALUE


def test_legacy_unlock_uses_an_intact_ciphertext_not_only_the_first(
    monkeypatch, tmp_path
):
    first_key = "bulkdl-site-row402-first-corrupt"
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(first_key, "row402-first-value")
    seed.set(_KEY, _VALUE)
    seed._data.pop("verifier", None)
    seed._data["ciphertexts"][first_key] = {"nonce": "AAAA", "ct": "AAAA"}
    assert list(seed._data["ciphertexts"]) == [first_key, _KEY]
    assert seed._save() is True
    seed.lock()

    reopened = _reopen_backend(monkeypatch, tmp_path)
    assert reopened.unlock(_MASTER) is True
    assert reopened.get(first_key) is None
    assert reopened.get(_KEY) == _VALUE
    assert sorted(reopened._data["verifier"]) == ["ct", "nonce"]


def test_legacy_unlock_backfills_verifier_before_last_secret_deletion(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    seed._data.pop("verifier", None)
    assert seed._save() is True
    seed.lock()

    legacy = _reopen_backend(monkeypatch, tmp_path)
    assert legacy.is_initialized() is True
    assert legacy.unlock(_MASTER) is True
    upgraded = json.loads(
        (tmp_path / "secrets.json").read_text(encoding="utf-8")
    )
    assert sorted(upgraded["verifier"]) == ["ct", "nonce"]

    assert legacy.delete(_KEY) is True
    legacy.lock()
    reopened = _reopen_backend(monkeypatch, tmp_path)
    assert reopened.list_keys() == []
    assert reopened.is_initialized() is True
    assert reopened.unlock(_WRONG) is False
    assert reopened.unlock(_MASTER) is True


def test_locked_legacy_vault_refuses_to_delete_its_last_commitment(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    seed._data.pop("verifier", None)
    assert seed._save() is True
    seed.lock()
    path = tmp_path / "secrets.json"
    legacy_disk = path.read_bytes()

    legacy = _reopen_backend(monkeypatch, tmp_path)
    assert legacy.is_initialized() is True
    assert legacy.is_unlocked() is False
    assert legacy.list_keys() == [_KEY]

    with pytest.raises(ss.SecretsUnlockRequiredError, match="unlock"):
        legacy.delete(_KEY)

    assert legacy.is_initialized() is True
    assert legacy.is_unlocked() is False
    assert legacy.list_keys() == [_KEY]
    assert path.read_bytes() == legacy_disk

    reopened = _reopen_backend(monkeypatch, tmp_path)
    assert reopened.is_initialized() is True
    assert reopened.list_keys() == [_KEY]
    assert reopened.unlock(_WRONG) is False
    assert reopened.unlock(_MASTER) is True
    assert reopened.delete(_KEY) is True
    assert reopened.is_initialized() is True


def test_delete_endpoint_preserves_locked_legacy_last_secret_and_reference(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    seed._data.pop("verifier", None)
    assert seed._save() is True
    seed.lock()
    path = tmp_path / "secrets.json"
    legacy_disk = path.read_bytes()

    legacy = _reopen_backend(monkeypatch, tmp_path)
    sites = {"row402": {"password": _REF}}
    save_calls = []
    monkeypatch.setattr(app_secrets, "_app_s_cfg", lambda: sites)
    monkeypatch.setattr(
        app_secrets,
        "_save_sites_config",
        lambda: save_calls.append("saved"),
    )

    response = _secrets_client().post(
        "/api/secrets/delete", json={"site_id": "row402"}
    )
    body = response.get_json()

    assert response.status_code == 409, body
    assert body["ok"] is False
    assert body["state"] == "locked"
    assert body["requires_unlock"] is True
    assert "unlock" in body["error"].lower()
    assert sites["row402"]["password"] == _REF
    assert save_calls == []
    assert legacy.is_initialized() is True
    assert legacy.is_unlocked() is False
    assert legacy.list_keys() == [_KEY]
    assert path.read_bytes() == legacy_disk


def test_legacy_verifier_upgrade_failure_keeps_original_vault_locked(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    seed._data.pop("verifier", None)
    assert seed._save() is True
    seed.lock()
    legacy_disk = (tmp_path / "secrets.json").read_bytes()

    legacy = _reopen_backend(monkeypatch, tmp_path)
    monkeypatch.setattr(legacy, "_save", lambda: False)
    with pytest.raises(ss.SecretsPersistError, match="upgrade"):
        legacy.unlock(_MASTER)

    assert legacy.is_unlocked() is False
    assert legacy.is_initialized() is True
    assert "verifier" not in legacy._data
    assert legacy.list_keys() == [_KEY]
    assert (tmp_path / "secrets.json").read_bytes() == legacy_disk


def test_unlock_endpoint_names_legacy_upgrade_persist_failure(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    seed._data.pop("verifier", None)
    assert seed._save() is True
    seed.lock()

    legacy = _reopen_backend(monkeypatch, tmp_path)
    monkeypatch.setattr(legacy, "_save", lambda: False)
    response = _secrets_client().post(
        "/api/secrets/unlock", json={"password": _MASTER}
    )
    body = response.get_json()

    assert response.status_code == 500, body
    assert body["ok"] is False
    assert body["state"] == "locked"
    assert body["is_initialized"] is True
    assert body["is_unlocked"] is False
    assert "existing vault remains locked and unchanged" in body["error"]
    assert legacy.list_keys() == [_KEY]
    assert "verifier" not in legacy._data


def test_valid_ciphertext_repairs_a_damaged_verifier(monkeypatch, tmp_path):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    damaged = {"nonce": "AAAA", "ct": "AAAA"}
    seed._data["verifier"] = damaged
    assert seed._save() is True
    seed.lock()

    reopened = _reopen_backend(monkeypatch, tmp_path)
    assert reopened.unlock(_WRONG) is False
    assert reopened.unlock(_MASTER) is True
    assert reopened.get(_KEY) == _VALUE
    repaired = json.loads(
        (tmp_path / "secrets.json").read_text(encoding="utf-8")
    )
    assert repaired["verifier"] != damaged
    assert sorted(repaired["verifier"]) == ["ct", "nonce"]


def test_damaged_verifier_repair_failure_restores_original_and_stays_locked(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    damaged = {"nonce": "AAAA", "ct": "AAAA"}
    seed._data["verifier"] = damaged
    assert seed._save() is True
    seed.lock()
    damaged_disk = (tmp_path / "secrets.json").read_bytes()

    reopened = _reopen_backend(monkeypatch, tmp_path)
    monkeypatch.setattr(reopened, "_save", lambda: False)
    with pytest.raises(ss.SecretsPersistError, match="upgrade"):
        reopened.unlock(_MASTER)

    assert reopened.is_unlocked() is False
    assert reopened.is_initialized() is True
    assert reopened._data["verifier"] == damaged
    assert reopened.list_keys() == [_KEY]
    assert (tmp_path / "secrets.json").read_bytes() == damaged_disk


def test_zero_secret_password_rotation_rotates_the_verifier(
    fresh_backend,
):
    backend, path = fresh_backend
    assert backend.unlock(_MASTER) is True
    before = json.loads(path.read_text(encoding="utf-8"))
    assert backend.list_keys() == []

    assert backend.change_password(_MASTER, _NEW_MASTER) is True
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["ciphertexts"] == {}
    assert after["salt"] != before["salt"]
    assert after["verifier"] != before["verifier"]

    backend.lock()
    assert backend.unlock(_MASTER) is False
    assert backend.unlock(_NEW_MASTER) is True
    assert backend.is_initialized() is True


def test_rotation_persist_failure_restores_salt_verifier_key_and_disk(
    fresh_backend, monkeypatch
):
    backend, path = fresh_backend
    assert backend.unlock(_MASTER) is True
    old_data = json.loads(json.dumps(backend._data))
    old_disk = path.read_bytes()
    old_key = backend._key
    monkeypatch.setattr(backend, "_save", lambda: False)

    with pytest.raises(ss.SecretsPersistError, match="rolled back"):
        backend.change_password(_MASTER, _NEW_MASTER)

    assert backend._data == old_data
    assert backend._key == old_key
    assert path.read_bytes() == old_disk
    backend.lock()
    assert backend.unlock(_NEW_MASTER) is False
    assert backend.unlock(_MASTER) is True


def test_change_password_endpoint_maps_legacy_preflight_upgrade_failure(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    seed._data.pop("verifier", None)
    assert seed._save() is True
    seed.lock()
    path = tmp_path / "secrets.json"
    legacy_disk = path.read_bytes()

    legacy = _reopen_backend(monkeypatch, tmp_path)
    monkeypatch.setattr(legacy, "_save", lambda: False)
    response = _secrets_client().post(
        "/api/secrets/change_password",
        json={"old_password": _MASTER, "new_password": _NEW_MASTER},
    )
    body = response.get_json()

    assert response.status_code == 500, body
    assert body["ok"] is False
    assert "rotation failed during persist" in body["error"]
    assert "old password is still in effect" in body["error"].lower()
    assert "incorrect" not in body["error"].lower()
    assert legacy.is_initialized() is True
    assert legacy.is_unlocked() is False
    assert legacy.list_keys() == [_KEY]
    assert "verifier" not in legacy._data
    assert path.read_bytes() == legacy_disk


def test_deleting_last_secret_does_not_uninitialize_the_vault(
    fresh_backend, monkeypatch, tmp_path
):
    backend, _path = fresh_backend
    assert backend.unlock(_MASTER) is True
    backend.set(_KEY, _VALUE)
    assert backend.delete(_KEY) is True
    assert backend.list_keys() == []
    assert backend.is_initialized() is True

    backend.lock()
    assert backend.unlock(_WRONG) is False
    assert backend.unlock(_MASTER) is True

    backend.lock()
    reopened = _reopen_backend(monkeypatch, tmp_path)
    assert reopened.list_keys() == []
    assert reopened.is_initialized() is True
    assert reopened.unlock(_WRONG) is False
    assert reopened.unlock(_MASTER) is True


def test_one_reference_over_uninitialized_empty_vault_is_not_missing(
    fresh_backend, monkeypatch
):
    backend, _path = fresh_backend
    sites = {"row402": {"password": _REF}}
    assert ss.password_reference_keys(sites) == [_KEY]
    assert backend.list_keys() == []
    assert backend.is_initialized() is False
    assert backend.is_unlocked() is False

    measured = app_health.credential_health(sites)
    assert measured["state"] == "uninitialized"
    assert measured["ok"] is False
    assert measured["reference_count"] == 1
    assert measured["stored_count"] == 0
    assert measured["is_initialized"] is False
    assert measured["is_unlocked"] is False

    response = _health_client(monkeypatch, sites).get("/api/health")
    body = response.get_json()
    assert response.status_code == 503, body
    assert body["degraded"] == "credential_vault_uninitialized"
    assert body["credentials"]["state"] == "uninitialized"


def test_four_vault_states_are_pairwise_exact(monkeypatch, tmp_path):
    produced = {}
    for label in ("uninitialized", "locked", "unlocked", "zero"):
        root = tmp_path / label
        root.mkdir()
        backend = _new_backend(monkeypatch, root)
        sites = {"row402": {"password": _REF}}
        if label == "locked":
            assert backend.unlock(_MASTER) is True
            backend.set(_KEY, _VALUE)
            backend.lock()
        elif label == "unlocked":
            assert backend.unlock(_MASTER) is True
            backend.set(_KEY, _VALUE)
        elif label == "zero":
            assert backend.unlock(_MASTER) is True

        produced[label] = app_health.credential_health(sites)

    assert {label: health["state"] for label, health in produced.items()} == {
        "uninitialized": "uninitialized",
        "locked": "locked",
        "unlocked": "unlocked",
        "zero": "unlocked_zero_resolved",
    }
    assert len({health["state"] for health in produced.values()}) == 4
    assert len({tuple(sorted(health)) for health in produced.values()}) == 1

    locked = produced["locked"]
    assert locked["is_initialized"] is True
    assert locked["is_unlocked"] is False
    assert locked["reference_count"] == 1
    assert locked["stored_count"] == 1
    assert locked["missing_count"] == 0
    assert locked["unavailable_count"] == 1

    zero = produced["zero"]
    assert zero["is_initialized"] is True
    assert zero["is_unlocked"] is True
    assert zero["reference_count"] == 1
    assert zero["stored_count"] == 0
    assert zero["resolved_count"] == 0
    assert zero["state"] == "unlocked_zero_resolved"
    assert zero["ok"] is False


def test_unlocked_zero_resolved_health_is_a_distinct_degradation(
    fresh_backend, monkeypatch
):
    backend, _path = fresh_backend
    assert backend.unlock(_MASTER) is True
    sites = {"row402": {"password": _REF}}
    assert backend.list_keys() == []
    assert ss.password_reference_keys(sites) == [_KEY]

    response = _health_client(monkeypatch, sites).get("/api/health")
    body = response.get_json()
    assert response.status_code == 503, body
    assert body["degraded"] == "credential_unlocked_zero_resolved"
    assert body["credentials"]["state"] == "unlocked_zero_resolved"
    assert body["credentials"]["is_initialized"] is True
    assert body["credentials"]["is_unlocked"] is True


class _IncoherentBackend:
    name = "master_password"

    def is_initialized(self):
        return False

    def is_unlocked(self):
        return True

    def list_keys(self):
        return []

    def get(self, _key):  # pragma: no cover - must not be reached
        raise AssertionError("health tried to resolve an incoherent vault")


def test_health_refuses_unlocked_over_uninitialized_pair(monkeypatch):
    monkeypatch.setattr(ss, "get_backend", lambda: _IncoherentBackend())
    measured = app_health.credential_health(
        {"row402": {"password": _REF}}
    )
    assert measured["state"] == "unknown"
    assert measured["ok"] is False
    assert measured["is_initialized"] is False
    assert measured["is_unlocked"] is True
    assert measured["resolved_count"] is None
    assert measured["stored_count"] is None
