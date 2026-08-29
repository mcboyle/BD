"""GUI Phase 3 Slice 5 — Settings Center UI/UX polish (presentation only).

Asserts the presentation polish (humanized labels, raw keys retained, site_editor help text,
no invented help, sticky helper text, set/not-set polish) AND that behavior is unchanged vs
Slice 4 (route count 11, only validate non-GET, editable set unchanged [count 197], a fixed
validate sample yields identical accepted/rejected, JSON contract unchanged — no label/help
keys, no secret value leak, no new persistence). Sandbox-valid; on-stash visual review of the
rendered page still required.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

from flask import Flask  # noqa: E402
from bulk_downloader import app_settings_center as sc  # noqa: E402

_CFG = {"sites": {"demo": {
    "name": "Demo", "max_concurrent": 4, "wait": 5,
    "username": "alice", "login_url": "https://ex.test/login",
    "password": "LEAK_PW_123", "stash_api_key": "LEAK_KEY_456",
}}}

# Golden invariants captured from Slice 4 (behavior must not change in a presentation slice).
# v3.66.468: +1 (chromium_extensions joined CFG_FIELDS as a gui-safe editable field).
_EDITABLE_COUNT = 214  # Row 374: +6 authenticated scene-crawler controls.
_SAMPLE = {"max_concurrent": 999, "wait": 200, "delay": 3.5, "headless": "true",
           "skip_if_exists": "no", "password": "x", "username": "u",
           "cookie_max_age_hours": 12, "nope": 1, "chunk_size_mb": 2}
_EXP_ACCEPTED = {"chunk_size_mb": 2, "cookie_max_age_hours": 12, "delay": 3.5,
                 "headless": True, "skip_if_exists": False}
_EXP_REJECTED = {
    "max_concurrent": "out of range \u2014 expected integer in [1, 32]",
    "wait": "out of range \u2014 expected number in [0, 120]",
    "password": "secret (display-never)",
    "username": "auth/login: gui-gated \u2014 not editable in this slice",
    "nope": "unknown field (not in CFG_FIELDS)",
}
_DESCRIPTOR_KEYS = {"key", "category", "gui_class", "secret", "preserve_on_blank",
                    "sticky_nonsecret", "type", "description", "default", "range",
                    "required", "source", "current",
                    # v3.66.468: structured choices for enum fields (e.g. backend
                    # teach/jd/qb), null for non-enum fields. Drives the GUI <select>.
                    "enum"}


def _app():
    app = Flask(__name__)
    sc.register_routes(app)
    return app


def _stage(cfg):
    d = tempfile.mkdtemp()
    p = Path(d) / "sites_config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    old = os.environ.get("BD_SITES_CONFIG_PATH")
    os.environ["BD_SITES_CONFIG_PATH"] = str(p)
    return p, old


def _unstage(old):
    if old is None:
        os.environ.pop("BD_SITES_CONFIG_PATH", None)
    else:
        os.environ["BD_SITES_CONFIG_PATH"] = old


# ── presentation ────────────────────────────────────────────────────

def test_humanize_label_from_key_only():
    assert sc._humanize("max_concurrent") == "Max concurrent"
    assert sc._humanize("stash_api_key") == "Stash API key"
    assert sc._humanize("login_url") == "Login URL"


def test_page_renders_humanized_labels_and_raw_keys():
    p, old = _stage(_CFG)
    try:
        page = _app().test_client().get("/cockpit/settings/site/demo")
        assert page.status_code == 200
        assert b"Max concurrent" in page.data       # humanized label
        assert b"max_concurrent" in page.data        # raw key still shown
    finally:
        _unstage(old)


def test_site_editor_help_text_appears():
    p, old = _stage(_CFG)
    try:
        page = _app().test_client().get("/cockpit/settings/site/demo").data
        # description for max_concurrent comes from site_editor._FIELD_TYPES
        assert b"Parallel downloads for this site" in page
    finally:
        _unstage(old)


def test_no_invented_help_where_unavailable():
    # _help_text must NOT fabricate text when no description exists
    placeholder = sc._help_text({"description": ""})
    assert "\u2014" in placeholder and "<span" in placeholder
    assert sc._help_text({}) == placeholder
    assert "Parallel" in sc._help_text({"description": "Parallel downloads for this site"})


def test_sticky_helper_text_shown():
    p, old = _stage(_CFG)
    try:
        page = _app().test_client().get("/cockpit/settings/site/demo").data
        assert b"Leave blank to keep current" in page
        assert b"preserve-on-blank" in page
        assert b"alice" in page and b"ex.test/login" in page   # sticky values round-trip
    finally:
        _unstage(old)


def test_secret_set_not_set_polish_and_no_leak():
    p, old = _stage(_CFG)
    try:
        app = _app()
        page = app.test_client().get("/cockpit/settings/site/demo").data
        assert b"set</span>" in page and b"not set</span>" in page
        assert b"display-never" in page
        for tok in (b"LEAK_PW_123", b"LEAK_KEY_456"):
            assert tok not in page
            raw = app.test_client().get("/api/settings/site/demo/editable").data
            assert tok not in raw
    finally:
        _unstage(old)


# ── invariance vs Slice 4 (presentation-only proof) ─────────────────

def test_route_count_and_only_validate_non_get():
    rules = [r for r in _app().url_map.iter_rules() if r.endpoint != "static"]
    assert len(rules) == 11, len(rules)
    nonget = [r for r in rules if (r.methods or set()) - {"HEAD", "OPTIONS", "GET"}]
    assert len(nonget) == 1 and str(nonget[0].rule).endswith("/validate")


def test_editable_set_unchanged():
    ed = sc._editable_field_set()
    assert len(ed) == _EDITABLE_COUNT, len(ed)
    assert "max_concurrent" in ed and "wait" in ed
    for gated in ("password", "username", "login_url", "cookie_file", "dl_selector"):
        assert gated not in ed, gated


def test_validate_outputs_match_fixed_sample():
    p, old = _stage(_CFG)
    try:
        r = sc._validate_updates(_SAMPLE)
        assert r["accepted"] == _EXP_ACCEPTED, r["accepted"]
        assert r["rejected"] == _EXP_REJECTED, r["rejected"]
    finally:
        _unstage(old)


def test_json_contract_unchanged_no_label_help_keys():
    p, old = _stage(_CFG)
    try:
        b = json.loads(_app().test_client().get("/api/settings/site/demo/editable").data)
        assert set(b.keys()) == {"ok", "sid", "editable_count", "fields", "field_meta",
                                 "groups", "write_via", "validate_via", "read_only_endpoint",
                                 # v3.66.310 Phase 4.1: separate gated block (secrets
                                 # presence-only) so all 51 site_keys are editor-covered.
                                 "gated_count", "gated_meta", "gated_groups"}
        for d in b["field_meta"].values():
            assert set(d.keys()) == _DESCRIPTOR_KEYS, set(d.keys()) ^ _DESCRIPTOR_KEYS
            assert "label" not in d and "help" not in d
        # gated_meta descriptors share the same shape; secrets stay presence-only.
        for d in b["gated_meta"].values():
            assert set(d.keys()) == _DESCRIPTOR_KEYS, set(d.keys()) ^ _DESCRIPTOR_KEYS
            if d["secret"]:
                assert isinstance(d["current"], dict) and set(d["current"]) == {"present"}
    finally:
        _unstage(old)


def test_no_new_persistence_static():
    src = (_REPO / "bulk_downloader" / "app_settings_center.py").read_text(encoding="utf-8")
    assert ".write_text(" not in src
    assert "json.dump(" not in src
    assert "shutil" not in src
    assert not re.search(r"open\([^)]*['\"][wa]", src)
    assert "PUT /api/sites" in src   # save still delegates to the audited PUT
