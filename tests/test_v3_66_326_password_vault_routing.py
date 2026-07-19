"""v3.66.326 — credentials route through the secrets vault, never plaintext.

The SPA Add-Site wizard's password field claimed "(stored encrypted)" but
POST /api/sites wrote the plaintext password into sites_config.json, and
neither create nor update consulted the secrets backend. This change routes a
typed password through secrets_store as a @cred: reference (mirroring
/api/captures/setup_site): plaintext backend -> 400 (encrypt first), locked
encrypted backend -> 401 secrets_locked (the SPA's unlock-prompt trigger),
unlocked -> stored. Plaintext is NEVER persisted to sites_config.json.

Runner conventions: zero-arg tests, no pytest fixtures, restore module
globals in try/finally. The default sandbox backend is PlaintextBackend, so
the create/update e2e tests exercise the plaintext-refusal path (which is
exactly what proves "no plaintext leak"); the locked/unlocked branches are
covered by the guard-helper unit test with stub backends.
"""
import json
import os


def _boot_empty():
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader import app as a
    from bulk_downloader import db
    db.db_init()
    a.SITES_FILE.write_text(json.dumps({}), encoding="utf-8")
    a._load_sites_config()
    return a, a.app.test_client()


def _boot_with_site():
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader import app as a
    from bulk_downloader import db
    db.db_init()
    a.SITES_FILE.write_text(json.dumps({"demo": {
        "name": "Demo", "max_concurrent": 4, "wait": 5,
        "login_url": "https://demo.example/login",
        "password": "ORIG_PW"}}), encoding="utf-8")
    a._load_sites_config()
    a.runners["demo"].update_config = lambda *_a, **_k: None
    return a, a.app.test_client()


# ── core guarantee: a typed password never lands as plaintext ───────────────

def test_create_with_password_never_persists_plaintext():
    a, c = _boot_empty()
    c.post("/api/sites", json={
        "name": "VaultSite",
        "login_url": "https://x.example/login",
        "password": "SUPERSECRET_CREATE_PW"})
    raw = a.SITES_FILE.read_text(encoding="utf-8")
    assert "SUPERSECRET_CREATE_PW" not in raw, \
        "plaintext password leaked into sites_config.json on create"


def test_create_with_password_on_plaintext_backend_flags_not_stored():
    # Plaintext backend -> the SITE is still created (200, {id}), but the
    # credential is NOT stored (no plaintext leak) and secrets_plaintext flags
    # it so the SPA can prompt to switch backends.
    from bulk_downloader import secrets_store as ss
    a, c = _boot_empty()
    orig = ss.get_backend
    try:
        class _Plain:
            name = "plaintext"
        ss.get_backend = lambda: _Plain()
        r = c.post("/api/sites", json={
            "name": "VaultSite",
            "login_url": "https://x.example/login",
            "password": "PW"})
        assert r.status_code == 200
        body = r.get_json() or {}
        assert "id" in body
        assert body.get("secrets_plaintext") is True
        assert body.get("cred_stored") is False
        # site created, but no plaintext anywhere
        raw = a.SITES_FILE.read_text(encoding="utf-8")
        assert "PW" not in raw or '"PW"' not in raw
        disk = json.loads(raw)
        assert any(s.get("name") == "VaultSite" for s in disk.values())
        created = next(s for s in disk.values() if s.get("name") == "VaultSite")
        # no plaintext password stored (absent, blank, or a @cred ref only)
        pw = created.get("password", "")
        assert pw in ("", None) or pw.startswith("@cred:")
    finally:
        ss.get_backend = orig


