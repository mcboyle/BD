"""Tests for /api/secrets/extension/* endpoints (v3.43.14, ext bridge).

The endpoints handle the extension-to-app authentication and the
list-for-origin / fetch-one flow that drives autofill.

Coverage:
  - pair_issue requires session auth (cookie+CSRF) and produces a token
  - pair redeems a pairing token and returns a vault token
  - list_paired enumerates extensions (with short IDs, not full tokens)
  - revoke removes an extension by short ID
  - list_for_origin with valid vault token returns matched entries
  - list_for_origin with invalid token returns 401
  - fetch_one with valid token + valid entry returns password
  - fetch_one rate-limited returns 429
  - fetch_one for unknown entry returns 403 (not 404 — don't leak presence)
  - ping works as liveness check
"""
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _client():
    """Test client in an isolated tempdir.

    v3.66.326: site login passwords now route through the encrypted vault as
    @cred: references (POST /api/sites no longer stores plaintext). The
    sandbox default backend is locked, so install an unlocked in-memory
    encrypted backend for the lifetime of the client — that's the normal
    operating mode the extension autofill flow assumes (a credential must be
    stored and resolvable for fetch_one / list_for_origin to return it).
    """
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    from bulk_downloader import secrets_store as _ss
    orig_cwd = os.getcwd()
    _orig_get_backend = _ss.get_backend
    _vault_store: dict = {}

    class _UnlockedVault:
        name = "master_password"

        def is_unlocked(self):
            return True

        def set(self, k, v):
            _vault_store[k] = v

        def get(self, k):
            return _vault_store.get(k)

    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        Path(td, "screenshots").mkdir(exist_ok=True)
        _ss.get_backend = lambda: _UnlockedVault()
        try:
            db_init()
            c = A.app.test_client()
            r = c.get('/api/pair'); token = r.get_json()['token']
            r = c.post('/api/pair/redeem', json={'token': token})
            csrf = r.get_json()['csrf_token']
            H = {'X-CSRF-Token': csrf}
            yield c, H, A
        finally:
            _ss.get_backend = _orig_get_backend
            os.chdir(orig_cwd)


def _pair_extension(c, H, label="test ext"):
    """Helper: do the full pair flow, return the vault token."""
    r = c.post('/api/secrets/extension/pair_issue', headers=H)
    assert r.status_code == 200, r.get_json()
    pairing_token = r.get_json()["pairing_token"]
    # Pair WITHOUT session cookies (simulates the extension's
    # cross-origin context). Fresh test client.
    c2 = c.application.test_client()
    r2 = c2.post('/api/secrets/extension/pair',
                 json={'pairing_token': pairing_token, 'label': label})
    assert r2.status_code == 200, r2.get_json()
    return r2.get_json()["vault_token"]


# ─── pair_issue + pair ────────────────────────────────────────────

def test_pair_issue_produces_token():
    with _client() as (c, H, A):
        r = c.post('/api/secrets/extension/pair_issue', headers=H)
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert isinstance(d["pairing_token"], str)
        assert len(d["pairing_token"]) >= 16
        assert d["expires_in_seconds"] > 0


def test_pair_redeems_pairing_token():
    with _client() as (c, H, A):
        r = c.post('/api/secrets/extension/pair_issue', headers=H)
        pair_tok = r.get_json()["pairing_token"]
        # Redeem from a fresh client (simulates extension cross-origin POST
        # without the main UI's session cookie — CSRF check skips)
        c2 = c.application.test_client()
        r2 = c2.post('/api/secrets/extension/pair',
                     json={'pairing_token': pair_tok, 'label': 'X'})
        assert r2.status_code == 200
        d = r2.get_json()
        assert d["ok"] is True
        assert isinstance(d["vault_token"], str)
        assert len(d["vault_token"]) >= 32


def test_pair_rejects_bad_token():
    with _client() as (c, H, A):
        c2 = c.application.test_client()
        r = c2.post('/api/secrets/extension/pair',
                    json={'pairing_token': 'not-a-real-token'})
        assert r.status_code == 401


def test_pair_requires_pairing_token():
    with _client() as (c, H, A):
        c2 = c.application.test_client()
        r = c2.post('/api/secrets/extension/pair', json={})
        assert r.status_code == 400


# ─── list_paired + revoke ─────────────────────────────────────────

def test_list_paired_returns_short_ids_only():
    with _client() as (c, H, A):
        vt = _pair_extension(c, H, "my ext")
        r = c.get('/api/secrets/extension/list_paired', headers=H)
        d = r.get_json()
        assert d["ok"] is True
        assert len(d["extensions"]) == 1
        ext = d["extensions"][0]
        assert ext["label"] == "my ext"
        # ID is a short prefix, not the full token
        assert ext["id"] == vt[:8]
        assert ext["id"] != vt


