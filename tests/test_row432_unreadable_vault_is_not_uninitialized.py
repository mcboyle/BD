"""Row 432: an unreadable or unparseable secrets.json is not "uninitialized".

`_load_or_init` wrapped `SECRETS_FILE.read_text` and `json.loads` in one
blanket `except Exception`, renamed the operator's live vault to
`secrets.json.corrupt-<hex>`, and returned fresh-init state.  A torn write,
an EIO, or a transient permission error at backend construction therefore
landed in `_unlock_locked`'s first-use branch, where ANY >= 8 character
password durably committed a new empty vault and reported ok/initialized.

CLAUDE.md A7: an unavailable measurement is UNKNOWN, never OK.  An unreadable
store must classify as its own state -- never "uninitialized" (the state the
product treats as safe to initialize) and never a clean "initialized" -- the
file must survive byte-identical, and every mutation must refuse with a
distinctive named damaged-vault diagnostic.

Every password in this module is a documented zero-entropy synthetic literal.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from flask import Flask
import pytest

from bulk_downloader import app_health
from bulk_downloader import app_secrets
from bulk_downloader import auth_throttle as at
from bulk_downloader import secrets_store as ss


BD_GATE_SCOPE = "module"

# Documented zero-entropy synthetic values.  None of these is a credential.
_MASTER_A = "row432-synthetic-master-password-a"
_MASTER_B = "row432-synthetic-master-password-b"
_KEY = "bulkdl-site-row432"
_VALUE = "row432-synthetic-value"
_REF = f"@cred:{_KEY}"
_ITERATIONS = 1_000
_UNPARSEABLE = b"{row432 not valid json"

# Row 514: the BARE site map production hands credential_health -- app.py
# fills s_cfg[sid] = cfg, and app_health._attach_credential_health passes that
# map straight through.  An outer {"sites": ...} wrapper is the on-disk
# settings-file shape; under it password_reference_keys examines the inner
# site MAP, which has no "password" key, so every arm measures 0 references.
_SITES_WITH_REF = {"row432": {"password": _REF}}
_SITES_WITHOUT_REF = {"row432": {"password": ""}}


# ── fixtures ────────────────────────────────────────────────────────


def _install(monkeypatch, root: Path):
    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not available")
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)
    monkeypatch.setattr(ss, "SECRETS_FILE", root / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", root / "secrets_meta.json")
    at.reset()


def _open_backend(monkeypatch):
    backend = ss.MasterPasswordBackend()
    monkeypatch.setattr(ss, "_backend", backend)
    monkeypatch.setattr(ss, "_backend_pref", "master_password")
    monkeypatch.setattr(ss, "_audited_cache", None)
    return backend


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _corrupt_siblings(root: Path) -> list[Path]:
    return sorted(root.glob("secrets.json.corrupt-*"))


def _initialized_vault(monkeypatch, root: Path) -> Path:
    """Build a real initialized vault holding exactly one credential.

    The precondition is then asserted from a re-read of the file itself, not
    from the fixture's own in-memory view.
    """
    _install(monkeypatch, root)
    backend = _open_backend(monkeypatch)
    backend._data["iterations"] = _ITERATIONS
    assert backend.is_initialized() is False
    assert backend.unlock(_MASTER_A) is True
    backend.set(_KEY, _VALUE)
    backend.lock()

    path = root / "secrets.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    assert "verifier" in blob, "precondition: verifier committed on disk"
    assert backend._COMMITMENT_AUTHORITY_FIELD in blob, (
        "precondition: commitment authority committed on disk"
    )
    assert _KEY in blob["ciphertexts"], "precondition: credential on disk"
    reopened = _open_backend(monkeypatch)
    assert reopened.list_keys() == [_KEY], "precondition: stored_count == 1"
    assert reopened.is_initialized() is True
    assert reopened.is_unlocked() is False
    return path


@pytest.fixture
def unparseable_vault(monkeypatch, tmp_path):
    """A genuinely initialized vault whose file is then unparseable."""
    path = _initialized_vault(monkeypatch, tmp_path)
    with pytest.raises(Exception):
        json.loads(_UNPARSEABLE.decode("utf-8"))
    path.write_bytes(_UNPARSEABLE)
    assert path.read_bytes() == _UNPARSEABLE
    assert _corrupt_siblings(tmp_path) == []
    yield path
    at.reset()


@pytest.fixture
def unreadable_vault(monkeypatch, tmp_path):
    """A genuinely initialized vault whose file is then unreadable."""
    if os.geteuid() == 0:
        pytest.skip("root ignores the 0o000 mode this precondition needs")
    path = _initialized_vault(monkeypatch, tmp_path)
    before = path.read_bytes()
    path.chmod(0o000)
    with pytest.raises(PermissionError):
        path.read_text(encoding="utf-8")
    try:
        yield path, before
    finally:
        try:
            path.chmod(0o600)
        except Exception:
            pass
        at.reset()


def _secrets_client():
    flask_app = Flask("row432-secrets")
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
        lambda _install_dir: {"sha": None, "built_at": None, "source": "unknown"},
    )
    flask_app = Flask("row432-health")
    flask_app.register_blueprint(app_health.health_bp)
    return flask_app.test_client()


# ── the two broken-file shapes ──────────────────────────────────────


def test_unparseable_store_is_not_uninitialized(unparseable_vault, monkeypatch):
    """RED: today this reports uninitialized and renames the live vault."""
    path = unparseable_vault
    root = path.parent
    digest_before = _digest(path)

    backend = _open_backend(monkeypatch)

    assert path.exists() is True, "the damaged vault must not be moved aside"
    assert _digest(path) == digest_before, "the damaged vault must be byte-identical"
    assert _corrupt_siblings(root) == [], "construction must create no .corrupt- sibling"
    assert backend.store_state() == "unreadable"
    assert backend.is_initialized() is True, (
        "an unavailable measurement is never permission to choose a new password"
    )
    assert backend.is_unlocked() is False


def test_unreadable_store_is_not_uninitialized(unreadable_vault, monkeypatch):
    """RED: a chmod-000 file renames away (rename needs dir perms, not file)."""
    path, before = unreadable_vault
    root = path.parent

    backend = _open_backend(monkeypatch)

    assert path.exists() is True, "the unreadable vault must not be moved aside"
    assert _corrupt_siblings(root) == [], "construction must create no .corrupt- sibling"
    assert backend.store_state() == "unreadable"
    assert backend.is_initialized() is True
    assert backend.is_unlocked() is False

    path.chmod(0o600)
    assert path.read_bytes() == before, "the unreadable vault survived byte-identical"


# ── the mutation seam: any password must be refused ─────────────────


def test_unlock_over_an_unparseable_store_refuses_with_a_named_diagnostic(
    unparseable_vault, monkeypatch
):
    path = unparseable_vault
    root = path.parent
    digest_before = _digest(path)
    backend = _open_backend(monkeypatch)

    refusals = 0
    for password in (_MASTER_A, _MASTER_B):
        with pytest.raises(ss.SecretsUnreadableError) as caught:
            backend.unlock(password)
        diagnostic = str(caught.value)
        assert "unreadable" in diagnostic.lower(), "distinctive named state"
        assert "incorrect password" not in diagnostic.lower(), "not bad-password"
        assert _MASTER_A not in diagnostic and _MASTER_B not in diagnostic
        # get_backend() caches one instance and the store is read once at
        # construction, so repairing the file cannot help this process.
        assert "restart" in diagnostic.lower(), "the remedy must be reachable"
        refusals += 1

    assert refusals == 2, "both the right and the wrong password are refused"
    assert backend.is_unlocked() is False
    assert _digest(path) == digest_before, "zero fresh vault bytes were written"
    assert _corrupt_siblings(root) == [], "the unlock path created no .corrupt- sibling"


def test_every_mutation_refuses_over_an_unparseable_store(
    unparseable_vault, monkeypatch
):
    path = unparseable_vault
    digest_before = _digest(path)
    backend = _open_backend(monkeypatch)

    refusals = 0
    for call in (
        lambda: backend.unlock(_MASTER_B),
        lambda: backend.unlock_with_status(_MASTER_B, minimum_initial_length=8),
        lambda: backend.set(_KEY, _VALUE),
        lambda: backend.delete(_KEY),
        lambda: backend.change_password(_MASTER_A, _MASTER_B),
    ):
        with pytest.raises(ss.SecretsUnreadableError):
            call()
        refusals += 1

    assert refusals == 5, "every mutation entry point refuses"
    assert _digest(path) == digest_before, "no mutation wrote a byte"


def test_unlock_endpoint_reports_the_damaged_state(unparseable_vault, monkeypatch):
    path = unparseable_vault
    digest_before = _digest(path)
    _open_backend(monkeypatch)
    client = _secrets_client()

    response = client.post("/api/secrets/unlock", json={"password": _MASTER_B})
    body = response.get_json()

    assert response.status_code == 409
    assert body["ok"] is False
    assert body["state"] == "unreadable"
    assert body["is_initialized"] is True
    assert body["is_unlocked"] is False
    assert _digest(path) == digest_before


def test_change_password_endpoint_reports_the_damaged_state(
    unparseable_vault, monkeypatch
):
    """Rotation must not read as damaged CONTENT, nor as uninitialized."""
    path = unparseable_vault
    digest_before = _digest(path)
    _open_backend(monkeypatch)
    client = _secrets_client()

    response = client.post(
        "/api/secrets/change_password",
        json={"old_password": _MASTER_A, "new_password": _MASTER_B},
    )
    body = response.get_json()

    assert response.status_code == 409
    assert body["ok"] is False
    assert body["state"] == "unreadable", "not 'uninitialized', not 'integrity_error'"
    assert _digest(path) == digest_before, "nothing rotated, nothing written"


# ── the four states, each reachable and distinguishable ─────────────


def test_the_four_store_states_are_distinct(monkeypatch, tmp_path):
    observed: list[str] = []

    uninit_root = tmp_path / "uninitialized"
    uninit_root.mkdir()
    _install(monkeypatch, uninit_root)
    assert (uninit_root / "secrets.json").exists() is False
    observed.append(_open_backend(monkeypatch).store_state())

    live_root = tmp_path / "live"
    live_root.mkdir()
    _initialized_vault(monkeypatch, live_root)
    locked = _open_backend(monkeypatch)
    observed.append(locked.store_state())
    assert locked.unlock(_MASTER_A) is True
    observed.append(locked.store_state())

    broken_root = tmp_path / "broken"
    broken_root.mkdir()
    broken = _initialized_vault(monkeypatch, broken_root)
    broken.write_bytes(_UNPARSEABLE)
    observed.append(_open_backend(monkeypatch).store_state())

    assert observed == ["uninitialized", "locked", "unlocked", "unreadable"]
    assert len(set(observed)) == 4, "all four states are mutually distinguishable"


# ── negative controls ───────────────────────────────────────────────


def test_negative_control_absent_store_still_reads_uninitialized(
    monkeypatch, tmp_path
):
    """A fresh host must not be made to look broken by this fix."""
    _install(monkeypatch, tmp_path)
    path = tmp_path / "secrets.json"
    assert path.exists() is False, "precondition: the store is genuinely absent"

    backend = _open_backend(monkeypatch)
    assert backend.store_state() == "uninitialized"
    assert backend.is_initialized() is False
    assert backend.list_keys() == []

    assert backend.unlock(_MASTER_A) is True, "first-use initialization still works"
    assert backend.is_initialized() is True
    assert backend.is_unlocked() is True
    assert path.exists() is True
    assert _corrupt_siblings(tmp_path) == []


def test_negative_control_healthy_store_still_reads_initialized(
    monkeypatch, tmp_path
):
    path = _initialized_vault(monkeypatch, tmp_path)
    backend = _open_backend(monkeypatch)

    assert backend.store_state() == "locked"
    assert backend.is_initialized() is True
    assert backend.unlock(_MASTER_A) is True, "the right password still unlocks"
    assert backend.get(_KEY) == _VALUE
    assert backend.store_state() == "unlocked"
    assert backend.unlock(_MASTER_B) is False, "the wrong password is still wrong"
    assert path.exists() is True


# ── downstream readers ──────────────────────────────────────────────


def test_health_never_reports_ok_over_an_unreadable_store(
    unparseable_vault, monkeypatch
):
    """Zero references previously made a damaged vault look healthy.

    Row 514: the with-a-reference arm must carry a MEASURED, nonzero
    reference denominator.  Both site maps once carried an extra outer
    "sites" wrapper, which is the on-disk settings-file shape and not the
    bare map production hands ``credential_health`` (app.py fills
    ``s_cfg[sid] = cfg``), so ``password_reference_keys`` read
    ``cfg.get("password")`` off the inner site MAP, found nothing, and both
    arms drove the same zero-reference input -- the second arm re-measured
    the first.
    """
    _open_backend(monkeypatch)

    # Preconditions, asserted from the production reader BEFORE any verdict.
    assert len(ss.password_reference_keys(_SITES_WITH_REF)) == 1, (
        "precondition: the with-a-reference arm must reference exactly 1 key"
    )
    assert ss.password_reference_keys(_SITES_WITH_REF) == [_KEY]
    assert ss.password_reference_keys(_SITES_WITHOUT_REF) == [], (
        "precondition: the without-a-reference arm references exactly 0 keys"
    )

    measured = app_health.credential_health(_SITES_WITHOUT_REF)
    assert measured["ok"] is False, "an unavailable measurement is never OK"
    assert measured["state"] == "unknown"
    assert measured["resolved_count"] is None
    assert measured["missing_count"] is None
    assert measured["reference_count"] == 0, (
        "negative control: the 0-reference arm keeps denominator 0"
    )

    referenced = app_health.credential_health(_SITES_WITH_REF)
    assert referenced["ok"] is False
    assert referenced["state"] == "unknown"
    assert referenced["reference_count"] == 1, (
        "the judged payload must report the measured nonzero denominator"
    )
    assert referenced != measured, (
        "the two arms must not be the same measurement twice"
    )

    # The unreadable classification must not have touched the file.
    assert unparseable_vault.read_bytes() == _UNPARSEABLE
    assert _corrupt_siblings(unparseable_vault.parent) == []


def test_health_reports_ok_over_a_readable_unlocked_store(monkeypatch, tmp_path):
    """Row 514 negative control: the corrected fixture did not break every verdict.

    The same bare production-shaped map that now measures 1 reference over a
    damaged vault must still reach an affirmatively healthy verdict when the
    vault is readable and unlocked.
    """
    _initialized_vault(monkeypatch, tmp_path)
    backend = _open_backend(monkeypatch)
    assert backend.unlock(_MASTER_A) is True
    assert backend.is_unlocked() is True
    assert backend.list_keys() == [_KEY], "precondition: exactly the 1 named key"
    assert ss.password_reference_keys(_SITES_WITH_REF) == [_KEY], (
        "precondition: exactly 1 reference, naming the stored key"
    )

    measured = app_health.credential_health(_SITES_WITH_REF)

    assert measured["ok"] is True
    assert measured["state"] == "unlocked"
    assert measured["reference_count"] == 1
    assert measured["resolved_count"] == 1
    assert measured["missing_count"] == 0


def test_health_endpoint_is_degraded_over_an_unreadable_store(
    unparseable_vault, monkeypatch
):
    _open_backend(monkeypatch)
    client = _health_client(monkeypatch, _SITES_WITHOUT_REF)

    response = client.get("/api/health")
    body = response.get_json()

    assert body["credentials"]["state"] == "unknown"
    assert body["credentials"]["ok"] is False
    assert body["ok"] is False


def test_resolve_password_fails_closed_over_an_unreadable_store(
    unparseable_vault, monkeypatch
):
    _open_backend(monkeypatch)
    assert ss.resolve_password(_REF) is None
    value, state = ss.resolve_password_state(_REF)
    assert value is None
    assert state in {"locked", "unknown"}, (
        "an unreadable store must never resolve to 'missing'"
    )


def test_secrets_status_endpoint_reports_the_damaged_state(
    unparseable_vault, monkeypatch
):
    _open_backend(monkeypatch)
    monkeypatch.setattr(app_secrets, "_app_s_cfg", lambda: _SITES_WITHOUT_REF)
    client = _secrets_client()

    response = client.get("/api/secrets/status")
    body = response.get_json()

    assert response.status_code == 409
    assert body["ok"] is False
    assert body["state"] == "unreadable"
    assert body["is_initialized"] is True
    assert body["stored_keys"] is None
