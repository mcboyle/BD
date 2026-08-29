"""Row 402: no surface reports UNLOCKED over a vault that was never created.

MEASURED on test7 at v3.66.1349. Before the call the host reported
is_initialized=False, is_unlocked=False, reference_count=1, stored_count=0.
POST /api/secrets/unlock with a master password returned HTTP 200 and the host
then reported is_unlocked=TRUE with is_initialized still False over zero stored
credentials, degraded still "credential_missing".

is_unlocked True while is_initialized False is not a state. No key was verified
against any committed material, nothing was decrypted, and a caller reading
is_unlocked alone cannot tell that apart from a genuinely usable vault --
CLAUDE.md A7, a surface reporting OK over a measurement it never took. The same
endpoint was meanwhile STRICTER about a wrong password on a real vault (401,
test4) than about ANY password on an empty one, which is the safety ordering
backwards.

THE DECISION. unlock() on an uninitialised vault now INITIALISES it explicitly
and says so, rather than refusing. The Unlock form is the product's only vault
creation path (frontend/src/routes/Secrets.tsx), so refusing would strand first
use behind a route and a UI that do not exist; and committing the password
stamps an AES-GCM verifier, which is what makes every LATER wrong password fail
even while the vault holds zero secrets. The commitment point therefore moves
from the first set() to the first unlock().

These tests build real AES-GCM vaults on the production code path (only the
PBKDF2 iteration count is reduced, so the fixture is affordable), assert the
exact nonzero shape each verdict judges before judging it, and exercise all
four distinguishable vault states plus the incoherent pair the health surface
must refuse to summarise.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3

from flask import Flask
import pytest

from bulk_downloader import app_health
from bulk_downloader import app_secrets
from bulk_downloader import secrets_store as ss


BD_GATE_SCOPE = "module"


_MASTER = "row402-synthetic-master-password"
_OTHER = "row402-synthetic-wrong-password"
_KEY = "bulkdl-site-row402"
_VALUE = "row402-synthetic-value"
_ITERATIONS = 1_000


@pytest.fixture
def vault_paths(monkeypatch, tmp_path):
    """Redirect every vault path and module singleton at this tmp tree."""
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    secrets_file = tmp_path / "secrets.json"
    monkeypatch.setattr(ss, "SECRETS_FILE", secrets_file)
    monkeypatch.setattr(ss, "SECRETS_META_FILE", tmp_path / "secrets_meta.json")
    monkeypatch.setattr(ss, "_backend", None)
    monkeypatch.setattr(ss, "_backend_pref", None)
    monkeypatch.setattr(ss, "_audited_cache", None)
    return secrets_file


@pytest.fixture
def fresh_backend(vault_paths):
    """An UNINITIALISED master-password backend plus its on-disk path."""
    if not ss._CRYPTO_AVAILABLE:
        # A7: an unavailable measurement is not a pass.
        pytest.skip("cryptography not available")
    assert ss.configure_backend("master_password") is True
    backend = ss.get_backend()
    assert backend.name == "master_password"
    # Production algorithm and real AES-GCM; only the work factor is reduced.
    backend._data["iterations"] = _ITERATIONS
    # PRECONDITION: this really is the uninitialised shape the row is about.
    assert vault_paths.exists() is False
    assert backend.list_keys() == []
    assert len(backend.list_keys()) == 0
    assert backend.is_unlocked() is False
    assert backend.is_initialized() is False
    return backend, vault_paths


@pytest.fixture
def secrets_client(monkeypatch):
    """The real /api/secrets blueprint with the throttle disabled and reset."""
    from bulk_downloader import auth_throttle as at

    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)
    at.reset()
    flask_app = Flask("row402-secrets")
    flask_app.register_blueprint(app_secrets.secrets_bp)
    yield flask_app.test_client()
    at.reset()


@pytest.fixture
def health_client(monkeypatch):
    """The real /api/health blueprint over an in-memory database."""
    sites: dict = {}

    @contextmanager
    def memory_db():
        connection = sqlite3.connect(":memory:")
        try:
            yield connection
        finally:
            connection.close()

    monkeypatch.setattr(app_health, "db_conn", memory_db)
    monkeypatch.setattr(app_health, "_app_runners", lambda: {})
    monkeypatch.setattr(app_health, "_app_s_cfg", lambda: sites)
    monkeypatch.setattr(app_health, "_app__app_boot_time", lambda: 0.0)
    monkeypatch.setattr(app_health, "app_test_mode", lambda: False)
    monkeypatch.setattr(
        app_health,
        "build_identity",
        lambda _install_dir: {"sha": None, "built_at": None, "source": "unknown"},
    )
    flask_app = Flask("row402-health")
    flask_app.register_blueprint(app_health.health_bp)
    return sites, flask_app.test_client()


# ── the backend contract ─────────────────────────────────────────────


def test_unlocking_an_uninitialised_vault_commits_it_rather_than_faking_it(
    fresh_backend,
):
    backend, path = fresh_backend

    assert backend.unlock(_MASTER) is True
    assert backend.is_unlocked() is True
    assert backend.is_initialized() is True, (
        "row 402: unlock() reported success over a vault it never committed; "
        "is_unlocked True with is_initialized False is the defect itself"
    )
    # The commitment is durable, not a process flag.
    assert path.exists() is True
    blob = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(blob.get("verifier"), dict), blob.keys()
    assert sorted(blob["verifier"]) == ["ct", "nonce"]
    # ... and it committed a PASSWORD, not a secret: still zero credentials.
    assert blob.get("ciphertexts") == {}
    assert len(blob["ciphertexts"]) == 0
    assert len(backend.list_keys()) == 0


def test_is_unlocked_is_never_true_while_is_initialized_is_false(fresh_backend):
    """The invariant, asserted directly over every reachable vault state."""
    backend, _path = fresh_backend
    observations: list[tuple[str, bool, bool]] = []

    def observe(label: str) -> None:
        observations.append(
            (label, backend.is_initialized(), backend.is_unlocked())
        )

    observe("fresh")
    backend.unlock(_MASTER)
    observe("after-first-unlock")
    backend.set(_KEY, _VALUE)
    observe("after-set")
    backend.lock()
    observe("after-lock")
    backend.unlock(_OTHER)
    observe("after-wrong-password")
    backend.unlock(_MASTER)
    observe("after-correct-unlock")

    # PRECONDITION: the denominator is the six states, not an empty list.
    assert len(observations) == 6
    assert [label for label, _i, _u in observations] == [
        "fresh",
        "after-first-unlock",
        "after-set",
        "after-lock",
        "after-wrong-password",
        "after-correct-unlock",
    ]
    incoherent = [o for o in observations if o[2] and not o[1]]
    assert incoherent == [], (
        f"row 402: {len(incoherent)} state(s) report unlocked over an "
        f"uninitialised vault: {incoherent}"
    )
    assert [(i, u) for _l, i, u in observations] == [
        (False, False),
        (True, True),
        (True, True),
        (True, False),
        (True, False),
        (True, True),
    ]


def test_a_wrong_password_is_refused_on_an_initialised_empty_vault(fresh_backend):
    """The safety ordering, restored: an empty vault is not a free pass."""
    backend, _path = fresh_backend
    assert backend.unlock(_MASTER) is True
    # PRECONDITION: committed, and holding exactly zero credentials.
    assert backend.is_initialized() is True
    assert backend.list_keys() == []
    assert len(backend.list_keys()) == 0
    backend.lock()
    assert backend.is_unlocked() is False

    assert backend.unlock(_OTHER) is False, (
        "row 402: any password still opens a committed but empty vault"
    )
    assert backend.is_unlocked() is False

    # NEGATIVE CONTROL: the committed password still opens the same vault.
    assert backend.unlock(_MASTER) is True
    assert backend.is_unlocked() is True


def test_change_password_rotates_the_verifier_on_a_zero_secret_vault(fresh_backend):
    """The fix must not brick the vault it just committed."""
    backend, _path = fresh_backend
    assert backend.unlock(_MASTER) is True
    # PRECONDITION: nothing to re-encrypt except the verifier itself.
    assert len(backend.list_keys()) == 0
    assert backend.change_password(_MASTER, _OTHER) is True
    backend.lock()

    assert backend.unlock(_MASTER) is False, "the old password still opens it"
    assert backend.is_unlocked() is False
    assert backend.unlock(_OTHER) is True, "the new password does not open it"
    assert backend.is_initialized() is True


def test_an_uninitialised_unlock_that_cannot_persist_stays_locked(
    fresh_backend, monkeypatch
):
    """A7 applied to the fix: an unpersisted commitment is not a commitment."""
    backend, path = fresh_backend
    save_calls: list[int] = []

    def failing_save() -> bool:
        save_calls.append(1)
        return False

    monkeypatch.setattr(backend, "_save", failing_save)
    assert backend.unlock(_MASTER) is False
    assert len(save_calls) == 1
    assert backend.is_unlocked() is False
    assert backend.is_initialized() is False
    assert path.exists() is False


def test_an_existing_vault_without_a_verifier_still_unlocks(vault_paths):
    """NEGATIVE CONTROL: a pre-row-402 vault on disk keeps working."""
    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not available")
    assert ss.configure_backend("master_password") is True
    seed = ss.get_backend()
    seed._data["iterations"] = _ITERATIONS
    assert seed.unlock(_MASTER) is True
    seed.set(_KEY, _VALUE)
    # Strip the verifier the way a vault written before this cut looks.
    seed._data.pop("verifier", None)
    assert seed._save() is True
    legacy = json.loads(vault_paths.read_text(encoding="utf-8"))
    # PRECONDITION: exactly the legacy shape -- one ciphertext, no verifier.
    assert "verifier" not in legacy
    assert len(legacy["ciphertexts"]) == 1

    ss._backend = None
    ss._backend_pref = None
    assert ss.configure_backend("master_password") is True
    reopened = ss.get_backend()
    assert reopened.is_initialized() is True
    assert reopened.is_unlocked() is False
    assert reopened.unlock(_OTHER) is False
    assert reopened.unlock(_MASTER) is True
    assert reopened.get(_KEY) == _VALUE


# ── the endpoint contract ────────────────────────────────────────────


def test_the_unlock_endpoint_names_the_initialisation_it_performed(
    fresh_backend, secrets_client
):
    backend, _path = fresh_backend

    response = secrets_client.post("/api/secrets/unlock", json={"password": _MASTER})
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    assert body["state"] == "initialized", body
    assert body["initialized_now"] is True, body
    assert body["is_initialized"] is True, body
    assert backend.is_initialized() is True

    # A later unlock of the SAME vault is not another initialisation.
    backend.lock()
    again = secrets_client.post("/api/secrets/unlock", json={"password": _MASTER})
    assert again.status_code == 200, again.get_data(as_text=True)
    later = again.get_json()
    assert later["ok"] is True
    assert later["state"] == "unlocked", later
    assert later["initialized_now"] is False, later
    assert later["is_initialized"] is True, later


def test_the_unlock_endpoint_still_refuses_a_wrong_password_on_a_real_vault(
    fresh_backend, secrets_client
):
    """NEGATIVE CONTROL: the row must not buy its state by breaking unlock."""
    backend, _path = fresh_backend
    assert backend.unlock(_MASTER) is True
    backend.set(_KEY, _VALUE)
    # PRECONDITION: a real initialised vault holding exactly one credential.
    assert backend.list_keys() == [_KEY]
    assert len(backend.list_keys()) == 1
    backend.lock()

    bad = secrets_client.post("/api/secrets/unlock", json={"password": _OTHER})
    assert bad.status_code == 401, bad.get_data(as_text=True)
    assert bad.get_json()["error"] == "incorrect password"
    assert backend.is_unlocked() is False
    assert backend.is_initialized() is True
    assert len(backend.list_keys()) == 1

    good = secrets_client.post("/api/secrets/unlock", json={"password": _MASTER})
    assert good.status_code == 200, good.get_data(as_text=True)
    opened = good.get_json()
    assert opened["ok"] is True
    assert opened["initialized_now"] is False, opened
    # Not a flag: the derived key really decrypts the stored credential.
    assert backend.get(_KEY) == _VALUE


# ── the health surface ───────────────────────────────────────────────


def test_health_distinguishes_four_vault_states(fresh_backend):
    backend, _path = fresh_backend
    referencing = {"row402": {"name": "Row 402", "password": f"@cred:{_KEY}"}}
    referenceless: dict = {}

    # 1. UNINITIALISED: nothing committed, one configured reference.
    assert len(ss.password_reference_keys(referencing)) == 1
    uninitialised = app_health.credential_health(referencing)
    assert uninitialised["state"] == "uninitialized", uninitialised
    assert uninitialised["is_initialized"] is False
    assert uninitialised["is_unlocked"] is False
    assert uninitialised["reference_count"] == 1
    assert uninitialised["stored_count"] == 0
    assert uninitialised["resolved_count"] == 0
    assert uninitialised["ok"] is False

    # 4. UNLOCKED BUT ZERO RESOLVED: open, and proving nothing.
    assert backend.unlock(_MASTER) is True
    assert len(ss.password_reference_keys(referenceless)) == 0
    unverified = app_health.credential_health(referenceless)
    assert unverified["state"] == "unlocked_unverified", unverified
    assert unverified["is_initialized"] is True
    assert unverified["is_unlocked"] is True
    assert unverified["reference_count"] == 0
    assert unverified["resolved_count"] == 0
    assert unverified["ok"] is True

    # 3. UNLOCKED: one configured reference actually decrypted.
    backend.set(_KEY, _VALUE)
    assert ss.resolve_password(referencing["row402"]["password"]) == _VALUE
    unlocked = app_health.credential_health(referencing)
    assert unlocked["state"] == "unlocked", unlocked
    assert unlocked["resolved_count"] == 1
    assert unlocked["stored_count"] == 1
    assert unlocked["ok"] is True

    # 2. LOCKED: committed, key forgotten, the ciphertext still on disk.
    backend.lock()
    locked = app_health.credential_health(referencing)
    assert locked["state"] == "locked", locked
    assert locked["is_initialized"] is True
    assert locked["is_unlocked"] is False
    assert locked["unavailable_count"] == 1
    assert locked["missing_count"] == 0
    assert locked["ok"] is False

    payloads = (uninitialised, locked, unlocked, unverified)
    assert len({p["state"] for p in payloads}) == 4, [p["state"] for p in payloads]
    # The key set is a contract; only the values move.
    assert len({tuple(sorted(p)) for p in payloads}) == 1


def test_the_health_endpoint_degrades_as_uninitialised_not_as_missing(
    fresh_backend, health_client
):
    _backend, _path = fresh_backend
    sites, client = health_client
    sites["row402"] = {"name": "Row 402", "password": f"@cred:{_KEY}"}
    # PRECONDITION: the exact test7 shape -- one reference, nothing stored.
    assert len(ss.password_reference_keys(sites)) == 1

    response = client.get("/api/health")
    body = response.get_json()
    assert response.status_code == 503, body
    assert body["degraded"] == "credential_vault_uninitialized", body["degraded"]
    assert body["credentials"]["state"] == "uninitialized"
    assert body["credentials"]["reference_count"] == 1
    assert body["credentials"]["stored_count"] == 0


class _IncoherentBackend:
    """A backend asserting the row 402 pair: open over nothing committed."""

    name = "master_password"

    def __init__(self) -> None:
        self.list_calls = 0

    def is_unlocked(self) -> bool:
        return True

    def is_initialized(self) -> bool:
        return False

    def list_keys(self) -> list[str]:
        self.list_calls += 1
        return []

    def get(self, key):  # pragma: no cover - must never be reached
        raise AssertionError("credential_health resolved over an incoherent pair")


def test_health_refuses_to_summarise_an_incoherent_vault_pair(monkeypatch):
    """Defence in depth: the pair is UNKNOWN at the surface, never OK."""
    liar = _IncoherentBackend()
    monkeypatch.setattr(ss, "get_backend", lambda: liar)
    sites = {"row402": {"password": f"@cred:{_KEY}"}}
    # PRECONDITION: a nonzero reference denominator to be wrong about.
    assert len(ss.password_reference_keys(sites)) == 1

    health = app_health.credential_health(sites)
    assert health["state"] == "unknown", health
    assert health["ok"] is False
    assert health["is_unlocked"] is True
    assert health["is_initialized"] is False
    assert health["resolved_count"] is None
    assert health["stored_count"] is None
    assert health["reference_count"] == 1
    assert liar.list_calls == 1

    payload = {"ok": True}
    app_health._attach_credential_health(payload, sites)
    assert payload["ok"] is False
    assert payload["degraded"] == "credential_state_unknown"
