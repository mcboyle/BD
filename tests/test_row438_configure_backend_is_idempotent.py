"""Row 438: configure_backend forked the vault into two writers.

``configure_backend("master_password")`` executed ``_backend =
MasterPasswordBackend()`` unconditionally -- no short-circuit when the named
backend was already active -- and ``POST /api/secrets/configure`` reached it
directly. The new instance snapshots secrets.json into its own ``_data`` at
construction and starts LOCKED, so re-selecting the ACTIVE backend also
silently relocked a deliberately-unlocked vault.

Any holder of the old instance -- a long-running import_apply loop that
captured ``backend = ss.get_backend()``, or the ``_audited_cache`` wrapper --
kept writing through the old object. Each instance's ``_save()`` persists its
ENTIRE ``_data`` under its own independent RLock, so the first save from the
stale-snapshot instance after the other persisted new entries rewrote
secrets.json without them: silent credential loss, with success reported to
both callers.

CLAUDE.md A7: a save whose success cannot be measured is UNKNOWN, never OK.

MEASURED AT THIS BASE, and it changes what the RED looks like: the SILENT
erasure the row describes is already intercepted. v3.66.1390 ("a vault write
proves it is writing over the vault it read") makes the stale holder's _save()
refuse with SecretsUnreadableError -- "the vault ... is not the file this
process read ... so this save would serialise a stale snapshot over it" --
rather than rewrite the file without the other writer's entries. So the fork
today costs a hard refusal and a relocked vault, not silent credential loss.
The fork itself, and the silent relock, are still live and are what this module
pins. The third test below RED-ed on that refusal, not on a vanished key.

Every password in this module is a documented zero-entropy synthetic literal.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bulk_downloader import auth_throttle as at
from bulk_downloader import secrets_store as ss


BD_GATE_SCOPE = "module"

# Documented zero-entropy synthetic values.  None of these is a credential.
_MASTER = "row438-synthetic-master-password"
_K0 = "bulkdl-site-row438-seed"
_K1 = "bulkdl-site-row438-one"
_K2 = "bulkdl-site-row438-two"
_VALUE = "row438-synthetic-value"
_ITERATIONS = 1_000


@pytest.fixture
def vault(monkeypatch, tmp_path) -> Path:
    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not available")
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", tmp_path / "secrets_meta.json")
    monkeypatch.setattr(ss, "_backend", None)
    monkeypatch.setattr(ss, "_audited_cache", None)
    monkeypatch.setattr(ss, "_backend_pref", None)
    at.reset()
    yield tmp_path / "secrets.json"
    at.reset()


def _ciphertexts(path: Path) -> dict:
    blob = json.loads(path.read_text(encoding="utf-8"))
    entries = blob.get("ciphertexts") or {}
    # The reserved rollback-commitment entry is product bookkeeping, not a
    # user credential, so it is excluded from every count below.
    reserved = ss.MasterPasswordBackend._ROLLBACK_COMMITMENT_KEY
    return {k: v for k, v in entries.items() if k != reserved}


def _raw(backend):
    """Unwrap the BD_SECRETS_AUDIT proxy if one is in play."""
    return getattr(backend, "_backend", backend)


def _seeded(monkeypatch, path: Path):
    """An initialized, UNLOCKED master-password vault holding exactly 1 key."""
    assert ss.configure_backend("master_password") is True
    a = _raw(ss.get_backend())
    a._data["iterations"] = _ITERATIONS
    assert a.unlock(_MASTER) is True
    a.set(_K0, _VALUE)
    assert sorted(_ciphertexts(path)) == [_K0], "precondition: exactly 1 key on disk"
    assert a.is_unlocked() is True, "precondition: deliberately unlocked"
    return a


# ── re-selecting the active backend ─────────────────────────────────


def test_reselecting_the_active_backend_returns_the_same_instance(
    vault, monkeypatch
):
    """Row 438 RED: a second distinct instance was constructed, and it was locked."""
    a = _seeded(monkeypatch, vault)

    assert ss.configure_backend("master_password") is True
    b = _raw(ss.get_backend())

    assert b is a, "re-selecting the ACTIVE backend must not fork a second writer"
    assert b.is_unlocked() is True, (
        "re-selection must not silently relock a deliberately-unlocked vault"
    )
    assert ss.get_backend_name() == "master_password"


def test_reselection_constructs_exactly_one_backend(vault, monkeypatch):
    """The construction count is the seam: exactly 1, not 2."""
    _seeded(monkeypatch, vault)
    built: list[int] = []
    real_init = ss.MasterPasswordBackend.__init__

    def _counting(self, *a, **k):
        built.append(1)
        return real_init(self, *a, **k)

    monkeypatch.setattr(ss.MasterPasswordBackend, "__init__", _counting)
    ss.MasterPasswordBackend()
    assert len(built) == 1, "precondition: the counter fires"
    built.clear()

    assert ss.configure_backend("master_password") is True

    assert built == [], "re-selection constructed a second MasterPasswordBackend"


def test_a_stale_holder_cannot_erase_the_other_instances_entries(
    vault, monkeypatch
):
    """The consequence: silent credential loss with success reported to both.

    A holder of the old instance keeps writing through it, and its _save()
    persists its ENTIRE stale _data -- so the first stale save after the other
    instance persisted new entries rewrites secrets.json without them.

    At this base the vault-identity guard turns that into a hard
    SecretsUnreadableError instead, which is the RED this test actually
    observed. Either way the fork is the defect: with one instance the write
    simply lands, and all three keys are on disk.
    """
    a = _seeded(monkeypatch, vault)

    assert ss.configure_backend("master_password") is True
    b = _raw(ss.get_backend())
    if b.is_unlocked() is False:
        assert b.unlock(_MASTER) is True

    before = len(_ciphertexts(vault))
    b.set(_K1, _VALUE)
    assert len(_ciphertexts(vault)) == before + 1, (
        "precondition: the on-disk ciphertext count rose by exactly 1"
    )
    assert _K1 in _ciphertexts(vault)

    # The stale holder writes. Its save reports success either way.
    a.set(_K2, _VALUE)

    on_disk = _ciphertexts(vault)
    assert _K1 in on_disk, (
        "the stale holder's save erased an entry the other writer persisted"
    )
    assert _K2 in on_disk
    assert _K0 in on_disk
    assert sorted(on_disk) == sorted([_K0, _K1, _K2])
    assert len(on_disk) == 3


# ── negative controls ───────────────────────────────────────────────


def test_selecting_a_different_backend_still_constructs_a_fresh_instance(
    vault, monkeypatch
):
    """Negative control: the short-circuit must not block a real switch."""
    a = _seeded(monkeypatch, vault)
    assert ss.get_backend_name() == "master_password"

    assert ss.configure_backend("plaintext") is True
    b = _raw(ss.get_backend())

    assert b is not a, "a genuinely different backend name constructs afresh"
    assert b.name == "plaintext"
    assert ss.get_backend_name() == "plaintext"
    assert ss._backend_pref == "plaintext", "_backend_pref switched"

    # And switching back constructs afresh again, because it is not active.
    assert ss.configure_backend("master_password") is True
    c = _raw(ss.get_backend())
    assert c is not b
    assert c.name == "master_password"
    assert ss._backend_pref == "master_password"


def test_an_unknown_backend_name_is_still_refused(vault, monkeypatch):
    """Negative control: the short-circuit did not make every name succeed."""
    a = _seeded(monkeypatch, vault)

    assert ss.configure_backend("row438-does-not-exist") is False

    assert _raw(ss.get_backend()) is a, "a refused switch changes nothing"
    assert ss._backend_pref == "master_password"


def test_selecting_from_no_active_backend_still_constructs(vault, monkeypatch):
    """Negative control: the short-circuit must not skip the FIRST selection."""
    assert ss._backend is None, "precondition: nothing is active yet"

    assert ss.configure_backend("master_password") is True

    backend = _raw(ss.get_backend())
    assert backend is not None
    assert backend.name == "master_password"
    assert ss._backend_pref == "master_password"
