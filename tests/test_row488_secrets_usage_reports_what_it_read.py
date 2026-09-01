"""Row 488 (and its declared duplicate row 553): /api/secrets/usage must not
answer "no secrets stored" over a vault nothing read.

``api_secrets_usage`` read the inventory inside a bare ``except Exception``
that substituted an empty list, laundering BOTH named refusals --
``SecretsUnreadableError`` (the file could not be read or parsed) and
``SecretsIntegrityError`` (a malformed ciphertexts container) -- into an
affirmative HTTP 200 ``ok:true`` claim that this host stores no secrets and
that no site references one.  The correct vocabulary already existed 37 lines
above in the same file, on ``/api/secrets/status``.

The response also self-contradicted: with ``stored`` emptied, the ``if stored:``
rotation filter was False, so the same 200 carried rotation entries naming the
exact keys ``stored_keys`` denied.

CLAUDE.md A7: an inventory that cannot be measured reads UNKNOWN, never zero.

Every password in this module is a documented zero-entropy synthetic literal.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from flask import Flask
import pytest

from bulk_downloader import app_secrets
from bulk_downloader import auth_throttle as at
from bulk_downloader import secrets_store as ss


BD_GATE_SCOPE = "module"

# Documented zero-entropy synthetic values.  None of these is a credential.
_MASTER = "row488-synthetic-master-password"
_KEY_REFERENCED = "bulkdl-site-row488a"
_KEY_UNREFERENCED = "bulkdl-site-row488b"
_VALUE = "row488-synthetic-value"
_ITERATIONS = 1_000
_UNPARSEABLE = b"{row488 not valid json"

# The BARE site map production hands the route (app.py fills s_cfg[sid] = cfg).
_SITES = {"row488a": {"password": f"@cred:{_KEY_REFERENCED}"}}


def _install(monkeypatch, root: Path):
    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not available")
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)
    monkeypatch.setattr(ss, "SECRETS_FILE", root / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", root / "secrets_meta.json")
    at.reset()


def _open_backend(monkeypatch):
    """Reconstruct past the get_backend cache without relying on
    configure_backend's construction behaviour (row 438 makes it idempotent)."""
    backend = ss.MasterPasswordBackend()
    monkeypatch.setattr(ss, "_backend", backend)
    monkeypatch.setattr(ss, "_backend_pref", "master_password")
    monkeypatch.setattr(ss, "_audited_cache", None)
    return backend


def _client(monkeypatch, sites=None):
    monkeypatch.setattr(
        app_secrets, "_app_s_cfg", lambda: (_SITES if sites is None else sites)
    )
    flask_app = Flask("row488-secrets")
    flask_app.register_blueprint(app_secrets.secrets_bp)
    return flask_app.test_client()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _two_key_vault(monkeypatch, root: Path) -> Path:
    """A real initialized vault holding exactly 2 keys, 1 of them referenced.

    Every precondition is then asserted from the FILES, not from the fixture's
    own in-memory view.
    """
    _install(monkeypatch, root)
    backend = _open_backend(monkeypatch)
    backend._data["iterations"] = _ITERATIONS
    assert backend.is_initialized() is False
    assert backend.unlock(_MASTER) is True
    backend.set(_KEY_REFERENCED, _VALUE)
    backend.set(_KEY_UNREFERENCED, _VALUE)
    backend.lock()

    path = root / "secrets.json"
    reopened = _open_backend(monkeypatch)
    assert sorted(reopened.list_keys()) == [_KEY_REFERENCED, _KEY_UNREFERENCED], (
        "precondition: exactly 2 stored keys, read back through the backend"
    )
    meta = json.loads((root / "secrets_meta.json").read_text(encoding="utf-8"))
    rotated = meta.get("rotated_at") or {}
    assert sorted(rotated) == [_KEY_REFERENCED, _KEY_UNREFERENCED], (
        "precondition: the readable meta file stamps exactly those 2 keys"
    )
    assert ss.password_reference_keys(_SITES) == [_KEY_REFERENCED], (
        "precondition: exactly 1 configured site references exactly 1 key"
    )
    return path


@pytest.fixture
def unparseable_vault(monkeypatch, tmp_path):
    """Root-safe damaged shape: unparseable bytes, not chmod 000."""
    path = _two_key_vault(monkeypatch, tmp_path)
    before = _digest(path)
    with pytest.raises(Exception):
        json.loads(_UNPARSEABLE.decode("utf-8"))
    path.write_bytes(_UNPARSEABLE)
    after = _digest(path)
    assert before != after
    backend = _open_backend(monkeypatch)
    with pytest.raises(ss.SecretsUnreadableError):
        backend.list_keys()
    assert path.read_bytes() == _UNPARSEABLE
    yield path
    at.reset()


@pytest.fixture
def malformed_container_vault(monkeypatch, tmp_path):
    """The second damaged shape: a parseable file with a malformed
    ciphertexts container, which list_keys refuses with SecretsIntegrityError."""
    path = _two_key_vault(monkeypatch, tmp_path)
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["ciphertexts"] = ["row488-not-a-mapping"]
    path.write_text(json.dumps(blob), encoding="utf-8")
    backend = _open_backend(monkeypatch)
    with pytest.raises(ss.SecretsIntegrityError):
        backend.list_keys()
    yield path
    at.reset()


