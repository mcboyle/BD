"""v3.66.663 -- POS-1: operator-selectable backup-encryption scope.

backup.py already did OPT-IN, operator-passphrase (PBKDF2->Fernet, nothing stored),
WHOLE-archive encryption (BDBK magic wrapper). This adds the operator-selectable SCOPE
the operator asked for: encrypt_scope="sensitive" encrypts only the sensitive members
in-place (secrets*.json, vault_tokens.json, cookies/*, state/*) -- ciphertext under their
normal arcnames -- leaving the rest of the backup plaintext + directly inspectable. The
manifest carries encryption metadata (scope, salt, iterations, members) so restore knows
which members to decrypt. scope="all" (default) keeps the current whole-wrap behavior; no
passphrase = plaintext (opt-in preserved). Passphrase is never stored.
"""
import io
import json
import zipfile
from pathlib import Path

from bulk_downloader import backup as bk


def _seed(tmp_path):
    (tmp_path / "sites_config.json").write_text('{"a":1}')       # non-sensitive
    (tmp_path / "secrets.json").write_text("VAULT-SECRET-BLOB")   # sensitive
    (tmp_path / "cookies").mkdir()
    (tmp_path / "cookies" / "site.json").write_text("SESSION-COOKIE")  # sensitive
    return tmp_path


def test_sensitive_scope_encrypts_only_sensitive_members(tmp_path):
    _seed(tmp_path)
    out = tmp_path / "bk.zip"
    r = bk.create_backup(out, base_dir=tmp_path, include_db=False,
                         passphrase="pw", encrypt_scope="sensitive")
    assert r["ok"] and r["encrypted"]
    raw = out.read_bytes()
    assert not raw.startswith(b"BDBK"), "sensitive scope must NOT whole-wrap"
    zf = zipfile.ZipFile(io.BytesIO(raw))
    # non-sensitive member is plaintext-readable without the passphrase
    assert zf.read("sites_config.json") == b'{"a":1}'
    # sensitive members are NOT plaintext in the zip
    assert b"VAULT-SECRET-BLOB" not in zf.read("secrets.json")
    assert b"SESSION-COOKIE" not in zf.read("cookies/site.json")
    # manifest records the encryption scope + members
    man = json.loads(zf.read("_manifest.json"))
    enc = man["encryption"]
    assert enc["scope"] == "sensitive"
    assert "secrets.json" in enc["members"]
    assert "cookies/site.json" in enc["members"]
    assert "sites_config.json" not in enc["members"]
    assert enc.get("salt") and enc.get("iterations")


def test_sensitive_round_trip_restores_plaintext(tmp_path):
    _seed(tmp_path)
    out = tmp_path / "bk.zip"
    bk.create_backup(out, base_dir=tmp_path, include_db=False,
                     passphrase="pw", encrypt_scope="sensitive")
    dest = tmp_path / "restored"
    r = bk.restore_backup(out, target_dir=dest, passphrase="pw")
    assert r["ok"], r.get("error")
    assert (dest / "secrets.json").read_text() == "VAULT-SECRET-BLOB"
    assert (dest / "cookies" / "site.json").read_text() == "SESSION-COOKIE"
    assert (dest / "sites_config.json").read_text() == '{"a":1}'


def test_sensitive_wrong_passphrase_fails_closed(tmp_path):
    _seed(tmp_path)
    out = tmp_path / "bk.zip"
    bk.create_backup(out, base_dir=tmp_path, include_db=False,
                     passphrase="right", encrypt_scope="sensitive")
    dest = tmp_path / "restored"
    r = bk.restore_backup(out, target_dir=dest, passphrase="wrong")
    assert not r["ok"]
    assert not (dest / "secrets.json").exists(), "no partial restore on bad passphrase"


def test_sensitive_missing_passphrase_fails_closed(tmp_path):
    _seed(tmp_path)
    out = tmp_path / "bk.zip"
    bk.create_backup(out, base_dir=tmp_path, include_db=False,
                     passphrase="pw", encrypt_scope="sensitive")
    r = bk.restore_backup(out, target_dir=tmp_path / "restored")
    assert not r["ok"]
    assert r.get("encrypted") is True


def test_scope_all_still_whole_wraps(tmp_path):
    _seed(tmp_path)
    out = tmp_path / "bk.zip"
    bk.create_backup(out, base_dir=tmp_path, include_db=False,
                     passphrase="pw", encrypt_scope="all")
    assert out.read_bytes().startswith(b"BDBK")


def test_no_passphrase_is_plaintext(tmp_path):
    _seed(tmp_path)
    out = tmp_path / "bk.zip"
    r = bk.create_backup(out, base_dir=tmp_path, include_db=False)
    assert r["ok"] and not r["encrypted"]
    zf = zipfile.ZipFile(out)
    assert zf.read("secrets.json") == b"VAULT-SECRET-BLOB"  # plaintext, opt-in
