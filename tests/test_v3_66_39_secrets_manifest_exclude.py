"""v3.66.39 B2: dev_suite._manifest_excluded must filter pwmgr secrets.

The release-zip builder and verifier share dev_suite._manifest_excluded()
to decide which files belong in a release. The password-manager runtime
files are generated next to the DB on first use, so in any tree where the
app or suite has run they would otherwise ship inside the release zip:

  vault_tokens.json  — LIVE bearer vault tokens (working credentials)
  secrets.json       — AES-GCM ciphertext blob + salt/iteration params
  secrets_meta.json  — the secret-key index

Same release-leak defect class as the shipped B5 vapid_keys.json fix,
with worse blast radius for vault_tokens.json. This test pins the fix and
ties each exclusion to the filename constant the owning module actually
writes, so a rename there fails loudly here.
"""
from __future__ import annotations


class TestSecretsFilesExcluded:
    def test_vault_tokens_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("vault_tokens.json") is True

    def test_secrets_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("secrets.json") is True

    def test_secrets_meta_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("secrets_meta.json") is True

    def test_excluded_even_in_a_subdir(self):
        # Generated next to the DB, which may live under a BD_HOME subdir.
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("bd_home/vault_tokens.json") is True
        assert _manifest_excluded("data/secrets.json") is True

    def test_example_config_still_ships(self):
        # Sanity: the exclusion isn't accidentally too broad — the
        # shipped example config and normal source must NOT be filtered.
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("sites_config.example.json") is False
        assert _manifest_excluded("bulk_downloader/secrets_store.py") is False

    def test_vapid_keys_still_excluded(self):
        """Regression guard — don't lose the B5 exclusion."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("vapid_keys.json") is True

    def test_exclusion_tracks_source_filename_constants(self):
        """Pin each exclusion to the constant the owning module writes,
        so a rename of the on-disk file forces an update here."""
        from bulk_downloader.dev_suite import _MANIFEST_EXCLUDE_NAMES
        from bulk_downloader import secrets_store as ss
        from bulk_downloader import extension_vault as ev
        assert ss.SECRETS_FILE.name in _MANIFEST_EXCLUDE_NAMES, (
            f"secrets_store.SECRETS_FILE={ss.SECRETS_FILE.name!r} must be "
            f"excluded — else the release zip ships the secrets blob")
        assert ss.SECRETS_META_FILE.name in _MANIFEST_EXCLUDE_NAMES, (
            f"secrets_store.SECRETS_META_FILE={ss.SECRETS_META_FILE.name!r} "
            f"must be excluded")
        assert ev.VAULT_TOKENS_FILE.name in _MANIFEST_EXCLUDE_NAMES, (
            f"extension_vault.VAULT_TOKENS_FILE={ev.VAULT_TOKENS_FILE.name!r} "
            f"must be excluded — these are LIVE bearer tokens")
