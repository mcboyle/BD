"""v3.43.14: credential persistence regression tests.

Pre-fix bugs:
  1. PRESERVE_IF_BLANK only included password — blank username
     submission wiped the stored value.
  2. No UI hint that password was stored, leading users to retype
     constantly and lose other fields if they forgot to retype them too.
  3. do_login() failed silently with "Missing credentials" — combined
     with headless=True default, users had no visible signal that
     autologin wasn't working because creds were gone.

These tests cover the structural behaviors so the bugs can't regress.
"""


def _make_site(client, csrf):
    """Helper: pair a session and create a site with creds. Returns the sid."""
    H = {'X-CSRF-Token': csrf}
    r = client.post('/api/sites', json={
        'name': 'CredTest',
        'login_url': 'https://example.com/login',
        'username': 'matt@example.com',
        'password': 'mySecret123',
    }, headers=H)
    return r.get_json()['id'], H


def _new_client():
    """Create a paired test client. Returns (client, csrf)."""
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    r = c.get('/api/pair'); token = r.get_json()['token']
    r = c.post('/api/pair/redeem', json={'token': token})
    csrf = r.get_json()['csrf_token']
    return c, csrf


def _cleanup(sid):
    """Helper: remove a test site so subsequent tests start clean."""
    from bulk_downloader import app as A
    A.s_cfg.pop(sid, None)
    A.s_meta.pop(sid, None)
    if sid in A.runners:
        try: A.runners[sid].stop()
        except Exception: pass
        A.runners.pop(sid, None)


def test_blank_username_preserves_stored_value():
    """v3.43.14 fix: PUT with username='' must keep the existing stored
    username. Previously this wiped the value and autologin would then
    fail silently."""
    from bulk_downloader import app as A
    c, csrf = _new_client()
    H = {'X-CSRF-Token': csrf}
    sid, _ = _make_site(c, csrf)
    # Edit with username blank (simulates user clearing it by mistake
    # OR submitting a form where they only meant to change one other field)
    r = c.put(f'/api/sites/{sid}', json={'username': '', 'wait': 5}, headers=H)
    assert r.status_code == 200, r.get_json()
    # Username preserved, wait updated
    assert A.s_cfg[sid]['username'] == 'matt@example.com'
    assert A.s_cfg[sid]['wait'] == 5
    _cleanup(sid)


def test_blank_password_preserves_stored_value():
    """Password preservation should still work (was working pre-v3.43.14,
    but regression coverage now that we touched PRESERVE_IF_BLANK).
    v3.66.326: the stored credential is a vault @cred: reference now, so a
    blank update must preserve the ref (and the underlying secret)."""
    from bulk_downloader import app as A
    from bulk_downloader import secrets_store as ss
    store = {}

    class _Unlocked:
        name = "master_password"

        def is_unlocked(self):
            return True

        def set(self, k, v):
            store[k] = v

        def get(self, k):
            return store.get(k)

    orig = ss.get_backend
    try:
        ss.get_backend = lambda: _Unlocked()
        c, csrf = _new_client()
        H = {'X-CSRF-Token': csrf}
        sid, _ = _make_site(c, csrf)
        ref = ss.make_password_reference(sid)
        assert A.s_cfg[sid]['password'] == ref            # @cred ref, not plaintext
        assert store[ss.site_password_key(sid)] == 'mySecret123'
        r = c.put(f'/api/sites/{sid}', json={'password': ''}, headers=H)
        assert r.status_code == 200
        assert A.s_cfg[sid]['password'] == ref            # preserved on blank
        assert store[ss.site_password_key(sid)] == 'mySecret123'
        _cleanup(sid)
    finally:
        ss.get_backend = orig


def test_blank_login_url_preserves_stored_value():
    """login_url is also in PRESERVE_IF_BLANK now."""
    from bulk_downloader import app as A
    c, csrf = _new_client()
    H = {'X-CSRF-Token': csrf}
    sid, _ = _make_site(c, csrf)
    r = c.put(f'/api/sites/{sid}', json={'login_url': ''}, headers=H)
    assert r.status_code == 200
    assert A.s_cfg[sid]['login_url'] == 'https://example.com/login'
    _cleanup(sid)


def test_explicit_username_change_does_apply():
    """Preservation ONLY kicks in when the value is blank. A real change
    should still go through."""
    from bulk_downloader import app as A
    c, csrf = _new_client()
    H = {'X-CSRF-Token': csrf}
    sid, _ = _make_site(c, csrf)
    r = c.put(f'/api/sites/{sid}', json={'username': 'new@example.com'}, headers=H)
    assert r.status_code == 200
    assert A.s_cfg[sid]['username'] == 'new@example.com'
    _cleanup(sid)


def test_meta_exposes_has_password():
    """The frontend needs to know whether a password is stored without
    seeing the value itself. _build_meta exposes has_password=true/false.
    v3.66.326: a vaulted @cred: reference still counts as 'has a password'."""
    from bulk_downloader import app as A
    from bulk_downloader import secrets_store as ss
    store = {}

    class _Unlocked:
        name = "master_password"

        def is_unlocked(self):
            return True

        def set(self, k, v):
            store[k] = v

        def get(self, k):
            return store.get(k)

    orig = ss.get_backend
    try:
        ss.get_backend = lambda: _Unlocked()
        c, csrf = _new_client()
        sid, _ = _make_site(c, csrf)
        # /api/status returns the meta which the UI uses for openEdit pre-fill
        r = c.get('/api/status')
        data = r.get_json()
        assert data[sid]['config']['has_password'] is True, \
            "has_password not exposed — UI can't show 'saved' hint"
        # Sanity: actual password not leaked
        assert 'password' not in data[sid]['config']
        _cleanup(sid)
    finally:
        ss.get_backend = orig


def test_meta_has_password_false_when_no_password():
    from bulk_downloader import app as A
    c, csrf = _new_client()
    H = {'X-CSRF-Token': csrf}
    r = c.post('/api/sites', json={'name': 'NoPasswordSite'}, headers=H)
    sid = r.get_json()['id']
    r = c.get('/api/status')
    data = r.get_json()
    assert data[sid]['config']['has_password'] is False
    _cleanup(sid)