# ── the two unavailable shapes ──────────────────────────────────────


def test_usage_never_claims_an_empty_inventory_over_an_unreadable_vault(
    unparseable_vault, monkeypatch
):
    """Row 488 RED: HTTP 200 ok:true with 0 stored_keys over a store nothing read."""
    before = _digest(unparseable_vault)
    client = _client(monkeypatch)

    response = client.get("/api/secrets/usage")
    body = response.get_json()

    assert response.status_code == 409, (
        "an inventory that could not be read is not an empty inventory"
    )
    assert body["ok"] is False
    assert body["state"] == "unreadable"
    assert body["stored_keys"] is None, (
        "a null inventory is distinguishable from an honest empty list"
    )
    assert body.get("error"), "the refusal carries the store's own diagnosis"
    # No field may be read as an inventory measurement over an unread store.
    assert not body.get("rotation"), (
        "the rotation map named the exact keys stored_keys denied"
    )
    assert not body.get("usage")
    assert not body.get("unreferenced")
    # The refusal is a read, and reads never touch the file.
    assert _digest(unparseable_vault) == before


def test_usage_never_claims_an_empty_inventory_over_a_malformed_container(
    malformed_container_vault, monkeypatch
):
    """Row 553's shape: SecretsIntegrityError must not launder into a confident empty."""
    before = _digest(malformed_container_vault)
    client = _client(monkeypatch)

    response = client.get("/api/secrets/usage")
    body = response.get_json()

    assert response.status_code == 409
    assert body["ok"] is False
    assert body["state"] == "integrity_error"
    assert body["stored_keys"] is None
    assert body.get("error")
    assert not body.get("rotation")
    assert _digest(malformed_container_vault) == before


def test_usage_and_status_agree_over_the_same_damaged_vault(
    unparseable_vault, monkeypatch
):
    """Row 553's stated consequence: usage printed ok:true over a vault the
    neighbouring endpoint refuses with 409."""
    client = _client(monkeypatch)

    status = client.get("/api/secrets/status")
    usage = client.get("/api/secrets/usage")

    assert status.status_code == 409, "precondition: the sibling arm refuses"
    assert status.get_json()["state"] == "unreadable"
    assert usage.status_code == status.status_code
    assert usage.get_json()["state"] == status.get_json()["state"]


# ── negative controls: the fix is not a blanket refusal ─────────────


def test_a_readable_locked_vault_still_answers_its_full_inventory(
    monkeypatch, tmp_path
):
    """Negative control 1: the fix added no unlock requirement to a route
    that never had one."""
    _two_key_vault(monkeypatch, tmp_path)
    backend = _open_backend(monkeypatch)
    assert backend.is_unlocked() is False, "precondition: locked"
    assert len(backend.list_keys()) == 2, "precondition: 2 keys readable while locked"
    client = _client(monkeypatch)

    response = client.get("/api/secrets/usage")
    body = response.get_json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert sorted(body["stored_keys"]) == [_KEY_REFERENCED, _KEY_UNREFERENCED]
    assert body["usage"][_KEY_REFERENCED] == ["row488a"]
    assert len([s for s in body["usage"].values() if s]) == 1, (
        "exactly 1 site in the usage map"
    )
    assert body["unreferenced"] == [_KEY_UNREFERENCED]


def test_a_genuinely_empty_readable_vault_still_answers_zero(
    monkeypatch, tmp_path
):
    """Negative control 2: an honest zero stays distinguishable from an
    unavailable one."""
    _install(monkeypatch, tmp_path)
    backend = _open_backend(monkeypatch)
    backend._data["iterations"] = _ITERATIONS
    assert backend.unlock(_MASTER) is True
    assert backend.list_keys() == [], "precondition: a genuinely empty vault"
    client = _client(monkeypatch, sites={})

    response = client.get("/api/secrets/usage")
    body = response.get_json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["stored_keys"] == []
    assert body.get("state") not in {"unreadable", "integrity_error"}
    assert body["usage"] == {}
    assert body["unreferenced"] == []


def test_an_unrecognised_inventory_failure_is_unknown_not_zero(
    monkeypatch, tmp_path
):
    """A7 self-audit: the fix must not leave the same fail-open one branch over.

    An error the route does not diagnose is still an inventory it did not
    read, so it may not be published as zero either.
    """
    _two_key_vault(monkeypatch, tmp_path)
    backend = _open_backend(monkeypatch)

    class _Boom(RuntimeError):
        pass

    def _explode():
        raise _Boom("row488 synthetic unrecognised inventory failure")

    monkeypatch.setattr(backend, "list_keys", _explode)
    assert not isinstance(_Boom(""), (ss.SecretsUnreadableError,
                                      ss.SecretsIntegrityError)), (
        "precondition: the raised type is neither diagnosed refusal"
    )
    client = _client(monkeypatch)

    response = client.get("/api/secrets/usage")
    body = response.get_json()

    assert response.status_code == 409
    assert body["ok"] is False
    assert body["state"] == "unknown", (
        "distinct from both diagnosed states, per CLAUDE.md A7"
    )
    assert body["stored_keys"] is None
    assert "_Boom" in body["error"], "the refusal names what actually failed"