def test_revoke_removes_extension():
    with _client() as (c, H, A):
        vt = _pair_extension(c, H, "to remove")
        # Get the short ID
        r = c.get('/api/secrets/extension/list_paired', headers=H)
        prefix = r.get_json()["extensions"][0]["id"]
        # Revoke
        r2 = c.post('/api/secrets/extension/revoke',
                    json={'id': prefix}, headers=H)
        assert r2.status_code == 200
        # Now the token doesn't work
        r3 = c.application.test_client().get(
            '/api/secrets/extension/ping',
            headers={"Authorization": f"Bearer {vt}"})
        assert r3.status_code == 401


# ─── ping ──────────────────────────────────────────────────────────

def test_ping_with_valid_token():
    with _client() as (c, H, A):
        vt = _pair_extension(c, H, "ping test")
        c2 = c.application.test_client()
        r = c2.get('/api/secrets/extension/ping',
                   headers={"Authorization": f"Bearer {vt}"})
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert d["label"] == "ping test"


def test_ping_without_token_rejected():
    with _client() as (c, H, A):
        c2 = c.application.test_client()
        r = c2.get('/api/secrets/extension/ping')
        assert r.status_code == 401


def test_ping_with_invalid_token_rejected():
    with _client() as (c, H, A):
        c2 = c.application.test_client()
        r = c2.get('/api/secrets/extension/ping',
                   headers={"Authorization": "Bearer fake-token-12345"})
        assert r.status_code == 401


# ─── list_for_origin ──────────────────────────────────────────────

def test_list_for_origin_returns_matched_sites():
    with _client() as (c, H, A):
        vt = _pair_extension(c, H)
        # Create a site with login_url + password
        r = c.post('/api/sites',
                   json={'name': 'My Wow', 'login_url': 'https://wowgirls.com/login',
                         'password': 'secret123', 'username': 'matt'},
                   headers=H)
        sid = r.get_json()['id']
        # Extension asks for matches
        c2 = c.application.test_client()
        r2 = c2.get('/api/secrets/extension/list_for_origin'
                    '?origin=https://venus.wowgirls.com/film/x',
                    headers={"Authorization": f"Bearer {vt}"})
        assert r2.status_code == 200
        d = r2.get_json()
        assert d["ok"] is True
        assert len(d["entries"]) >= 1
        entry = next(e for e in d["entries"] if e["id"] == f"site:{sid}")
        # Critically: NO password in the metadata response
        assert "password" not in entry


def test_list_for_origin_empty_when_no_match():
    with _client() as (c, H, A):
        vt = _pair_extension(c, H)
        c2 = c.application.test_client()
        r = c2.get('/api/secrets/extension/list_for_origin'
                   '?origin=https://no-matching-site.example/',
                   headers={"Authorization": f"Bearer {vt}"})
        assert r.status_code == 200
        d = r.get_json()
        assert d["entries"] == []


def test_list_for_origin_requires_origin_param():
    with _client() as (c, H, A):
        vt = _pair_extension(c, H)
        c2 = c.application.test_client()
        r = c2.get('/api/secrets/extension/list_for_origin',
                   headers={"Authorization": f"Bearer {vt}"})
        assert r.status_code == 400


def test_list_for_origin_requires_vault_token():
    with _client() as (c, H, A):
        c2 = c.application.test_client()
        r = c2.get('/api/secrets/extension/list_for_origin'
                   '?origin=https://example.com/')
        assert r.status_code == 401


# ─── fetch_one ──────────────────────────────────────────────────────

def test_fetch_one_returns_password():
    with _client() as (c, H, A):
        vt = _pair_extension(c, H)
        r = c.post('/api/sites',
                   json={'name': 'X', 'login_url': 'https://example.com/',
                         'password': 'mypass', 'username': 'user1'},
                   headers=H)
        sid = r.get_json()['id']
        # Fetch
        c2 = c.application.test_client()
        r2 = c2.post('/api/secrets/extension/fetch_one',
                     json={'id': f"site:{sid}",
                           'origin': 'https://example.com/'},
                     headers={"Authorization": f"Bearer {vt}"})
        assert r2.status_code == 200, r2.get_json()
        d = r2.get_json()
        assert d["ok"] is True
        assert d["password"] == "mypass"
        assert d["username"] == "user1"


def test_fetch_one_unknown_entry_denied_with_403():
    """Don't leak presence — 403 'denied' for unknown OR rate-limited."""
    with _client() as (c, H, A):
        vt = _pair_extension(c, H)
        c2 = c.application.test_client()
        r = c2.post('/api/secrets/extension/fetch_one',
                    json={'id': 'site:does-not-exist'},
                    headers={"Authorization": f"Bearer {vt}"})
        assert r.status_code == 403


