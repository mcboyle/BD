"""RED-first guard for REDACT-SOT Cut 2: redact-on-export unification.

Grounded @3.66.628. The portable/downloadable EXPORT redactors had drifted from
the config-domain SoT (site_editor.is_secret_config_key, unified in Cut 1):

  * marketplace._redact (POST /api/marketplace/export/<sid> -> a downloadable,
    shareable bundle) only stripped SECRET_FIELDS-exact, so it LEAKED into the
    portable bundle: private_key, cookies, cookies_b64, passphrase, preshared_key,
    master_password, vault_token, client_secret.
  * diagnostics_bundle._is_sensitive_key (its broad regex set) missed the anchored
    key-material names private_key / passphrase / preshared_key.

Both are routed through the shared SoT so a portable export can never fall behind
a newly added secret field. The encrypt-on-export half already exists
(backup.py PBKDF2+Fernet) and is out of scope.

Pre-fix: export_template carries the leak keys AND diagnostics misses the three ->
these tests fail. Post-fix: both stripped.

Sandbox-safe: zero-arg tests, no fixtures, no I/O.
"""
from bulk_downloader import marketplace as MP
from bulk_downloader import diagnostics_bundle as DB


# Real secret keys that must never reach a portable export bundle.
_EXPORT_LEAKS = [
    "private_key", "cookies", "cookies_b64", "passphrase",
    "preshared_key", "master_password", "vault_token", "client_secret",
]


def test_marketplace_export_redacts_unified_secrets():
    # A site config carrying credential fields that the OLD SECRET_FIELDS-exact
    # set missed. After the fix none of them survive the export redaction.
    site_cfg = {
        "name": "demo",
        "endpoint": "https://example.com",   # benign, must survive
        "private_key": "PRIV", "cookies": "CK", "cookies_b64": "CK64",
        "passphrase": "PP", "preshared_key": "PSK", "master_password": "MP",
        "vault_token": "VT", "client_secret": "CS",
        "plex_token": "PLEX",                # already-caught, regression guard
    }
    out = MP._redact(site_cfg)
    leaked = [k for k in _EXPORT_LEAKS if k in out]
    assert not leaked, f"marketplace export bundle leaks: {leaked}"
    assert "plex_token" not in out, "regressed: plex_token no longer redacted"
    # benign non-secret field is preserved
    assert out.get("endpoint") == "https://example.com", "benign field dropped"
    assert out.get("name") == "demo", "benign field dropped"


def test_marketplace_export_redacts_nested_secrets():
    # _redact recurses into nested dicts; a nested credential must also go.
    cfg = {"name": "n", "nested": {"private_key": "K", "ok": "keep"}}
    out = MP._redact(cfg)
    assert "private_key" not in out.get("nested", {}), "nested private_key leaked"
    assert out["nested"].get("ok") == "keep", "nested benign field dropped"


def test_export_template_bundle_has_no_leaks():
    # End-to-end: the public export_template() output config carries no leak keys.
    bundle = MP.export_template(
        "sid-1",
        {"name": "demo", "private_key": "PRIV", "passphrase": "PP",
         "vault_token": "VT", "endpoint": "https://x"},
        name="demo",
    )
    # locate the config portion of the bundle
    import json
    blob = json.dumps(bundle)
    for k in ("PRIV", "PP", "VT"):
        assert k not in blob, f"secret value {k} present in export bundle"


def test_diagnostics_bundle_redacts_key_material():
    # diagnostics_bundle was missing the anchored key-material names.
    rec = {"private_key": "K", "passphrase": "P", "preshared_key": "PSK",
           "note": "keep"}
    out = DB.redact_secrets(rec)
    for k in ("private_key", "passphrase", "preshared_key"):
        assert out[k] == "<redacted>", f"diagnostics leaks {k}: {out.get(k)!r}"
    assert out["note"] == "keep", "diagnostics over-redacted a benign field"
