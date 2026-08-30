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
import subprocess
import threading

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
_ROLLBACK_BASE = "44c8701c30b1ec2347aea712bf57cb620140818e"


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


def _strip_row402_commitments(backend):
    """Turn a candidate-written vault into the ciphertext-only legacy shape."""
    authority = backend._COMMITMENT_AUTHORITY_FIELD
    ciphertexts = backend._data["ciphertexts"]
    commitment = backend._data.get(authority)

    assert "verifier" in backend._data
    assert isinstance(commitment, str)
    assert commitment in ciphertexts

    backend._data.pop("verifier")
    backend._data.pop(authority)
    ciphertexts.pop(commitment)

    assert "verifier" not in backend._data
    assert authority not in backend._data
    assert commitment not in ciphertexts


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
    assert list(blob["ciphertexts"]) == [backend._ROLLBACK_COMMITMENT_KEY]
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


def test_first_unlock_is_rejected_by_base44_when_password_is_wrong(
    fresh_backend,
):
    """The durable first-use commitment must survive a code rollback.

    This executes the exact ``MasterPasswordBackend`` implementation from the
    release immediately below row 402.  That implementation ignores the new
    top-level verifier and authenticates only against the first ciphertext, so
    candidate-written data must carry a backward-readable reserved commitment.
    """
    backend, path = fresh_backend
    assert backend.unlock(_MASTER) is True
    blob = json.loads(path.read_text(encoding="utf-8"))
    commitment_key = backend._ROLLBACK_COMMITMENT_KEY
    assert list(blob["ciphertexts"])[0] == commitment_key
    assert backend.list_keys() == []
    assert backend.get(commitment_key) is None
    assert backend.delete(commitment_key) is False
    backend.set(commitment_key, "row402-user-value-at-internal-name")
    dynamic_commitment_key = backend._data[
        backend._COMMITMENT_AUTHORITY_FIELD
    ]
    assert dynamic_commitment_key != commitment_key
    assert list(backend._data["ciphertexts"])[0] == dynamic_commitment_key
    assert backend.list_keys() == [commitment_key]
    assert backend.get(commitment_key) == "row402-user-value-at-internal-name"
    assert backend.get(dynamic_commitment_key) is None
    assert backend.delete(dynamic_commitment_key) is False

    source = subprocess.run(
        [
            "git",
            "show",
            f"{_ROLLBACK_BASE}:bulk_downloader/secrets_store.py",
        ],
        check=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
    ).stdout
    namespace = {
        "__name__": "row402_base44_secrets_store",
        "__file__": "<base44>/bulk_downloader/secrets_store.py",
    }
    exec(compile(source, namespace["__file__"], "exec"), namespace)
    namespace["SECRETS_FILE"] = path
    namespace["SECRETS_META_FILE"] = path.with_name("secrets_meta.json")
    rollback_backend = namespace["MasterPasswordBackend"]()

    assert rollback_backend.unlock(_WRONG) is False
    assert rollback_backend.is_unlocked() is False
    assert rollback_backend.unlock(_MASTER) is True
    assert (
        rollback_backend.get(commitment_key)
        == "row402-user-value-at-internal-name"
    )


@pytest.mark.parametrize("failure_mode", ["seal", "save_false", "save_raise"])
def test_commitment_relocation_failure_is_atomic(
    fresh_backend,
    monkeypatch,
    failure_mode,
):
    backend, path = fresh_backend
    assert backend.unlock(_MASTER) is True
    authority_key = backend._data[backend._COMMITMENT_AUTHORITY_FIELD]
    before_memory = json.loads(json.dumps(backend._data))
    before_disk = path.read_bytes()

    if failure_mode == "seal":
        def fail_seal(_key):
            raise RuntimeError("synthetic relocation seal failure")

        monkeypatch.setattr(backend, "_seal_rollback_commitment", fail_seal)
        error = RuntimeError
    elif failure_mode == "save_false":
        monkeypatch.setattr(backend, "_save", lambda: False)
        error = ss.SecretsPersistError
    else:
        def fail_save():
            raise OSError("synthetic relocation save exception")

        monkeypatch.setattr(backend, "_save", fail_save)
        error = OSError

    with pytest.raises(error):
        backend.set(authority_key, "row402-colliding-user-value")

    assert backend._data == before_memory
    assert path.read_bytes() == before_disk


def test_repeated_authority_name_collisions_relocate_without_reserving_names(
    fresh_backend,
    monkeypatch,
    tmp_path,
):
    backend, _path = fresh_backend
    assert backend.unlock(_MASTER) is True
    expected = {}
    for index in range(3):
        collided_name = backend._data[backend._COMMITMENT_AUTHORITY_FIELD]
        value = f"row402-relocated-user-value-{index}"
        backend.set(collided_name, value)
        expected[collided_name] = value
        assert backend._data[backend._COMMITMENT_AUTHORITY_FIELD] not in expected
        assert list(backend._data["ciphertexts"])[0] == (
            backend._data[backend._COMMITMENT_AUTHORITY_FIELD]
        )

    backend.lock()
    reopened = _reopen_backend(monkeypatch, tmp_path)
    assert reopened.unlock(_MASTER) is True
    assert reopened.list_keys() == sorted(expected)
    assert {name: reopened.get(name) for name in expected} == expected
    deleted_name = next(iter(expected))
    assert reopened.delete(deleted_name) is True
    assert reopened.get(deleted_name) is None
    assert reopened.list_keys() == sorted(set(expected) - {deleted_name})


@pytest.mark.parametrize("relocated_authority", [False, True])
def test_base44_password_rotation_is_reconciled_by_authenticated_commitment(
    fresh_backend, monkeypatch, tmp_path, relocated_authority
):
    backend, path = fresh_backend
    assert backend.unlock(_MASTER) is True
    preferred_key = backend._ROLLBACK_COMMITMENT_KEY
    if relocated_authority:
        backend.set(preferred_key, "row402-dynamic-authority-user-value")
    backend.set(_KEY, _VALUE)
    before = json.loads(path.read_text(encoding="utf-8"))
    commitment_key = before[backend._COMMITMENT_AUTHORITY_FIELD]
    assert commitment_key in before["ciphertexts"]
    assert list(before["ciphertexts"])[0] == commitment_key
    assert (commitment_key != preferred_key) is relocated_authority

    source = subprocess.run(
        [
            "git",
            "show",
            f"{_ROLLBACK_BASE}:bulk_downloader/secrets_store.py",
        ],
        check=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
    ).stdout
    namespace = {
        "__name__": "row402_base44_rotation_store",
        "__file__": "<base44>/bulk_downloader/secrets_store.py",
    }
    exec(compile(source, namespace["__file__"], "exec"), namespace)
    namespace["SECRETS_FILE"] = path
    namespace["SECRETS_META_FILE"] = path.with_name("secrets_meta.json")
    rollback_backend = namespace["MasterPasswordBackend"]()
    assert rollback_backend.unlock(_MASTER) is True
    assert rollback_backend.change_password(_MASTER, _NEW_MASTER) is True
    rollback_backend.lock()

    rolled_back = json.loads(path.read_text(encoding="utf-8"))
    assert rolled_back["verifier"] == before["verifier"]
    assert rolled_back["ciphertexts"][commitment_key] != (
        before["ciphertexts"][commitment_key]
    )
    assert rolled_back[backend._COMMITMENT_AUTHORITY_FIELD] == commitment_key

    reopened = _reopen_backend(monkeypatch, tmp_path)
    assert reopened.unlock(_NEW_MASTER) is True
    assert reopened.get(_KEY) == _VALUE
    if relocated_authority:
        assert (
            reopened.get(preferred_key)
            == "row402-dynamic-authority-user-value"
        )
    repaired = json.loads(path.read_text(encoding="utf-8"))
    assert repaired["verifier"] != rolled_back["verifier"]
    assert repaired[backend._COMMITMENT_AUTHORITY_FIELD] == commitment_key
    assert list(repaired["ciphertexts"])[0] == commitment_key

    reopened.lock()
    assert reopened.unlock(_MASTER) is False
    assert reopened.unlock(_NEW_MASTER) is True


