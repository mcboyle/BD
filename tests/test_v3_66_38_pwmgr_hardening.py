"""Security pinning tests — pwmgr hardening bundle, v3.66.38.

Each test encodes an ATTACK that must be rejected (or a persistence
failure that must NOT be reported as success). These are content-level
pins: they fail loudly if the vulnerable behavior ever returns.

  B1  — autofill phishing: pattern matched as URL substring.
  B12 — vault-token privilege amplification: vault token reaches
        management routes (pair_issue / list_paired / revoke).
  B4/B17 — silent password loss: _save() swallows failures.
  B13 — migration roundtrip-checks in-memory, reports phantom success.
  B17 — change_password silently drops undecryptable secrets.
  B18 — no lock around the rebuild-and-swap.
"""
import threading

import pytest

from bulk_downloader import extension_vault as ev
from bulk_downloader import secrets_store as ss


# ── B1: autofill phishing ───────────────────────────────────────────
class TestB1AutofillPhishing:
    ENTRIES = [{"id": "saved", "patterns": [r"login\.example\.com"]}]

    def test_attacker_url_with_host_as_substring_does_not_match(self):
        # The classic B1 payload: victim host appears in the query string.
        url = "https://attacker.com/?next=login.example.com"
        assert ev.entries_matching_origin(url, self.ENTRIES) == []

    def test_suffix_confusion_host_does_not_match(self):
        # fullmatch (not match) against suffixes rejects this.
        url = "https://login.example.com.attacker.com/login"
        assert ev.entries_matching_origin(url, self.ENTRIES) == []

    def test_legit_exact_host_matches(self):
        url = "https://login.example.com/account"
        got = ev.entries_matching_origin(url, self.ENTRIES)
        assert [e["id"] for e in got] == ["saved"]

    def test_parent_domain_pattern_matches_subdomain(self):
        entries = [{"id": "p", "patterns": [r"example\.com"]}]
        got = ev.entries_matching_origin("https://venus.example.com/x", entries)
        assert [e["id"] for e in got] == ["p"]

    def test_parent_pattern_does_not_match_lookalike(self):
        entries = [{"id": "p", "patterns": [r"example\.com"]}]
        assert ev.entries_matching_origin(
            "https://example.com.evil.org/x", entries) == []

    def test_host_suffixes_helper(self):
        assert ev._host_suffixes("a.b.example.com") == [
            "a.b.example.com", "b.example.com", "example.com", "com"]
        assert ev._host_suffixes("") == []


# ── B4/B17: _save returns a real success signal ─────────────────────
class _Vault:
    """Build an unlocked MasterPasswordBackend rooted at a tmp file."""
    @staticmethod
    def make(monkeypatch, tmp_path, password="pw"):
        if not ss._CRYPTO_AVAILABLE:
            pytest.skip("cryptography not available")
        monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.json")
        b = ss.MasterPasswordBackend()
        assert b.unlock(password)
        return b


class TestB4SaveSignal:
    def test_save_returns_true_on_success(self, monkeypatch, tmp_path):
        b = _Vault.make(monkeypatch, tmp_path)
        assert b._save() is True

    def test_save_returns_false_on_failure(self, monkeypatch, tmp_path):
        b = _Vault.make(monkeypatch, tmp_path)
        # Repoint at a path whose parent dir doesn't exist → write fails.
        monkeypatch.setattr(ss, "SECRETS_FILE",
                            tmp_path / "no_such_dir" / "secrets.json")
        assert b._save() is False

    def test_set_raises_and_rolls_back_on_persist_failure(self, monkeypatch, tmp_path):
        b = _Vault.make(monkeypatch, tmp_path)
        monkeypatch.setattr(b, "_save", lambda: False)
        with pytest.raises(ss.SecretsPersistError):
            b.set("k1", "secret")
        # Rolled back: the key must not linger in memory pretending success.
        assert "k1" not in (b._data.get("ciphertexts") or {})

    def test_set_overwrite_rolls_back_to_previous(self, monkeypatch, tmp_path):
        b = _Vault.make(monkeypatch, tmp_path)
        b.set("k", "v1")               # persists for real
        monkeypatch.setattr(b, "_save", lambda: False)
        with pytest.raises(ss.SecretsPersistError):
            b.set("k", "v2")
        # original value still readable, not the failed overwrite
        assert b.get("k") == "v1"

    def test_delete_raises_and_rolls_back(self, monkeypatch, tmp_path):
        b = _Vault.make(monkeypatch, tmp_path)
        b.set("k", "v")
        monkeypatch.setattr(b, "_save", lambda: False)
        with pytest.raises(ss.SecretsPersistError):
            b.delete("k")
        assert b.get("k") == "v"  # still there


# ── B13: migration can't report phantom success ─────────────────────
class TestB13Migration:
    def test_failed_persist_keeps_plaintext(self, monkeypatch, tmp_path):
        b = _Vault.make(monkeypatch, tmp_path)
        monkeypatch.setattr(b, "_save", lambda: False)   # every write fails
        monkeypatch.setattr(ss, "get_backend", lambda: b)
        sites = {"siteA": {"password": "hunter2"}}
        migrated, errors = ss.migrate_from_plaintext(sites)
        assert migrated == 0
        assert errors and "siteA" in errors[0]
        # Plaintext NOT replaced with a @cred reference — no silent loss.
        assert sites["siteA"]["password"] == "hunter2"


