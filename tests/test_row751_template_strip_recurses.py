"""Row 751: nested user-template credentials are omitted and named."""
BD_GATE_SCOPE = "repo-wide"

import json

from bulk_downloader import user_templates
from bulk_downloader.user_templates import (
    _secret_keys_in, _strip_secrets, export_user_templates,
    preview_user_templates_import)


def _template():
    # Zero-entropy synthetic credential fixture.
    return {"id": "t1", "name": "T", "patterns": ["x"],
            "captcha_api_key": "SYNTHETIC-NOT-A-KEY",
            "config_defaults": {"captcha_api_key": "SYNTHETIC-NOT-A-KEY",
                                "captcha_provider": "anticaptcha",
                                "nested": [{"password": "AAAA"}]}}


def test_template_secret_strip_and_preview_names_descendants():
    # Zero-entropy synthetic credential fixture.
    template = {"id": "t1", "name": "T", "captcha_api_key": "SYNTHETIC-NOT-A-KEY", "config_defaults": {"captcha_api_key": "SYNTHETIC-NOT-A-KEY", "captcha_provider": "anticaptcha", "nested": [{"password": "AAAA"}]}}
    assert _secret_keys_in(template) == ["captcha_api_key", "config_defaults.captcha_api_key", "config_defaults.nested[0].password"]
    stripped = _strip_secrets(template)
    assert "captcha_api_key" not in stripped["config_defaults"]
    assert "password" not in stripped["config_defaults"]["nested"][0]


def test_the_public_export_and_import_preview_omit_and_name_nested_secrets(monkeypatch):
    """Row 751(b)'s acceptance is stated on the PUBLIC surface: the bytes an
    operator downloads, and the list the import preview shows them.  Both
    are asserted through the two private helpers and nowhere else."""
    monkeypatch.setattr(user_templates, "_load", lambda: [_template()])
    payload = export_user_templates()
    assert payload["templates"], "precondition: the export has a nonzero subject"
    assert "SYNTHETIC-NOT-A-KEY" not in json.dumps(payload)
    assert "AAAA" not in json.dumps(payload)

    preview = preview_user_templates_import({"templates": [_template()]})
    item = preview["items"][0]
    assert "config_defaults.captcha_api_key" in item["secrets_omitted"]
    assert "config_defaults.nested[0].password" in item["secrets_omitted"]
    assert preview["counts"]["secrets_omitted"] == 1
