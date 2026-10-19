"""dscs F01 + F04 + F19 -- settings writes must not lie or wipe; validate must gate.

F01: POST /api/global_config saved through app._save_app_config, whose read step caught
every error and fell back to merged={} -- so a corrupt or non-object app_config.json was
REPLACED by app.py's own keys, erasing foreign keys such as api_auth_token_secret (stored
by api_tokens through global_config.set_config). A failed write was only logged, so the
route still answered 200. set_config already refuses an unreadable store and writes
nothing; this writer now does the same, and a refused or failed save is a 500.

F04 (validate half): /api/settings/site/<sid>/validate accepted a value outside
site_editor._FIELD_ENUMS, coerced True -> 1 for an integer field (PUT /api/sites/<sid>
rejects it), and answered 200 ok:true for a site the sites store does not hold.

F19: /cockpit/settings rendered plain <form action='/api/settings/runtime' method='post'>
with no script, so Save navigated the browser to raw JSON (and a cookie session could not
send X-CSRF-Token from a plain form). The page now submits through fetch and stays put.
"""
import json
import os
import pathlib
import sys
import tempfile
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"  # O906.1 gate marker (precollect guard)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SECRET = "dscs-f01-foreign-secret"


# ── F01 ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app_mod():
    os.environ.setdefault("BD_HOME", tempfile.mkdtemp(prefix="dscs-f01-home-"))
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    import bulk_downloader.app as A
    A.app.config["TESTING"] = True
    A.app.config["PROPAGATE_EXCEPTIONS"] = False  # an unhandled error must surface as its 500
    return A


@pytest.fixture
def gc(app_mod, monkeypatch, tmp_path):
    """POST /api/global_config against an app_config.json in a private cwd."""
    A = app_mod
    monkeypatch.chdir(tmp_path)  # APP_CFG_FILE is the relative Path("app_config.json")
    cfg_before = dict(A._app_cfg)
    seen_before = dict(A._APP_CFG_DISK_SEEN)
    c = A.app.test_client()
    tok = (c.get("/api/csrf").get_json(silent=True) or {}).get("csrf_token", "")
    hdr = {"X-CSRFToken": tok, "X-CSRF-Token": tok}

    def post(body):
        return c.post("/api/global_config", headers=hdr, json=body)

    yield post, tmp_path / "app_config.json"
    A._app_cfg.clear()
    A._app_cfg.update(cfg_before)
    A._APP_CFG_DISK_SEEN.clear()
    A._APP_CFG_DISK_SEEN.update(seen_before)


@pytest.mark.parametrize("raw", [
    '{"api_auth_token_secret": "%s", "ui_logging_level": "basic"' % SECRET,  # truncated JSON
    '["api_auth_token_secret", "%s"]' % SECRET,                              # not an object
], ids=["corrupt", "non-object"])
def test_f01_unreadable_store_is_kept_and_save_is_not_200(gc, raw):
    post, cfg = gc
    cfg.write_text(raw, encoding="utf-8")
    r = post({"ui_logging_level": "basic"})
    after = cfg.read_text(encoding="utf-8")
    assert after == raw, (
        "F01-FOREIGN-KEYS-WIPED: an unreadable app_config.json was replaced "
        "(status %s, file now %r)" % (r.status_code, after[:160]))
    assert r.status_code >= 500 and r.get_json()["ok"] is False, (
        "F01-READ-FAIL-200: the refused save answered %s %r" % (r.status_code, r.get_json()))


def test_f01_write_failure_is_not_200_and_file_is_intact(gc, monkeypatch):
    post, cfg = gc
    raw = json.dumps({"api_auth_token_secret": SECRET, "ui_logging_level": "basic"})
    cfg.write_text(raw, encoding="utf-8")
    real_replace = pathlib.Path.replace

    def failing_replace(self, target):
        if Path(target).name == "app_config.json":
            raise OSError(28, "No space left on device (dscs-f01 fixture)")
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", failing_replace)
    r = post({"ui_logging_level": "basic"})
    assert r.status_code >= 500 and r.get_json()["ok"] is False, (
        "F01-WRITE-FAIL-200: a save whose write failed answered %s %r"
        % (r.status_code, r.get_json()))
    assert cfg.read_text(encoding="utf-8") == raw


def test_f01_neg_normal_save_keeps_foreign_keys_and_is_200(gc):
    post, cfg = gc
    cfg.write_text(json.dumps({"api_auth_token_secret": SECRET, "ui_logging_level": "basic"}),
                   encoding="utf-8")
    r = post({"ui_logging_level": "basic"})
    assert r.status_code == 200, (r.status_code, r.get_json())
    disk = json.loads(cfg.read_text(encoding="utf-8"))
    assert disk.get("api_auth_token_secret") == SECRET, disk
    assert disk.get("ui_logging_level") == "basic", disk


