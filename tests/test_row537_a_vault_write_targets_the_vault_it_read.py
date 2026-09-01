"""Rows 537, 538, 539 and 540: a write acts on a vault it never proved.

Four confirmed defects, one sentence. v3.66.1384 taught the vault to CLASSIFY
its state correctly, and left every WRITE path free to act on a different file
than the one it classified.

  537  _save() ends in tmp.replace(SECRETS_FILE) and serialises self._data --
       a construction-time snapshot -- over whatever now occupies the path.
       POST /api/backup/restore writes secrets.json to that same relative path
       and never invalidates the cached backend, so the next ordinary credential
       save destroys the restored vault, its salt and every credential in it,
       with no error and no warning.

  538  The row-482 re-probe added at v3.66.1384 is ADVISORY ONLY: it guards the
       first-use branch of _unlock_locked, and the write it is meant to prevent
       is an unconditional os.replace two frames later. A restore landing in the
       window between the probe and the rename destroys the vault anyway --
       measured at 67 clobbers in 400 natural-race trials.

  539  delete() calls _refuse_if_unreadable_locked, which inspects only
       _load_error, and then mutates a vault it has not validated. Over a store
       whose KDF metadata or commitment envelope is damaged -- repairable by
       fixing one field -- it destroys the ciphertext permanently while LOCKED,
       having never unlocked, and answers 200 ok:true.

  540  The same function's `self._data.get("ciphertexts") or {}` launders a
       damaged container into "not present", so the route then wipes the
       operator's only @cred: pointer to a credential the vault has just
       admitted it cannot enumerate.

The fix is one rule: a write proves it is writing over the vault it read, and
refuses when it cannot.
"""
from __future__ import annotations

import json
import os

import pytest

from bulk_downloader import secrets_store as ss

BD_GATE_SCOPE = "module"

_PASSWORD = "row537-synthetic-master-password"
_KEY = "bulkdl-site-row537"
_VALUE = "row537-synthetic-value"


@pytest.fixture
def vault(monkeypatch, tmp_path):
    d = tmp_path / "install"
    d.mkdir()
    monkeypatch.setattr(ss, "SECRETS_FILE", d / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", d / "secrets_meta.json")
    monkeypatch.setattr(ss, "_backend", None)
    monkeypatch.setattr(ss, "_backend_pref", None)
    monkeypatch.setattr(ss, "_audited_cache", None)
    assert ss.configure_backend("master_password") is True
    b = ss.get_backend()
    b._data["iterations"] = 1_000
    assert b.unlock(_PASSWORD) is True
    b.set(_KEY, _VALUE)
    return b, d


# ── 537/538: a write must target the vault it read ────────────────────────

def test_a_save_refuses_when_the_vault_on_disk_was_replaced(vault):
    """RED. The restore case, with no race at all: the file is simply a
    different vault by the time the next credential save happens."""
    backend, d = vault
    ours = ss.SECRETS_FILE.read_bytes()

    # A different vault now occupies the path -- restored from a backup, which
    # writes secrets.json to the working directory and invalidates nothing.
    foreign = json.loads(ours.decode())
    foreign["salt"] = "AAAAAAAAAAAAAAAAAAAAAA=="
    foreign["ciphertexts"] = {"bulkdl-site-someone-elses": {"v": 1, "ct": "zzz"}}
    foreign_bytes = json.dumps(foreign, indent=2).encode()
    ss.SECRETS_FILE.write_bytes(foreign_bytes)
    assert ss.SECRETS_FILE.read_bytes() != ours

    # A silent no-op would itself be a fail-open: the caller would believe the
    # credential was stored. The refusal must be visible.
    with pytest.raises(ss.SecretsIntegrityError):
        backend.set("bulkdl-site-row537-second", "another-value")

    assert ss.SECRETS_FILE.read_bytes() == foreign_bytes, (
        "the ordinary credential save serialised this process's stale snapshot "
        "over a vault it never read -- the restored salt and every credential "
        "in it are gone, with no error and no warning")


def test_the_ordinary_save_path_still_works(vault):
    """POSITIVE CONTROL: the guard must not break every normal write, or the
    vault becomes unwritable and the fix is worse than the defect."""
    backend, _d = vault
    backend.set("bulkdl-site-row537-normal", "value-two")
    assert backend.get("bulkdl-site-row537-normal") == "value-two"
    ss._backend = None
    fresh = ss.MasterPasswordBackend()
    assert fresh.unlock(_PASSWORD) is True
    assert fresh.get("bulkdl-site-row537-normal") == "value-two", (
        "the write did not reach disk")


def test_a_vault_that_appeared_after_construction_is_not_written_over(vault, tmp_path):
    """538: the re-probe was advisory. The WRITE has to refuse too."""
    _backend, d = vault
    ss._backend = None
    ss.SECRETS_FILE.unlink()
    fresh = ss.MasterPasswordBackend()          # constructed over an absent file
    assert fresh._load_error is None

    donor = {"version": 1, "kdf": "pbkdf2-sha256", "iterations": 1000,
             "salt": "BBBBBBBBBBBBBBBBBBBBBB==",
             "ciphertexts": {"bulkdl-site-restored": {"v": 1, "ct": "yyy"}}}
    donor_bytes = json.dumps(donor, indent=2).encode()
    ss.SECRETS_FILE.write_bytes(donor_bytes)

    with pytest.raises(ss.SecretsIntegrityError):
        fresh.unlock("any-password-at-all-8")
    assert ss.SECRETS_FILE.read_bytes() == donor_bytes


# ── 539/540: delete must validate what it mutates ─────────────────────────

def test_delete_refuses_a_damaged_ciphertexts_container(vault):
    """540. `or {}` turned a damaged container into 'not present', and the
    route then wiped the operator's only pointer to the credential."""
    backend, _d = vault
    backend._data["ciphertexts"] = ["not", "a", "mapping"]
    with pytest.raises(ss.SecretsIntegrityError):
        backend.delete(_KEY)


def test_delete_refuses_a_damaged_vault_rather_than_destroying_it(vault):
    """539. Over a repairable store, delete destroyed the ciphertext
    permanently while LOCKED, never having unlocked, and answered ok:true."""
    backend, _d = vault
    backend._key = None                      # locked
    del backend._data["salt"]                # damaged, repairable by one field
    before = dict(backend._data.get("ciphertexts") or {})
    with pytest.raises(ss.SecretsIntegrityError):
        backend.delete(_KEY)
    assert dict(backend._data.get("ciphertexts") or {}) == before, (
        "the ciphertext was destroyed over a vault delete never validated")


def test_delete_still_removes_a_key_from_a_healthy_vault(vault):
    """POSITIVE CONTROL."""
    backend, _d = vault
    backend.set("bulkdl-site-row537-doomed", "gone-soon")
    assert backend.delete("bulkdl-site-row537-doomed") is True
    assert backend.get("bulkdl-site-row537-doomed") is None
    assert backend.get(_KEY) == _VALUE, "an unrelated credential was removed"
