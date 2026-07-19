"""RED-first guard for F-CORE_BD14-01 (v3.66.558).

marketplace._redact / export_template MUST strip every field in the
authoritative site_editor.SECRET_FIELDS SoT before a template is written to a
portable export bundle (and before the import preview echoes it). Prior to the
fix, marketplace._REDACT_KEYS covered only 2/10 of SECRET_FIELDS
(password, auth_token) and even name-mismatched (tpdb_key vs the real
tpdb_api_key), so qb_password / captcha_api_key / stash_api_key / plex_token /
jellyfin_api_key / ha_token / tpdb_api_key / ai_api_key leaked into the
downloadable EOL/marketplace export -- an AUTOMATION_POLICY no-secrets-in-outputs
violation and a broken "secrets omitted" promise.

These tests are written to FAIL on pristine 3.66.557 source and pass after the
fix. They are SoT-driven: if SECRET_FIELDS grows, the guard grows with it.
"""

from bulk_downloader import marketplace, site_editor


def test_redact_covers_every_secret_field():
    """_redact must drop every SECRET_FIELDS key and preserve non-secret keys."""
    raw = {f: f"SECRET-VALUE-{f}" for f in site_editor.SECRET_FIELDS}
    raw["name"] = "KeepMe"          # non-secret -> must survive
    raw["url"] = "https://example.com/keep"
    red = marketplace._redact(raw)

    leaked = sorted(f for f in site_editor.SECRET_FIELDS if f in red)
    assert not leaked, f"_redact left secret fields in the output: {leaked}"
    # no secret VALUE survives anywhere in the flat copy
    assert "SECRET-VALUE-" not in repr(red), "a secret value survived _redact"
    # non-secret structure preserved
    assert red.get("name") == "KeepMe"
    assert red.get("url") == "https://example.com/keep"


def test_export_template_bundle_omits_every_secret_field():
    """The end-to-end export bundle template must carry no SECRET_FIELDS value."""
    cfg = {f: f"SECRET-VALUE-{f}" for f in site_editor.SECRET_FIELDS}
    cfg["name"] = "KeepMe"
    bundle = marketplace.export_template("site1", cfg, sign_with=None)
    tmpl = bundle.get("template") or {}

    leaked = sorted(f for f in site_editor.SECRET_FIELDS if f in tmpl)
    assert not leaked, f"secret fields leaked into export bundle template: {leaked}"
    assert "SECRET-VALUE-" not in repr(bundle), "a secret value survived into the bundle"
    assert tmpl.get("name") == "KeepMe", "non-secret field was dropped from the export"