def test_f01_absent_store_still_saves(gc):
    post, cfg = gc
    assert not cfg.exists()
    r = post({"ui_logging_level": "basic"})
    assert r.status_code == 200, (r.status_code, r.get_json())
    assert json.loads(cfg.read_text(encoding="utf-8")).get("ui_logging_level") == "basic"


# ── F04 / F19 (blueprint on a bare Flask app, like the settings-center suites) ──

@pytest.fixture
def sc(monkeypatch, tmp_path):
    from flask import Flask
    from bulk_downloader import app_settings_center as SC
    store = tmp_path / "sites_config.json"
    store.write_text(json.dumps({"sites": {"demo": {"name": "demo", "backend": "teach"}}}),
                     encoding="utf-8")
    monkeypatch.setenv("BD_SITES_CONFIG_PATH", str(store))
    app = Flask(__name__)
    SC.register_routes(app)
    return app.test_client(), store


def _validate(client, sid, updates):
    return client.post(f"/api/settings/site/{sid}/validate", json={"updates": updates})


def test_f04_enum_value_outside_choices_is_rejected(sc):
    c, _ = sc
    j = _validate(c, "demo", {"backend": "TOTALLY_BOGUS_ENUM_VALUE_XYZ"}).get_json()
    assert "backend" in j["rejected"] and "backend" not in j["accepted"], (
        "F04-ENUM-SKIPPED: backend=TOTALLY_BOGUS_ENUM_VALUE_XYZ accepted %r" % j)
    ok = _validate(c, "demo", {"backend": "jd"}).get_json()
    assert ok["accepted"] == {"backend": "jd"}, ok  # control: a listed choice passes


def test_f04_bool_for_integer_field_is_rejected(sc):
    c, _ = sc
    j = _validate(c, "demo", {"crawler_max_pages": True}).get_json()
    assert "crawler_max_pages" in j["rejected"], (
        "F04-BOOL-COERCED: crawler_max_pages=True accepted as %r"
        % j["accepted"].get("crawler_max_pages"))
    ok = _validate(c, "demo", {"crawler_max_pages": "5"}).get_json()
    # control: "5" still parses (compared as items, not a dict literal: it is no count pin)
    assert list(ok["accepted"].items()) == [("crawler_max_pages", 5)], ok


def test_f04_unknown_site_is_404(sc):
    c, _ = sc
    r = _validate(c, "dscs-no-such-site", {"crawler_max_pages": 5})
    assert r.status_code == 404 and r.get_json()["ok"] is False, (
        "F04-UNKNOWN-SID-200: validate for a site the store lacks answered %s %r"
        % (r.status_code, r.get_json()))
    assert _validate(c, "demo", {"crawler_max_pages": 5}).status_code == 200  # control


def test_f04_validate_still_writes_nothing(sc):
    c, store = sc
    before = store.read_text(encoding="utf-8")
    _validate(c, "demo", {"backend": "jd", "crawler_max_pages": True})
    _validate(c, "dscs-no-such-site", {"backend": "jd"})
    assert store.read_text(encoding="utf-8") == before


def test_f19_runtime_forms_submit_via_fetch_and_stay_on_page(sc):
    c, _ = sc
    page = c.get("/cockpit/settings").get_data(as_text=True)
    # control: both runtime forms still render with their no-JS action/method
    assert page.count("<form action='/api/settings/runtime' method='post'") == 2, page[:400]
    assert "<option value='true'" in page and "<option value='false'" in page
    assert "<script" in page, (
        "F19-RAW-JSON-NAV: /cockpit/settings has no script; Save navigates to raw JSON")
    for needle in ("preventDefault", "fetch(", "X-CSRF-Token", "/api/csrf"):
        assert needle in page, f"F19-RAW-JSON-NAV: script lacks {needle!r}"


# ── r2 S1: every _save_app_config caller F01 can now fail rolls back and audits ──

S1_PATH = "/dscs-s1/widened/root"


