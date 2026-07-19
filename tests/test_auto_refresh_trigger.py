"""auto_refresh capture-time trigger (A5 finish-tail) — characterization tests.

Pins the operator-toggle mode-selector substrate and the promote_draft wire:
  * default-OFF / inert: on_fresh_capture handled=False with the master off,
    and promote_draft writes the reviewed template exactly as before (no
    auto_refresh path taken) — the byte-identical-when-off floor;
  * on_capture mode: drift-gated swap, operator-tunable max_drift, over-tolerance
    leaves live untouched (keystone no-swap-on-reject);
  * confirm mode: stage only, live untouched, gold snapshotted (wins over on_capture);
  * ineligible cases (no live template, live not enabled) -> handled=False so the
    caller's normal write path runs.
Synthetic fixtures only; browser-free; stdlib + project modules.
"""
import contextlib
import json
from pathlib import Path

from bulk_downloader import lifecycle_drift as ld
from bulk_downloader import lifecycle_automation as la
from bulk_downloader import template_manager as tm


@contextlib.contextmanager
def _toggles(on_set, keystone=True):
    orig_read, orig_keystone = la._read_toggle, la.keystone_available
    on_keys = {la.AUTOMATION_TOGGLES[n] for n in on_set}
    la._read_toggle = lambda key: key in on_keys
    la.keystone_available = lambda: keystone
    try:
        yield
    finally:
        la._read_toggle, la.keystone_available = orig_read, orig_keystone


def _tpl(v, status="enabled"):
    return {"host": "example.com", "status": status, "version": v,
            "selectors": {"download": {"trigger": f".dl{v}", "row_selectors": [".row"]},
                          "player": {"play_button": f".p{v}"}},
            "resolutions": [1080, 720], "api": {},
            "network_patterns": ["https://example.com/video/play.mp4"]}


def _setup(tmp_path, live, gold=None):
    rd = tmp_path / "templates" / "reviewed"
    rd.mkdir(parents=True)
    if live is not None:
        (rd / "example.com.template.json").write_text(json.dumps(live), "utf-8")
    if gold is not None:
        (rd / "example.com.template.json.bak").write_text(json.dumps(gold), "utf-8")
    return rd


def _live(rd):
    return json.loads((rd / "example.com.template.json").read_text("utf-8"))


# ── new toggles exist + are default-OFF in the substrate ─────────────────────

def test_new_mode_toggles_registered_and_default_off():
    for name in ("auto_refresh_on_capture", "auto_refresh_confirm"):
        assert name in la.AUTOMATION_TOGGLES
    st = la.toggle_status()
    assert st["auto_refresh_on_capture"]["toggle_on"] is False
    assert st["auto_refresh_confirm"]["toggle_on"] is False


# ── dispatcher: default + ineligible cases return handled=False ──────────────

def test_master_off_is_not_handled(tmp_path):
    rd = _setup(tmp_path, live=_tpl(1), gold=_tpl(1))
    with _toggles(on_set=set()):
        r = ld.on_fresh_capture("example.com", _tpl(2), reviewed_dir=rd)
    assert r["handled"] is False and "master off" in r["reason"]


def test_no_live_template_not_handled(tmp_path):
    rd = _setup(tmp_path, live=None)
    with _toggles(on_set={"auto_refresh", "auto_refresh_on_capture"}, keystone=True):
        r = ld.on_fresh_capture("example.com", _tpl(2), reviewed_dir=rd)
    assert r["handled"] is False and "no live" in r["reason"]


def test_live_not_enabled_not_handled(tmp_path):
    rd = _setup(tmp_path, live=_tpl(1, status="reviewed"))
    with _toggles(on_set={"auto_refresh", "auto_refresh_on_capture"}, keystone=True):
        r = ld.on_fresh_capture("example.com", _tpl(2), reviewed_dir=rd)
    assert r["handled"] is False and "not enabled" in r["reason"]


def test_mode_off_but_master_on_not_handled(tmp_path):
    # master auto_refresh on, but no capture-time mode -> sweep-driven only
    rd = _setup(tmp_path, live=_tpl(1), gold=_tpl(1))
    with _toggles(on_set={"auto_refresh"}, keystone=True):
        r = ld.on_fresh_capture("example.com", _tpl(1), reviewed_dir=rd)
    assert r["handled"] is False and "no capture-time mode" in r["reason"]


# ── on_capture mode: drift-gated swap ────────────────────────────────────────

def test_on_capture_swaps_within_tolerance(tmp_path):
    # candidate identical to gold -> drift 0 -> swap at max_drift=0
    rd = _setup(tmp_path, live=_tpl(1), gold=_tpl(1))
    with _toggles(on_set={"auto_refresh", "auto_refresh_on_capture"}, keystone=True):
        r = ld.on_fresh_capture("example.com", _tpl(1), reviewed_dir=rd)
    assert r["handled"] and r["mode"] == "on_capture"
    assert r.get("refreshed") is True
    assert _live(rd)["status"] == la.STATUS_ENABLED


