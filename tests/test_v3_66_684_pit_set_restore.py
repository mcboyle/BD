"""v3.66.684 (F8 / PIT) — template-set restore-to-known-good-date.

selector_versions ships PER-TEMPLATE version history + revert
(record_template_version / list_versions / revert_selectors). The gap
this cut closes: a SET-WIDE point-in-time restore — roll EVERY template
back to the state it was in as of a given date. plan_set_restore /
restore_set_to_date compute that plan (pure, read-only, module posture:
never raise); user_templates.restore_template_set applies it through the
single writer (save_user_template), non-destructively (a template with no
version at/before the cutoff is skipped, never emptied or deleted).
"""
import json
import os

import pytest

from bulk_downloader import selector_versions as sv


# A: #x@100, #y@200, #z@300   B: #p@250, #q@350
def _hist():
    def _v(ts, sel):
        return {"version": f"{ts}-{sel}", "ts": ts, "source": "save",
                "note": "", "selectors": {"download": {"row_selectors": [sel]}}}
    return {
        "A": [_v(100, "#x"), _v(200, "#y"), _v(300, "#z")],
        "B": [_v(250, "#p"), _v(350, "#q")],
    }


def _write_hist(base, doc):
    with open(os.path.join(str(base), "selector_history.json"), "w") as fh:
        json.dump(doc, fh)


def _sel(row):
    return {"download": {"row_selectors": [row]}}


# ── plan_set_restore: the pure by-date selection ────────────────────

def test_plan_picks_latest_at_or_before_cutoff(tmp_path):
    _write_hist(tmp_path, _hist())
    plan = sv.plan_set_restore(250, base_dir=tmp_path)
    got = {r["template_id"]: r["selectors"] for r in plan["restore"]}
    assert got["A"] == _sel("#y")   # A: latest <=250 is ts=200
    assert got["B"] == _sel("#p")   # B: latest <=250 is ts=250 (inclusive)
    assert plan["skipped"] == []
    assert plan["as_of"] == 250


def test_plan_skips_templates_with_no_version_before_cutoff(tmp_path):
    _write_hist(tmp_path, _hist())
    plan = sv.plan_set_restore(150, base_dir=tmp_path)
    got = {r["template_id"]: r["selectors"] for r in plan["restore"]}
    assert got == {"A": _sel("#x")}                 # A: ts=100
    assert [s["template_id"] for s in plan["skipped"]] == ["B"]  # B earliest 250


def test_plan_all_skipped_before_any_history(tmp_path):
    _write_hist(tmp_path, _hist())
    plan = sv.plan_set_restore(50, base_dir=tmp_path)
    assert plan["restore"] == []
    assert sorted(s["template_id"] for s in plan["skipped"]) == ["A", "B"]


def test_plan_future_cutoff_picks_newest(tmp_path):
    _write_hist(tmp_path, _hist())
    plan = sv.plan_set_restore(999, base_dir=tmp_path)
    got = {r["template_id"]: r["selectors"] for r in plan["restore"]}
    assert got == {"A": _sel("#z"), "B": _sel("#q")}


def test_plan_restore_is_sorted_and_well_formed(tmp_path):
    _write_hist(tmp_path, _hist())
    plan = sv.plan_set_restore(999, base_dir=tmp_path)
    assert [r["template_id"] for r in plan["restore"]] == ["A", "B"]  # sorted
    for r in plan["restore"]:
        assert set(r.keys()) >= {"template_id", "version", "ts", "selectors"}


def test_restore_set_to_date_returns_selector_map(tmp_path):
    _write_hist(tmp_path, _hist())
    m = sv.restore_set_to_date(250, base_dir=tmp_path)
    assert m == {"A": _sel("#y"), "B": _sel("#p")}


def test_plan_is_read_only(tmp_path):
    _write_hist(tmp_path, _hist())
    p = os.path.join(str(tmp_path), "selector_history.json")
    before = open(p).read()
    sv.plan_set_restore(250, base_dir=tmp_path)
    sv.restore_set_to_date(200, base_dir=tmp_path)
    assert open(p).read() == before          # nothing written


def test_plan_empty_history_never_raises(tmp_path):
    plan = sv.plan_set_restore(250, base_dir=tmp_path)   # no history file
    assert plan == {"as_of": 250, "restore": [], "skipped": []}


# ── restore_template_set: the single-writer apply orchestrator ──────

@pytest.fixture
def seeded(tmp_path, monkeypatch):
    from bulk_downloader import user_templates as ut
    monkeypatch.chdir(tmp_path)
    # current state: A -> #z, B -> #q (matches newest history)
    ut.save_user_template("A site", "d", ["asite"], _sel("#z"), tid="A")
    ut.save_user_template("B site", "d", ["bsite"], _sel("#q"), tid="B")
    _write_hist(tmp_path, _hist())           # controlled timestamps
    return tmp_path, ut


def test_restore_template_set_dry_run_writes_nothing(seeded):
    _tmp, ut = seeded
    res = ut.restore_template_set(250, dry_run=True)
    assert res["dry_run"] is True
    # unchanged on disk
    assert ut.get_user_template("A")["learned"] == _sel("#z")
    assert ut.get_user_template("B")["learned"] == _sel("#q")
    assert sorted(res["restored"]) == ["A", "B"]   # plan says both


def test_restore_template_set_applies_via_single_writer(seeded):
    _tmp, ut = seeded
    res = ut.restore_template_set(250)
    assert res["dry_run"] is False
    assert ut.get_user_template("A")["learned"] == _sel("#y")   # rolled back
    assert ut.get_user_template("B")["learned"] == _sel("#p")
    assert sorted(res["restored"]) == ["A", "B"]


def test_restore_skips_history_template_absent_from_store(seeded):
    tmp, ut = seeded
    # add a history-only template C with no current record
    doc = _hist()
    doc["C"] = [{"version": "120-#c", "ts": 120, "source": "save",
                 "note": "", "selectors": _sel("#c")}]
    _write_hist(tmp, doc)
    res = ut.restore_template_set(250)
    assert "C" not in res["restored"]
    assert any(s["template_id"] == "C" for s in res["skipped"])
    assert ut.get_user_template("C") is None    # never created
