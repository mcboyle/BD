"""Tests for security-critical invariants.

What we care about:
  - Passwords NEVER appear in /api/status responses (top-level OR per-account)
  - Path traversal is refused at the API boundary
  - Path allowlist is enforced when configured
  - Rate limit blocks excessive action requests
  - Public IP rejection in /api/global_config endpoint validation
"""
import json
import os


class TestPasswordPrivacy:
    """The frontend NEVER sees passwords. A regression where /api/status
    starts returning the `password` field — at top level or inside an
    account entry — is a security incident."""

    def test_top_level_password_not_in_status(self, fresh_app):
        r = fresh_app.post("/api/sites", json={
            "name": "test-pw", "username": "u1",
            "password": "super_secret_password_xyz"
        })
        sid = r.get_json()["id"]
        status = fresh_app.get("/api/status").get_json()
        cfg = status[sid]["config"]
        # `password` key absent entirely
        assert "password" not in cfg
        # No field's value contains the password string
        for k, v in cfg.items():
            if isinstance(v, str):
                assert "super_secret_password_xyz" not in v, \
                    f"Password leaked in field {k!r}"

    def test_account_passwords_not_in_status(self, fresh_app):
        r = fresh_app.post("/api/sites", json={
            "name": "multi-acct",
            "accounts": [
                {"label": "primary", "username": "matt",
                 "password": "secret_for_primary"},
                {"label": "alt", "username": "matt2",
                 "password": "secret_for_alt"},
            ],
        })
        sid = r.get_json()["id"]
        status = fresh_app.get("/api/status").get_json()
        accounts = status[sid]["config"].get("accounts", [])
        assert len(accounts) == 2
        for a in accounts:
            # Field doesn't exist
            assert "password" not in a, "Account password leaked"
            # has_password flag is set so UI knows there IS one
            assert a.get("has_password") is True
            # Confirm no string field contains the password value
            for k, v in a.items():
                if isinstance(v, str):
                    assert "secret_for" not in v, f"Leaked in {k!r}"

    def test_password_preserved_on_blank_update(self, fresh_app):
        # The classic "save the modal with the password field blank because
        # the user didn't change it" case must not wipe creds. v3.66.326: the
        # credential is a vault @cred: reference now, so the REF (and the
        # underlying secret) must survive a blank update.
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
            r = fresh_app.post("/api/sites", json={
                "name": "preserve", "username": "u",
                "password": "keep_this_password"
            })
            sid = r.get_json()["id"]
            from bulk_downloader.app_state import s_cfg
            ref = s_cfg[sid]["password"]
            # stored as a @cred ref, never plaintext
            assert ref == ss.make_password_reference(sid)
            assert store[ss.site_password_key(sid)] == "keep_this_password"
            # Update something else without sending password
            fresh_app.put(f"/api/sites/{sid}", json={"max_concurrent": 4})
            # The credential ref (and secret) must still be intact
            assert s_cfg[sid]["password"] == ref
            assert store[ss.site_password_key(sid)] == "keep_this_password"
        finally:
            ss.get_backend = orig


class TestConfigExportSecrets:
    """F-APP05-02: /api/config/export must redact the FULL secret-field set
    (site_editor.SECRET_FIELDS), not only `password`. A default export that
    still carried plex_token / *_api_key / auth_token in cleartext was a
    silent secret leak. The redacted export must also round-trip: re-importing
    it in merge mode preserves the existing secrets instead of wiping them."""

    _SECRETS = {
        "password": "PW_SECRET",
        "jellyfin_api_key": "JF_SECRET",
        "plex_token": "PLEX_SECRET",
        "ha_token": "HA_SECRET",
        "tpdb_api_key": "TPDB_SECRET",
        "ai_api_key": "AI_SECRET",
        "auth_token": "AUTH_SECRET",
    }

    def _seed(self, fresh_app):
        from bulk_downloader.app_state import s_cfg
        sid = fresh_app.post("/api/sites", json={"name": "SecretSite"}).get_json()["id"]
        s_cfg[sid].update(self._SECRETS)
        return sid

    def test_default_export_redacts_all_secrets(self, fresh_app):
        self._seed(fresh_app)
        body = fresh_app.get("/api/config/export").get_data(as_text=True)
        leaked = [v for v in self._SECRETS.values() if v in body]
        assert not leaked, f"secrets leaked in default export: {leaked}"

    def test_include_passwords_keeps_secrets(self, fresh_app):
        self._seed(fresh_app)
        body = fresh_app.get("/api/config/export?include_passwords=1").get_data(as_text=True)
        missing = [v for v in self._SECRETS.values() if v not in body]
        assert not missing, f"include_passwords=1 dropped secrets: {missing}"

    def test_redacted_export_roundtrip_preserves_secrets(self, fresh_app):
        from bulk_downloader.app_state import s_cfg
        sid = self._seed(fresh_app)
        export = fresh_app.get("/api/config/export").get_json()
        assert fresh_app.post("/api/config/import", json=export).status_code == 200
        for k, orig in self._SECRETS.items():
            assert s_cfg[sid].get(k) == orig, \
                f"{k} wiped by redacted round-trip: {s_cfg[sid].get(k)!r}"


