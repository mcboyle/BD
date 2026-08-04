"""GUI Phase 3 Slice 4 — per-site Settings Center read/write polish.

Verifies the polish without booting app.py (Flask is in prestaged_site_packages):
field descriptors (current/default/source/type/range) on the editable endpoint, grouping
driven by the runtime _categorize, display-never secrets (presence only, no value leak),
sticky-not-secret (username/login_url) preserve-on-blank marking, clear validation
messages with site_editor ranges, non-mutation, no direct writes, and containment
(11 routes, only validate is non-GET, version unchanged). Sandbox-valid; on-stash
operator click-through still required for the live page + a real save.
"""
import json
import os
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
    "username": "alice", "login_url": "https://example.test/login",
    "password": "SUPERSECRETVALUE", "stash_api_key": "KEY12345",
}}}


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


def test_editable_has_field_descriptors():
    p, old = _stage(_CFG)
    try:
        b = json.loads(_app().test_client().get("/api/settings/site/demo/editable").data)
        assert "groups" in b and "field_meta" in b, list(b.keys())
        assert b.get("validate_via") == "POST /api/settings/site/demo/validate"
        fm = b["field_meta"]
        assert fm, "no editable descriptors"
        for k, d in fm.items():
            for key in ("category", "gui_class", "secret", "type", "default",
                        "range", "required", "source", "current", "preserve_on_blank"):
                assert key in d, (k, key)
            assert d["secret"] is False           # editable surface is gui-safe only
            assert d["gui_class"] == "gui-safe"
            assert d["default"] == "\u2014"        # no per-field default source -> em dash
            assert "CFG_FIELDS (app.py)" in d["source"]
    finally:
        _unstage(old)


def test_groups_match_runtime_categorize():
    p, old = _stage(_CFG)
    try:
        b = json.loads(_app().test_client().get("/api/settings/site/demo/editable").data)
        for cat, descriptors in b["groups"].items():
            for d in descriptors:
                assert sc._categorize(d["key"]) == cat, (d["key"], cat)
                assert d["category"] == cat
    finally:
        _unstage(old)


def test_numeric_metadata_from_site_editor():
    p, old = _stage(_CFG)
    try:
        b = json.loads(_app().test_client().get("/api/settings/site/demo/editable").data)
        fm = b["field_meta"]
        # max_concurrent: integer with range [1,32] from site_editor NUMERIC_RANGES
        mc = fm.get("max_concurrent")
        assert mc and mc["type"] == "integer" and mc["range"] == [1, 32], mc
        assert "site_editor.py" in mc["source"]
        # wait: number with range [0,120]; current value round-trips (non-secret)
        w = fm.get("wait")
        assert w and w["type"] == "number" and w["range"] == [0, 120], w
        assert w["current"] == 5
    finally:
        _unstage(old)


def test_secrets_absent_from_editable_and_no_value_leak():
    p, old = _stage(_CFG)
    try:
        app = _app()
        b = json.loads(app.test_client().get("/api/settings/site/demo/editable").data)
        for s in ("password", "stash_api_key", "cookie_file"):
            assert s not in b["field_meta"], s   # secrets never in the editable surface
        page = app.test_client().get("/cockpit/settings/site/demo")
        assert page.status_code == 200
        data = page.data
        # display-never: the real secret VALUES must never appear in the page
        assert b"SUPERSECRETVALUE" not in data
        assert b"KEY12345" not in data
        # presence indicators are rendered instead
        assert b"display-never" in data
        assert b"set</span>" in data and b"not set</span>" in data
    finally:
        _unstage(old)


def test_sticky_nonsecret_preserve_on_blank():
    p, old = _stage(_CFG)
    try:
        # contract: username/login_url are NOT secrets, preserve-on-blank, and gated
        for k in ("username", "login_url"):
            assert sc._is_secret(k) is False
            assert sc._preserve_on_blank(k) is True
            assert k in sc._STICKY_NONSECRET
            assert k not in sc._editable_field_set()   # auth/login is gated here
        # page round-trips their current values (non-secret) in the sticky section
        page = _app().test_client().get("/cockpit/settings/site/demo").data
        assert b"preserve-on-blank" in page
        assert b"alice" in page                       # username value round-trips
        assert b"example.test/login" in page          # login_url value round-trips
    finally:
        _unstage(old)


def test_validate_messages_clear():
    p, old = _stage(_CFG)
    try:
        c = _app().test_client()
        v = c.post("/api/settings/site/demo/validate", json={"updates": {
            "max_concurrent": 999,        # int out of range
            "wait": 200,                  # number out of range
            "headless": "maybe",          # bad bool
            "password": "x",              # secret => gated
            "totally_unknown_field": 1,   # unknown
            "wait_ok": None,
        }}).get_json()
        rej = v["rejected"]
        assert "out of range" in rej["max_concurrent"] and "[1, 32]" in rej["max_concurrent"]
        assert "out of range" in rej["wait"] and "[0, 120]" in rej["wait"]
        assert "boolean" in rej["headless"]
        assert rej["password"] == "secret (display-never)"
        assert "unknown field" in rej["totally_unknown_field"]
        # a valid in-range edit is accepted with correct typing
        v2 = c.post("/api/settings/site/demo/validate", json={"updates": {
            "max_concurrent": 8, "wait": 10, "headless": "true"}}).get_json()
        assert v2["accepted"] == {"max_concurrent": 8, "wait": 10, "headless": True}, v2["accepted"]
    finally:
        _unstage(old)


def test_validate_is_nonmutating_on_disk():
    p, old = _stage(_CFG)
    try:
        before = p.read_bytes()
        _app().test_client().post("/api/settings/site/demo/validate", json={"updates": {
            "max_concurrent": 9, "wait": 11}})
        assert p.read_bytes() == before          # dry-run wrote nothing
        body = json.loads(_app().test_client().post(
            "/api/settings/site/demo/validate", json={"updates": {"max_concurrent": 9}}).data)
        assert "PUT /api/sites/<sid>" in body["note"]
    finally:
        _unstage(old)


def test_no_direct_write_in_blueprint():
    src = (_REPO / "bulk_downloader" / "app_settings_center.py").read_text(encoding="utf-8")
    assert ".write_text(" not in src
    assert "json.dump(" not in src
    assert "shutil" not in src
    # no open(...) in write/append mode
    import re
    assert not re.search(r"open\([^)]*['\"][wa]", src)
    # the only persistence is the documented delegation to the audited PUT
    assert "PUT /api/sites" in src


def test_containment_routes_and_version():
    app = _app()
    rules = [r for r in app.url_map.iter_rules() if r.endpoint != "static"]
    assert len(rules) == 11, len(rules)
    nonget = [r for r in rules if (r.methods or set()) - {"HEAD", "OPTIONS", "GET"}]
    assert len(nonget) == 1, [str(r.rule) for r in nonget]
    assert str(nonget[0].rule).endswith("/validate")
    from bulk_downloader import __version__
    assert __version__ == "3.66.856", __version__


def test_editor_page_renders_grouped():
    p, old = _stage(_CFG)
    try:
        page = _app().test_client().get("/cockpit/settings/site/demo")
        assert page.status_code == 200
        low = page.data.lower()
        assert b"<!doctype" in low or b"<html" in low
        assert b"save flow" in low
        assert b"group(s)" in low
        # at least one runtime category label appears as a section header
        cats = {sc._categorize(k) for k in sc._editable_field_set()}
        assert any(c.encode() in page.data for c in cats), cats
    finally:
        _unstage(old)
