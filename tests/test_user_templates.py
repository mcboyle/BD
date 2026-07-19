"""Tests for user_templates module (v3.43.9).

Coverage:
  - Storage round-trip: save -> _load -> get returns the same template
  - Validation rejects malformed templates (missing fields, bad regex,
    mismatched url_attribute list length)
  - ID generation produces collision-free IDs across rapid saves
  - delete returns False on unknown id, True on existing
  - export/import round-trip preserves all fields
  - import merge mode skips on id collision
  - import replace mode wipes existing
  - import validation reports errors per-template without aborting whole batch
  - suggest_for_url() matches saved patterns
  - templates.suggest_for_url() merges built-in + user with user first
  - templates.list_templates() includes source field
  - The teach_save_template endpoint works without CSRF

Disk handling: each test uses tempfile + manual rebind of USER_TEMPLATES_FILE.
"""
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _with_temp_user_templates():
    """Bind user_templates.USER_TEMPLATES_FILE to a tempdir for one test."""
    from bulk_downloader import user_templates as ut
    with tempfile.TemporaryDirectory() as td:
        orig = ut.USER_TEMPLATES_FILE
        ut.USER_TEMPLATES_FILE = Path(td) / "user_templates.json"
        try:
            yield ut
        finally:
            ut.USER_TEMPLATES_FILE = orig


def _good_learned():
    """A minimal valid `learned` block for tests."""
    return {"download": {
        "row_selectors": ["a.btn[href]"],
        "url_attribute": "href",
    }}


# ── Storage round-trip ─────────────────────────────────────────────

def test_save_and_get_template():
    with _with_temp_user_templates() as ut:
        ok, t = ut.save_user_template(
            name="My Test Template",
            description="A test",
            patterns=[r"example\.com"],
            learned=_good_learned(),
        )
        assert ok is True, t
        assert t["id"].startswith("user_")
        assert t["name"] == "My Test Template"
        assert "created_ts" in t
        # round-trip via _load
        got = ut.get_user_template(t["id"])
        assert got is not None
        assert got["name"] == "My Test Template"


def test_save_multiple_templates_unique_ids():
    """Two saves with the same name produce different ids."""
    import time
    with _with_temp_user_templates() as ut:
        ok1, t1 = ut.save_user_template("Same", "d", [], _good_learned())
        time.sleep(1.01)  # ensure timestamp suffix differs
        ok2, t2 = ut.save_user_template("Same", "d", [], _good_learned())
        assert ok1 and ok2
        assert t1["id"] != t2["id"]
        assert len(ut.list_user_templates()) == 2


def test_update_existing_template():
    """Saving with an existing tid replaces in place."""
    with _with_temp_user_templates() as ut:
        ok, t = ut.save_user_template("Original", "d", [], _good_learned())
        original_id = t["id"]
        original_created = t["created_ts"]
        ok, t2 = ut.save_user_template(
            "Updated", "new description", [], _good_learned(), tid=original_id,
        )
        assert ok
        assert t2["id"] == original_id
        assert t2["name"] == "Updated"
        assert t2["created_ts"] == original_created  # preserved
        assert "updated_ts" in t2
        # Still only one template on disk
        assert len(ut.list_user_templates()) == 1


# ── Validation ─────────────────────────────────────────────────────

def test_save_rejects_empty_name():
    with _with_temp_user_templates() as ut:
        ok, err = ut.save_user_template("", "d", [], _good_learned())
        assert ok is False
        assert "name" in err.lower()


def test_save_rejects_empty_description():
    with _with_temp_user_templates() as ut:
        ok, err = ut.save_user_template("Name", "", [], _good_learned())
        assert ok is False
        assert "description" in err.lower()


def test_save_rejects_bad_regex():
    with _with_temp_user_templates() as ut:
        ok, err = ut.save_user_template(
            "X", "d", ["[unclosed bracket"], _good_learned(),
        )
        assert ok is False
        assert "regex" in err.lower() or "bad" in err.lower()


def test_save_rejects_empty_row_selectors():
    with _with_temp_user_templates() as ut:
        bad_learned = {"download": {"row_selectors": []}}
        ok, err = ut.save_user_template("X", "d", [], bad_learned)
        assert ok is False
        assert "row_selectors" in err


def test_save_rejects_mismatched_url_attribute_list():
    """url_attribute as list must be parallel to row_selectors."""
    with _with_temp_user_templates() as ut:
        bad = {"download": {
            "row_selectors": ["a", "b", "c"],
            "url_attribute": ["href", "src"],  # 2 entries vs 3 selectors
        }}
        ok, err = ut.save_user_template("X", "d", [], bad)
        assert ok is False
        assert "parallel" in err.lower() or "url_attribute" in err