class TestPathTraversal:
    """Phase 26.6 — reject malicious path values at the API boundary
    before they can write outside the intended directory."""

    def test_dotdot_segment_rejected(self, fresh_app):
        r = fresh_app.post("/api/sites", json={
            "name": "bad", "download_dir": "/data/../etc"
        })
        assert r.status_code == 400
        assert ".." in r.get_json()["error"]

    def test_relative_path_rejected(self, fresh_app):
        r = fresh_app.post("/api/sites", json={
            "name": "bad", "download_dir": "data/downloads"
        })
        assert r.status_code == 400
        assert "absolute" in r.get_json()["error"]

    def test_absolute_path_accepted(self, fresh_app):
        # v3.47.8 (#80): fresh installs auto-populate path_allowlist with
        # BD_HOME + ~/Downloads/bulk_downloader. To test the legacy
        # permissive behavior (any absolute path accepted), clear the
        # allowlist first.
        fresh_app.post("/api/global_config",
                       json={"path_allowlist": []})
        r = fresh_app.post("/api/sites", json={
            "name": "good", "download_dir": "/tmp/downloads"
        })
        assert r.status_code == 200
        assert "id" in r.get_json()

    def test_dotdot_in_cookie_file_rejected(self, fresh_app):
        r = fresh_app.post("/api/sites", json={
            "name": "bad", "cookie_file": "/tmp/../etc/cookies"
        })
        assert r.status_code == 400

    def test_dotdot_in_spillover_rejected(self, fresh_app):
        r = fresh_app.post("/api/sites", json={
            "name": "bad",
            "spillover_dirs": "/tmp/ok\n/data/../etc"
        })
        assert r.status_code == 400
        assert "spillover" in r.get_json()["error"]


class TestPathAllowlist:
    def test_allowlist_enforced_when_set(self, fresh_app):
        # Set an allowlist
        fresh_app.post("/api/global_config",
                       json={"path_allowlist": ["/tmp/allowed"]})
        # Outside fails
        r = fresh_app.post("/api/sites", json={
            "name": "outside", "download_dir": "/tmp/other"
        })
        assert r.status_code == 400
        # Inside succeeds
        r = fresh_app.post("/api/sites", json={
            "name": "inside", "download_dir": "/tmp/allowed/site1"
        })
        assert r.status_code == 200

    def test_empty_allowlist_permissive(self, fresh_app):
        # v3.47.8 (#80): fresh installs auto-seed the allowlist; clear it
        # to test the underlying permissive behavior. The empty-allowlist
        # semantic itself hasn't changed — only the default-on-fresh-install.
        fresh_app.post("/api/global_config",
                       json={"path_allowlist": []})
        # No allowlist → any absolute path accepted
        r = fresh_app.post("/api/sites", json={
            "name": "anywhere", "download_dir": "/literally/anywhere"
        })
        assert r.status_code == 200

    def test_allowlist_must_be_array(self, fresh_app):
        r = fresh_app.post("/api/global_config",
                          json={"path_allowlist": "not_an_array"})
        assert r.status_code == 400

    def test_first_run_seeds_allowlist(self, fresh_app):
        # v3.47.8 (#80): on first run (no app_config.json exists), the
        # allowlist should be auto-populated with BD_HOME + the default
        # downloads dir, narrowing the surface for accidental misuse.
        # The fresh_app fixture creates a clean BD_HOME, so this should
        # fire.
        r = fresh_app.get("/api/global_config")
        assert r.status_code == 200
        allowlist = (r.get_json() or {}).get("path_allowlist") or []
        # Exact paths are environment-dependent; just verify non-empty
        # and that the entries are absolute strings.
        assert len(allowlist) >= 1, \
            f"first-run allowlist should be seeded; got {allowlist!r}"
        for p in allowlist:
            assert isinstance(p, str) and os.path.isabs(p), \
                f"allowlist entry should be an absolute path, got {p!r}"

    def test_seed_does_not_overwrite_existing_config(self, fresh_app, tmp_path):
        # If app_config.json already existed (existing install), the
        # auto-seed must NOT fire. This protects users who have explicitly
        # set or cleared the allowlist from a sudden behavior change on
        # upgrade.
        # The fresh_app fixture already loaded the app once and seeded.
        # Now explicitly clear the allowlist via API + verify subsequent
        # state stays empty (auto-seed should NOT re-fire mid-session).
        r = fresh_app.post("/api/global_config",
                           json={"path_allowlist": []})
        assert r.status_code == 200
        r = fresh_app.get("/api/global_config")
        allowlist = (r.get_json() or {}).get("path_allowlist") or []
        assert allowlist == [], \
            f"explicitly-cleared allowlist must stay empty; got {allowlist!r}"