@pytest.fixture
def failing_save(app_mod, monkeypatch, tmp_path):
    """POSTs against a private app_config.json; audit rows captured; arm() makes the
    write fail (Path.replace raises, as in the F01 write test) and disarm() undoes it."""
    A = app_mod
    from bulk_downloader import audit as AU
    from bulk_downloader import app_kernel as K
    monkeypatch.chdir(tmp_path)
    cfg_before = dict(A._app_cfg)
    seen_before = dict(A._APP_CFG_DISK_SEEN)
    calls = []
    monkeypatch.setattr(AU, "audit_log", lambda **kw: calls.append(kw))
    cfg = tmp_path / "app_config.json"
    cfg.write_text(json.dumps({"api_auth_token_secret": SECRET}), encoding="utf-8")
    c = A.app.test_client()
    tok = (c.get("/api/csrf").get_json(silent=True) or {}).get("csrf_token", "")
    hdr = {"X-CSRFToken": tok, "X-CSRF-Token": tok}
    real_replace = pathlib.Path.replace

    def failing_replace(self, target):
        if Path(target).name == "app_config.json":
            raise OSError(28, "No space left on device (dscs-s1 fixture)")
        return real_replace(self, target)

    class H:
        live = K._app_cfg
        audit = calls
        disk = cfg

        @staticmethod
        def post(url, body):
            return c.post(url, headers=hdr, json=body)

        @staticmethod
        def arm():
            monkeypatch.setattr(pathlib.Path, "replace", failing_replace)

        @staticmethod
        def disarm():
            monkeypatch.setattr(pathlib.Path, "replace", real_replace)

    yield H
    A._app_cfg.clear()
    A._app_cfg.update(cfg_before)
    A._APP_CFG_DISK_SEEN.clear()
    A._APP_CFG_DISK_SEEN.update(seen_before)


def _add(h):
    return h.post("/api/captures/allowlist_add", {"confirm": True, "path": S1_PATH})


def test_s1_failed_allowlist_save_is_not_live_and_is_audited(failing_save):
    h = failing_save
    h.arm()
    r = _add(h)
    assert r.status_code >= 500 and r.get_json()["ok"] is False, (r.status_code, r.get_json())
    assert S1_PATH not in (h.live.get("path_allowlist") or []), (
        "S1-LIVE-WIDENED-AFTER-500: the refused widening stayed live in _app_cfg")
    assert S1_PATH not in (json.loads(h.disk.read_text()).get("path_allowlist") or [])
    failed = [a for a in h.audit if a.get("target") == "path_allowlist"]
    assert [a["action"] for a in failed] == ["add_failed"], (
        "S1-FAILED-WIDENING-UNAUDITED: audit rows %r" % h.audit)
    assert failed[0]["after"]["outcome"] == "failed"
    assert S1_PATH in failed[0]["after"]["attempted"]
    r2 = _add(h)  # the write still fails: a retry must not take the idempotent branch
    assert r2.status_code >= 500, (
        "S1-RETRY-IDEMPOTENT-200: retry after a failed widening answered %s %r"
        % (r2.status_code, r2.get_json()))
    h.disarm()
    r3 = _add(h)  # once the write works, the retry is a real save: on disk and audited
    assert r3.status_code == 200 and S1_PATH in r3.get_json()["allowlist"], r3.get_json()
    assert S1_PATH in json.loads(h.disk.read_text())["path_allowlist"]
    assert [a["action"] for a in h.audit] == ["add_failed", "add_failed", "add"], h.audit


def test_s1_neg_writable_allowlist_save_is_200_and_audited(failing_save):
    h = failing_save
    r = _add(h)  # control: a writable save widens on disk and is audited once
    assert r.status_code == 200 and S1_PATH in r.get_json()["allowlist"], r.get_json()
    assert S1_PATH in json.loads(h.disk.read_text())["path_allowlist"]
    assert json.loads(h.disk.read_text())["api_auth_token_secret"] == SECRET
    assert [a["action"] for a in h.audit] == ["add"], h.audit


def test_s1_failed_global_config_save_rolls_back_and_is_audited(failing_save):
    h = failing_save
    prior = h.live.get("path_allowlist")
    h.arm()
    r = h.post("/api/global_config", {"path_allowlist": [S1_PATH]})
    assert r.status_code >= 500 and r.get_json()["ok"] is False, (
        "S1-GC-FAIL-200: a global_config save whose write failed answered %s %r"
        % (r.status_code, r.get_json()))
    assert h.live.get("path_allowlist") == prior, (
        "S1-GC-LIVE-AFTER-500: path_allowlist %r stayed live" % h.live.get("path_allowlist"))
    failed = [a for a in h.audit if a.get("action") == "save_failed"]
    assert len(failed) == 1 and failed[0]["target"] == "global_config", (
        "S1-GC-FAILED-SAVE-UNAUDITED: audit rows %r" % h.audit)
    assert failed[0]["after"]["keys"] == ["path_allowlist"]
    assert S1_PATH not in json.dumps(failed[0]), "audit row must carry key names, not values"


def test_s1_neg_writable_global_config_save_is_200_unflagged(failing_save):
    h = failing_save
    r = h.post("/api/global_config", {"path_allowlist": [S1_PATH]})
    assert r.status_code == 200, (r.status_code, r.get_json())
    assert S1_PATH in json.loads(h.disk.read_text())["path_allowlist"]
    assert not [a for a in h.audit if a.get("action") == "save_failed"], h.audit