def test_save_accepts_empty_patterns():
    """Patterns list may be empty — template just won't auto-suggest."""
    with _with_temp_user_templates() as ut:
        ok, t = ut.save_user_template("X", "d", [], _good_learned())
        assert ok
        assert t["patterns"] == []


# ── Delete ─────────────────────────────────────────────────────────

def test_delete_existing_template():
    with _with_temp_user_templates() as ut:
        ok, t = ut.save_user_template("X", "d", [], _good_learned())
        assert ut.delete_user_template(t["id"]) is True
        assert ut.get_user_template(t["id"]) is None


def test_delete_unknown_id_returns_false():
    with _with_temp_user_templates() as ut:
        assert ut.delete_user_template("user_does_not_exist_123") is False


# ── Export / Import ────────────────────────────────────────────────

def test_export_returns_full_payload():
    with _with_temp_user_templates() as ut:
        ut.save_user_template("A", "d", [r"a\.com"], _good_learned())
        ut.save_user_template("B", "d", [r"b\.com"], _good_learned())
        payload = ut.export_user_templates()
        assert payload["version"] == 1
        assert len(payload["templates"]) == 2
        names = [t["name"] for t in payload["templates"]]
        assert "A" in names and "B" in names


def test_import_merge_appends():
    with _with_temp_user_templates() as ut:
        ut.save_user_template("Existing", "d", [], _good_learned())
        # Build an import payload with one new template (different id)
        incoming = {
            "version": 1,
            "templates": [{
                "id": "user_imported_xyz",
                "name": "Imported",
                "description": "from elsewhere",
                "patterns": [r"imp\.com"],
                "learned": _good_learned(),
            }],
        }
        added, skipped, errors = ut.import_user_templates(incoming, merge=True)
        assert added == 1
        assert skipped == 0
        assert errors == []
        assert len(ut.list_user_templates()) == 2


def test_import_merge_skips_id_collision():
    with _with_temp_user_templates() as ut:
        ok, t = ut.save_user_template("Original", "d", [], _good_learned())
        incoming = {
            "version": 1,
            "templates": [{
                "id": t["id"],  # same id as existing
                "name": "Would-be Duplicate",
                "description": "d",
                "patterns": [],
                "learned": _good_learned(),
            }],
        }
        added, skipped, errors = ut.import_user_templates(incoming, merge=True)
        assert added == 0
        assert skipped == 1
        # Existing template name unchanged
        assert ut.get_user_template(t["id"])["name"] == "Original"


def test_import_replace_wipes_existing():
    with _with_temp_user_templates() as ut:
        ut.save_user_template("Old", "d", [], _good_learned())
        ut.save_user_template("Also Old", "d", [], _good_learned())
        incoming = {
            "version": 1,
            "templates": [{
                "id": "user_only_new",
                "name": "Only New",
                "description": "d",
                "patterns": [],
                "learned": _good_learned(),
            }],
        }
        added, skipped, errors = ut.import_user_templates(incoming, merge=False)
        assert added == 1
        # The two old templates should be gone
        all_now = ut.list_user_templates()
        assert len(all_now) == 1
        assert all_now[0]["name"] == "Only New"


def test_import_reports_per_template_errors():
    """A batch with mixed valid + invalid imports the valid, errors on the rest."""
    with _with_temp_user_templates() as ut:
        incoming = {"version": 1, "templates": [
            {"id": "user_good", "name": "Good", "description": "d",
             "patterns": [], "learned": _good_learned()},
            {"id": "user_bad", "name": "", "description": "d",  # empty name
             "patterns": [], "learned": _good_learned()},
            {"id": "user_also_good", "name": "Good2", "description": "d",
             "patterns": [], "learned": _good_learned()},
        ]}
        added, skipped, errors = ut.import_user_templates(incoming, merge=True)
        assert added == 2
        assert len(errors) == 1
        assert "name" in errors[0].lower()


# ── suggest_for_url integration ────────────────────────────────────

def test_user_suggest_matches_pattern():
    with _with_temp_user_templates() as ut:
        ok, t = ut.save_user_template(
            "Mine", "d", [r"mysite\.com"], _good_learned(),
        )
        matches = ut.suggest_for_url("https://www.mysite.com/page")
        assert t["id"] in matches


def test_user_suggest_empty_when_no_patterns():
    with _with_temp_user_templates() as ut:
        ut.save_user_template("Mine", "d", [], _good_learned())
        # Template saved but no patterns -> no auto-suggest
        assert ut.suggest_for_url("https://anywhere.com") == []


