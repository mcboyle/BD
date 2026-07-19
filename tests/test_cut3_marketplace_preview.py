"""Cut 3 — read-only import-PREVIEW mirror for marketplace bundles.

`preview_import_template(bundle, *, target_site_id=None, verify_with=None,
existing_site_ids=())` is the read-only sibling of `import_template`. It
validates + verifies + classifies WITHOUT emitting an importable config the
caller would persist. Contract:

    {
      ok: bool,
      site_id: str,
      status: "new"|"changed",          # changed if site_id already exists
      config_preview: dict,             # _redact()'d template (no secrets)
      secrets_omitted: [keys],          # secret keys found in the bundle template
      signature_check: {...},
      metadata: {...},
      warnings: [str],
      errors: [str],                    # on invalid/sig-fail; ok False
    }

RED on pristine 372: `preview_import_template` does not exist yet.

The mirror writes nothing by construction (the existing import_template doesn't
persist either — the route just returns the config), so the read-only guarantee
is about the shape + the redaction, which these tests pin.
"""
from bulk_downloader import marketplace as mp


def _bundle(site_id="acme", template=None, sign_with=None):
    b = {
        "schema": mp.SCHEMA_VERSION,
        "exported_at": "2026-01-01T00:00:00Z",
        "site_id": site_id,
        "template": template if template is not None else {
            "name": "Acme", "login_url": "https://acme.test/login",
            "learned": {"download": {"row_selectors": ["a[href]"],
                                     "url_attribute": "href"}},
        },
        "metadata": {"name": "Acme", "description": "", "author": "",
                     "compatible_bd_version": "3.43.80"},
    }
    if sign_with:
        b["signature"] = mp.sign_bundle(b, secret=sign_with)
    return b


def test_preview_valid_bundle_new_site():
    out = mp.preview_import_template(_bundle("acme"), existing_site_ids=set())
    assert out["ok"] is True, out
    assert out["site_id"] == "acme"
    assert out["status"] == "new"
    assert isinstance(out["config_preview"], dict)
    assert out["secrets_omitted"] == []


def test_preview_changed_when_target_site_exists():
    out = mp.preview_import_template(
        _bundle("acme"), target_site_id="existing_id",
        existing_site_ids={"existing_id", "other"})
    assert out["ok"] is True
    assert out["site_id"] == "existing_id"
    assert out["status"] == "changed"


def test_preview_redacts_and_lists_secrets_omitted():
    tmpl = {
        "name": "Secretive",
        "password": "hunter2",
        "cookies_b64": "AAAA",
        "auth_token": "t0ken",
        "login_url": "https://x.test",
    }
    out = mp.preview_import_template(_bundle("sec", template=tmpl),
                                     existing_site_ids=set())
    assert out["ok"] is True
    # config_preview must NOT carry any secret value
    cp = out["config_preview"]
    assert "password" not in cp
    assert "cookies_b64" not in cp
    assert "auth_token" not in cp
    assert cp.get("login_url") == "https://x.test"
    # secrets_omitted reports exactly the stripped secret keys
    assert set(out["secrets_omitted"]) == {"password", "cookies_b64", "auth_token"}


def test_preview_invalid_bundle_is_not_ok():
    out = mp.preview_import_template({"schema": "wrong", "site_id": 123},
                                     existing_site_ids=set())
    assert out["ok"] is False
    assert out["errors"]


def test_preview_signature_failure_is_not_ok():
    signed = _bundle("acme", sign_with="correct-secret")
    out = mp.preview_import_template(signed, verify_with="wrong-secret",
                                     existing_site_ids=set())
    assert out["ok"] is False
    assert out["signature_check"]["signed"] is True
    assert out["signature_check"]["ok"] is False


def test_preview_signed_bundle_ok_with_right_secret():
    signed = _bundle("acme", sign_with="s3cret")
    out = mp.preview_import_template(signed, verify_with="s3cret",
                                     existing_site_ids=set())
    assert out["ok"] is True
    assert out["signature_check"]["ok"] is True
