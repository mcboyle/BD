"""Security pinning test — AF5 (v3.66.42): sensitive files must be written
owner-only (0600), not at the umask default (typically world-readable
0644) on a multi-user host. Mirrors the existing 0600 treatment of
vapid_keys.json in push.py.
"""
import os
import stat

import pytest

from bulk_downloader import cookies as ck
from bulk_downloader import extension_vault as ev
from bulk_downloader import secrets_store as ss

posix_only = pytest.mark.skipif(os.name != "posix",
                                reason="POSIX file modes only")


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@posix_only
def test_vault_tokens_written_0600(monkeypatch, tmp_path):
    f = tmp_path / "vault_tokens.json"
    monkeypatch.setattr(ev, "VAULT_TOKENS_FILE", f)
    assert ev._save_tokens({"pairing": {}, "redeemed": {}}) is True
    assert _mode(f) == 0o600, oct(_mode(f))


@posix_only
def test_cookie_jar_written_0600(tmp_path):
    p = tmp_path / "site_cookies.json"
    ck.save_cookies_to_file(str(p), [{"name": "sid", "value": "secret"}],
                            validate=False)
    assert _mode(p) == 0o600, oct(_mode(p))


@posix_only
def test_secrets_blob_written_0600(monkeypatch, tmp_path):
    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not available")
    f = tmp_path / "secrets.json"
    monkeypatch.setattr(ss, "SECRETS_FILE", f)
    b = ss.MasterPasswordBackend()
    assert b.unlock("pw")
    b.set("k", "v")            # triggers _save
    assert f.exists()
    assert _mode(f) == 0o600, oct(_mode(f))