def test_templates_module_merges_user_and_builtin():
    """templates.list_templates() includes user-saved templates with source='user'."""
    from bulk_downloader import templates as tpls
    with _with_temp_user_templates() as ut:
        ok, t = ut.save_user_template("Mine", "d", [], _good_learned())
        items = tpls.list_templates()
        # All built-ins + one user
        user_items = [i for i in items if i.get("source") == "user"]
        builtin_items = [i for i in items if i.get("source") == "builtin"]
        assert len(user_items) == 1
        assert user_items[0]["name"] == "Mine"
        assert len(builtin_items) >= 50  # we shipped 52 in v3.43.8


def test_templates_module_get_finds_user_template():
    from bulk_downloader import templates as tpls
    with _with_temp_user_templates() as ut:
        ok, t = ut.save_user_template("Findme", "d", [], _good_learned())
        got = tpls.get(t["id"])
        assert got is not None
        assert got["name"] == "Findme"


def test_user_template_wins_in_suggest():
    """When both a user template and a built-in match the URL, the user
    template should be suggested first."""
    from bulk_downloader import templates as tpls
    with _with_temp_user_templates() as ut:
        # Save a user template matching wowgirls.com (where wowgirls_network
        # built-in also matches)
        ok, t = ut.save_user_template(
            "My WowGirls override", "d", [r"wowgirls\.com"], _good_learned(),
        )
        matches = tpls.suggest_for_url("https://wowgirls.com/abc")
        # User template should come first
        assert t["id"] in matches
        assert matches.index(t["id"]) < matches.index("wowgirls_network")


# ── teach_save_template endpoint ───────────────────────────────────

def test_teach_save_template_endpoint():
    """Endpoint accepts a save request.

    Note on auth: in production the teach endpoints are hit from the
    takeover browser (different origin) which doesn't carry the main
    app's session cookie, so CSRF check skips. In tests the test_client
    DOES carry the cookie after pair_redeem, so we have to send the
    CSRF token here. This proves CSRF is properly wired; it doesn't
    test the takeover-browser flow specifically (which would need an
    integration test launching Playwright)."""
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    with _with_temp_user_templates() as ut:
        db_init()
        c = A.app.test_client()
        r = c.get('/api/pair'); token = r.get_json()['token']
        r = c.post('/api/pair/redeem', json={'token': token})
        csrf = r.get_json()['csrf_token']
        H = {'X-CSRF-Token': csrf}
        r = c.post('/api/sites', json={'name': 'ts'}, headers=H)
        sid = r.get_json()['id']
        r = c.post(f'/api/sites/{sid}/teach_save_template', json={
            'name': 'My saved template',
            'description': 'Saved from teach',
            'patterns': [r'example\.com'],
            'learned': _good_learned(),
        }, headers=H)
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d['ok'] is True
        assert d['template']['name'] == 'My saved template'
        assert d['template']['id'].startswith('user_')


def test_teach_save_template_rejects_invalid():
    """The teach endpoint validates same as the main one."""
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    with _with_temp_user_templates() as ut:
        db_init()
        c = A.app.test_client()
        r = c.get('/api/pair'); token = r.get_json()['token']
        r = c.post('/api/pair/redeem', json={'token': token})
        csrf = r.get_json()['csrf_token']
        H = {'X-CSRF-Token': csrf}
        r = c.post('/api/sites', json={'name': 'ts'}, headers=H)
        sid = r.get_json()['id']
        r = c.post(f'/api/sites/{sid}/teach_save_template', json={
            'name': '',  # empty name
            'description': 'd',
            'patterns': [],
            'learned': _good_learned(),
        }, headers=H)
        assert r.status_code == 400
        d = r.get_json()
        assert d['ok'] is False
        assert 'name' in d['error'].lower()


def test_teach_save_template_skips_csrf_without_session():
    """When no session cookie is present (takeover-browser scenario),
    CSRF check is skipped per the v3.40 design. Verify this works for
    the new endpoint."""
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    with _with_temp_user_templates() as ut:
        db_init()
        # First create a site with one client
        c = A.app.test_client()
        r = c.get('/api/pair'); token = r.get_json()['token']
        r = c.post('/api/pair/redeem', json={'token': token})
        csrf = r.get_json()['csrf_token']
        H = {'X-CSRF-Token': csrf}
        r = c.post('/api/sites', json={'name': 'ts'}, headers=H)
        sid = r.get_json()['id']
        # Now use a FRESH client (no session cookie) to simulate the
        # takeover browser's cross-origin POST
        c2 = A.app.test_client()
        r = c2.post(f'/api/sites/{sid}/teach_save_template', json={
            'name': 'No-session save',
            'description': 'd',
            'patterns': [],
            'learned': _good_learned(),
        })
        # CSRF check should skip because no bd_session cookie present.
        # _check_token also skips because no auth token is configured.
        # So the request should reach the endpoint and succeed.
        assert r.status_code == 200, r.get_json()
        assert r.get_json()['ok'] is True
