"""RED-first guard for the REDACTION SoT config-domain unification (REDACT-SOT Cut 1).

Grounded @3.66.626. Decisions: D1 fail-OPEN (no fuzzy/entropy widening; bare
ambiguous terms like ``session``/``sid``/``key`` do NOT trigger), D2 anchored
middle-ground, D3 frontend deferred to a later cut.

The config-domain secret-key predicate had drifted across four module groups, each
maintaining its own keyword set that MISSED real, reachable secret fields:

  * app_settings_center._is_secret  (regex: password|token|api_key|secret + cookie_file)
      -> MISSED: private_key, cookies, cookies_b64, passphrase, preshared_key
  * vpn / vpn_config _SECRET_KEY_HINTS (substring: key|password|passwd|token|secret|
      auth|credential|private)
      -> MISSED: cookies, cookies_b64, passphrase
      -> and OVER-redacted a benign numeric via bare "key": key_rotation_days
  * user_templates._SECRET_KEYS (exact allowlist of 11 keys)
      -> MISSED: private_key, passphrase, master_password, vault_token,
                 preshared_key, client_secret

The fix routes all four through a single shared config-domain predicate
``site_editor.is_secret_config_key`` (SECRET_FIELDS-exact UNION a curated substring
floor), so they can never drift again and the confirmed leaks close.

D1 controls (must NOT over-redact -- these are real non-secret config fields):
  key_rotation_days (bare "key"), session_timeout (bare "session"),
  sid_cookie_name (bare "sid"), login_url, username, success_url, user_field.

Pre-fix: the shared predicate does not exist AND the per-module leaks are open ->
these tests fail. Post-fix: shared predicate present, leaks closed, benign kept.

Sandbox-safe: zero-arg tests, no pytest fixtures, no I/O.
"""
from bulk_downloader import site_editor as SE


# Real, reachable secret config/VPN/template keys that MUST be classified secret.
_REAL_SECRETS = [
    "password", "plex_token", "stash_api_key", "auth_token",       # SECRET_FIELDS
    "private_key", "cookies", "cookies_b64", "passphrase",         # confirmed leaks
    "preshared_key", "master_password", "vault_token", "client_secret",
]

# Real non-secret config fields that MUST survive (D1 fail-open). Each contains a
# bare ambiguous substring (key/session/sid/auth/login) that a naive widening
# would wrongly flag.
_BENIGN_KEPT = [
    "key_rotation_days", "session_timeout", "sid_cookie_name",
    "login_url", "username", "success_url", "user_field", "submit_btn",
    "cookie_max_age_hours",
]


def test_shared_config_predicate_exists():
    assert hasattr(SE, "is_secret_config_key"), (
        "site_editor must export a single shared config-domain secret-key "
        "predicate is_secret_config_key() for the SoT unification"
    )


def test_shared_predicate_flags_all_real_secrets():
    missed = [k for k in _REAL_SECRETS if not SE.is_secret_config_key(k)]
    assert not missed, f"shared predicate misses real secret keys: {missed}"


def test_shared_predicate_keeps_benign_fields_open():
    # D1 fail-open: an ambiguous bare-substring match must NOT redact a real
    # non-secret config field.
    over = [k for k in _BENIGN_KEPT if SE.is_secret_config_key(k)]
    assert not over, f"shared predicate over-redacts benign fields (D1): {over}"


def test_shared_predicate_covers_all_secret_fields():
    # Every canonical SECRET_FIELDS entry must classify secret (superset property).
    missed = [k for k in SE.SECRET_FIELDS if not SE.is_secret_config_key(k)]
    assert not missed, f"shared predicate misses SECRET_FIELDS: {missed}"


# ---- per-module migration: each drifting module now closes its real leaks ----

def test_app_settings_center_closes_leaks():
    from bulk_downloader import app_settings_center as A
    leaks = ["private_key", "cookies", "cookies_b64", "passphrase", "preshared_key"]
    missed = [k for k in leaks if not A._is_secret(k)]
    assert not missed, f"app_settings_center._is_secret still leaks: {missed}"
    # regression: previously-caught keys still caught
    for k in ("password", "plex_token", "stash_api_key", "cookie_file"):
        assert A._is_secret(k), f"regressed on {k}"
    # D1: benign kept
    for k in ("login_url", "username", "session_timeout"):
        assert not A._is_secret(k), f"app_settings_center over-redacts {k}"


def test_vpn_closes_leaks_keeping_conservative_posture():
    from bulk_downloader import vpn as V
    red = V._redact_config({
        "cookies": "C", "passphrase": "P", "private_key": "K",
        "key_rotation_days": 30, "password": "pw", "endpoint": "1.2.3.4:51820",
    })
    # Leak closure via the shared floor -- these were MISSED by the old vpn hints.
    assert red["cookies"] == "***", "vpn leaks cookies"
    assert red["passphrase"] == "***", "vpn leaks passphrase"
    # Regression guard: previously-caught still caught.
    assert red["private_key"] == "***", "vpn regressed on private_key"
    assert red["password"] == "***", "vpn regressed on password"
    # Option A (documented policy): vpn deliberately over-redacts anything with a
    # bare 'key' hint -- key material leakage is far costlier than a masked int.
    assert red["key_rotation_days"] == "***", (
        "vpn keeps its conservative over-redaction posture (Option A)"
    )
    # A genuinely non-secret field with no hint match is preserved.
    assert red["endpoint"] == "1.2.3.4:51820", "vpn must preserve endpoint"


def test_user_templates_closes_leaks():
    from bulk_downloader import user_templates as U
    t = {
        "name": "demo", "private_key": "K", "passphrase": "P",
        "master_password": "M", "vault_token": "V", "client_secret": "C",
        "password": "pw",
    }
    stripped = U._strip_secrets(t)
    for k in ("private_key", "passphrase", "master_password", "vault_token",
              "client_secret", "password"):
        assert k not in stripped, f"user_templates export still carries {k}"
    assert stripped.get("name") == "demo", "user_templates dropped a benign field"
