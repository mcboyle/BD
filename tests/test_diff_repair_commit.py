"""diff_repair COMMIT — POST /api/sites/<sid>/learned/apply_repairs (Phase 29).

Proves the operator-verified commit path applies accepted repairs into a site's
learned.download selectors: in-place replace (old must already exist), removed
deletes, dry_run previews without writing, bad role / missing old_selector are
rejected (not applied), and an unknown sid is 404. Mirrors
test_put_numeric_range_backstop's BD_HOME-staged app boot; update_config is
stubbed (scheduler thread is orthogonal to the validate/persist path here).
"""
import json
import os

import pytest

pytestmark = pytest.mark.bd_module_wipe


def _boot_with_site():
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader import app as a
    from bulk_downloader import db
    db.db_init()
    a.SITES_FILE.write_text(json.dumps({"demo": {"name": "Demo"}}),
                            encoding="utf-8")
    a._load_sites_config()
    # The on-disk loader normalizes/strips a hand-written learned block, so seed
    # it directly on the live config the route actually reads (s_cfg).
    a.s_cfg["demo"]["learned"] = {"download": {
        "row_selectors": ["a.old", "a.keep"],
        "trigger_selectors": ["button.dl"],
    }}
    a.runners["demo"].update_config = lambda *_a, **_k: None
    return a, a.app.test_client()


def _rows(a):
    return a.s_cfg["demo"]["learned"]["download"]["row_selectors"]


def test_replace_applies_in_place_and_persists():
    a, c = _boot_with_site()
    r = c.post("/api/sites/demo/learned/apply_repairs", json={
        "repairs": [{"old_selector": "a.old", "new_selector": "a.new",
                     "role": "row_selectors"}]})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] and body["count"] == 1 and not body["rejected"]
    assert _rows(a) == ["a.new", "a.keep"]            # replaced in place, order kept


def test_dry_run_previews_without_writing():
    a, c = _boot_with_site()
    r = c.post("/api/sites/demo/learned/apply_repairs", json={
        "repairs": [{"old_selector": "a.old", "new_selector": "a.new",
                     "role": "row_selectors"}],
        "dry_run": True})
    body = r.get_json()
    assert body["ok"] and body["dry_run"] and body["count"] == 1
    assert _rows(a) == ["a.old", "a.keep"]            # disk untouched


def test_removed_deletes_selector():
    a, c = _boot_with_site()
    r = c.post("/api/sites/demo/learned/apply_repairs", json={
        "repairs": [], "removed": ["a.keep"]})
    body = r.get_json()
    assert body["ok"] and len(body["removed"]) == 1
    assert _rows(a) == ["a.old"]


def test_missing_old_selector_rejected_not_applied():
    a, c = _boot_with_site()
    r = c.post("/api/sites/demo/learned/apply_repairs", json={
        "repairs": [{"old_selector": "a.nope", "new_selector": "a.new",
                     "role": "row_selectors"}]})
    body = r.get_json()
    assert body["ok"] and body["count"] == 0 and len(body["rejected"]) == 1
    assert _rows(a) == ["a.old", "a.keep"]            # no blind append, no change


def test_invalid_role_rejected():
    a, c = _boot_with_site()
    r = c.post("/api/sites/demo/learned/apply_repairs", json={
        "repairs": [{"old_selector": "a.old", "new_selector": "a.new",
                     "role": "bogus"}]})
    body = r.get_json()
    assert body["count"] == 0 and len(body["rejected"]) == 1
    assert _rows(a) == ["a.old", "a.keep"]


def test_unknown_site_is_404():
    _a, c = _boot_with_site()
    r = c.post("/api/sites/zzz/learned/apply_repairs", json={"repairs": []})
    assert r.status_code == 404
