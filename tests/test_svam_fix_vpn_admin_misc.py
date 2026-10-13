"""svam-fix: backend halves of the spa-vpn-admin-misc sweep findings (bd-worker-S42-D, O1789 wave 1).

F1  POST /api/auth/users bootstrap: the very first account is the initial admin, whatever role
    the body names (the SPA sent "operator", leaving no admin to create the rest).
F2  GET /api/auth/whoami reports bootstrap = "no account exists yet", so the SPA no longer keys
    bootstrap on the multi_user config flag.
F4  POST /api/sites keeps the wizard's start_url (it was not in CFG_FIELDS, so it was dropped).
F6  POST /api/backup/preview previews the body it is given (include_db / passphrase), not a fixed
    include_db=True, passphrase=None archive.

The usernames, passwords and URLs below are fixed test strings, not credentials.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"


def _client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return A, c, {"X-CSRF-Token": csrf}


def _no_accounts():
    from bulk_downloader import user_accounts as UA
    for u in list(UA.list_users()):
        UA.delete_user(u["username"])
    assert UA.count() == 0


def test_bootstrap_account_is_admin_and_whoami_reports_bootstrap():
    from bulk_downloader import user_accounts as UA
    _A, c, h = _client()
    _no_accounts()
    try:
        assert c.get("/api/auth/whoami").get_json().get("bootstrap") is True, \
            "whoami does not report bootstrap while no account exists"
        r = c.post("/api/auth/users", headers=h,
                   json={"username": "svamfirst", "password": "svam-pw-1", "role": "operator"})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()["user"]["role"] == "admin", (
            "bootstrap account stored as %r: no admin can ever be created" % r.get_json()["user"]["role"])
        assert c.get("/api/auth/whoami").get_json().get("bootstrap") is False
        # control: after bootstrap an admin still creates the role it asks for
        assert c.post("/api/auth/login", headers=h,
                      json={"username": "svamfirst", "password": "svam-pw-1"}).status_code == 200
        r2 = c.post("/api/auth/users", headers=h,
                    json={"username": "svamsecond", "password": "svam-pw-2", "role": "operator"})
        assert r2.status_code == 200 and r2.get_json()["user"]["role"] == "operator"
    finally:
        _no_accounts()
        assert UA.count() == 0


def test_add_site_keeps_start_url():
    _A, c, h = _client()
    body = {"name": "SvamWizard", "start_url": "http://192.0.2.20/library",
            "login_url": "http://192.0.2.20/login", "crawler_listing_url": "http://192.0.2.20/list"}
    r = c.post("/api/sites", json=body, headers=h)
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    j = r.get_json() or {}
    sid = j.get("id") or j.get("site_id") or (j.get("site") or {}).get("id")
    cfg = (c.get("/api/status").get_json().get(sid) or {}).get("config") or {}
    try:
        assert cfg.get("login_url") == "http://192.0.2.20/login"   # control: a kept field
        assert cfg.get("start_url") == "http://192.0.2.20/library", "add-site dropped start_url"
    finally:
        c.delete(f"/api/sites/{sid}", headers=h)


def test_backup_preview_honours_include_db_and_passphrase():
    from bulk_downloader import backup as bd_backup
    _A, c, h = _client()
    seen = []
    orig = bd_backup.create_backup

    def spy(out_path, **kw):
        seen.append(kw)
        return {"ok": True, "files": 0, "size_bytes": 0}

    bd_backup.create_backup = spy
    try:
        r = c.post("/api/backup/preview", headers=h,
                   json={"include_db": False, "passphrase": "svam-preview-pass"})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        assert seen, "preview did not build an archive"
        assert seen[-1].get("include_db") is False, "preview ignored include_db=false: %r" % seen[-1]
        assert seen[-1].get("passphrase") == "svam-preview-pass", "preview ignored the passphrase"
        # control: an empty body previews the default (DB included, unencrypted)
        c.post("/api/backup/preview", headers=h, json={})
        assert seen[-1].get("include_db") is True and seen[-1].get("passphrase") is None
    finally:
        bd_backup.create_backup = orig
