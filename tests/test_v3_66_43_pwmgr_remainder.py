"""Security/correctness pinning tests — v3.65.2 pwmgr remainder, v3.66.43.

Each test encodes the broken behaviour as a contract that must hold after
the fix; it fails loudly if the vulnerable behaviour ever returns.

  B5  — migrate_from_plaintext rolls back the backend write on a
        roundtrip-verify mismatch (no password in both stores).
  B6  — MasterPasswordBackend.unlock(wrong) leaves the backend LOCKED.
  B7  — CSV importers handle Title-cased headers (no empty records).
  B8  — Bitwarden parser prefers the first http(s) URI.
  B9  — revoke_by_prefix rejects prefixes < 4 chars.
  B10 — validate_vault_token coalesces last_used_at writes.
  B11 — find_plaintext_passwords / migrate walk accounts[].
  B14 — import_apply slugs by name+username with a per-batch counter.
  B15 — change_password distinguishes wrong-password from corruption/persist.
  B16 — delete clears the canonical @cred: reference.
  B19 — _validate_tunnel_dict rejects empty / ':'-bearing tunnel_id.
  NEW-2 — .tmp siblings of secret files are manifest-excluded.
  NEW-3 — audit_fetch sanitizes control chars (no log injection).
  NEW-4 — validate_vault_token lazy-GC honors _save_tokens (no phantom GC).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from bulk_downloader import extension_vault as ev
from bulk_downloader import password_import as pi
from bulk_downloader import secrets_store as ss
from bulk_downloader import vpn_config as vc


# ── helpers ─────────────────────────────────────────────────────────
def _make_backend(monkeypatch, tmp_path, password="pw"):
    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not available")
    monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.json")
    b = ss.MasterPasswordBackend()
    assert b.unlock(password)
    return b


# ── B6: failed unlock leaves the backend LOCKED ─────────────────────
class TestB6UnlockReset:
    def test_wrong_password_after_good_unlock_relocks(self, monkeypatch, tmp_path):
        b = _make_backend(monkeypatch, tmp_path)
        b.set("k", "v")
        assert b.is_unlocked() is True
        assert b.unlock("wrong") is False
        assert b.is_unlocked() is False
        assert b.get("k") is None

    def test_first_use_branch_unaffected(self, monkeypatch, tmp_path):
        # No ciphertexts yet → any password accepted (first-use).
        monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.json")
        b = ss.MasterPasswordBackend()
        assert b.unlock("anything") is True
        assert b.is_unlocked() is True


# ── B5: rollback on roundtrip mismatch ──────────────────────────────
class TestB5MigrateRollback:
    def test_rollback_no_double_storage(self, monkeypatch, tmp_path):
        b = _make_backend(monkeypatch, tmp_path)
        monkeypatch.setattr(ss, "get_backend", lambda: b)
        # Corrupt the readback so the roundtrip verify fails.
        monkeypatch.setattr(b, "get", lambda k: "WRONG")
        cfg = {"sid1": {"password": "plain"}}
        migrated, errors = ss.migrate_from_plaintext(cfg)
        assert migrated == 0
        # plaintext kept in config
        assert cfg["sid1"]["password"] == "plain"
        # backend copy rolled back — restore the real get to check
        monkeypatch.undo()
        assert b.get(ss.site_password_key("sid1")) is None
        assert any("rolled back" in e for e in errors)


# ── B11: accounts[] are walked ──────────────────────────────────────
class TestB11Accounts:
    def test_find_returns_compound_scopes(self):
        cfg = {"sid1": {"password": "",
                        "accounts": [{"username": "alice", "password": "plain1"},
                                     {"username": "bob", "password": "plain2"}]}}
        got = ss.find_plaintext_passwords(cfg)
        assert ("sid1::account:0", "plain1") in got
        assert ("sid1::account:1", "plain2") in got

    def test_split_scope_helper(self):
        assert ss._split_account_scope("sid1") == ("sid1", None)
        assert ss._split_account_scope("sid1::account:3") == ("sid1", 3)

    def test_account_helpers_exist(self):
        assert ss.account_password_key("wow", 2) == "bulkdl-site-wow-account-2"
        assert ss.make_account_password_reference("wow", 2) == \
            ss.CRED_PREFIX + "bulkdl-site-wow-account-2"

    def test_migrate_routes_accounts(self, monkeypatch, tmp_path):
        b = _make_backend(monkeypatch, tmp_path)
        monkeypatch.setattr(ss, "get_backend", lambda: b)
        cfg = {"sid1": {"password": "",
                        "accounts": [{"username": "a", "password": "p1"},
                                     {"username": "b", "password": "p2"}]}}
        migrated, errors = ss.migrate_from_plaintext(cfg)
        assert migrated == 2, errors
        assert cfg["sid1"]["accounts"][0]["password"] == \
            ss.make_account_password_reference("sid1", 0)
        assert cfg["sid1"]["accounts"][1]["password"] == \
            ss.make_account_password_reference("sid1", 1)


# ── B7: Title-cased CSV headers ─────────────────────────────────────
class TestB7CaseInsensitiveHeaders:
    def test_chrome_title_case(self):
        text = "Name,URL,Username,Password\nGmail,https://g.com,a@x.com,secret1\n"
        recs = pi._parse_chrome(text)
        assert len(recs) == 1
        assert recs[0]["password"] == "secret1"
        assert recs[0]["name"] == "Gmail"

    def test_lastpass_title_case(self):
        text = ("URL,Username,Password,Extra,Name,Grouping,Fav\n"
                "https://g.com,a@x.com,secret2,note,Gmail,grp,0\n")
        recs = pi._parse_lastpass(text)
        assert len(recs) == 1
        assert recs[0]["password"] == "secret2"

    def test_1password_title_case(self):
        text = "Title,Website,Username,Password,Notes\nGmail,https://g.com,a,secret3,n\n"
        recs = pi._parse_1password_csv(text)
        assert len(recs) == 1
        assert recs[0]["password"] == "secret3"

    def test_top_level_detection_with_title_case(self):
        data = b"Name,URL,Username,Password\nGmail,https://g.com,a@x.com,secret\n"
        fmt, recs = pi.import_passwords(data)
        assert fmt == "chrome_csv"
        assert recs[0]["password"] == "secret"


# ── B8: Bitwarden URI preference ────────────────────────────────────
class TestB8BitwardenUri:
    def _item(self, uris):
        return json.dumps({"items": [
            {"type": 1, "name": "Bank",
             "login": {"username": "u", "password": "p", "uris": uris}}]})

    def test_prefers_https_over_app_uri(self):
        recs = pi._parse_bitwarden(self._item([
            {"uri": "androidapp://com.example.bank"},
            {"uri": "iosapp://com.example.bank"},
            {"uri": "https://bank.example.com/login"}]))
        assert recs[0]["url"] == "https://bank.example.com/login"

    def test_falls_back_to_app_uri(self):
        recs = pi._parse_bitwarden(self._item([
            {"uri": "androidapp://com.example.bank"}]))
        assert recs[0]["url"] == "androidapp://com.example.bank"

    def test_empty_uris_still_emits_entry(self):
        recs = pi._parse_bitwarden(self._item([]))
        assert len(recs) == 1
        assert recs[0]["url"] == ""


# ── B9 / B10 / NEW-4: extension vault token paths ───────────────────
def _issue_token(monkeypatch, tmp_path):
    monkeypatch.setattr(ev, "VAULT_TOKENS_FILE", tmp_path / "vault_tokens.json")
    pt = ev.issue_pairing_token()
    vt = ev.redeem_pairing_token(pt, "label")
    assert vt
    return vt


class TestB9RevokePrefix:
    def test_empty_prefix_revokes_nothing(self, monkeypatch, tmp_path):
        vt = _issue_token(monkeypatch, tmp_path)
        assert ev.revoke_by_prefix("") is False
        assert ev.validate_vault_token(vt) is not None

    def test_three_char_prefix_rejected(self, monkeypatch, tmp_path):
        _issue_token(monkeypatch, tmp_path)
        assert ev.revoke_by_prefix("abc") is False

    def test_real_prefix_revokes(self, monkeypatch, tmp_path):
        vt = _issue_token(monkeypatch, tmp_path)
        assert ev.revoke_by_prefix(vt[:8]) is True
        assert ev.validate_vault_token(vt) is None


class TestB10Coalesce:
    def test_window_constant_floor(self):
        assert ev.LAST_USED_COALESCE_SECONDS >= 30.0

    def test_writes_coalesced(self, monkeypatch, tmp_path):
        vt = _issue_token(monkeypatch, tmp_path)
        # One call to stabilize last_used_at, then snapshot.
        ev.validate_vault_token(vt)
        mtime = ev.VAULT_TOKENS_FILE.stat().st_mtime_ns
        for _ in range(20):
            assert ev.validate_vault_token(vt) is not None
        assert ev.VAULT_TOKENS_FILE.stat().st_mtime_ns == mtime


class TestNew4LazyGcRollback:
    def test_gc_persist_failure_leaves_token_on_disk(self, monkeypatch, tmp_path):
        vt = _issue_token(monkeypatch, tmp_path)
        # Age the token past the idle threshold on disk.
        data = ev._load_tokens()
        data["redeemed"][vt]["last_used_at"] = ev._now() - (31 * 86400)
        assert ev._save_tokens(data) is True
        # Now make the GC write fail.
        monkeypatch.setattr(ev, "_save_tokens", lambda d: False)
        assert ev.validate_vault_token(vt) is None  # correct auth result
        # On-disk state must still hold the token (no silent loss).
        on_disk = json.loads(ev.VAULT_TOKENS_FILE.read_text(encoding="utf-8"))
        assert vt in on_disk.get("redeemed", {})


# ── NEW-3: audit log injection ──────────────────────────────────────
class TestNew3AuditInjection:
    def test_newline_and_tab_injection_neutralized(self, monkeypatch, tmp_path):
        log = tmp_path / "vault_access.log"
        monkeypatch.setattr(ev, "VAULT_AUDIT_LOG", log)
        ev.audit_fetch({"label": "x"}, "bulkdl-site-bank",
                       "https://attacker.com\nFORGED\tline", True)
        content = log.read_text(encoding="utf-8")
        assert content.count("\n") == 1            # exactly one entry
        assert "FORGED" in content                 # preserved, but inline
        line = content.rstrip("\n")
        assert line.count("\t") == 4               # 5 fields, 4 separators


# ── NEW-2: .tmp manifest excludes ───────────────────────────────────
class TestNew2ManifestTmp:
    def test_tmp_siblings_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        for name in ("secrets.json.tmp", "secrets_meta.json.tmp",
                     "vault_tokens.json.tmp"):
            assert _manifest_excluded(name) is True
            assert _manifest_excluded(f"bd_home/{name}") is True

    def test_legit_files_still_ship(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("tests/test_secrets_store.py") is False
        assert _manifest_excluded("docs/secrets_design.md") is False


# ── B19: tunnel_id validation ───────────────────────────────────────
class TestB19TunnelId:
    BASE = {"name": "n", "provider": "p", "backend": "b"}

    def test_colon_rejected(self):
        with pytest.raises(ValueError, match=":"):
            vc._validate_tunnel_dict({"tunnel_id": "vpn:bad", **self.BASE})

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            vc._validate_tunnel_dict({"tunnel_id": "", **self.BASE})

    def test_normal_ok(self):
        out = vc._validate_tunnel_dict({"tunnel_id": "vpn-prod-1", **self.BASE})
        assert out["tunnel_id"] == "vpn-prod-1"


# ── app route tests: B14 / B15 / B16 ────────────────────────────────
@contextmanager
def _client(monkeypatch):
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    orig = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        Path(td, "screenshots").mkdir(exist_ok=True)
        try:
            db_init()
            # Force an unlocked master-password backend rooted in tmp.
            monkeypatch.setattr(ss, "SECRETS_FILE", Path(td) / "secrets.json")
            b = ss.MasterPasswordBackend()
            assert b.unlock("masterpw")
            ss._backend = b
            c = A.app.test_client()
            r = c.get("/api/pair"); token = r.get_json()["token"]
            r = c.post("/api/pair/redeem", json={"token": token})
            csrf = r.get_json()["csrf_token"]
            yield c, {"X-CSRF-Token": csrf}, A, b
        finally:
            ss._backend = None
            os.chdir(orig)


class TestB14ImportSlug:
    def test_distinct_usernames_distinct_keys(self, monkeypatch):
        if not ss._CRYPTO_AVAILABLE:
            pytest.skip("cryptography not available")
        with _client(monkeypatch) as (c, H, A, b):
            recs = [{"name": "Gmail", "username": "alice", "password": "p1"},
                    {"name": "Gmail", "username": "bob", "password": "p2"},
                    {"name": "Gmail", "username": "alice-dup", "password": "p3"}]
            r = c.post("/api/secrets/import_apply",
                       json={"records": recs, "site_ids": ["", "", ""]},
                       headers=H)
            assert r.status_code == 200, r.get_json()
            assert r.get_json()["saved"] == 3
            keys = set(b.list_keys())
            assert "bulkdl-import-gmail-alice" in keys
            assert "bulkdl-import-gmail-bob" in keys
            assert "bulkdl-import-gmail-alice-dup" in keys

    def test_identical_records_counter_suffix(self, monkeypatch):
        if not ss._CRYPTO_AVAILABLE:
            pytest.skip("cryptography not available")
        with _client(monkeypatch) as (c, H, A, b):
            recs = [{"name": "Gmail", "username": "alice", "password": "p1"},
                    {"name": "Gmail", "username": "alice", "password": "p2"},
                    {"name": "Gmail", "username": "alice", "password": "p3"}]
            r = c.post("/api/secrets/import_apply",
                       json={"records": recs, "site_ids": ["", "", ""]},
                       headers=H)
            assert r.status_code == 200, r.get_json()
            keys = set(b.list_keys())
            assert "bulkdl-import-gmail-alice" in keys
            assert "bulkdl-import-gmail-alice-2" in keys
            assert "bulkdl-import-gmail-alice-3" in keys


class TestB15ChangePassword:
    def test_wrong_old_password_401(self, monkeypatch):
        if not ss._CRYPTO_AVAILABLE:
            pytest.skip("cryptography not available")
        with _client(monkeypatch) as (c, H, A, b):
            b.set("k", "v")
            r = c.post("/api/secrets/change_password",
                       json={"old_password": "WRONG", "new_password": "longenough"},
                       headers=H)
            assert r.status_code == 401
            assert "incorrect" in r.get_json()["error"]

    def test_clean_rotation_200(self, monkeypatch):
        if not ss._CRYPTO_AVAILABLE:
            pytest.skip("cryptography not available")
        with _client(monkeypatch) as (c, H, A, b):
            b.set("k", "v")
            r = c.post("/api/secrets/change_password",
                       json={"old_password": "masterpw", "new_password": "newlongpw"},
                       headers=H)
            assert r.status_code == 200, r.get_json()
            assert r.get_json()["ok"] is True

    def test_persist_failure_not_reported_as_wrong_password(self, monkeypatch):
        if not ss._CRYPTO_AVAILABLE:
            pytest.skip("cryptography not available")
        with _client(monkeypatch) as (c, H, A, b):
            b.set("k", "v")

            def _boom(self, data):  # noqa: ARG001
                return False
            monkeypatch.setattr(ss.MasterPasswordBackend, "_save", _boom)
            r = c.post("/api/secrets/change_password",
                       json={"old_password": "masterpw", "new_password": "newlongpw"},
                       headers=H)
            assert r.status_code == 500
            assert "incorrect" not in r.get_json()["error"]


class TestB16Delete:
    def test_site_id_clears_canonical_ref(self, monkeypatch):
        if not ss._CRYPTO_AVAILABLE:
            pytest.skip("cryptography not available")
        with _client(monkeypatch) as (c, H, A, b):
            A.s_cfg["sid1"] = {"password": ss.make_password_reference("sid1")}
            b.set(ss.site_password_key("sid1"), "pw")
            r = c.post("/api/secrets/delete",
                       json={"site_id": "sid1"}, headers=H)
            assert r.status_code == 200, r.get_json()
            assert r.get_json()["config_cleaned"] is True
            assert A.s_cfg["sid1"]["password"] == ""

    def test_raw_key_path_does_not_clean_config(self, monkeypatch):
        if not ss._CRYPTO_AVAILABLE:
            pytest.skip("cryptography not available")
        with _client(monkeypatch) as (c, H, A, b):
            A.s_cfg["sid2"] = {"password": ss.make_password_reference("sid2")}
            b.set("bulkdl-site-sid2", "pw")
            r = c.post("/api/secrets/delete",
                       json={"key": "bulkdl-site-sid2"}, headers=H)
            assert r.status_code == 200, r.get_json()
            assert r.get_json()["config_cleaned"] is False
            assert A.s_cfg["sid2"]["password"] == ss.make_password_reference("sid2")


# ── NEW-5: aiassist never sends literal @cred upstream ──────────────
class TestNew5AiKeyLeak:
    def test_unresolvable_cred_returns_empty(self, monkeypatch):
        from bulk_downloader import aiassist
        monkeypatch.setitem(aiassist._config, "api_key", "@cred:nonexistent")
        monkeypatch.setattr(ss, "resolve_password", lambda v: None)
        assert aiassist._resolve_api_key() == ""

    def test_resolve_raise_returns_empty_not_literal(self, monkeypatch):
        from bulk_downloader import aiassist
        monkeypatch.setitem(aiassist._config, "api_key", "@cred:nonexistent")

        def _boom(v):
            raise RuntimeError("locked")
        monkeypatch.setattr(ss, "resolve_password", _boom)
        out = aiassist._resolve_api_key()
        assert out == ""
        assert "@cred:" not in out

    def test_plaintext_passthrough(self, monkeypatch):
        from bulk_downloader import aiassist
        monkeypatch.setitem(aiassist._config, "api_key", "sk-realkey")
        assert aiassist._resolve_api_key() == "sk-realkey"


# ── NEW-6: WindowsCredentialBackend propagates index-save failure ───
class _FakeKeyring:
    def __init__(self):
        self.store = {}
    def set_password(self, svc, k, v):
        self.store[(svc, k)] = v
    def get_password(self, svc, k):
        return self.store.get((svc, k))
    def delete_password(self, svc, k):
        self.store.pop((svc, k), None)


class TestNew6WindowsIndexFailure:
    def _backend(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ss, "SECRETS_META_FILE", tmp_path / "secrets_meta.json")
        b = ss.WindowsCredentialBackend()
        fk = _FakeKeyring()
        monkeypatch.setattr(ss, "keyring", fk, raising=False)
        return b, fk

    def test_set_raises_and_rolls_back_keyring(self, monkeypatch, tmp_path):
        b, fk = self._backend(monkeypatch, tmp_path)
        monkeypatch.setattr(b, "_save_index", lambda keys: False)
        with pytest.raises(ss.SecretsPersistError):
            b.set("bulkdl-site-x", "pw")
        # keyring write rolled back
        assert fk.get_password(ss.KEYRING_SERVICE, "bulkdl-site-x") is None

    def test_delete_raises_on_index_failure(self, monkeypatch, tmp_path):
        b, fk = self._backend(monkeypatch, tmp_path)
        b.set("bulkdl-site-y", "pw")  # real save succeeds here
        monkeypatch.setattr(b, "_save_index", lambda keys: False)
        with pytest.raises(ss.SecretsPersistError):
            b.delete("bulkdl-site-y")


# ── NEW-7: runner._resolve_safe never returns literal @cred ─────────
class TestNew7ResolveSafe:
    def test_import_failure_cred_returns_empty(self, monkeypatch):
        from bulk_downloader import runner
        import sys as _sys
        monkeypatch.setitem(_sys.modules, "bulk_downloader.secrets_store", None)
        assert runner._resolve_safe("@cred:foo") == ""

    def test_plaintext_passthrough(self):
        from bulk_downloader import runner
        assert runner._resolve_safe("real_password") == "real_password"

    def test_runtime_exception_returns_empty(self, monkeypatch):
        from bulk_downloader import runner
        monkeypatch.setattr(ss, "resolve_password",
                            lambda v: (_ for _ in ()).throw(RuntimeError("x")))
        assert runner._resolve_safe("@cred:foo") == ""


# ── NEW-10: extension label sanitized at intake ─────────────────────
class TestNew10LabelSanitize:
    def test_script_payload_neutralized(self):
        out = ev._sanitize_label("<script>alert(1)</script>")
        for ch in "<>();'\"":
            assert ch not in out

    def test_empty_falls_back(self):
        assert ev._sanitize_label("") == "extension"
        assert ev._sanitize_label(None) == "extension"

    def test_normal_label_preserved(self):
        assert ev._sanitize_label("Chrome on My Laptop") == "Chrome on My Laptop"

    def test_roundtrip_through_redeem(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ev, "VAULT_TOKENS_FILE", tmp_path / "vault_tokens.json")
        pt = ev.issue_pairing_token()
        vt = ev.redeem_pairing_token(pt, "<img src=x onerror=alert(1)>")
        toks = ev.list_vault_tokens()
        label = next(t["label"] for t in toks if t.get("label"))
        for ch in "<>=":
            assert ch not in label


# ── NEW-12: pairing-token expiry boundary agreement ─────────────────
class TestNew12ExpiryBoundary:
    def test_cleanup_and_redeem_agree_at_boundary(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ev, "VAULT_TOKENS_FILE", tmp_path / "vault_tokens.json")
        # Freeze the clock so the boundary is exact (no live-clock race).
        monkeypatch.setattr(ev, "_now", lambda: 1_000_000.0)
        boundary_ts = 1_000_000.0 - ev.PAIRING_TOKEN_EXPIRY_SECONDS
        # Exactly at the boundary: not yet expired (matches redeem's `>`).
        assert ev._pairing_expired(boundary_ts) is False
        # One tick past: expired.
        assert ev._pairing_expired(boundary_ts - 1) is True


# ── NEW-13: release-zip secret-leak audit tool ──────────────────────
class TestNew13ZipAudit:
    def _audit(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "audit_release_zips",
            Path(__file__).resolve().parent.parent / "audit_release_zips.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_flags_secret_tmp(self, tmp_path):
        import zipfile
        mod = self._audit()
        zp = tmp_path / "rel.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("BulkDownloader/secrets.json.tmp", "blob")
            zf.writestr("BulkDownloader/app.py", "ok")
        assert "secrets.json.tmp" in mod.audit_zip(zp)

    def test_clean_zip(self, tmp_path):
        import zipfile
        mod = self._audit()
        zp = tmp_path / "clean.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("BulkDownloader/app.py", "ok")
            zf.writestr("BulkDownloader/conf/app_config.example.json", "{}")
        assert mod.audit_zip(zp) == set()