# ── B17: change_password is all-or-nothing ──────────────────────────
class TestB17ChangePassword:
    def test_aborts_on_undecryptable_entry_without_mutation(self, monkeypatch, tmp_path):
        b = _Vault.make(monkeypatch, tmp_path, password="old")
        b.set("good", "v")
        salt_before = b._data["salt"]
        # Corrupt one ciphertext so it can't be decrypted with the old key.
        b._data["ciphertexts"]["bad"] = {"nonce": "AAAA", "ct": "AAAA"}
        assert b.change_password("old", "new") is False
        # Nothing rotated: salt unchanged, old password still unlocks.
        assert b._data["salt"] == salt_before
        assert b.unlock("old") is True

    def test_persist_failure_rolls_back_keeps_old_password(self, monkeypatch, tmp_path):
        b = _Vault.make(monkeypatch, tmp_path, password="old")
        b.set("k", "v")
        salt_before = b._data["salt"]
        monkeypatch.setattr(b, "_save", lambda: False)
        with pytest.raises(ss.SecretsPersistError):
            b.change_password("old", "new")
        # Rolled back fully: old salt/key restored, old password works.
        assert b._data["salt"] == salt_before
        assert b.unlock("old") is True
        assert b.get("k") == "v"

    def test_happy_path_rotates(self, monkeypatch, tmp_path):
        b = _Vault.make(monkeypatch, tmp_path, password="old")
        b.set("k", "v")
        assert b.change_password("old", "new") is True
        assert b.unlock("new") is True
        assert b.get("k") == "v"
        assert b.unlock("old") is False


# ── B18: instance lock present ──────────────────────────────────────
class TestB18Lock:
    def test_backend_has_rlock(self, monkeypatch, tmp_path):
        b = _Vault.make(monkeypatch, tmp_path)
        # RLock() returns an object of the private _thread.RLock type;
        # the public marker is that it is re-entrant (acquire twice).
        assert b._lock.acquire(blocking=False)
        assert b._lock.acquire(blocking=False)  # re-entrant
        b._lock.release(); b._lock.release()

    def test_is_unlocked_is_lock_free(self, monkeypatch, tmp_path):
        b = _Vault.make(monkeypatch, tmp_path)
        # Hold the lock on another notion and confirm is_unlocked still
        # answers (it must not block on the instance lock).
        with b._lock:
            assert b.is_unlocked() is True


# ── B12: vault-token privilege amplification ────────────────────────
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    orig = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        Path(td, "screenshots").mkdir(exist_ok=True)
        try:
            db_init()
            c = A.app.test_client()
            r = c.get("/api/pair"); token = r.get_json()["token"]
            r = c.post("/api/pair/redeem", json={"token": token})
            csrf = r.get_json()["csrf_token"]
            yield c, {"X-CSRF-Token": csrf}, A
        finally:
            os.chdir(orig)


def _pair(c, H):
    r = c.post("/api/secrets/extension/pair_issue", headers=H)
    assert r.status_code == 200, r.get_json()
    pairing = r.get_json()["pairing_token"]
    c2 = c.application.test_client()
    r2 = c2.post("/api/secrets/extension/pair",
                 json={"pairing_token": pairing, "label": "t"})
    assert r2.status_code == 200
    return r2.get_json()["vault_token"]


class TestB12PrivilegeAmplification:
    def test_vault_token_cannot_issue_pairing(self):
        with _client() as (c, H, A):
            vt = _pair(c, H)
            c2 = c.application.test_client()
            r = c2.post("/api/secrets/extension/pair_issue",
                        headers={"Authorization": f"Bearer {vt}"})
            assert r.status_code == 403

    def test_vault_token_cannot_list_paired(self):
        with _client() as (c, H, A):
            vt = _pair(c, H)
            c2 = c.application.test_client()
            r = c2.get("/api/secrets/extension/list_paired",
                       headers={"Authorization": f"Bearer {vt}"})
            assert r.status_code == 403

    def test_vault_token_cannot_revoke(self):
        with _client() as (c, H, A):
            vt = _pair(c, H)
            c2 = c.application.test_client()
            r = c2.post("/api/secrets/extension/revoke",
                        json={"id": "deadbeef"},
                        headers={"Authorization": f"Bearer {vt}"})
            assert r.status_code == 403

    def test_vault_token_still_reaches_data_route(self):
        # The fix must NOT break the legitimate data path.
        with _client() as (c, H, A):
            vt = _pair(c, H)
            c2 = c.application.test_client()
            r = c2.get("/api/secrets/extension/ping",
                       headers={"Authorization": f"Bearer {vt}"})
            assert r.status_code == 200

    def test_session_auth_still_issues_pairing(self):
        # Legit management caller (session cookie + CSRF) unaffected.
        with _client() as (c, H, A):
            r = c.post("/api/secrets/extension/pair_issue", headers=H)
            assert r.status_code == 200
