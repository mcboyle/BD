"""Tests for the v3.43.14 secrets API endpoints.

Covers the endpoint surface that exists in this sandbox without
keyring/cryptography installed:

  GET  /api/secrets/status
  POST /api/secrets/configure
  POST /api/secrets/migrate   (with plaintext backend → refuses)
  POST /api/secrets/import_file
  POST /api/secrets/import_apply   (with plaintext backend → refuses)
  POST /api/secrets/delete
  POST /api/secrets/unlock   (plaintext backend → no-op ok)

Tests that need an actual keyring or master-password backend live in
test_secrets_store.py — same file but only the parts that work
without those libs.
"""
import json
import tempfile
import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _client_with_isolated_cwd():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    from bulk_downloader import secrets_store as ss
    orig_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        # The site-creation path calls mkdir on screenshots/<sid>, which
        # needs the parent screenshots/ to exist. Constants.py creates
        # it at import-time, but only in the original cwd.
        Path(td, "screenshots").mkdir(exist_ok=True)
        try:
            db_init()
            c = A.app.test_client()
            r = c.get('/api/pair'); token = r.get_json()['token']
            r = c.post('/api/pair/redeem', json={'token': token})
            csrf = r.get_json()['csrf_token']
            H = {'X-CSRF-Token': csrf}
            # Force backend reset so each test starts clean
            ss._backend = None
            ss._backend_pref = None
            yield c, H
        finally:
            os.chdir(orig_cwd)


def test_status_returns_backend_info():
    with _client_with_isolated_cwd() as (c, H):
        r = c.get('/api/secrets/status', headers=H)
        d = r.get_json()
        assert r.status_code == 200
        assert d['ok'] is True
        assert 'backend' in d
        assert 'plaintext_count' in d
        assert 'keyring_available' in d
        assert 'crypto_available' in d


def test_status_reports_plaintext_passwords():
    with _client_with_isolated_cwd() as (c, H):
        from bulk_downloader import app as A
        # A legacy or hand-edited sites_config can still hold a PLAINTEXT
        # password — detecting those for migration is this endpoint's whole
        # purpose. v3.66.326 routes newly-created passwords to the vault, so
        # seed the plaintext directly to exercise find_plaintext_passwords
        # rather than relying on create (which no longer produces plaintext).
        r = c.post('/api/sites', json={'name': 'wow'}, headers=H)
        assert r.status_code == 200
        sid = r.get_json()['id']
        A.s_cfg[sid]['password'] = 'secret123'   # simulate legacy plaintext

        r = c.get('/api/secrets/status', headers=H)
        d = r.get_json()
        assert d['plaintext_count'] >= 1
        assert sid in d['plaintext_sites']


def test_configure_plaintext():
    with _client_with_isolated_cwd() as (c, H):
        r = c.post('/api/secrets/configure', json={'backend': 'plaintext'},
                   headers=H)
        assert r.status_code == 200
        assert r.get_json()['backend'] == 'plaintext'


def test_configure_unknown_rejected():
    with _client_with_isolated_cwd() as (c, H):
        r = c.post('/api/secrets/configure',
                   json={'backend': 'does_not_exist'}, headers=H)
        assert r.status_code == 400


def test_migrate_refuses_with_plaintext_backend():
    """Migration to plaintext makes no sense — must error."""
    with _client_with_isolated_cwd() as (c, H):
        c.post('/api/secrets/configure', json={'backend': 'plaintext'},
               headers=H)
        # Create a site with a password
        r = c.post('/api/sites', json={'name': 'wow', 'password': 'secret123'},
                   headers=H)
        sid = r.get_json()['id']
        # Try to migrate
        r = c.post('/api/secrets/migrate', json={}, headers=H)
        d = r.get_json()
        # Endpoint returns ok:true even when migration count is 0, but
        # errors list explains the no-op
        assert d['ok'] is True
        assert d['migrated'] == 0
        assert d['errors']


def test_import_file_json_bitwarden():
    """Bitwarden JSON content can be parsed via the API."""
    with _client_with_isolated_cwd() as (c, H):
        payload = json.dumps({
            "encrypted": False,
            "items": [{
                "type": 1, "name": "WowGirls",
                "login": {"username": "matt", "password": "p",
                          "uris": [{"uri": "https://wowgirls.com"}]}
            }]
        })
        r = c.post('/api/secrets/import_file',
                   json={'content': payload}, headers=H)
        d = r.get_json()
        assert r.status_code == 200
        assert d['ok'] is True
        assert d['format'] == 'bitwarden_json'
        assert d['count'] == 1


def test_import_file_rejects_garbage():
    with _client_with_isolated_cwd() as (c, H):
        r = c.post('/api/secrets/import_file',
                   json={'content': 'not a password file'}, headers=H)
        assert r.status_code == 400
        d = r.get_json()
        assert d['ok'] is False


def test_import_apply_refuses_with_plaintext_backend():
    """Apply must require an encrypted backend."""
    with _client_with_isolated_cwd() as (c, H):
        c.post('/api/secrets/configure', json={'backend': 'plaintext'},
               headers=H)
        r = c.post('/api/secrets/import_apply', json={
            'records': [{'name': 'X', 'password': 'p',
                          'url': 'https://x.com', 'username': 'u'}],
            'site_ids': [''],
        }, headers=H)
        assert r.status_code == 400
        d = r.get_json()
        assert 'plaintext' in d['error'].lower()


def test_unlock_noop_for_plaintext_backend():
    """Unlock on a backend that doesn't need it should return ok."""
    with _client_with_isolated_cwd() as (c, H):
        c.post('/api/secrets/configure', json={'backend': 'plaintext'},
               headers=H)
        r = c.post('/api/secrets/unlock',
                   json={'password': 'anything'}, headers=H)
        # Plaintext doesn't have unlock; returns ok with a note
        assert r.status_code == 200
        d = r.get_json()
        assert d['ok'] is True


def test_change_password_rejected_on_plaintext():
    """change_password is only for master_password backend."""
    with _client_with_isolated_cwd() as (c, H):
        c.post('/api/secrets/configure', json={'backend': 'plaintext'},
               headers=H)
        r = c.post('/api/secrets/change_password',
                   json={'old_password': 'a', 'new_password': 'newpass123'},
                   headers=H)
        assert r.status_code == 400
        d = r.get_json()
        assert 'master password' in d['error'].lower()


def test_change_password_rejects_short_new_password():
    with _client_with_isolated_cwd() as (c, H):
        c.post('/api/secrets/configure', json={'backend': 'plaintext'},
               headers=H)
        r = c.post('/api/secrets/change_password',
                   json={'old_password': 'a', 'new_password': 'short'},
                   headers=H)
        assert r.status_code == 400


def test_delete_with_key():
    with _client_with_isolated_cwd() as (c, H):
        r = c.post('/api/secrets/delete',
                   json={'key': 'bulkdl-site-does-not-exist'}, headers=H)
        d = r.get_json()
        assert d['ok'] is True
        assert d['removed'] is False


def test_delete_missing_key_arg():
    with _client_with_isolated_cwd() as (c, H):
        r = c.post('/api/secrets/delete', json={}, headers=H)
        assert r.status_code == 400


def test_delete_with_site_id_derives_key():
    with _client_with_isolated_cwd() as (c, H):
        r = c.post('/api/secrets/delete',
                   json={'site_id': 'wow'}, headers=H)
        d = r.get_json()
        assert d['ok'] is True
        assert d['key'] == 'bulkdl-site-wow'