def test_on_capture_refuses_over_tolerance_live_untouched(tmp_path):
    rd = _setup(tmp_path, live=_tpl(1), gold=_tpl(1))
    before = (rd / "example.com.template.json").read_bytes()
    # candidate drifts heavily from gold; max_drift default 0 -> no swap
    with _toggles(on_set={"auto_refresh", "auto_refresh_on_capture"}, keystone=True):
        r = ld.on_fresh_capture("example.com", _tpl(9), reviewed_dir=rd)
    assert r["handled"] and r.get("refreshed") is False
    assert (rd / "example.com.template.json").read_bytes() == before


def test_on_capture_max_drift_is_operator_tunable(tmp_path, monkeypatch):
    rd = _setup(tmp_path, live=_tpl(1), gold=_tpl(1))
    # raise tolerance high enough to admit the drift -> swap proceeds
    monkeypatch.setattr(ld, "_auto_refresh_max_drift", lambda: 99)
    with _toggles(on_set={"auto_refresh", "auto_refresh_on_capture"}, keystone=True):
        r = ld.on_fresh_capture("example.com", _tpl(9), reviewed_dir=rd)
    assert r["handled"] and r["max_drift"] == 99 and r.get("refreshed") is True


def test_max_drift_reader_reads_config(monkeypatch):
    import bulk_downloader.global_config as gc
    monkeypatch.setattr(gc, "get", lambda key, default=None:
                        7 if key == "automation.auto_refresh_max_drift" else default)
    assert ld._auto_refresh_max_drift() == 7


# ── confirm mode: stage only, live untouched, gold snapshotted ───────────────

def test_confirm_mode_stages_without_swapping(tmp_path):
    rd = _setup(tmp_path, live=_tpl(1))   # no gold yet
    before = (rd / "example.com.template.json").read_bytes()
    with _toggles(on_set={"auto_refresh", "auto_refresh_confirm"}, keystone=True):
        r = ld.on_fresh_capture("example.com", _tpl(5), reviewed_dir=rd)
    assert r["handled"] and r["mode"] == "confirm" and r["swapped"] is False
    # live untouched; stage + gold present
    assert (rd / "example.com.template.json").read_bytes() == before
    assert (rd / "example.com.template.json.stage").is_file()
    assert (rd / "example.com.template.json.bak").is_file()


def test_confirm_wins_over_on_capture(tmp_path):
    rd = _setup(tmp_path, live=_tpl(1), gold=_tpl(1))
    before = (rd / "example.com.template.json").read_bytes()
    with _toggles(on_set={"auto_refresh", "auto_refresh_on_capture", "auto_refresh_confirm"},
                  keystone=True):
        r = ld.on_fresh_capture("example.com", _tpl(1), reviewed_dir=rd)
    assert r["mode"] == "confirm" and r["swapped"] is False
    assert (rd / "example.com.template.json").read_bytes() == before


# ── promote_draft wire: byte-identical when off; routes when on ──────────────

def _write_draft(drafts_dir, tpl):
    drafts_dir.mkdir(parents=True, exist_ok=True)
    (drafts_dir / "example.com.template-draft.json").write_text(json.dumps(tpl), "utf-8")


def test_promote_draft_unchanged_when_modes_off(tmp_path):
    rd = tmp_path / "reviewed"; dd = tmp_path / "drafts"
    _write_draft(dd, _tpl(3))
    with _toggles(on_set=set()):
        res = tm.promote_draft("example.com.template-draft.json", reviewed_dir=rd, drafts_dir=dd)
    assert res["ok"] and "auto_refresh" not in res        # hook not taken
    assert (rd / "example.com.template.json").is_file()    # normal write happened
    assert json.loads((rd / "example.com.template.json").read_text())["status"] == "enabled"


def test_promote_draft_first_promote_unaffected_even_with_mode_on(tmp_path):
    # no existing live -> on_fresh_capture handled=False -> normal write
    rd = tmp_path / "reviewed"; dd = tmp_path / "drafts"
    _write_draft(dd, _tpl(3))
    with _toggles(on_set={"auto_refresh", "auto_refresh_on_capture"}, keystone=True):
        res = tm.promote_draft("example.com.template-draft.json", reviewed_dir=rd, drafts_dir=dd)
    assert res["ok"] and "auto_refresh" not in res
    assert (rd / "example.com.template.json").is_file()


def test_promote_draft_routes_through_hook_when_enabled_live_exists(tmp_path):
    rd = tmp_path / "reviewed"; dd = tmp_path / "drafts"
    rd.mkdir(parents=True); (rd / "example.com.template.json").write_text(json.dumps(_tpl(1)))
    (rd / "example.com.template.json.bak").write_text(json.dumps(_tpl(1)))  # gold
    _write_draft(dd, _tpl(1))   # identical -> within tolerance
    with _toggles(on_set={"auto_refresh", "auto_refresh_on_capture"}, keystone=True):
        res = tm.promote_draft("example.com.template-draft.json", reviewed_dir=rd, drafts_dir=dd)
    assert res["ok"] and "auto_refresh" in res
    assert res["auto_refresh"]["handled"] and res["auto_refresh"]["mode"] == "on_capture"
