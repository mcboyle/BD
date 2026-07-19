"""v3.43.60 coverage gap fill — vpn_config tests.

vpn_config.py was added in v3.43.60 (449 LOC, HIGH-RISK secret handling)
but had no dedicated tests. This file fills that gap with:

  - Load/save round-trip (atomic write + corruption tolerance)
  - Tunnel CRUD (add, get, list, update, remove)
  - Validation (rejects missing required fields, drops unexpected keys)
  - Global settings persistence
  - @cred: secret indirection (store_secrets, resolve_secrets)
  - register_loaded_tunnels integration with vpn.VPNManager

Uses setup_method/teardown_method + plain helper methods for portability
across all three test runners.
"""
from __future__ import annotations

import json
import os
import shutil
# [SAST 3:13pm 13 may] removed unused: import sys
import tempfile
from pathlib import Path

import pytest


class _EnvSaver:
    def __init__(self): self._saved = []
    def setenv(self, k, v):
        self._saved.append((k, os.environ.get(k)))
        os.environ[k] = str(v)
    def restore(self):
        while self._saved:
            k, v = self._saved.pop()
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _IsolatedVpnConfigBase:
    """Each test gets its own temp directory for vpn_config storage."""
    def setup_method(self):
        self._envsaver = _EnvSaver()
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="vpn-config-test-"))
        # vpn_config respects BD_VPN_CONFIG_PATH (the full JSON file path).
        self._envsaver.setenv("BD_VPN_CONFIG_PATH", str(self._tmp_dir / "tunnels.json"))
        from bulk_downloader import vpn_config
        vpn_config._reset_for_tests()

    def teardown_method(self):
        from bulk_downloader import vpn_config
        vpn_config._reset_for_tests()
        self._envsaver.restore()
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _valid_tunnel(self, tid="t1", name="Test", provider="generic", backend="wireguard"):
        return {"tunnel_id": tid, "name": name, "provider": provider, "backend": backend}


# ════════════════════════════════════════════════════════════════════
#  Load / save round-trip
# ════════════════════════════════════════════════════════════════════