def test_unlock_endpoint_enforces_first_use_length_but_legacy_short_unlocks(
    fresh_backend, monkeypatch, tmp_path
):
    backend, path = fresh_backend
    client = _secrets_client()

    too_short = client.post(
        "/api/secrets/unlock", json={"password": "1234567"}
    )
    assert too_short.status_code == 400, too_short.get_json()
    assert "at least 8" in too_short.get_json()["error"]
    assert backend.is_initialized() is False
    assert backend.is_unlocked() is False
    assert path.exists() is False

    # A verifier-less pre-row402 vault may legitimately use a short password.
    short_password = "short"
    assert backend.unlock(short_password) is True
    backend.set(_KEY, _VALUE)
    _strip_row402_commitments(backend)
    assert backend._save() is True
    backend.lock()

    legacy = _reopen_backend(monkeypatch, tmp_path)
    unlocked = client.post(
        "/api/secrets/unlock", json={"password": short_password}
    )
    assert unlocked.status_code == 200, unlocked.get_json()
    assert unlocked.get_json()["state"] == "unlocked"
    assert legacy.get(_KEY) == _VALUE


def test_concurrent_first_use_reports_exactly_one_initializer(
    fresh_backend, monkeypatch
):
    backend, _path = fresh_backend
    barrier = threading.Barrier(2)

    def release_together(_label):
        barrier.wait(timeout=5)
        return True, 0.0

    monkeypatch.setattr(at, "check", release_together)
    responses = []
    response_lock = threading.Lock()

    def request_unlock():
        response = _secrets_client().post(
            "/api/secrets/unlock", json={"password": _MASTER}
        )
        with response_lock:
            responses.append((response.status_code, response.get_json()))

    threads = [threading.Thread(target=request_unlock) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert [status for status, _body in responses] == [200, 200]
    bodies = [body for _status, body in responses]
    assert sum(body["initialized_now"] is True for body in bodies) == 1
    assert sorted(body["state"] for body in bodies) == [
        "initialized",
        "unlocked",
    ]
    assert all(body["is_initialized"] is True for body in bodies)
    assert all(body["is_unlocked"] is True for body in bodies)
    assert backend.is_initialized() is True
    assert backend.is_unlocked() is True


def test_first_use_persist_error_response_uses_atomic_failure_snapshot(
    monkeypatch,
):
    class ConcurrentlyInitializedBackend:
        name = "master_password"

        def unlock_with_status(self, _password, **_kwargs):
            error = ss.SecretsPersistError("synthetic first-use failure")
            error.vault_status = {
                "is_initialized": False,
                "is_unlocked": False,
            }
            # Model another serialized request succeeding immediately after
            # this request's failed attempt but before its response is built.
            self.initialized = True
            self.unlocked = True
            raise error

        def unlock(self, _password):  # pragma: no cover - status API is used
            raise AssertionError("non-atomic unlock path used")

        def is_initialized(self):
            raise AssertionError("response re-read initialized outside lock")

        def is_unlocked(self):
            raise AssertionError("response re-read unlocked outside lock")

    backend = ConcurrentlyInitializedBackend()
    monkeypatch.setattr(ss, "get_backend", lambda: backend)
    at.reset()

    response = _secrets_client().post(
        "/api/secrets/unlock", json={"password": _MASTER}
    )
    body = response.get_json()

    assert response.status_code == 500, body
    assert body["state"] == "uninitialized"
    assert body["is_initialized"] is False
    assert body["is_unlocked"] is False
    assert "not committed" in body["error"]


@pytest.mark.parametrize("atomic_status", [False, True])
def test_unlock_endpoint_refuses_incoherent_success_status(
    monkeypatch,
    atomic_status,
):
    class IncoherentBackend:
        name = "master_password"

        def __init__(self):
            self.unlocked = False
            self.lock_calls = 0
            if not atomic_status:
                self.unlock_with_status = None

        def unlock(self, _password):
            self.unlocked = True
            return True

        def unlock_with_status(self, _password, **_kwargs):
            self.unlocked = True
            return {
                "unlocked": True,
                "initialized_now": False,
                "is_initialized": False,
                "is_unlocked": True,
            }

        def is_initialized(self):
            return False

        def is_unlocked(self):
            return self.unlocked

        def lock(self):
            self.lock_calls += 1
            self.unlocked = False

    backend = IncoherentBackend()
    monkeypatch.setattr(ss, "get_backend", lambda: backend)
    at.reset()

    response = _secrets_client().post(
        "/api/secrets/unlock", json={"password": _MASTER}
    )
    body = response.get_json()

    assert response.status_code == 500, body
    assert body["ok"] is False
    assert body["state"] == "unknown"
    assert "incoherent" in body["error"].lower()
    assert "is_initialized" not in body
    assert "is_unlocked" not in body
    assert backend.lock_calls == 1
    assert backend.is_unlocked() is False


def test_compat_unlock_without_status_method_maps_false_to_locked_401(
    monkeypatch,
):
    class LegacyBackendWithoutLockProbe:
        name = "master_password"

        def unlock(self, _password):
            return False

        def is_initialized(self):
            return True

    backend = LegacyBackendWithoutLockProbe()
    monkeypatch.setattr(ss, "get_backend", lambda: backend)
    recorded = []
    monkeypatch.setattr(
        at, "record_success", lambda label: recorded.append(("success", label))
    )
    monkeypatch.setattr(
        at, "record_failure", lambda label: recorded.append(("failure", label))
    )
    at.reset()

    response = _secrets_client().post(
        "/api/secrets/unlock", json={"password": _WRONG}
    )
    body = response.get_json()

    assert response.status_code == 401, body
    assert body == {
        "ok": False,
        "state": "locked",
        "error": "incorrect password",
    }
    assert recorded == [("failure", at.LABEL_MASTER_PASSWORD)]


@pytest.mark.parametrize(
    "unlock_status",
    [
        None,
        {},
        {
            "unlocked": "false",
            "initialized_now": "false",
            "is_initialized": "false",
            "is_unlocked": "false",
        },
    ],
)
def test_unlock_endpoint_relocks_after_invalid_atomic_status(
    monkeypatch,
    unlock_status,
):
    class NonDictStatusBackend:
        name = "master_password"

        def __init__(self):
            self.unlocked = False
            self.lock_calls = 0

        def unlock_with_status(self, _password, **_kwargs):
            self.unlocked = True
            return unlock_status

        def unlock(self, _password):  # pragma: no cover
            raise AssertionError("non-atomic unlock path used")

        def lock(self):
            self.lock_calls += 1
            self.unlocked = False

    backend = NonDictStatusBackend()
    monkeypatch.setattr(ss, "get_backend", lambda: backend)
    recorded = []
    monkeypatch.setattr(
        at, "record_success", lambda label: recorded.append(("success", label))
    )
    monkeypatch.setattr(
        at, "record_failure", lambda label: recorded.append(("failure", label))
    )
    at.reset()

    response = _secrets_client().post(
        "/api/secrets/unlock", json={"password": _MASTER}
    )
    body = response.get_json()
    assert response.status_code == 500, body
    assert body["ok"] is False
    assert body["state"] == "unknown"
    assert "incoherent" in body["error"].lower()
    assert backend.lock_calls == 1
    assert backend.unlocked is False
    assert recorded == []


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


def test_first_unlock_sealing_failure_leaves_no_partial_commitment(
    fresh_backend,
    monkeypatch,
):
    backend, path = fresh_backend
    before = json.loads(json.dumps(backend._data))

    def fail_rollback_seal(_key):
        raise RuntimeError("synthetic rollback commitment seal failure")

    monkeypatch.setattr(
        backend, "_seal_rollback_commitment", fail_rollback_seal
    )
    with pytest.raises(RuntimeError, match="synthetic rollback"):
        backend.unlock(_MASTER)

    assert backend._data == before
    assert backend.is_initialized() is False
    assert backend.is_unlocked() is False
    assert path.exists() is False


@pytest.mark.parametrize("malformed_ciphertexts", [[], ["not-an-envelope"], None])
def test_malformed_ciphertext_container_never_becomes_first_use(
    fresh_backend, monkeypatch, tmp_path, malformed_ciphertexts
):
    backend, path = fresh_backend
    backend._data["ciphertexts"] = malformed_ciphertexts
    assert backend._save() is True
    original_disk = path.read_bytes()
    original_memory = json.loads(json.dumps(backend._data))

    reopened = _reopen_backend(monkeypatch, tmp_path)
    assert reopened.is_initialized() is True
    with pytest.raises(ss.SecretsIntegrityError, match="ciphertexts"):
        reopened.unlock(_MASTER)

    assert reopened.is_unlocked() is False
    assert reopened.is_initialized() is True
    assert reopened._data == original_memory
    assert path.read_bytes() == original_disk


def test_authority_without_authenticated_material_never_becomes_first_use(
    fresh_backend, monkeypatch, tmp_path
):
    backend, path = fresh_backend
    backend._data[
        backend._COMMITMENT_AUTHORITY_FIELD
    ] = backend._ROLLBACK_COMMITMENT_KEY
    assert backend._save() is True
    original_disk = path.read_bytes()
    original_memory = json.loads(json.dumps(backend._data))

    reopened = _reopen_backend(monkeypatch, tmp_path)
    assert reopened.is_initialized() is True
    with pytest.raises(ss.SecretsIntegrityError, match="authoritative"):
        reopened.unlock(_MASTER)

    assert reopened.is_unlocked() is False
    assert reopened.is_initialized() is True
    assert reopened._data == original_memory
    assert path.read_bytes() == original_disk


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
    _strip_row402_commitments(seed)
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


@pytest.mark.parametrize("with_other_credential", [False, True])
def test_legacy_reserved_name_collision_survives_upgrade_and_rotation(
    monkeypatch,
    tmp_path,
    with_other_credential,
):
    """A pre-row402 user key may equal the candidate's internal key name."""
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    _strip_row402_commitments(seed)
    collision_key = seed._ROLLBACK_COMMITMENT_KEY
    seed._data["ciphertexts"][collision_key] = (
        seed._data["ciphertexts"].pop(_KEY)
    )
    if with_other_credential:
        seed.set(_KEY, "row402-second-legacy-value")
    assert seed._save() is True
    seed.lock()

    path = tmp_path / "secrets.json"
    original_disk = path.read_bytes()
    expected_user_keys = [collision_key]
    if with_other_credential:
        expected_user_keys.append(_KEY)
    expected_user_keys.sort()

    legacy = _reopen_backend(monkeypatch, tmp_path)
    assert legacy.list_keys() == expected_user_keys
    assert legacy.unlock(_WRONG) is False
    assert path.read_bytes() == original_disk
    assert legacy.unlock(_MASTER) is True
    assert legacy.get(collision_key) == _VALUE
    if with_other_credential:
        assert legacy.get(_KEY) == "row402-second-legacy-value"

    commitment_key = legacy._data[legacy._COMMITMENT_AUTHORITY_FIELD]
    assert commitment_key != collision_key
    assert list(legacy._data["ciphertexts"])[0] == commitment_key
    assert legacy.list_keys() == expected_user_keys

    legacy.lock()
    restarted = _reopen_backend(monkeypatch, tmp_path)
    assert restarted.unlock(_WRONG) is False
    assert restarted.unlock(_MASTER) is True
    assert restarted.get(collision_key) == _VALUE
    assert restarted.change_password(_MASTER, _NEW_MASTER) is True
    restarted.lock()
    assert restarted.unlock(_MASTER) is False
    assert restarted.unlock(_NEW_MASTER) is True
    assert restarted.get(collision_key) == _VALUE
    assert restarted.list_keys() == expected_user_keys


@pytest.mark.parametrize("with_other_credential", [False, True])
def test_legacy_collision_value_equal_to_public_sentinel_is_not_internal(
    monkeypatch,
    tmp_path,
    with_other_credential,
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    sentinel = seed._ROLLBACK_COMMITMENT_PLAINTEXT.decode("utf-8")
    seed.set(_KEY, sentinel)
    _strip_row402_commitments(seed)
    collision_key = seed._ROLLBACK_COMMITMENT_KEY
    seed._data["ciphertexts"][collision_key] = (
        seed._data["ciphertexts"].pop(_KEY)
    )
    if with_other_credential:
        # Keep synthetic credentials deliberately low-entropy: Gitleaks scans
        # every commit in the PR range, not only the final tree.
        seed.set(_KEY, "test")
    assert seed._save() is True
    seed.lock()

    legacy = _reopen_backend(monkeypatch, tmp_path)
    assert legacy.unlock(_MASTER) is True
    assert legacy._data[legacy._COMMITMENT_AUTHORITY_FIELD] != collision_key
    assert legacy.get(collision_key) == sentinel
    expected = [collision_key]
    if with_other_credential:
        expected.append(_KEY)
        assert legacy.get(_KEY) == "test"
    assert legacy.list_keys() == sorted(expected)


def test_authority_marker_rejects_an_ordinary_sentinel_valued_user_key(
    monkeypatch,
    tmp_path,
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    sentinel = seed._ROLLBACK_COMMITMENT_PLAINTEXT.decode("utf-8")
    seed.set(_KEY, sentinel)
    assert not seed._is_rollback_commitment_name(_KEY)
    seed._data[seed._COMMITMENT_AUTHORITY_FIELD] = _KEY
    assert seed._save() is True
    seed.lock()

    path = tmp_path / "secrets.json"
    original_disk = path.read_bytes()
    reopened = _reopen_backend(monkeypatch, tmp_path)
    original_memory = json.loads(json.dumps(reopened._data))
    with pytest.raises(
        ss.SecretsIntegrityError, match="commitment marker is invalid"
    ):
        reopened.unlock(_MASTER)

    assert reopened.is_unlocked() is False
    assert reopened._data == original_memory
    assert path.read_bytes() == original_disk


def test_direct_rotation_of_unupgraded_legacy_collision_preserves_user_data(
    monkeypatch,
    tmp_path,
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    _strip_row402_commitments(seed)
    collision_key = seed._ROLLBACK_COMMITMENT_KEY
    seed._data["ciphertexts"][collision_key] = (
        seed._data["ciphertexts"].pop(_KEY)
    )
    assert seed._save() is True
    seed.lock()

    legacy = _reopen_backend(monkeypatch, tmp_path)
    assert legacy.change_password(_MASTER, _NEW_MASTER) is True
    assert legacy._data[legacy._COMMITMENT_AUTHORITY_FIELD] != collision_key
    legacy.lock()
    assert legacy.unlock(_MASTER) is False
    assert legacy.unlock(_NEW_MASTER) is True
    assert legacy.get(collision_key) == _VALUE
    assert legacy.list_keys() == [collision_key]


def test_legacy_unlock_uses_an_intact_ciphertext_not_only_the_first(
    monkeypatch, tmp_path
):
    first_key = "bulkdl-site-row402-first-corrupt"
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(first_key, "row402-first-value")
    seed.set(_KEY, _VALUE)
    _strip_row402_commitments(seed)
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
    _strip_row402_commitments(seed)
    assert seed._save() is True
    seed.lock()

    legacy = _reopen_backend(monkeypatch, tmp_path)
    assert legacy.is_initialized() is True
    assert legacy.unlock(_MASTER) is True
    upgraded = json.loads(
        (tmp_path / "secrets.json").read_text(encoding="utf-8")
    )
    assert sorted(upgraded["verifier"]) == ["ct", "nonce"]
    assert list(upgraded["ciphertexts"])[0] == legacy._ROLLBACK_COMMITMENT_KEY
    assert legacy.list_keys() == [_KEY]

    assert legacy.delete(_KEY) is True
    legacy.lock()
    reopened = _reopen_backend(monkeypatch, tmp_path)
    assert reopened.list_keys() == []
    assert reopened.is_initialized() is True
    assert reopened.unlock(_WRONG) is False
    assert reopened.unlock(_MASTER) is True


def test_verifier_only_row402_vault_backfills_rollback_commitment(
    fresh_backend, monkeypatch, tmp_path
):
    backend, path = fresh_backend
    assert backend.unlock(_MASTER) is True
    commitment_key = backend._ROLLBACK_COMMITMENT_KEY
    backend._data["ciphertexts"].pop(commitment_key)
    backend._data.pop(backend._COMMITMENT_AUTHORITY_FIELD, None)
    assert backend._save() is True
    backend.lock()
    verifier_only = path.read_bytes()

    reopened = _reopen_backend(monkeypatch, tmp_path)
    assert reopened.unlock(_MASTER) is True
    upgraded = json.loads(path.read_text(encoding="utf-8"))
    assert upgraded["verifier"] == json.loads(
        verifier_only.decode("utf-8")
    )["verifier"]
    assert list(upgraded["ciphertexts"])[0] == commitment_key
    assert reopened.list_keys() == []

    reopened.lock()
    assert reopened.unlock(_WRONG) is False
    assert reopened.unlock(_MASTER) is True


def test_locked_legacy_vault_refuses_to_delete_its_last_commitment(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    _strip_row402_commitments(seed)
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
    _strip_row402_commitments(seed)
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


@pytest.mark.parametrize("verifier_shape", ["valid", "malformed"])
def test_delete_endpoint_requires_unlock_for_last_user_secret_with_any_verifier(
    fresh_backend, monkeypatch, verifier_shape
):
    backend, path = fresh_backend
    assert backend.unlock(_MASTER) is True
    backend.set(_KEY, _VALUE)
    if verifier_shape == "malformed":
        backend._data["verifier"] = {"nonce": "AAAA", "ct": "AAAA"}
        assert backend._save() is True
    backend.lock()
    sites = {"row402": {"password": _REF}}
    save_calls = []
    monkeypatch.setattr(app_secrets, "_app_s_cfg", lambda: sites)
    monkeypatch.setattr(
        app_secrets,
        "_save_sites_config",
        lambda: save_calls.append("saved"),
    )
    before_memory = json.loads(json.dumps(backend._data))
    before_disk = path.read_bytes()

    response = _secrets_client().post(
        "/api/secrets/delete", json={"site_id": "row402"}
    )
    body = response.get_json()

    assert response.status_code == 409, body
    assert body["ok"] is False
    assert body["state"] == "locked"
    assert body["requires_unlock"] is True
    assert backend._data == before_memory
    assert path.read_bytes() == before_disk
    assert backend.list_keys() == [_KEY]
    assert sites["row402"]["password"] == _REF
    assert save_calls == []


def test_locked_delete_preserves_last_well_formed_legacy_ciphertext_and_ref(
    monkeypatch,
    tmp_path,
):
    malformed_key = "bulkdl-row402-malformed"
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(malformed_key, "row402-malformed-value")
    seed.set(_KEY, _VALUE)
    _strip_row402_commitments(seed)
    seed._data["ciphertexts"][malformed_key] = {
        "nonce": "AAAA",
        "ct": "AAAA",
    }
    assert seed._save() is True
    seed.lock()
    path = tmp_path / "secrets.json"
    original_disk = path.read_bytes()

    legacy = _reopen_backend(monkeypatch, tmp_path)
    original_memory = json.loads(json.dumps(legacy._data))
    with pytest.raises(ss.SecretsUnlockRequiredError, match="unlock"):
        legacy.delete(_KEY)
    assert legacy._data == original_memory
    assert path.read_bytes() == original_disk
    assert legacy.list_keys() == sorted([_KEY, malformed_key])

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
    assert body["state"] == "locked"
    assert body["requires_unlock"] is True
    assert legacy._data == original_memory
    assert path.read_bytes() == original_disk
    assert sites["row402"]["password"] == _REF
    assert save_calls == []


def test_locked_delete_may_remove_malformed_legacy_entry_when_intact_remains(
    monkeypatch,
    tmp_path,
):
    malformed_key = "bulkdl-row402-malformed"
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(malformed_key, "row402-malformed-value")
    seed.set(_KEY, _VALUE)
    _strip_row402_commitments(seed)
    seed._data["ciphertexts"][malformed_key] = {
        "nonce": "AAAA",
        "ct": "AAAA",
    }
    assert seed._save() is True
    seed.lock()

    legacy = _reopen_backend(monkeypatch, tmp_path)
    assert legacy.delete(malformed_key) is True
    assert legacy.list_keys() == [_KEY]
    assert legacy.unlock(_MASTER) is True
    assert legacy.get(_KEY) == _VALUE


def test_legacy_verifier_upgrade_failure_keeps_original_vault_locked(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    _strip_row402_commitments(seed)
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


def test_legacy_commitment_sealing_failure_preserves_exact_vault(
    monkeypatch,
    tmp_path,
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    _strip_row402_commitments(seed)
    assert seed._save() is True
    seed.lock()
    path = tmp_path / "secrets.json"
    original_disk = path.read_bytes()

    legacy = _reopen_backend(monkeypatch, tmp_path)
    original_memory = json.loads(json.dumps(legacy._data))

    def fail_rollback_seal(_key):
        raise RuntimeError("synthetic rollback commitment seal failure")

    monkeypatch.setattr(
        legacy, "_seal_rollback_commitment", fail_rollback_seal
    )
    with pytest.raises(RuntimeError, match="synthetic rollback"):
        legacy.unlock(_MASTER)

    assert legacy._data == original_memory
    assert legacy.is_initialized() is True
    assert legacy.is_unlocked() is False
    assert path.read_bytes() == original_disk


def test_unlock_endpoint_names_legacy_upgrade_persist_failure(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    _strip_row402_commitments(seed)
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


@pytest.mark.parametrize("attempted_password", [_MASTER, _NEW_MASTER])
@pytest.mark.parametrize("with_matching_decoy", [False, True])
def test_present_verifier_disagreement_is_loud_and_never_rebound(
    monkeypatch, tmp_path, attempted_password, with_matching_decoy
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    seed._data["ciphertexts"].pop(seed._ROLLBACK_COMMITMENT_KEY)
    seed._data.pop(seed._COMMITMENT_AUTHORITY_FIELD, None)
    different_key = seed._derive_key(_NEW_MASTER)
    disagreeing = seed._seal_verifier(different_key)
    seed._data["verifier"] = disagreeing
    if with_matching_decoy:
        seed._data["ciphertexts"]["row402-verifier-matching-decoy"] = (
            seed._seal_verifier(different_key)
        )
    assert seed._save() is True
    seed.lock()
    original_disk = (tmp_path / "secrets.json").read_bytes()
    original_memory = json.loads(json.dumps(seed._data))

    reopened = _reopen_backend(monkeypatch, tmp_path)
    with pytest.raises(ss.SecretsIntegrityError, match="disagree"):
        reopened.unlock(attempted_password)

    assert reopened.is_unlocked() is False
    assert reopened._data == original_memory
    assert (tmp_path / "secrets.json").read_bytes() == original_disk


@pytest.mark.parametrize("commitment_shape", ["both", "commitment_only"])
@pytest.mark.parametrize("with_matching_decoy", [False, True])
def test_no_authority_commitment_cannot_override_user_ciphertext(
    monkeypatch,
    tmp_path,
    commitment_shape,
    with_matching_decoy,
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    seed._data.pop(seed._COMMITMENT_AUTHORITY_FIELD)
    chosen_key = seed._derive_key(_NEW_MASTER)
    seed._data["ciphertexts"][
        seed._ROLLBACK_COMMITMENT_KEY
    ] = seed._seal_rollback_commitment(chosen_key)
    if with_matching_decoy:
        seed._data["ciphertexts"]["row402-matching-decoy"] = (
            seed._seal_verifier(chosen_key)
        )
    if commitment_shape == "both":
        seed._data["verifier"] = seed._seal_verifier(chosen_key)
    else:
        seed._data.pop("verifier")
    assert seed._save() is True
    seed.lock()
    path = tmp_path / "secrets.json"
    original_disk = path.read_bytes()

    reopened = _reopen_backend(monkeypatch, tmp_path)
    original_memory = json.loads(json.dumps(reopened._data))
    save_calls = []
    monkeypatch.setattr(
        reopened, "_save", lambda: save_calls.append("save") or True
    )
    with pytest.raises(ss.SecretsIntegrityError, match="disagree"):
        reopened.unlock(_NEW_MASTER)

    assert save_calls == []
    assert reopened.is_unlocked() is False
    assert reopened._data == original_memory
    assert path.read_bytes() == original_disk


@pytest.mark.parametrize("dynamic_authority", [False, True])
@pytest.mark.parametrize("attempted_password", [_MASTER, _NEW_MASTER])
def test_stripped_authority_with_injected_ciphertext_fails_closed(
    monkeypatch,
    tmp_path,
    dynamic_authority,
    attempted_password,
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    preferred_key = seed._ROLLBACK_COMMITMENT_KEY
    if dynamic_authority:
        seed.set(preferred_key, "row402-user-at-preferred-name")
    seed.set(_KEY, _VALUE)
    authority_key = seed._data[seed._COMMITMENT_AUTHORITY_FIELD]
    assert (authority_key != preferred_key) is dynamic_authority

    seed._data.pop("verifier")
    seed._data.pop(seed._COMMITMENT_AUTHORITY_FIELD)
    attacker_key = seed._derive_key(_NEW_MASTER)
    seed._data["ciphertexts"]["row402-injected-ciphertext"] = (
        seed._seal_verifier(attacker_key)
    )
    assert seed._save() is True
    seed.lock()

    path = tmp_path / "secrets.json"
    original_disk = path.read_bytes()
    reopened = _reopen_backend(monkeypatch, tmp_path)
    original_memory = json.loads(json.dumps(reopened._data))
    with pytest.raises(ss.SecretsIntegrityError, match="disagree"):
        reopened.unlock(attempted_password)

    assert reopened.is_unlocked() is False
    assert reopened._data == original_memory
    assert path.read_bytes() == original_disk


@pytest.mark.parametrize("attempted_password", [_MASTER, _NEW_MASTER])
@pytest.mark.parametrize("with_matching_decoy", [False, True])
def test_retargeted_authority_cannot_override_user_ciphertext(
    monkeypatch,
    tmp_path,
    attempted_password,
    with_matching_decoy,
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    original_authority = seed._data[seed._COMMITMENT_AUTHORITY_FIELD]
    attacker_authority = f"{original_authority}.1"
    assert attacker_authority not in seed._data["ciphertexts"]
    attacker_key = seed._derive_key(_NEW_MASTER)
    seed._data["ciphertexts"][attacker_authority] = (
        seed._seal_rollback_commitment(attacker_key)
    )
    seed._data[seed._COMMITMENT_AUTHORITY_FIELD] = attacker_authority
    seed._data["verifier"] = seed._seal_verifier(attacker_key)
    if with_matching_decoy:
        seed._data["ciphertexts"]["row402-authority-matching-decoy"] = (
            seed._seal_verifier(attacker_key)
        )
    assert seed._save() is True
    seed.lock()

    path = tmp_path / "secrets.json"
    original_disk = path.read_bytes()
    reopened = _reopen_backend(monkeypatch, tmp_path)
    original_memory = json.loads(json.dumps(reopened._data))
    with pytest.raises(ss.SecretsIntegrityError, match="disagree"):
        reopened.unlock(attempted_password)

    assert reopened.is_unlocked() is False
    assert reopened._data == original_memory
    assert path.read_bytes() == original_disk


@pytest.mark.parametrize("commitment_shape", ["both", "commitment_only"])
def test_no_authority_public_commitments_upgrade_when_user_ciphertext_agrees(
    fresh_backend,
    commitment_shape,
):
    backend, _path = fresh_backend
    assert backend.unlock(_MASTER) is True
    backend.set(_KEY, _VALUE)
    backend._data.pop(backend._COMMITMENT_AUTHORITY_FIELD)
    if commitment_shape == "commitment_only":
        backend._data.pop("verifier")
    assert backend._save() is True
    backend.lock()

    assert backend.unlock(_MASTER) is True
    assert backend.get(_KEY) == _VALUE
    old_unmarked_entry = backend._ROLLBACK_COMMITMENT_KEY
    new_authority = backend._data[backend._COMMITMENT_AUTHORITY_FIELD]
    assert new_authority != old_unmarked_entry
    assert list(backend._data["ciphertexts"])[0] == new_authority
    assert backend.get(old_unmarked_entry) == (
        backend._ROLLBACK_COMMITMENT_PLAINTEXT.decode("utf-8")
    )
    assert backend.list_keys() == sorted([_KEY, old_unmarked_entry])


@pytest.mark.parametrize("commitment_shape", ["both", "commitment_only"])
def test_no_authority_ciphertexts_are_preserved_as_legacy_user_data(
    fresh_backend,
    commitment_shape,
):
    backend, _path = fresh_backend
    assert backend.unlock(_MASTER) is True
    backend._data.pop(backend._COMMITMENT_AUTHORITY_FIELD)
    if commitment_shape == "commitment_only":
        backend._data.pop("verifier")
    assert backend._save() is True
    backend.lock()

    assert backend.unlock(_MASTER) is True
    old_unmarked_entry = backend._ROLLBACK_COMMITMENT_KEY
    new_authority = backend._data[backend._COMMITMENT_AUTHORITY_FIELD]
    assert new_authority != old_unmarked_entry
    assert list(backend._data["ciphertexts"])[0] == new_authority
    assert backend.list_keys() == [old_unmarked_entry]
    assert backend.get(old_unmarked_entry) == (
        backend._ROLLBACK_COMMITMENT_PLAINTEXT.decode("utf-8")
    )


def test_unlock_endpoint_maps_verifier_disagreement_without_mutation(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    seed._data["ciphertexts"].pop(seed._ROLLBACK_COMMITMENT_KEY)
    seed._data.pop(seed._COMMITMENT_AUTHORITY_FIELD, None)
    seed._data["verifier"] = seed._seal_verifier(
        seed._derive_key(_NEW_MASTER)
    )
    assert seed._save() is True
    seed.lock()
    original_disk = (tmp_path / "secrets.json").read_bytes()

    reopened = _reopen_backend(monkeypatch, tmp_path)
    response = _secrets_client().post(
        "/api/secrets/unlock", json={"password": _MASTER}
    )
    body = response.get_json()

    assert response.status_code == 409, body
    assert body["ok"] is False
    assert body["state"] == "integrity_error"
    assert body["is_initialized"] is True
    assert body["is_unlocked"] is False
    assert "disagree" in body["error"].lower()
    assert reopened.is_unlocked() is False
    assert (tmp_path / "secrets.json").read_bytes() == original_disk


def test_malformed_present_verifier_is_never_silently_repaired(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    seed._data["ciphertexts"].pop(seed._ROLLBACK_COMMITMENT_KEY)
    seed._data.pop(seed._COMMITMENT_AUTHORITY_FIELD, None)
    damaged = {"nonce": "AAAA", "ct": "AAAA"}
    seed._data["verifier"] = damaged
    assert seed._save() is True
    seed.lock()
    damaged_disk = (tmp_path / "secrets.json").read_bytes()

    reopened = _reopen_backend(monkeypatch, tmp_path)
    save_calls = []
    monkeypatch.setattr(
        reopened, "_save", lambda: save_calls.append("save") or False
    )
    with pytest.raises(ss.SecretsIntegrityError, match="malformed"):
        reopened.unlock(_MASTER)

    assert save_calls == []
    assert reopened.is_unlocked() is False
    assert reopened.is_initialized() is True
    assert reopened._data["verifier"] == damaged
    with pytest.raises(ss.SecretsIntegrityError, match="malformed"):
        reopened.list_keys()
    assert list(reopened._data["ciphertexts"]) == [_KEY]
    assert (tmp_path / "secrets.json").read_bytes() == damaged_disk


def test_authoritative_compatibility_repair_failure_rolls_back_and_locks(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    seed._data["verifier"] = {"nonce": "AAAA", "ct": "AAAA"}
    assert seed._save() is True
    seed.lock()
    path = tmp_path / "secrets.json"
    original_disk = path.read_bytes()
    original_memory = json.loads(json.dumps(seed._data))

    reopened = _reopen_backend(monkeypatch, tmp_path)
    monkeypatch.setattr(reopened, "_save", lambda: False)
    with pytest.raises(ss.SecretsPersistError, match="compatibility repair"):
        reopened.unlock(_MASTER)

    assert reopened.is_unlocked() is False
    assert reopened._data == original_memory
    assert reopened.list_keys() == [_KEY]
    assert path.read_bytes() == original_disk


def test_authoritative_repair_sealing_exception_locks_preunlocked_backend(
    fresh_backend,
    monkeypatch,
):
    backend, path = fresh_backend
    assert backend.unlock(_MASTER) is True
    backend.set(_KEY, _VALUE)
    backend._data["verifier"] = {"nonce": "AAAA", "ct": "AAAA"}
    assert backend._save() is True
    original_memory = json.loads(json.dumps(backend._data))
    original_disk = path.read_bytes()
    assert backend.is_unlocked() is True

    def fail_verifier_seal(_key):
        raise RuntimeError("synthetic verifier repair seal failure")

    monkeypatch.setattr(backend, "_seal_verifier", fail_verifier_seal)
    with pytest.raises(RuntimeError, match="synthetic verifier"):
        backend.unlock(_MASTER)

    assert backend._data == original_memory
    assert path.read_bytes() == original_disk
    assert backend.is_unlocked() is False
    assert backend.get(_KEY) is None


def test_zero_secret_password_rotation_rotates_the_verifier(
    fresh_backend,
):
    backend, path = fresh_backend
    assert backend.unlock(_MASTER) is True
    before = json.loads(path.read_text(encoding="utf-8"))
    assert backend.list_keys() == []

    assert backend.change_password(_MASTER, _NEW_MASTER) is True
    after = json.loads(path.read_text(encoding="utf-8"))
    commitment_key = backend._ROLLBACK_COMMITMENT_KEY
    assert list(after["ciphertexts"]) == [commitment_key]
    assert after["salt"] != before["salt"]
    assert after["verifier"] != before["verifier"]
    assert after["ciphertexts"][commitment_key] != before["ciphertexts"][commitment_key]
    assert backend.list_keys() == []

    backend.lock()
    assert backend.unlock(_MASTER) is False
    assert backend.unlock(_NEW_MASTER) is True
    assert backend.is_initialized() is True


def test_rotation_restores_rollback_commitment_to_first_ciphertext(
    fresh_backend,
):
    backend, path = fresh_backend
    assert backend.unlock(_MASTER) is True
    backend.set(_KEY, _VALUE)
    commitment_key = backend._ROLLBACK_COMMITMENT_KEY
    commitment = backend._data["ciphertexts"][commitment_key]
    backend._data["ciphertexts"] = {
        _KEY: backend._data["ciphertexts"][_KEY],
        commitment_key: commitment,
    }
    assert backend._save() is True
    assert list(json.loads(path.read_text())["ciphertexts"])[0] == _KEY

    assert backend.change_password(_MASTER, _NEW_MASTER) is True

    rotated = json.loads(path.read_text(encoding="utf-8"))
    assert list(rotated["ciphertexts"])[0] == commitment_key
    assert backend.list_keys() == [_KEY]
    assert backend.get(_KEY) == _VALUE


def test_change_password_refuses_uninitialized_vault_without_unlock_or_mutation(
    fresh_backend, monkeypatch
):
    backend, path = fresh_backend
    before = json.loads(json.dumps(backend._data))
    unlock_calls = []

    def forbidden_unlock(password):
        unlock_calls.append(password)
        raise AssertionError("uninitialized change-password called unlock")

    monkeypatch.setattr(backend, "unlock", forbidden_unlock)
    response = _secrets_client().post(
        "/api/secrets/change_password",
        json={"old_password": _MASTER, "new_password": _NEW_MASTER},
    )
    body = response.get_json()

    assert response.status_code == 409, body
    assert body["ok"] is False
    assert body["state"] == "uninitialized"
    assert "initialize" in body["error"].lower()
    assert unlock_calls == []
    assert backend._data == before
    assert backend.is_initialized() is False
    assert backend.is_unlocked() is False
    assert path.exists() is False


def test_direct_change_password_refuses_uninitialized_vault_without_commit(
    fresh_backend,
):
    backend, path = fresh_backend
    before = json.loads(json.dumps(backend._data))

    with pytest.raises(ss.SecretsUninitializedError, match="initialize"):
        backend.change_password(_MASTER, _NEW_MASTER)

    assert backend._data == before
    assert backend.is_initialized() is False
    assert backend.is_unlocked() is False
    assert path.exists() is False


def test_change_password_cannot_race_failed_first_use_into_initialization(
    fresh_backend,
    monkeypatch,
):
    backend, path = fresh_backend
    before = json.loads(json.dumps(backend._data))
    save_entered = threading.Event()
    release_save = threading.Event()
    state_probe_started = threading.Event()
    save_calls = []
    unlock_calls = []
    real_save = backend._save
    real_is_initialized = backend.is_initialized
    real_unlock = backend.unlock
    call_lock = threading.Lock()

    def fail_first_save_only():
        with call_lock:
            save_calls.append("save")
            call_number = len(save_calls)
        if call_number == 1:
            save_entered.set()
            assert release_save.wait(timeout=10)
            return False
        return real_save()

    def observed_is_initialized():
        state_probe_started.set()
        return real_is_initialized()

    def observed_unlock(password):
        unlock_calls.append(password)
        return real_unlock(password)

    monkeypatch.setattr(backend, "_save", fail_first_save_only)
    monkeypatch.setattr(backend, "is_initialized", observed_is_initialized)
    monkeypatch.setattr(backend, "unlock", observed_unlock)
    responses = {}

    def first_use_request():
        response = _secrets_client().post(
            "/api/secrets/unlock", json={"password": _MASTER}
        )
        responses["unlock"] = (response.status_code, response.get_json())

    def change_request():
        response = _secrets_client().post(
            "/api/secrets/change_password",
            json={
                "old_password": _MASTER,
                "new_password": _NEW_MASTER,
            },
        )
        responses["change"] = (response.status_code, response.get_json())

    first_use = threading.Thread(target=first_use_request)
    change = threading.Thread(target=change_request)
    first_use.start()
    assert save_entered.wait(timeout=10)
    change.start()
    assert state_probe_started.wait(timeout=10)
    release_save.set()
    first_use.join(timeout=10)
    change.join(timeout=10)

    assert not first_use.is_alive()
    assert not change.is_alive()
    assert responses["unlock"][0] == 500, responses["unlock"][1]
    assert responses["unlock"][1]["state"] == "uninitialized"
    assert responses["change"][0] == 409, responses["change"][1]
    assert responses["change"][1]["state"] == "uninitialized"
    assert unlock_calls == []
    assert save_calls == ["save"]
    assert backend._data == before
    assert real_is_initialized() is False
    assert backend.is_unlocked() is False
    assert path.exists() is False


@pytest.mark.parametrize(
    "rotation_status",
    [
        None,
        {"changed": True, "reason": "incorrect_password"},
        {"changed": True, "reason": "corrupt"},
        {"changed": False, "reason": "changed"},
    ],
)
def test_change_password_endpoint_refuses_incoherent_atomic_status(
    monkeypatch,
    rotation_status,
):
    class IncoherentRotationBackend:
        name = "master_password"

        def is_initialized(self):
            return True

        def change_password_with_status(self, _old, _new):
            return rotation_status

        def change_password(self, _old, _new):  # pragma: no cover
            raise AssertionError("non-atomic rotation path used")

    backend = IncoherentRotationBackend()
    monkeypatch.setattr(ss, "get_backend", lambda: backend)
    recorded = []
    monkeypatch.setattr(
        at, "record_success", lambda label: recorded.append(("success", label))
    )
    monkeypatch.setattr(
        at, "record_failure", lambda label: recorded.append(("failure", label))
    )
    at.reset()

    response = _secrets_client().post(
        "/api/secrets/change_password",
        json={"old_password": _MASTER, "new_password": _NEW_MASTER},
    )
    body = response.get_json()
    assert response.status_code == 500, body
    assert body["ok"] is False
    assert body["state"] == "unknown"
    assert "incoherent" in body["error"].lower()
    assert recorded == []


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


def test_legacy_rotation_later_persist_failure_restores_exact_precall_state(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    _strip_row402_commitments(seed)
    assert seed._save() is True
    seed.lock()

    path = tmp_path / "secrets.json"
    legacy = _reopen_backend(monkeypatch, tmp_path)
    before_memory = json.loads(json.dumps(legacy._data))
    before_disk = path.read_bytes()
    before_key = legacy._key
    before_salt = legacy._data["salt"]
    real_save = legacy._save
    save_salts = []

    def fail_only_the_rotation_publish():
        save_salts.append(legacy._data["salt"])
        if legacy._data["salt"] != before_salt:
            return False
        return real_save()

    monkeypatch.setattr(legacy, "_save", fail_only_the_rotation_publish)
    with pytest.raises(ss.SecretsPersistError, match="rolled back"):
        legacy.change_password(_MASTER, _NEW_MASTER)

    assert save_salts and save_salts[-1] != before_salt
    assert save_salts == [save_salts[-1]], (
        "rotation published a legacy compatibility upgrade before its final "
        "transaction was known to succeed"
    )
    assert legacy._data == before_memory
    assert legacy._key == before_key is None
    assert path.read_bytes() == before_disk


def test_legacy_rotation_later_corruption_restores_exact_precall_state(
    monkeypatch, tmp_path
):
    malformed_key = "bulkdl-site-row402-malformed-rotation"
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    seed.set(malformed_key, "row402-malformed-value")
    _strip_row402_commitments(seed)
    seed._data["ciphertexts"][malformed_key] = {
        "nonce": "AAAA",
        "ct": "AAAA",
    }
    assert seed._save() is True
    seed.lock()

    path = tmp_path / "secrets.json"
    legacy = _reopen_backend(monkeypatch, tmp_path)
    before_memory = json.loads(json.dumps(legacy._data))
    before_disk = path.read_bytes()
    before_key = legacy._key
    real_save = legacy._save
    save_calls = []

    def observed_save():
        save_calls.append("save")
        return real_save()

    monkeypatch.setattr(legacy, "_save", observed_save)
    assert legacy.change_password(_MASTER, _NEW_MASTER) is False

    assert save_calls == [], (
        "rotation published a legacy compatibility upgrade before finding "
        "the corrupt ciphertext"
    )
    assert legacy._data == before_memory
    assert legacy._key == before_key is None
    assert path.read_bytes() == before_disk


def test_change_password_endpoint_maps_legacy_preflight_upgrade_failure(
    monkeypatch, tmp_path
):
    seed = _new_backend(monkeypatch, tmp_path)
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    _strip_row402_commitments(seed)
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


def test_zero_reference_uninitialized_vault_is_not_ready(
    fresh_backend, monkeypatch
):
    backend, path = fresh_backend
    assert backend.list_keys() == []
    assert backend.is_initialized() is False
    assert backend.is_unlocked() is False
    assert path.exists() is False

    measured = app_health.credential_health({})
    assert measured["state"] == "uninitialized"
    assert measured["reference_count"] == 0
    assert measured["missing_count"] == 0
    assert measured["ok"] is False

    response = _health_client(monkeypatch, {}).get("/api/health")
    body = response.get_json()
    assert response.status_code == 503, body
    assert body["ok"] is False
    assert body["degraded"] == "credential_vault_uninitialized"
    assert body["credentials"] == measured


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


@pytest.mark.parametrize("with_unreferenced_secret", [False, True])
def test_locked_initialized_vault_without_configured_references_is_healthy(
    fresh_backend, monkeypatch, with_unreferenced_secret
):
    backend, _path = fresh_backend
    assert backend.unlock(_MASTER) is True
    if with_unreferenced_secret:
        backend.set(_KEY, _VALUE)
    backend.lock()

    measured = app_health.credential_health({})
    assert measured == {
        "backend": "master_password",
        "is_initialized": True,
        "is_unlocked": False,
        "missing_count": 0,
        "ok": True,
        "reference_count": 0,
        "resolved_count": 0,
        "state": "locked_no_references",
        "stored_count": int(with_unreferenced_secret),
        "unavailable_count": 0,
    }

    response = _health_client(monkeypatch, {}).get("/api/health")
    body = response.get_json()
    assert response.status_code == 200, body
    assert body["ok"] is True
    assert "degraded" not in body
    assert body["credentials"] == measured


@pytest.mark.parametrize(
    "malformed_ciphertexts",
    [
        None,
        ["not-an-envelope"],
        [{"nonce": "not-an-envelope", "ct": "not-an-envelope"}],
    ],
)
def test_zero_reference_health_is_unknown_for_malformed_ciphertext_container(
    fresh_backend,
    monkeypatch,
    tmp_path,
    malformed_ciphertexts,
):
    backend, path = fresh_backend
    backend._data["ciphertexts"] = malformed_ciphertexts
    assert backend._save() is True
    persisted = path.read_bytes()

    damaged = _reopen_backend(monkeypatch, tmp_path)
    assert damaged.is_initialized() is True
    measured = app_health.credential_health({})
    assert measured == {
        "backend": "master_password",
        "is_initialized": True,
        "is_unlocked": False,
        "missing_count": None,
        "ok": False,
        "reference_count": 0,
        "resolved_count": None,
        "state": "unknown",
        "stored_count": None,
        "unavailable_count": None,
    }

    response = _health_client(monkeypatch, {}).get("/api/health")
    body = response.get_json()
    assert response.status_code == 503, body
    assert body["degraded"] == "credential_state_unknown"
    assert body["credentials"] == measured
    assert path.read_bytes() == persisted


@pytest.mark.parametrize(
    "damage",
    [
        "invalid_authority",
        "missing_authoritative_commitment",
        "malformed_authoritative_commitment",
        "malformed_verifier_without_authority",
        "malformed_commitment_without_authority",
        "all_malformed_legacy_users",
        "verifier_with_all_malformed_users",
    ],
)
def test_zero_reference_health_is_unknown_for_structurally_invalid_commitment(
    fresh_backend,
    monkeypatch,
    tmp_path,
    damage,
):
    backend, path = fresh_backend
    assert backend.unlock(_MASTER) is True
    authority = backend._COMMITMENT_AUTHORITY_FIELD
    commitment = backend._ROLLBACK_COMMITMENT_KEY
    malformed = {"nonce": "AAAA", "ct": "AAAA"}
    if damage == "invalid_authority":
        backend._data[authority] = "not-the-reserved-commitment"
    elif damage == "missing_authoritative_commitment":
        backend._data["ciphertexts"].pop(commitment)
    elif damage == "malformed_authoritative_commitment":
        backend._data["ciphertexts"][commitment] = malformed
    elif damage == "malformed_verifier_without_authority":
        backend._data.pop(authority)
        backend._data["verifier"] = malformed
    elif damage == "malformed_commitment_without_authority":
        backend._data.pop(authority)
        backend._data["ciphertexts"][commitment] = malformed
    elif damage == "all_malformed_legacy_users":
        backend.set(_KEY, _VALUE)
        _strip_row402_commitments(backend)
        backend._data["ciphertexts"][_KEY] = malformed
    elif damage == "verifier_with_all_malformed_users":
        backend.set(_KEY, _VALUE)
        backend._data.pop(authority)
        backend._data["ciphertexts"].pop(commitment)
        backend._data["ciphertexts"][_KEY] = malformed
    else:  # pragma: no cover - parameter list is exhaustive
        raise AssertionError(damage)
    assert backend._save() is True
    backend.lock()
    persisted = path.read_bytes()

    damaged = _reopen_backend(monkeypatch, tmp_path)
    assert damaged.is_initialized() is True
    with pytest.raises(ss.SecretsIntegrityError):
        damaged.list_keys()
    measured = app_health.credential_health({})
    assert measured["state"] == "unknown"
    assert measured["ok"] is False
    assert measured["reference_count"] == 0
    assert measured["stored_count"] is None

    response = _health_client(monkeypatch, {}).get("/api/health")
    body = response.get_json()
    assert response.status_code == 503, body
    assert body["degraded"] == "credential_state_unknown"
    assert body["credentials"] == measured
    assert path.read_bytes() == persisted


@pytest.mark.parametrize(
    "damage",
    [
        "missing_salt",
        "malformed_salt",
        "missing_iterations",
        "malformed_iterations",
        "overflow_iterations",
    ],
)
def test_zero_reference_health_is_unknown_for_unusable_kdf_metadata(
    fresh_backend,
    monkeypatch,
    tmp_path,
    damage,
):
    backend, path = fresh_backend
    assert backend.unlock(_MASTER) is True
    if damage == "missing_salt":
        backend._data.pop("salt")
    elif damage == "malformed_salt":
        backend._data["salt"] = "not-valid-base64%%%"
    elif damage == "missing_iterations":
        backend._data.pop("iterations")
    elif damage == "malformed_iterations":
        backend._data["iterations"] = "not-an-integer"
    elif damage == "overflow_iterations":
        backend._data["iterations"] = 2 ** 100
    else:  # pragma: no cover - parameter list is exhaustive
        raise AssertionError(damage)
    assert backend._save() is True
    backend.lock()
    persisted = path.read_bytes()

    damaged = _reopen_backend(monkeypatch, tmp_path)
    with pytest.raises(ss.SecretsIntegrityError, match="KDF"):
        damaged.list_keys()
    measured = app_health.credential_health({})
    assert measured["state"] == "unknown"
    assert measured["ok"] is False
    assert measured["reference_count"] == 0
    assert measured["stored_count"] is None

    health_response = _health_client(monkeypatch, {}).get("/api/health")
    health_body = health_response.get_json()
    assert health_response.status_code == 503, health_body
    assert health_body["degraded"] == "credential_state_unknown"

    unlock_response = _secrets_client().post(
        "/api/secrets/unlock", json={"password": _MASTER}
    )
    unlock_body = unlock_response.get_json()
    assert unlock_response.status_code == 409, unlock_body
    assert unlock_body["state"] == "integrity_error"
    assert damaged.is_unlocked() is False
    assert path.read_bytes() == persisted


def test_secrets_status_reports_structured_integrity_error_for_damaged_vault(
    fresh_backend, monkeypatch
):
    backend, path = fresh_backend
    backend._data["ciphertexts"] = None
    assert backend._save() is True
    backend.lock()
    persisted = path.read_bytes()
    assert backend.is_initialized() is True
    with pytest.raises(ss.SecretsIntegrityError, match="ciphertexts"):
        backend.list_keys()

    monkeypatch.setattr(app_secrets, "_app_s_cfg", lambda: {})
    response = _secrets_client().get("/api/secrets/status")
    body = response.get_json()

    assert response.status_code == 409, response.get_data(as_text=True)
    assert body is not None
    assert body["ok"] is False
    assert body["state"] == "integrity_error"
    assert body["backend"] == "master_password"
    assert body["is_initialized"] is True
    assert body["is_unlocked"] is False
    assert body["stored_keys"] is None
    assert "ciphertexts" in body["error"]
    assert path.read_bytes() == persisted


def test_authoritative_malformed_unreferenced_user_remains_zero_ref_healthy(
    fresh_backend,
):
    backend, _path = fresh_backend
    assert backend.unlock(_MASTER) is True
    backend.set(_KEY, _VALUE)
    backend._data["ciphertexts"][_KEY] = {
        "nonce": "AAAA",
        "ct": "AAAA",
    }
    assert backend._save() is True
    backend.lock()

    assert backend.list_keys() == [_KEY]
    measured = app_health.credential_health({})
    assert measured["state"] == "locked_no_references"
    assert measured["ok"] is True
    assert measured["reference_count"] == 0
    assert measured["stored_count"] == 1


def test_locked_initialized_vault_with_missing_reference_remains_loud(
    fresh_backend,
):
    backend, _path = fresh_backend
    assert backend.unlock(_MASTER) is True
    backend.lock()

    measured = app_health.credential_health(
        {"row402": {"password": _REF}}
    )
    assert measured["ok"] is False
    assert measured["state"] == "missing_credentials"
    assert measured["reference_count"] == 1
    assert measured["stored_count"] == 0
    assert measured["missing_count"] == 1
    assert measured["unavailable_count"] == 0


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