class TestRateLimit:
    """Phase 26.7 — burst protection on action endpoints."""

    def test_rate_limit_blocks_burst(self, fresh_app):
        r = fresh_app.post("/api/sites", json={"name": "rl-test"})
        sid = r.get_json()["id"]
        # Hammer one action; first 10 succeed, then 429s
        ok_count = 0
        rate_limited_count = 0
        for _ in range(15):
            r = fresh_app.post(f"/api/sites/{sid}/pause")
            if r.status_code == 200: ok_count += 1
            elif r.status_code == 429: rate_limited_count += 1
        assert ok_count == 10
        assert rate_limited_count == 5

    def test_rate_limit_per_action(self, fresh_app):
        # Hitting different actions doesn't share a bucket
        r = fresh_app.post("/api/sites", json={"name": "rl2"})
        sid = r.get_json()["id"]
        # Burn out the "pause" bucket
        for _ in range(11):
            fresh_app.post(f"/api/sites/{sid}/pause")
        # A different action should still work
        r = fresh_app.post(f"/api/sites/{sid}/stop")
        assert r.status_code == 200, "Different action shouldn't be rate-limited"


class TestEndpointPrivacy:
    """Phase 27 — refuse public AI endpoints via global_config."""

    def test_public_endpoint_refused(self, fresh_app):
        r = fresh_app.post("/api/global_config",
                          json={"ai_endpoint": "http://8.8.8.8:11434"})
        assert r.status_code == 400
        assert "public" in r.get_json()["error"].lower() or \
               "refusing" in r.get_json()["error"].lower()

    def test_lan_endpoint_accepted(self, fresh_app):
        r = fresh_app.post("/api/global_config",
                          json={"ai_endpoint": "http://10.0.70.20:11434"})
        assert r.status_code == 200
        body = r.get_json()
        assert body.get("ai_endpoint") == "http://10.0.70.20:11434"

    def test_empty_endpoint_clears(self, fresh_app):
        # First set
        fresh_app.post("/api/global_config",
                       json={"ai_endpoint": "http://localhost:11434"})
        # Then clear
        r = fresh_app.post("/api/global_config",
                          json={"ai_endpoint": ""})
        assert r.status_code == 200
        assert r.get_json().get("ai_endpoint") == ""