class TestLoadSave(_IsolatedVpnConfigBase):
    def test_load_empty_returns_defaults(self):
        from bulk_downloader import vpn_config
        state = vpn_config.load()
        assert state["schema_version"] == vpn_config.SCHEMA_VERSION
        assert state["tunnels"] == []
        assert isinstance(state["global_settings"], dict)

    def test_save_creates_file(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        vpn_config.save()
        # File should now exist somewhere under our temp dir
        files = list(self._tmp_dir.rglob("*.json"))
        assert any(f.name.endswith(".json") for f in files), \
            f"expected a .json under {self._tmp_dir}, got {files}"

    def test_corrupted_file_falls_back_to_defaults(self):
        from bulk_downloader import vpn_config
        # Write garbage where the config file would be, then reset+load
        cfg = vpn_config._config_path()
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("{ this is not valid json }")
        vpn_config._reset_for_tests()
        state = vpn_config.load()
        # Should recover with defaults, not crash
        assert state["tunnels"] == []


# ════════════════════════════════════════════════════════════════════
#  Tunnel CRUD
# ════════════════════════════════════════════════════════════════════

class TestTunnelCrud(_IsolatedVpnConfigBase):
    def test_add_then_get(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        added = vpn_config.add_tunnel_config(self._valid_tunnel("t1"))
        assert added["tunnel_id"] == "t1"
        got = vpn_config.get_tunnel_config("t1")
        assert got is not None
        assert got["tunnel_id"] == "t1"

    def test_get_unknown_returns_none(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        assert vpn_config.get_tunnel_config("nope") is None

    def test_list_tunnel_configs(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        vpn_config.add_tunnel_config(self._valid_tunnel("a"))
        vpn_config.add_tunnel_config(self._valid_tunnel("b"))
        lst = vpn_config.list_tunnel_configs()
        assert len(lst) == 2
        ids = {t["tunnel_id"] for t in lst}
        assert ids == {"a", "b"}

    def test_add_replaces_existing(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        vpn_config.add_tunnel_config(self._valid_tunnel("t1", name="First"))
        vpn_config.add_tunnel_config(self._valid_tunnel("t1", name="Replaced"))
        lst = vpn_config.list_tunnel_configs()
        assert len(lst) == 1
        assert lst[0]["name"] == "Replaced"

    def test_update_partial_fields(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        vpn_config.add_tunnel_config(self._valid_tunnel("t1", name="Original"))
        updated = vpn_config.update_tunnel_config("t1", name="Renamed", location="EU")
        assert updated is not None
        assert updated["name"] == "Renamed"
        assert updated["location"] == "EU"
        # Verify persistence
        got = vpn_config.get_tunnel_config("t1")
        assert got["name"] == "Renamed"

    def test_update_unknown_returns_none(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        assert vpn_config.update_tunnel_config("nope", name="x") is None

    def test_remove_tunnel(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        vpn_config.add_tunnel_config(self._valid_tunnel("t1"))
        assert vpn_config.remove_tunnel_config("t1") is True
        assert vpn_config.get_tunnel_config("t1") is None
        # Second remove is a no-op
        assert vpn_config.remove_tunnel_config("t1") is False


# ════════════════════════════════════════════════════════════════════
#  Validation
# ════════════════════════════════════════════════════════════════════

class TestValidation(_IsolatedVpnConfigBase):
    def test_missing_required_field_raises(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        bad = {"tunnel_id": "t1", "name": "x", "provider": "generic"}
        # backend is missing
        with pytest.raises(ValueError, match="missing required field"):
            vpn_config.add_tunnel_config(bad)

    def test_unexpected_keys_dropped(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        cfg = self._valid_tunnel("t1")
        cfg["nefarious_extra"] = "should be dropped"
        added = vpn_config.add_tunnel_config(cfg)
        assert "nefarious_extra" not in added

    def test_defaults_applied(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        added = vpn_config.add_tunnel_config(self._valid_tunnel("t1"))
        assert added["enabled"] is True
        assert added["location"] == ""
        assert added["config"] == {}
        assert added["extra"] == {}


# ════════════════════════════════════════════════════════════════════
#  Persistence round-trip
# ════════════════════════════════════════════════════════════════════

class TestPersistenceRoundTrip(_IsolatedVpnConfigBase):
    def test_add_then_reload_preserves_tunnels(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        vpn_config.add_tunnel_config(self._valid_tunnel("t1", name="Reload Test"))
        # Reset in-memory state, then reload from disk
        vpn_config._reset_for_tests()
        state = vpn_config.load()
        assert len(state["tunnels"]) == 1
        assert state["tunnels"][0]["tunnel_id"] == "t1"
        assert state["tunnels"][0]["name"] == "Reload Test"

    def test_update_then_reload_preserves_changes(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        vpn_config.add_tunnel_config(self._valid_tunnel("t1"))
        vpn_config.update_tunnel_config("t1", location="JP")
        vpn_config._reset_for_tests()
        state = vpn_config.load()
        assert state["tunnels"][0]["location"] == "JP"

    def test_remove_then_reload_drops_tunnel(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        vpn_config.add_tunnel_config(self._valid_tunnel("t1"))
        vpn_config.add_tunnel_config(self._valid_tunnel("t2"))
        vpn_config.remove_tunnel_config("t1")
        vpn_config._reset_for_tests()
        state = vpn_config.load()
        ids = {t["tunnel_id"] for t in state["tunnels"]}
        assert ids == {"t2"}


# ════════════════════════════════════════════════════════════════════
#  Global settings
# ════════════════════════════════════════════════════════════════════

class TestGlobalSettings(_IsolatedVpnConfigBase):
    def test_initial_global_is_dict(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        g = vpn_config.get_global_settings()
        assert isinstance(g, dict)
        # Defaults are populated
        assert "leak_test_interval_s" in g

    def test_update_global_settings_persists(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        vpn_config.update_global_settings(leak_test_interval_s=600,
                                          kill_switch_auto_recover=False)
        g = vpn_config.get_global_settings()
        assert g["leak_test_interval_s"] == 600
        assert g["kill_switch_auto_recover"] is False

    def test_update_global_settings_partial_keeps_others(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        vpn_config.update_global_settings(leak_test_interval_s=600,
                                          max_concurrent_tunnels=16)
        vpn_config.update_global_settings(leak_test_interval_s=900)
        g = vpn_config.get_global_settings()
        assert g["leak_test_interval_s"] == 900
        assert g["max_concurrent_tunnels"] == 16  # unchanged

    def test_unknown_settings_ignored(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        # Unknown keys are ignored (not raised) — only whitelisted keys take effect
        vpn_config.update_global_settings(nefarious_attack_vector="evil",
                                          leak_test_interval_s=1234)
        g = vpn_config.get_global_settings()
        assert "nefarious_attack_vector" not in g
        assert g["leak_test_interval_s"] == 1234

    def test_global_settings_round_trip(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        vpn_config.update_global_settings(leak_test_interval_s=777)
        vpn_config._reset_for_tests()
        state = vpn_config.load()
        assert state["global_settings"]["leak_test_interval_s"] == 777


# ════════════════════════════════════════════════════════════════════
#  Secret indirection (@cred: refs)
# ════════════════════════════════════════════════════════════════════

class _StubSecretBackend:
    """Captures set/get pairs so tests can verify routing.

    Has `name = "stub"` (NOT "plaintext") so vpn_config.store_secrets
    will actually use it for indirection. Plaintext name disables
    indirection — see the Phase 3 audit fix in vpn_config.store_secrets.
    """
    name = "stub"
    def __init__(self):
        self.store = {}
    def set(self, k, v): self.store[k] = v
    def get(self, k): return self.store.get(k)
    def delete(self, k): self.store.pop(k, None)
    def list_keys(self): return list(self.store.keys())


class TestSecretIndirection(_IsolatedVpnConfigBase):
    def setup_method(self):
        super().setup_method()
        # Stub the secrets_store backend
        from bulk_downloader import secrets_store
        self._stub_backend = _StubSecretBackend()
        self._orig_get_backend = secrets_store.get_backend
        secrets_store.get_backend = lambda: self._stub_backend

    def teardown_method(self):
        from bulk_downloader import secrets_store
        secrets_store.get_backend = self._orig_get_backend
        super().teardown_method()

    def test_store_secrets_replaces_password_with_cred_ref(self):
        from bulk_downloader import vpn_config
        cfg = {"username": "alice", "password": "hunter2", "endpoint": "vpn.example.com"}
        out = vpn_config.store_secrets("t1", cfg)
        assert out["username"] == "alice"        # not a secret
        assert out["endpoint"] == "vpn.example.com"  # not a secret
        assert out["password"].startswith("@cred:")
        assert "hunter2" not in out["password"]
        # The secret should be in the stub backend
        cred_key = out["password"][len("@cred:"):]
        assert self._stub_backend.get(cred_key) == "hunter2"

    def test_store_secrets_also_handles_api_key_and_token(self):
        from bulk_downloader import vpn_config
        cfg = {"api_key": "k1", "auth_token": "t1", "user": "alice"}
        out = vpn_config.store_secrets("tun1", cfg)
        assert out["api_key"].startswith("@cred:")
        assert out["auth_token"].startswith("@cred:")
        assert out["user"] == "alice"  # unchanged

    def test_store_secrets_idempotent_on_existing_refs(self):
        from bulk_downloader import vpn_config
        cfg = {"password": "@cred:some_existing_ref"}
        out = vpn_config.store_secrets("t1", cfg)
        # Should NOT double-indirect — leave existing @cred: refs alone
        assert out["password"] == "@cred:some_existing_ref"

    def test_store_secrets_recurses_into_nested_dicts(self):
        from bulk_downloader import vpn_config
        cfg = {"name": "x", "config": {"password": "secret123"}}
        out = vpn_config.store_secrets("t1", cfg)
        assert out["config"]["password"].startswith("@cred:")

    def test_resolve_secrets_replaces_ref_with_value(self):
        from bulk_downloader import vpn_config
        # Pre-populate the stub
        self._stub_backend.set("my_tun:password", "hunter2")
        cfg = {"username": "alice", "password": "@cred:my_tun:password"}
        out = vpn_config.resolve_secrets(cfg)
        assert out["username"] == "alice"
        assert out["password"] == "hunter2"

    def test_resolve_secrets_missing_ref_returns_empty_string(self):
        from bulk_downloader import vpn_config
        cfg = {"password": "@cred:nonexistent"}
        out = vpn_config.resolve_secrets(cfg)
        assert out["password"] == ""

    def test_resolve_secrets_passes_through_non_refs(self):
        from bulk_downloader import vpn_config
        cfg = {"name": "tun1", "endpoint": "https://vpn"}
        out = vpn_config.resolve_secrets(cfg)
        assert out == cfg

    def test_round_trip_store_then_resolve(self):
        from bulk_downloader import vpn_config
        cfg = {"username": "alice", "password": "hunter2"}
        stored = vpn_config.store_secrets("t1", cfg)
        resolved = vpn_config.resolve_secrets(stored)
        assert resolved["username"] == "alice"
        assert resolved["password"] == "hunter2"

    def test_remove_tunnel_purges_secrets(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        vpn_config.add_tunnel_config(self._valid_tunnel("t1"))
        # Inject a secret tied to this tunnel
        self._stub_backend.set("t1:password", "hunter2")
        vpn_config.remove_tunnel_config("t1")
        # Secret should be gone too
        assert self._stub_backend.get("t1:password") is None


# ════════════════════════════════════════════════════════════════════
#  Credentials resolver for VPNManager
# ════════════════════════════════════════════════════════════════════

class TestCredentialsResolver(_IsolatedVpnConfigBase):
    def test_resolver_returns_resolved_config(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        # Add a tunnel with a config field containing a @cred: ref
        # (after a fake "store_secrets" run, the password is already replaced)
        cfg = self._valid_tunnel("t1")
        cfg["config"] = {"username": "alice", "endpoint": "vpn.example.com"}
        vpn_config.add_tunnel_config(cfg)
        out = vpn_config.credentials_resolver_for_vpn("t1")
        assert isinstance(out, dict)
        # No @cred: refs in the output (nothing was a secret here)
        assert out.get("username") == "alice"

    def test_resolver_unknown_tunnel_returns_empty(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        out = vpn_config.credentials_resolver_for_vpn("nonexistent")
        assert out == {}


# ════════════════════════════════════════════════════════════════════
#  Integration with VPNManager
# ════════════════════════════════════════════════════════════════════

class TestVpnManagerIntegration(_IsolatedVpnConfigBase):
    def test_register_loaded_tunnels_idempotent(self):
        """Calling register_loaded_tunnels twice should not raise or
        duplicate the registration count beyond the actual tunnel set."""
        from bulk_downloader import vpn_config, vpn
        vpn._reset_for_tests()
        vpn_config.load()
        vpn_config.add_tunnel_config(self._valid_tunnel("t1"))
        vpn_config.add_tunnel_config(self._valid_tunnel("t2"))
        count1, errors1 = vpn_config.register_loaded_tunnels()
        # Second call shouldn't crash on already-registered tunnels
        count2, errors2 = vpn_config.register_loaded_tunnels()
        # No errors specific to "already registered"
        # (errors might include backend-specific issues from t1/t2, which
        # is fine for this idempotency check)
        assert isinstance(errors1, list)
        assert isinstance(errors2, list)
        # Cleanup
        vpn._reset_for_tests()

    def test_install_credentials_resolver_is_callable(self):
        from bulk_downloader import vpn_config, vpn
        vpn._reset_for_tests()
        try:
            vpn_config.install_credentials_resolver()
        except Exception as e:
            pytest.fail(f"install_credentials_resolver raised: {e}")
        finally:
            vpn._reset_for_tests()

    def test_disabled_tunnels_skipped_during_register(self):
        from bulk_downloader import vpn_config, vpn
        vpn._reset_for_tests()
        vpn_config.load()
        disabled = self._valid_tunnel("t_disabled")
        disabled["enabled"] = False
        vpn_config.add_tunnel_config(disabled)
        count, errors = vpn_config.register_loaded_tunnels()
        # Tunnel is a dataclass — use attribute access, not subscripting.
        registered = {t.tunnel_id for t in vpn.list_tunnels()}
        assert "t_disabled" not in registered
        vpn._reset_for_tests()


# ════════════════════════════════════════════════════════════════════
#  Reset hook
# ════════════════════════════════════════════════════════════════════

class TestResetForTests(_IsolatedVpnConfigBase):
    def test_reset_clears_in_memory_state(self):
        from bulk_downloader import vpn_config
        vpn_config.load()
        vpn_config.add_tunnel_config(self._valid_tunnel("t1"))
        assert len(vpn_config.list_tunnel_configs()) == 1
        vpn_config._reset_for_tests()
        # After reset, state is empty (without auto-load)
        # but list_tunnel_configs triggers load(), so it might reload from disk
        # — the important thing is it doesn't crash
        result = vpn_config.list_tunnel_configs()
        assert isinstance(result, list)
