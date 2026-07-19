"""HIGH — PUT numeric-range backstop (closes SETTINGS_CENTER_PUT_RANGE_BACKSTOP_FINDING).

Proves the audited write path ``PUT /api/sites/<sid>`` now rejects out-of-range numeric
values (previously 200 + persist), while valid PUTs, blank/preserve-on-blank secrets, and
non-numeric fields are unaffected. Also unit-tests the shared site_editor helper that both
the dry-run gate's range table and this backstop source from.

No pytest fixtures — stages a temp sites_config under BD_HOME and loads it. The app reads
BD_HOME at import (see conftest.py), so importing inside each test keeps it self-contained.
"""
import json
import os

import pytest

# App-booting test: re-read BD_HOME at import so SITES_FILE and the SQLite DB path
# bind to the autouse fixture's tmp_path (see conftest isolated_bd_home).
pytestmark = pytest.mark.bd_module_wipe


# ── unit: the shared helper ───────────────────────────────────────────────────
def test_helper_rejects_out_of_range():
    from bulk_downloader import site_editor as se
    errs = se.validate_numeric_updates({"max_concurrent": 9999})
    assert "max_concurrent" in errs
    assert "between 1 and 32" in errs["max_concurrent"]


def test_helper_accepts_in_range_and_skips_blank():
    from bulk_downloader import site_editor as se
    assert se.validate_numeric_updates({"max_concurrent": 8, "wait": 5}) == {}
    assert se.validate_numeric_updates({"max_concurrent": ""}) == {}      # blank skipped
    assert se.validate_numeric_updates({"max_concurrent": None}) == {}    # None skipped


def test_helper_ignores_non_numeric_fields():
    from bulk_downloader import site_editor as se
    assert se.validate_numeric_updates({"name": "X", "username": "u", "headless": True}) == {}


def test_helper_rejects_non_numeric_value_and_wrong_types():
    from bulk_downloader import site_editor as se
    assert "wait" in se.validate_numeric_updates({"wait": "abc"})
    assert "max_concurrent" in se.validate_numeric_updates({"max_concurrent": [1]})
    assert "max_concurrent" in se.validate_numeric_updates({"max_concurrent": True})


def test_helper_numeric_string_is_range_checked():
    from bulk_downloader import site_editor as se
    # numeric strings parse (the string-preflight may coerce e.g. min_resolution to str)
    assert se.validate_numeric_updates({"min_resolution": "99999"})  # out of (0, 8640)
    assert se.validate_numeric_updates({"min_resolution": "1080"}) == {}


# ── helpers for full-app tests ─────────────────────────────────────────────────
def _boot_with_site():
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"   # read at (re)import under the marker
    from bulk_downloader import app as a
    from bulk_downloader import db
    db.db_init()                               # ensure history/queue tables in tmp_path DB
    a.SITES_FILE.write_text(json.dumps({"demo": {
        "name": "Demo", "max_concurrent": 4, "wait": 5, "password": "ORIG_PW"}}),
        encoding="utf-8")
    a._load_sites_config()
    # update_config only swaps the cfg and bounces the scheduler (a live thread). That
    # subsystem is orthogonal to the validation/persist path under test, and the real
    # call was already exercised end-to-end manually. Stub it to keep the test fast and
    # free of a background scheduler thread.
    a.runners["demo"].update_config = lambda *_a, **_k: None
    return a, a.app.test_client()


def _disk(a):
    return json.loads(a.SITES_FILE.read_text(encoding="utf-8"))["demo"]


# ── audited PUT behavior ────────────────────────────────────────────────────────
def test_direct_put_out_of_range_int_rejected_and_not_persisted():
    a, c = _boot_with_site()
    r = c.put("/api/sites/demo", json={"max_concurrent": 9999})
    assert r.status_code == 400
    assert _disk(a)["max_concurrent"] == 4          # unchanged on disk


def test_direct_put_out_of_range_number_rejected_and_not_persisted():
    a, c = _boot_with_site()
    r = c.put("/api/sites/demo", json={"wait": 200})  # NUMERIC_RANGES wait = (0, 120)
    assert r.status_code == 400
    assert _disk(a)["wait"] == 5


def test_valid_in_range_put_succeeds_and_persists():
    a, c = _boot_with_site()
    r = c.put("/api/sites/demo", json={"max_concurrent": 8, "wait": 10})
    assert r.status_code == 200
    d = _disk(a)
    assert d["max_concurrent"] == 8 and d["wait"] == 10


def test_valid_put_preserves_blank_secret():
    a, c = _boot_with_site()
    r = c.put("/api/sites/demo", json={"max_concurrent": 8, "password": ""})
    assert r.status_code == 200
    assert _disk(a)["password"] == "ORIG_PW"        # preserve-on-blank intact


def test_non_numeric_field_put_unaffected():
    a, c = _boot_with_site()
    r = c.put("/api/sites/demo", json={"name": "Renamed"})
    assert r.status_code == 200
    assert _disk(a)["name"] == "Renamed"