class TestPhase40CSRFAndPairing:
    """Phase 40: CSRF token enforcement + QR pairing redemption."""

    def test_csrf_endpoint_no_session(self, fresh_app):
        # P0.1 (v3.66.202) contract re-expression: /api/csrf is now the
        # app-level session bootstrap. No session cookie → it MINTS one
        # (same anonymous csrf_bootstrap session GET / mints) instead of
        # refusing. Old contract (ok=false + "no session") retired here;
        # Full bootstrap proof lives in tests/test_csrf_session_bootstrap.py.
        r = fresh_app.get("/api/csrf")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert d.get("csrf_token")
        assert "bd_session=" in ";".join(r.headers.getlist("Set-Cookie"))

    def test_pair_endpoint_registers_token(self, fresh_app):
        # GET /api/pair returns a token that can later be redeemed
        r = fresh_app.get("/api/pair")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert d["token"]
        assert d["url"].endswith(d["token"]) or f"t={d['token']}" in d["url"]
        # Token is in the in-memory store
        from bulk_downloader.app_state import _pairing_tokens
        assert d["token"] in _pairing_tokens

    def test_pair_redeem_unknown_token(self, fresh_app):
        # Bogus token → 404
        r = fresh_app.post("/api/pair/redeem",
                          json={"token": "completely-bogus-token-xyz"})
        assert r.status_code == 404

    def test_pair_redeem_no_token(self, fresh_app):
        # Empty body → 400
        r = fresh_app.post("/api/pair/redeem", json={})
        assert r.status_code == 400

    def test_pair_redeem_success_sets_cookie(self, fresh_app):
        # Generate a pair token, then redeem it
        r = fresh_app.get("/api/pair")
        token = r.get_json()["token"]
        r = fresh_app.post("/api/pair/redeem", json={"token": token})
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert d["csrf_token"]
        # Session cookie set
        cookies = r.headers.getlist("Set-Cookie")
        assert any("bd_session=" in c for c in cookies)
        # Token is now removed from the store
        from bulk_downloader.app_state import _pairing_tokens
        assert token not in _pairing_tokens

    def test_pair_redeem_is_one_shot(self, fresh_app):
        r = fresh_app.get("/api/pair")
        token = r.get_json()["token"]
        # First redemption succeeds
        r1 = fresh_app.post("/api/pair/redeem", json={"token": token})
        assert r1.status_code == 200
        # Second fails
        r2 = fresh_app.post("/api/pair/redeem", json={"token": token})
        assert r2.status_code == 404

    def test_pair_token_expiry(self, fresh_app):
        # Inject an expired token directly and check redemption returns 410
        from bulk_downloader.app_state import _pairing_tokens
        import time as _t
        _pairing_tokens["expired-token-xyz"] = {
            "created": _t.time() - 9999,
            "expires_at": _t.time() - 1,
        }
        r = fresh_app.post("/api/pair/redeem", json={"token": "expired-token-xyz"})
        assert r.status_code == 410

    def test_csrf_enforced_on_post_with_session(self, fresh_app):
        # Redeem first to get a session
        r = fresh_app.get("/api/pair")
        token = r.get_json()["token"]
        r = fresh_app.post("/api/pair/redeem", json={"token": token})
        csrf = r.get_json()["csrf_token"]
        # POST without CSRF header → 403
        r = fresh_app.post("/api/sites", json={"name": "no-csrf"})
        assert r.status_code == 403
        # POST with CSRF header → 200
        r = fresh_app.post("/api/sites",
                          json={"name": "with-csrf"},
                          headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200

    def test_get_requests_not_csrf_blocked(self, fresh_app):
        # Get session
        r = fresh_app.get("/api/pair")
        token = r.get_json()["token"]
        fresh_app.post("/api/pair/redeem", json={"token": token})
        # GET should work without CSRF header
        r = fresh_app.get("/api/status")
        assert r.status_code == 200


class TestPhase41PreflightRegressions:
    """Regression tests for bugs discovered by preflight.py validation."""

    def test_name_must_be_string_int_rejected(self, fresh_app):
        # POST /api/sites with name=12345 used to succeed; should now coerce to str
        r = fresh_app.post("/api/sites", json={"name": 12345})
        assert r.status_code == 200
        # Round-trip should give "12345" not 12345
        sid = r.get_json()["id"]
        s = fresh_app.get("/api/status").get_json()
        assert s[sid]["config"]["name"] == "12345"

    def test_name_list_rejected_with_400(self, fresh_app):
        # name=['array'] is not coercible to a meaningful string — reject
        r = fresh_app.post("/api/sites", json={"name": ["array"]})
        assert r.status_code == 400
        assert "must be a string" in r.get_json().get("error", "")

    def test_name_dict_rejected_with_400(self, fresh_app):
        r = fresh_app.post("/api/sites", json={"name": {"obj": "ect"}})
        assert r.status_code == 400

    def test_name_none_coerced_to_empty(self, fresh_app):
        r = fresh_app.post("/api/sites", json={"name": None})
        assert r.status_code == 200
        sid = r.get_json()["id"]
        s = fresh_app.get("/api/status").get_json()
        # None is coerced to "" which then gets the default name "Site N"
        assert isinstance(s[sid]["config"]["name"], str)

    def test_config_import_handles_malformed_existing(self, fresh_app):
        # Even if s_cfg somehow has a non-string name (legacy data), import
        # should not crash with AttributeError on .strip()
        from bulk_downloader.app_state import s_cfg
        # Inject malformed entry directly (simulating corrupted state)
        s_cfg["bad_sid"] = {"name": 12345}
        # Now try an import — should not 500
        export = fresh_app.get("/api/config/export").get_json()
        r = fresh_app.post("/api/config/import", json=export)
        assert r.status_code == 200
        # Clean up
        s_cfg.pop("bad_sid", None)

    def test_put_string_validation(self, fresh_app):
        # Create a site, then PUT with bad name type
        r = fresh_app.post("/api/sites", json={"name": "ok"})
        sid = r.get_json()["id"]
        # PUT with name=list should 400
        r = fresh_app.put(f"/api/sites/{sid}", json={"name": ["bad"]})
        assert r.status_code == 400
