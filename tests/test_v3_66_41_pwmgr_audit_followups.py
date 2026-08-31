"""Security pinning tests — pwmgr/vault audit follow-ups, v3.66.41.

Source-audit findings in the same defect families as the LIVE bundle:
  AF1 — non-constant-time auth-token comparison (app.py).
  AF2 — malformed secrets.json silently wiped (secrets_store).
  AF3 — _save_tokens swallowed failures → phantom revocation (extension_vault).
  AF4 — malformed vault_tokens.json silently wiped (extension_vault).
"""
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from bulk_downloader import extension_vault as ev
from bulk_downloader import secrets_store as ss


# ── AF1: constant-time auth-token comparison ────────────────────────
class TestAF1ConstantTime:
    def test_token_eq_matches_and_rejects(self):
        from bulk_downloader import app as A
        assert A._token_eq("abc123", "abc123") is True
        assert A._token_eq("abc123", "abc124") is False
        assert A._token_eq("", "x") is False

    def test_token_eq_non_ascii_is_false_not_raise(self):
        from bulk_downloader import app as A
        # compare_digest rejects non-ASCII str; helper must not raise.
        assert A._token_eq("p\u00e1ss", "pass") is False

    def test_uses_compare_digest_not_bare_eq(self):
        # Pin the fix at the source level: the auth gate must not compare
        # the token with a bare ==.
        import inspect
        from bulk_downloader import app as A
        src = inspect.getsource(A._check_token)
        assert "_token_eq(" in src
        assert "== tok" not in src

    def test_gate_rejects_wrong_bearer_accepts_right(self, monkeypatch):
        from bulk_downloader import app as A
        monkeypatch.setenv("BD_AUTH_TOKEN", "right-secret")
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd(); os.chdir(td)
            try:
                Path(td, "screenshots").mkdir(exist_ok=True)
                c = A.app.test_client()
                # /api/status is gated (not in the unauth allowlist).
                bad = c.get("/api/status",
                            headers={"Authorization": "Bearer wrong-secret"})
                good = c.get("/api/status",
                             headers={"Authorization": "Bearer right-secret"})
                assert bad.status_code == 401
                assert good.status_code != 401
            finally:
                os.chdir(cwd)


# ── AF2: malformed secrets.json backed up, never wiped ──────────────
class TestAF2SecretsBackup:
    def test_malformed_file_is_preserved_in_place(self, monkeypatch, tmp_path):
        """AF2's guarantee, strengthened by row 432 (v3.66.1363).

        AF2 required that a malformed secrets.json never be wiped; it met that
        by renaming the file aside and reinitializing fresh. Row 432 measured
        that the rename WAS the defect: SECRETS_FILE.replace() needs directory
        permission rather than file permission, so a transient unreadable file
        renamed the operator's live vault away, and the fresh dict then
        classified an INITIALIZED host as UNINITIALIZED -- the one state in
        which any password durably commits a new empty vault.

        Preservation is now strictly stronger: byte-identical, in place, under
        its own name, with no reinit and no mutation permitted.
        """
        if not ss._CRYPTO_AVAILABLE:
            pytest.skip("cryptography not available")
        f = tmp_path / "secrets.json"
        f.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(ss, "SECRETS_FILE", f)
        b = ss.MasterPasswordBackend()              # triggers _load_or_init
        # Preserved in place, byte-identical, under its own name.
        assert f.exists(), "malformed secrets.json must be preserved, not wiped"
        assert f.read_text(encoding="utf-8") == "{not valid json"
        # No move-aside sibling: the rename is what row 432 removed.
        assert list(tmp_path.glob("secrets.json.corrupt-*")) == []
        # No silent reinit: the store is UNREADABLE, never uninitialized.
        assert b.store_state() == "unreadable"
        assert b.is_initialized() is True
        assert b._data.get("ciphertexts") is None
        # And nothing may write over it.
        with pytest.raises(ss.SecretsUnreadableError):
            b.unlock("af2-synthetic-zero-entropy-password")
        assert f.read_text(encoding="utf-8") == "{not valid json"


# ── AF3: revocation must persist or report failure ──────────────────
class TestAF3RevokePersistence:
    def _setup(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ev, "VAULT_TOKENS_FILE", tmp_path / "vault_tokens.json")
        pt = ev.issue_pairing_token()
        return ev.redeem_pairing_token(pt, "ext")

    def test_save_tokens_returns_bool(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ev, "VAULT_TOKENS_FILE", tmp_path / "vault_tokens.json")
        assert ev._save_tokens({"pairing": {}, "redeemed": {}}) is True

    def test_revoke_reports_failure_when_save_fails(self, monkeypatch, tmp_path):
        vt = self._setup(monkeypatch, tmp_path)
        assert vt and ev.validate_vault_token(vt) is not None
        monkeypatch.setattr(ev, "_save_tokens", lambda data: False)
        # Revocation can't persist → must report False, not phantom success.
        assert ev.revoke_vault_token(vt) is False

    def test_revoke_by_prefix_reports_failure_when_save_fails(self, monkeypatch, tmp_path):
        vt = self._setup(monkeypatch, tmp_path)
        monkeypatch.setattr(ev, "_save_tokens", lambda data: False)
        assert ev.revoke_by_prefix(vt[:8]) is False

    def test_revoke_succeeds_normally(self, monkeypatch, tmp_path):
        vt = self._setup(monkeypatch, tmp_path)
        assert ev.revoke_vault_token(vt) is True
        assert ev.validate_vault_token(vt) is None


# ── AF4: malformed vault_tokens.json preserved in place, never wiped ─
class TestAF4VaultTokensBackup:
    def test_malformed_file_is_preserved_in_place(self, monkeypatch, tmp_path):
        """AF4's guarantee, strengthened by the row-432 family.

        AF4 required that a malformed vault_tokens.json never be wiped; it met
        that by renaming the file to ``vault_tokens.json.corrupt-<ts>`` and
        returning fresh-empty state. The rename WAS the defect:
        ``Path.replace()`` needs directory permission rather than file
        permission, so a chmod-000 file or a transient EIO renamed the
        operator's LIVE token store away exactly as readily as a torn write
        did -- and the fresh dict then reported 0 paired extensions, reported
        revocation of a still-live token as a no-op, and let the next
        ``issue_pairing_token()`` publish a new store under the real name.

        Preservation is now strictly stronger: byte-identical, in place, under
        its own name, with no reinit and every store-touching call refused.
        """
        f = tmp_path / "vault_tokens.json"
        f.write_text("totally not json", encoding="utf-8")
        monkeypatch.setattr(ev, "VAULT_TOKENS_FILE", f)
        mtime = f.stat().st_mtime_ns
        with pytest.raises(ev.VaultTokensUnreadableError):
            ev._load_tokens()
        # Preserved in place, byte-identical, under its own name.
        assert f.exists(), "malformed vault_tokens.json must be preserved"
        assert f.read_text(encoding="utf-8") == "totally not json"
        assert f.stat().st_mtime_ns == mtime
        # No move-aside sibling: the rename is what this change removed.
        assert list(tmp_path.glob("vault_tokens.json.corrupt-*")) == []
        # UNREADABLE is its own state -- never "absent", never empty.
        assert ev.store_state() == "unreadable"
        # And nothing may write a fresh store over it.
        with pytest.raises(ev.VaultTokensUnreadableError):
            ev.issue_pairing_token()
        assert f.read_text(encoding="utf-8") == "totally not json"
