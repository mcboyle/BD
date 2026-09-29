"""O1567 fx-hustler1-cred-ref: a "@cred:" reference sent as a site password.

Live (wrk-191, o1513-a6-hustler1-live): the clone site was configured with
PUT /api/sites/<sid> {"password": "@cred:bulkdl-site-<base>"} so it would use
the base site's vault entry without the secret ever leaving the app. The
update path treated that string as a newly-typed password and stored the
LITERAL reference text in the clone's own vault key; every login then typed
"@cred:bulkdl-site-<base>" into the password field and the site answered
"Email or Password incorrect" (no navigation, handed off as manual takeover).

A "@cred:" value is a reference, never a password:
- the site's own reference round-trips as "keep the stored secret";
- a reference to another SITE's key copies that secret inside the vault when
  both sites log in on the same host (never onto a different login host);
- anything else is refused with a distinctive error, nothing stored.
"""
BD_GATE_SCOPE = "module"



def _stub(stored):
    class _Open:
        name = "master_password"

        def is_unlocked(self):
            return True

        def set(self, key, password):
            stored[key] = password

        def get(self, key):
            return stored.get(key)
    return _Open()


def _run(sites, sid, value, stored):
    from bulk_downloader import app as a
    from bulk_downloader import secrets_store as ss
    orig = ss.get_backend
    try:
        ss.get_backend = lambda: _stub(stored)
        for k, v in sites.items():
            a.s_cfg[k] = v
        return a._store_site_password_in_vault(sid, value)
    finally:
        ss.get_backend = orig
        for k in sites:
            a.s_cfg.pop(k, None)


def test_ref_to_same_host_site_copies_the_secret_not_the_reference():
    stored = {"bulkdl-site-base01": "REAL_SECRET"}
    sites = {
        "base01": {"name": "base", "login_url": "https://ex.example/login/",
                   "password": "@cred:bulkdl-site-base01"},
        "clone1": {"name": "clone", "login_url": "https://ex.example/login/"},
    }
    ok, err = _run(sites, "clone1", "@cred:bulkdl-site-base01", stored)
    assert ok is True and err is None, err
    assert stored.get("bulkdl-site-clone1") == "REAL_SECRET", (
        "clone's vault entry must hold the base secret, got the literal "
        "reference" if stored.get("bulkdl-site-clone1", "").startswith("@cred:")
        else "clone's vault entry missing/wrong")
    assert sites["clone1"]["password"] == "@cred:bulkdl-site-clone1"


def test_own_reference_round_trip_keeps_the_stored_secret():
    stored = {"bulkdl-site-clone1": "KEEP_ME"}
    sites = {"clone1": {"name": "clone", "login_url": "https://ex.example/l",
                        "password": "@cred:bulkdl-site-clone1"}}
    ok, err = _run(sites, "clone1", "@cred:bulkdl-site-clone1", stored)
    assert ok is True and err is None, err
    assert stored["bulkdl-site-clone1"] == "KEEP_ME"


def test_ref_to_other_login_host_is_refused_and_nothing_stored():
    stored = {"bulkdl-site-base01": "REAL_SECRET"}
    sites = {
        "base01": {"name": "base", "login_url": "https://ex.example/login/"},
        "clone1": {"name": "clone", "login_url": "https://other.example/login"},
    }
    ok, err = _run(sites, "clone1", "@cred:bulkdl-site-base01", stored)
    assert ok is False and "cred-ref" in (err or ""), err
    assert "bulkdl-site-clone1" not in stored


def test_ref_to_missing_key_is_refused_and_nothing_stored():
    stored = {}
    sites = {"clone1": {"name": "clone", "login_url": "https://ex.example/l"}}
    ok, err = _run(sites, "clone1", "@cred:bulkdl-site-nosuch", stored)
    assert ok is False and "cred-ref" in (err or ""), err
    assert stored == {}