def test_fetch_one_rate_limited():
    """Two consecutive fetches for the same entry return 429."""
    with _client() as (c, H, A):
        vt = _pair_extension(c, H)
        r = c.post('/api/sites',
                   json={'name': 'X', 'password': 'p', 'username': 'u'},
                   headers=H)
        sid = r.get_json()['id']
        c2 = c.application.test_client()
        # First fetch ok
        r1 = c2.post('/api/secrets/extension/fetch_one',
                     json={'id': f"site:{sid}"},
                     headers={"Authorization": f"Bearer {vt}"})
        assert r1.status_code == 200
        # Second immediately = 429
        r2 = c2.post('/api/secrets/extension/fetch_one',
                     json={'id': f"site:{sid}"},
                     headers={"Authorization": f"Bearer {vt}"})
        assert r2.status_code == 429


def test_fetch_one_requires_id():
    with _client() as (c, H, A):
        vt = _pair_extension(c, H)
        c2 = c.application.test_client()
        r = c2.post('/api/secrets/extension/fetch_one',
                    json={},
                    headers={"Authorization": f"Bearer {vt}"})
        assert r.status_code == 400


# ─── Auth gate (BD_AUTH_TOKEN scenario) ────────────────────────────

def test_pair_works_when_bd_auth_token_set():
    """When BD_AUTH_TOKEN is set globally, the pair endpoint must still
    be reachable so a fresh extension can pair (the pairing token is
    its auth).

    v3.66.21: save/restore the original ``bulk_downloader.app`` module
    around the forced reload. Without restoration the next test that
    does ``from bulk_downloader import app`` reimports fresh, creating
    a new ``_app_cfg`` dict — and every test file that already held
    ``from bulk_downloader import app as bd_app`` at module load now
    has a stale reference to the original dict. This was the root
    cause of the u27/u28/u29 cluster failures in the broader-sweep
    run; mutations to ``bd_app._app_cfg`` in those tests landed on
    the orphaned dict while ``config_snapshot`` / ``config_restore``
    (which do their own ``from . import app`` inside the function)
    saw the new live dict — leading to the snapshot capturing default
    values rather than the test's set values.
    """
    import os, sys, tempfile
    from pathlib import Path
    orig_cwd = os.getcwd()
    orig_env = os.environ.get("BD_AUTH_TOKEN")
    # Save the current bulk_downloader.app module (and any submodules
    # that may have imported it transitively). We restore them in the
    # finally block so subsequent tests see the same module instance —
    # and the same _app_cfg dict — they had before this test ran.
    saved_modules = {k: v for k, v in sys.modules.items()
                     if k.startswith("bulk_downloader")}
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        Path(td, "screenshots").mkdir(exist_ok=True)
        os.environ["BD_AUTH_TOKEN"] = "server-bearer-token"
        # Force-reload app to pick up env var
        if "bulk_downloader.app" in sys.modules:
            del sys.modules["bulk_downloader.app"]
        try:
            from bulk_downloader import app as A
            from bulk_downloader.db import db_init
            db_init()
            c = A.app.test_client()
            # First we still need to authenticate to call pair_issue
            r = c.post('/api/secrets/extension/pair_issue',
                       headers={"Authorization": "Bearer server-bearer-token"})
            assert r.status_code == 200, r.get_json()
            pair_tok = r.get_json()["pairing_token"]
            # Now call pair WITHOUT any auth (fresh extension scenario)
            c2 = A.app.test_client()
            r2 = c2.post('/api/secrets/extension/pair',
                         json={'pairing_token': pair_tok, 'label': 'X'})
            assert r2.status_code == 200, r2.get_json()
        finally:
            os.chdir(orig_cwd)
            if orig_env is not None:
                os.environ["BD_AUTH_TOKEN"] = orig_env
            else:
                os.environ.pop("BD_AUTH_TOKEN", None)
            # Restore the original module so the next test's stale
            # `bd_app` references stay valid.
            for k in [m for m in sys.modules
                      if m.startswith("bulk_downloader")]:
                del sys.modules[k]
            sys.modules.update(saved_modules)
            # v3.66.436: also re-point the parent package's submodule
            # attributes at the restored modules. The reload above set
            # `bulk_downloader.app` (the package ATTRIBUTE, distinct from
            # sys.modules) to the throwaway reloaded module; restoring
            # only sys.modules leaves that attribute dangling. After the
            # Phase-4 app.py split, extracted blueprints (e.g. app_pair)
            # resolve shared state like `_csrf_key` via the live app
            # module — a dangling package attribute splits the key
            # between the served app and the blueprint, so redeem signs a
            # CSRF token the app's _check_csrf then rejects.
            _pkg = saved_modules.get("bulk_downloader")
            if _pkg is not None:
                for _name, _mod in saved_modules.items():
                    _parts = _name.split(".")
                    if len(_parts) == 2:
                        setattr(_pkg, _parts[1], _mod)
