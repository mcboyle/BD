"""data-writes L1-L4, L6 (bughunt data-writes FINDINGS, static leads reproduced on 444a05db).

L1 batch_ops.bulk_move created target_dir before its dry_run check, so the SPA "preview" made the
   typed directory on disk.
L2 list_user_templates served _load() without _strip_secrets, while export stripped: a store row
   written before F11 (or hand-copied) reached GET /api/user_templates in clear.
L3 import accepted a hand-written built-in id, which the create route refuses (dtr-fix F4).
L4 import skipped lint_learned, which save_user_template runs: row_selectors ["a"] was stored.
L6 _strip_secrets was key-based only: an allowlisted URL key (login_url) kept https://user:pw@host.
"""

from __future__ import annotations

import json

import pytest

from bulk_downloader import batch_ops, user_templates

BD_GATE_SCOPE = "module"

_SAFE = {"download": {"row_selectors": ["a.dl"], "url_attribute": "href"}}
_UNSAFE = {"download": {"row_selectors": ["a"], "url_attribute": "href"}}


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "user_templates.json"
    monkeypatch.setattr(user_templates, "USER_TEMPLATES_FILE", path)
    assert user_templates.list_user_templates() == [], "the isolated store is not empty"
    return path


def _tpl(tid, learned=_SAFE, **extra):
    t = {"id": tid, "name": tid, "description": "dw fixture", "patterns": [r"dw\.example"],
         "learned": learned, "source": "user_teach"}
    t.update(extra)
    return t


def _write_raw(path, templates):
    path.write_text(json.dumps({"version": 1, "templates": templates}), encoding="utf-8")


# -- L1 ---------------------------------------------------------------------------------------

