"""v3.64.3: opt-in cross-device sound preference sync.

Two new /api/global_config keys:

    sound_sync_enabled   (bool) — opt-in toggle; default false
    sound_on_complete    (bool) — server-side value; only consulted by
                                  clients when sync is enabled

When sync is OFF (the default), client devices continue to use
localStorage["bd-sound-on-complete"], unchanged from v3.64.2. When ON,
they read/write the server value. Both keys are accepted independently
by the POST handler so a client can flip sync on without immediately
clobbering the server's value, and so a sound-OFF device that has
never opted into sync POSTs nothing about either key.

These tests cover the backend allowlist + round-trip only; the
client-side hook (useSyncedSoundPref) is exercised by the SPA tests.
"""
from __future__ import annotations


class TestSoundSyncAllowlist:
    def test_post_sound_sync_enabled_true(self, fresh_app):
        r = fresh_app.post("/api/global_config",
                           json={"sound_sync_enabled": True})
        assert r.status_code == 200
        r = fresh_app.get("/api/global_config")
        assert (r.get_json() or {}).get("sound_sync_enabled") is True

    def test_post_sound_sync_enabled_false_explicit(self, fresh_app):
        # Set ON then back OFF — verify the OFF write is honored, not
        # ignored as a "no-op default".
        fresh_app.post("/api/global_config",
                       json={"sound_sync_enabled": True})
        r = fresh_app.post("/api/global_config",
                           json={"sound_sync_enabled": False})
        assert r.status_code == 200
        r = fresh_app.get("/api/global_config")
        assert (r.get_json() or {}).get("sound_sync_enabled") is False

    def test_post_sound_on_complete_value(self, fresh_app):
        r = fresh_app.post("/api/global_config",
                           json={"sound_on_complete": True})
        assert r.status_code == 200
        r = fresh_app.get("/api/global_config")
        assert (r.get_json() or {}).get("sound_on_complete") is True

    def test_post_paired_adoption(self, fresh_app):
        """The hook's adoption path POSTs both keys in one body."""
        r = fresh_app.post("/api/global_config",
                           json={"sound_sync_enabled": True,
                                 "sound_on_complete": True})
        assert r.status_code == 200
        cfg = fresh_app.get("/api/global_config").get_json() or {}
        assert cfg.get("sound_sync_enabled") is True
        assert cfg.get("sound_on_complete") is True

    def test_unknown_field_does_not_create_sound_keys(self, fresh_app):
        """A POST not mentioning either key must leave them untouched.
        Critical for the per-device default — a device that doesn't
        opt in must not accidentally seed the server."""
        r = fresh_app.post("/api/global_config",
                           json={"watch_interval_sec": 45})
        assert r.status_code == 200
        cfg = fresh_app.get("/api/global_config").get_json() or {}
        # The keys may or may not be present (default state); they must
        # NOT have been set to anything truthy by this POST.
        assert not cfg.get("sound_sync_enabled")
        assert not cfg.get("sound_on_complete")

    def test_coerces_truthy_value_to_bool(self, fresh_app):
        """Strings/ints/etc. coerce to bool — matches watch_archive's
        bool() pattern in the same handler."""
        r = fresh_app.post("/api/global_config",
                           json={"sound_sync_enabled": 1})
        assert r.status_code == 200
        # Bool coercion: any truthy becomes True.
        cfg = fresh_app.get("/api/global_config").get_json() or {}
        assert cfg.get("sound_sync_enabled") is True

    def test_default_is_unset(self, fresh_app):
        """A fresh app has neither key set — the per-device default
        from v3.64.2 must remain the out-of-box behavior."""
        cfg = fresh_app.get("/api/global_config").get_json() or {}
        # Either absent or false — both are "default per-device" from
        # the client's perspective.
        assert not cfg.get("sound_sync_enabled")