def test_create_with_password_on_locked_backend_flags_secrets_locked():
    # Locked encrypted backend -> site created (200, {id}), credential not
    # stored, secrets_locked set (the SPA's unlock-prompt trigger). No plaintext.
    from bulk_downloader import secrets_store as ss
    a, c = _boot_empty()
    orig = ss.get_backend
    try:
        class _Locked:
            name = "master_password"
            def is_unlocked(self):
                return False
        ss.get_backend = lambda: _Locked()
        r = c.post("/api/sites", json={
            "name": "LockedSite",
            "login_url": "https://x.example/login",
            "password": "PW"})
        assert r.status_code == 200
        body = r.get_json() or {}
        assert "id" in body
        assert body.get("secrets_locked") is True
        assert body.get("cred_stored") is False
        disk = json.loads(a.SITES_FILE.read_text(encoding="utf-8"))
        created = next(s for s in disk.values() if s.get("name") == "LockedSite")
        pw = created.get("password", "")
        assert pw in ("", None) or pw.startswith("@cred:")
    finally:
        ss.get_backend = orig


def test_create_without_password_still_works_on_plaintext_backend():
    # No password -> no vault needed -> create proceeds normally.
    a, c = _boot_empty()
    r = c.post("/api/sites", json={
        "name": "NoPwSite", "login_url": "https://y.example/login"})
    assert r.status_code == 200
    disk = json.loads(a.SITES_FILE.read_text(encoding="utf-8"))
    assert any(s.get("name") == "NoPwSite" for s in disk.values())


def test_update_with_new_password_never_persists_plaintext():
    a, c = _boot_with_site()
    c.put("/api/sites/demo", json={"password": "NEWTYPED_SECRET_PW"})
    raw = a.SITES_FILE.read_text(encoding="utf-8")
    assert "NEWTYPED_SECRET_PW" not in raw, \
        "plaintext password leaked into sites_config.json on update"


def test_update_blank_password_preserves_existing():
    # preserve-on-blank must still hold after the vault routing change.
    a, c = _boot_with_site()
    r = c.put("/api/sites/demo", json={"max_concurrent": 6, "password": ""})
    assert r.status_code == 200
    disk = json.loads(a.SITES_FILE.read_text(encoding="utf-8"))["demo"]
    assert disk["password"] == "ORIG_PW"
    assert disk["max_concurrent"] == 6


# ── guard branches that drive the SPA unlock prompt ─────────────────────────

def test_vault_guard_branches():
    from bulk_downloader import app as a
    from bulk_downloader import secrets_store as ss
    orig = ss.get_backend
    try:
        class _Plain:
            name = "plaintext"
        ss.get_backend = lambda: _Plain()
        allowed, status, body = a._vault_guard_for_password()
        assert allowed is False and status == 400 and body.get("secrets_plaintext")

        class _Locked:
            name = "master_password"
            def is_unlocked(self):
                return False
        ss.get_backend = lambda: _Locked()
        allowed, status, body = a._vault_guard_for_password()
        assert allowed is False and status == 401 and body.get("secrets_locked")

        class _Open:
            name = "master_password"
            def is_unlocked(self):
                return True
        ss.get_backend = lambda: _Open()
        allowed, status, body = a._vault_guard_for_password()
        assert allowed is True and status is None and body is None
    finally:
        ss.get_backend = orig


def test_store_helper_writes_cred_ref_not_plaintext():
    # With an unlocked stub backend, the helper stores via backend.set and
    # writes a @cred: reference onto s_cfg[sid]["password"].
    from bulk_downloader import app as a
    from bulk_downloader import secrets_store as ss
    orig = ss.get_backend
    try:
        stored = {}

        class _Open:
            name = "master_password"
            def is_unlocked(self):
                return True
            def set(self, key, password):
                stored[key] = password
        ss.get_backend = lambda: _Open()
        a.s_cfg["zz"] = {"name": "ZZ"}
        ok, err = a._store_site_password_in_vault("zz", "PLAINTEXT_PW")
        assert ok is True and err is None
        ref = a.s_cfg["zz"]["password"]
        assert ref.startswith(ss.CRED_PREFIX)
        assert "PLAINTEXT_PW" not in ref
        # the actual secret went to the backend, not the config ref
        assert "PLAINTEXT_PW" in stored.values()
    finally:
        ss.get_backend = orig
        a.s_cfg.pop("zz", None)
