"""RED-first guard for v3.66.557: user_templates secret-strip (F-CORE_BD10-01).

preview_user_templates_import reports secret-bearing keys as "secrets omitted" (promising
they'll be dropped), but import_user_templates does NOT strip _SECRET_KEYS -- merge appends
the full incoming dict, replace saves the full validated set -- and export_user_templates
returns _load() unmodified. So password/api_key/cookies/... persist into user_templates.json
AND leak verbatim into the downloadable export (a preview-contract violation + AUTOMATION_POLICY
'no secrets in templates/outputs').

The fix strips _SECRET_KEYS from every incoming template in BOTH import paths before _save,
and (defense in depth) strips them in export_user_templates.

RED on the pre-557 tree: after import the secret keys are on disk, and the export JSON
contains the secret values. GREEN once they're stripped.

Convention: zero-arg fns; _save/_load patched to an in-memory store (no disk), restored
in try/finally.
"""
import json
import bulk_downloader.user_templates as ut

_SECRETS = {"password": "PW_SECRET_wit", "api_key": "KEY_SECRET_wit", "cookies": "sess=1"}


def _tmpl():
    t = {"id": "t-secret", "name": "Test Template", "description": "desc",
         "learned": {"download": {"row_selectors": ["a.dl"]}}, "site_id": "example.com"}
    t.update(_SECRETS)
    return t


_store = []


def _patch():
    orig_save, orig_load = ut._save, ut._load
    ut._save = lambda templates: _store.__setitem__(slice(None), list(templates))
    ut._load = lambda: list(_store)
    _store.clear()

    def restore():
        ut._save, ut._load = orig_save, orig_load
    return restore


def test_import_merge_strips_secrets():
    restore = _patch()
    try:
        ut.import_user_templates({"templates": [_tmpl()]}, merge=True)
        saved = ut._load()
        assert saved, "template should have been imported"
        keys = set(saved[0].keys())
        for k in _SECRETS:
            assert k not in keys, f"secret key {k} must be stripped on import (merge); got {sorted(keys)}"
        assert saved[0].get("name") == "Test Template", "non-secret fields must be kept"
    finally:
        restore()


def test_import_replace_strips_secrets():
    restore = _patch()
    try:
        ut.import_user_templates({"templates": [_tmpl()]}, merge=False)
        saved = ut._load()
        assert saved, "template should have been imported"
        for k in _SECRETS:
            assert all(k not in t for t in saved), f"secret {k} persisted in replace mode"
    finally:
        restore()


def test_export_omits_secret_values():
    restore = _patch()
    try:
        ut._save([_tmpl()])   # a pre-existing secret-bearing template already on disk
        blob = json.dumps(ut.export_user_templates())
        for v in _SECRETS.values():
            assert v not in blob, f"export must not leak the secret value {v!r}"
    finally:
        restore()
