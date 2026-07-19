"""Tests for secrets_store (v3.43.14).

Tests run in this dev sandbox without keyring/cryptography installed.
Coverage:
  - PlaintextBackend: no-op behavior
  - Backend auto-detection picks the right default
  - resolve_password() handles None, plaintext, and @cred: references
  - find_plaintext_passwords walks sites_config correctly
  - migrate_from_plaintext() short-circuits when backend is plaintext
  - make_password_reference() and site_password_key() generate stable
    namespaced keys

Backends that need keyring or cryptography are tested separately when
those libs are present (production install pip-installs both).
"""
from contextlib import contextmanager
from pathlib import Path
import tempfile

from bulk_downloader import secrets_store as ss


@contextmanager
def _isolated_cwd():
    """Switch to a tmpdir for tests that write secrets.json or
    secrets_meta.json. Restores cwd after."""
    import os
    orig = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        try:
            yield Path(td)
        finally:
            os.chdir(orig)


def _reset_backend():
    """Force re-detection so tests don't pick up state from other tests."""
    ss._backend = None
    ss._backend_pref = None


# ─── Backend lookup ───────────────────────────────────────────────

def test_plaintext_backend_get_returns_none():
    b = ss.PlaintextBackend()
    assert b.get("any-key") is None


def test_plaintext_backend_set_is_noop():
    b = ss.PlaintextBackend()
    b.set("k", "v")
    assert b.get("k") is None  # didn't store anywhere


def test_plaintext_backend_list_keys_empty():
    assert ss.PlaintextBackend().list_keys() == []


def test_configure_plaintext():
    _reset_backend()
    assert ss.configure_backend("plaintext") is True
    assert ss.get_backend_name() == "plaintext"


def test_configure_unknown_backend_returns_false():
    _reset_backend()
    assert ss.configure_backend("does_not_exist") is False


# ─── References ────────────────────────────────────────────────────

def test_site_password_key_stable():
    """Key derivation must be stable so renames don't lose passwords."""
    assert ss.site_password_key("abc") == "bulkdl-site-abc"
    assert ss.site_password_key("wow") == "bulkdl-site-wow"


def test_make_password_reference_format():
    ref = ss.make_password_reference("wow")
    assert ref == "@cred:bulkdl-site-wow"
    assert ref.startswith(ss.CRED_PREFIX)


# ─── resolve_password ─────────────────────────────────────────────

def test_resolve_password_none():
    assert ss.resolve_password(None) is None
    assert ss.resolve_password("") is None


def test_resolve_password_plaintext_passthrough():
    """Anything without @cred: prefix is plaintext, returned as-is."""
    assert ss.resolve_password("my-password") == "my-password"
    assert ss.resolve_password("p@ss with spaces!") == "p@ss with spaces!"


def test_resolve_password_cred_reference_with_plaintext_backend():
    """When the backend is plaintext (which doesn't store anything),
    a @cred: reference resolves to None — correct behavior for migration
    edge cases where the JSON has a ref but the backend doesn't have
    the value (shouldn't happen in practice)."""
    _reset_backend()
    ss.configure_backend("plaintext")
    assert ss.resolve_password("@cred:some-key") is None


# ─── find_plaintext_passwords ─────────────────────────────────────

def test_find_plaintext_passwords_empty():
    assert ss.find_plaintext_passwords({}) == []
    assert ss.find_plaintext_passwords({"sid": {"password": ""}}) == []
    assert ss.find_plaintext_passwords({"sid": {}}) == []


def test_find_plaintext_passwords_picks_up_plaintext():
    sites = {
        "wow": {"password": "secret1"},
        "ultraf": {"password": "secret2"},
    }
    found = ss.find_plaintext_passwords(sites)
    assert len(found) == 2
    assert ("wow", "secret1") in found
    assert ("ultraf", "secret2") in found


def test_find_plaintext_passwords_skips_references():
    """Already-migrated entries (with @cred: prefix) are not flagged."""
    sites = {
        "wow": {"password": "@cred:bulkdl-site-wow"},
        "ultraf": {"password": "still plaintext"},
    }
    found = ss.find_plaintext_passwords(sites)
    assert found == [("ultraf", "still plaintext")]


def test_find_plaintext_passwords_ignores_non_dict():
    """Defensive: a malformed sites_config entry shouldn't crash."""
    sites = {"wow": "not a dict", "ultraf": {"password": "p"}}
    found = ss.find_plaintext_passwords(sites)
    assert found == [("ultraf", "p")]


# ─── migrate_from_plaintext ───────────────────────────────────────

def test_migrate_short_circuits_on_plaintext_backend():
    """Migration must refuse to proceed when the backend is plaintext —
    there's nothing to migrate TO."""
    _reset_backend()
    ss.configure_backend("plaintext")
    sites = {"wow": {"password": "secret"}}
    count, errors = ss.migrate_from_plaintext(sites)
    assert count == 0
    assert errors
    assert any("plaintext" in e.lower() for e in errors)
    # Original password preserved
    assert sites["wow"]["password"] == "secret"


# ─── Detect-default ───────────────────────────────────────────────

def test_detect_default_returns_known_name():
    """The default-backend detector must return one of the three
    recognized names so configure_backend() doesn't reject it."""
    name = ss._detect_default_backend_name()
    assert name in ("windows_credential", "master_password", "plaintext")