def test_l1_bulk_move_preview_creates_no_directory(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(batch_ops, "_matching_rows", lambda _f: [{"id": 1, "filename": str(src)}])
    target = tmp_path / "typed" / "new_dir"

    out = batch_ops.bulk_move({}, target_dir=str(target), dry_run=True)

    assert not target.exists(), "DW-L1: bulk_move dry_run=True created target_dir %s" % target
    assert out["ok"] and out["dry_run"] and out["candidates_matched"] == 1
    assert out["sample"][0]["to"] == str(target / "src.mp4")
    assert src.exists()


def test_l1_control_real_move_still_creates_the_directory(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from bulk_downloader import db

    src = tmp_path / "src.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(batch_ops, "_matching_rows", lambda _f: [{"id": 1, "filename": str(src)}])

    @contextmanager
    def conn():
        class _Cx:
            def execute(self, *_a):
                return None
        yield _Cx()

    monkeypatch.setattr(db, "db_conn", conn)
    monkeypatch.setattr(db, "db_fts_snapshot", lambda _cx, _ids: [])
    monkeypatch.setattr(db, "db_fts_resync", lambda _cx, _old: None)
    target = tmp_path / "typed" / "new_dir"

    out = batch_ops.bulk_move({}, target_dir=str(target), dry_run=False)

    assert out["processed"] == 1 and out["errors"] == 0, out
    assert (target / "src.mp4").read_bytes() == b"x"


# -- L2 ---------------------------------------------------------------------------------------

def test_l2_list_strips_secrets_like_export(store):
    _write_raw(store, [_tpl("user_legacy", api_key="LEAKED-KEY-L2")])

    listed = json.dumps(user_templates.list_user_templates())
    exported = json.dumps(user_templates.export_user_templates())

    assert "LEAKED-KEY-L2" not in exported, "control: export no longer strips api_key"
    assert "LEAKED-KEY-L2" not in listed, "DW-L2: list_user_templates served api_key in clear"
    assert "user_legacy" in listed


# -- L3 ---------------------------------------------------------------------------------------

def _hand_written_builtin():
    from bulk_downloader.site_templates import SEEDED_BUILTIN_IDS, TEMPLATES
    ids = {t["id"] for t in TEMPLATES} - SEEDED_BUILTIN_IDS
    assert "video_js" in ids, "fixture: video_js is no longer a hand-written built-in"
    return "video_js"


@pytest.mark.parametrize("merge", [True, False])
def test_l3_import_refuses_a_hand_written_builtin_id(store, merge):
    bid = _hand_written_builtin()
    payload = {"templates": [_tpl(bid), _tpl("user_ok_l3")]}

    added, _skipped, errors = user_templates.import_user_templates(payload, merge=merge)

    ids = [t["id"] for t in user_templates.list_user_templates()]
    assert bid not in ids, "DW-L3: import stored built-in id %r (merge=%s)" % (bid, merge)
    assert "user_ok_l3" in ids and added == 1, (added, errors)
    assert any("built-in" in e for e in errors), errors


def test_l3_preview_reports_the_builtin_id_invalid(store):
    bid = _hand_written_builtin()
    pv = user_templates.preview_user_templates_import({"templates": [_tpl(bid)]})
    assert pv["counts"]["invalid"] == 1, pv


def test_l3_control_seeded_user_builtin_id_still_imports(store):
    from bulk_downloader.site_templates import SEEDED_BUILTIN_IDS
    if not SEEDED_BUILTIN_IDS:
        pytest.skip("no seeded built-ins")
    sid = sorted(SEEDED_BUILTIN_IDS)[0]
    added, _s, errors = user_templates.import_user_templates({"templates": [_tpl(sid)]})
    assert added == 1, errors


# -- L4 ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("merge", [True, False])
def test_l4_import_refuses_unsafe_selectors(store, merge):
    payload = {"templates": [_tpl("user_unsafe_l4", learned=_UNSAFE), _tpl("user_safe_l4")]}

    added, _skipped, errors = user_templates.import_user_templates(payload, merge=merge)

    ids = [t["id"] for t in user_templates.list_user_templates()]
    assert "user_unsafe_l4" not in ids, (
        "DW-L4: import stored row_selectors ['a'] that save_user_template refuses (merge=%s)" % merge)
    assert "user_safe_l4" in ids and added == 1, (added, errors)
    assert any("unsafe selectors" in e for e in errors), errors


def test_l4_control_save_refuses_the_same_selectors(store):
    ok, err = user_templates.save_user_template("x", "dw fixture", [r"dw\.example"], _UNSAFE)
    assert not ok and "unsafe selectors" in err


@pytest.mark.parametrize("merge", [True, False])
def test_l4_import_malformed_trigger_selectors_is_a_row_error(store, merge):
    bad = {"download": {"row_selectors": ["a.dl"], "trigger_selectors": 5}}
    payload = {"templates": [_tpl("user_bad_trig", learned=bad), _tpl("user_ok_trig")]}

    pv = user_templates.preview_user_templates_import(payload, merge=merge)
    added, _skipped, errors = user_templates.import_user_templates(payload, merge=merge)

    assert pv["counts"]["invalid"] == 1 and pv["counts"]["new"] == 1, pv["counts"]
    ids = [t["id"] for t in user_templates.list_user_templates()]
    assert ids == ["user_ok_trig"] and added == 1, (ids, added, errors)
    assert any("trigger_selectors must be a list" in e for e in errors), errors


# -- L6 ---------------------------------------------------------------------------------------

def test_l6_url_userinfo_is_stripped_on_export(store):
    cd = {"login_url": "https://alice:hunter2-L6@dw.example/login",
          "success_url": "https://dw.example/home"}
    _write_raw(store, [_tpl("user_l6_raw", config_defaults=cd)])

    exported = json.dumps(user_templates.export_user_templates())

    assert "hunter2-L6" not in exported, "DW-L6: export kept login_url userinfo"
    assert "alice" not in exported
    row = user_templates.export_user_templates()["templates"][0]["config_defaults"]
    assert row["login_url"] == "https://dw.example/login", row
    assert row["success_url"] == "https://dw.example/home", row


def test_l6_import_stores_no_userinfo(store):
    cd = {"login_url": "https://alice:hunter2-L6@dw.example/login"}
    added, _s, errors = user_templates.import_user_templates(
        {"templates": [_tpl("user_l6", config_defaults=cd)]})
    assert added == 1, errors
    assert "hunter2-L6" not in store.read_text(), "DW-L6: import stored login_url userinfo"


def test_l6_control_patterns_and_plain_urls_untouched():
    t = _tpl("user_l6c", patterns=[r"https?://[^/]+@cdn\.example/"],
             config_defaults={"login_url": "https://dw.example/a@b"})
    out = user_templates._strip_secrets(t)
    assert out["patterns"] == t["patterns"]
    assert out["config_defaults"]["login_url"] == "https://dw.example/a@b"
