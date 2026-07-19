"""A5 -- auto-promote a reviewed candidate on a CLEAN staged diff (v3.66.476).

A `reviewed_not_enabled` candidate is auto-promoted to enabled ONLY when its
staged diff vs the current gold is clean: no new API host, no blocked term, no
risky-selector delta, and not marked auto_promotable=False (the A3 risky
quarantine flag). Any boundary-crossing case -- new API base host, first-time
host (no gold), blocked term, risky selector -- stays a manual operator confirm.
A0-backed; gated by the auto_promote toggle (keystone-required).

Two layers tested:
  * compute_clean_diff -- boundary detection (unit, crafted candidate/gold).
  * auto_promote_if_clean -- the gated orchestration (clean -> A0 write enabled;
    boundary -> staged, never enabled; toggle off -> skip).

Zero-arg + tempfile; runs under run_tests.py AND pytest.
"""
import contextlib
import json
import shutil
import tempfile
from pathlib import Path

from bulk_downloader import auto_promote as ap
from bulk_downloader import lifecycle_automation as la


def _tpl(v, status="enabled", api_host="api.good.example", extra=None):
    t = {"host": "example.com", "status": status, "version": str(v),
         "selectors": {"player": {"play_button": f".p{v}"}},
         "api": {"base": f"https://{api_host}/api/v1", "watch": "/m/{id}/watch"},
         "network_patterns": []}
    if extra:
        t.update(extra)
    return t


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


# ── compute_clean_diff (boundary detection) ──────────────────────────────────

def test_clean_diff_clean_case():
    r = ap.compute_clean_diff(_tpl(2), _tpl(1),
                              gate_fn=lambda t: [], lint_fn=lambda t: False)
    assert r["clean"] is True, r
    assert r["boundary"] == [], r


def test_clean_diff_new_api_host_is_boundary():
    cand = _tpl(2, api_host="api.NEW-host.example")
    r = ap.compute_clean_diff(cand, _tpl(1, api_host="api.good.example"),
                              gate_fn=lambda t: [], lint_fn=lambda t: False)
    assert r["clean"] is False, r
    assert any("api host" in b for b in r["boundary"]), r
    assert r["new_api_hosts"], r


def test_clean_diff_auto_promotable_false_is_boundary():
    cand = _tpl(2, extra={"auto_promotable": False})
    r = ap.compute_clean_diff(cand, _tpl(1),
                              gate_fn=lambda t: [], lint_fn=lambda t: False)
    assert r["clean"] is False, r
    assert any("auto_promotable" in b for b in r["boundary"]), r


def test_clean_diff_blocked_term_is_boundary():
    r = ap.compute_clean_diff(_tpl(2), _tpl(1),
                              gate_fn=lambda t: ["blocked term: sentry"],
                              lint_fn=lambda t: False)
    assert r["clean"] is False, r
    assert any("blocked-term" in b or "gate" in b for b in r["boundary"]), r


def test_clean_diff_risky_selector_is_boundary():
    r = ap.compute_clean_diff(_tpl(2), _tpl(1),
                              gate_fn=lambda t: [], lint_fn=lambda t: True)
    assert r["clean"] is False, r
    assert any("risky selector" in b for b in r["boundary"]), r


def test_clean_diff_first_time_host_is_boundary():
    r = ap.compute_clean_diff(_tpl(2), None,
                              gate_fn=lambda t: [], lint_fn=lambda t: False)
    assert r["clean"] is False, r
    assert any("first-time" in b for b in r["boundary"]), r


# ── auto_promote_if_clean (gated orchestration) ──────────────────────────────

def _setup(d, gold):
    rd = Path(d) / "templates" / "reviewed"
    rd.mkdir(parents=True, exist_ok=True)
    # the current gold lives at the .bak (last-enabled gold)
    (rd / "example.com.template.json.bak").write_text(json.dumps(gold), "utf-8")
    return rd


def _live(rd):
    p = rd / "example.com.template.json"
    return json.loads(p.read_text("utf-8")) if p.is_file() else None


def test_auto_promote_toggle_off_is_skipped():
    d = tempfile.mkdtemp()
    rd = _setup(d, gold=_tpl(1))
    r = ap.auto_promote_if_clean("example.com", _tpl(2), reviewed_dir=rd,
                                 clean_fn=lambda: {"clean": True, "boundary": []})
    assert r.get("skipped"), r
    assert _live(rd) is None, "nothing should be written with the toggle off"
    shutil.rmtree(d, ignore_errors=True)


def test_auto_promote_clean_enables_a0_backed():
    d = tempfile.mkdtemp()
    rd = _setup(d, gold=_tpl(1))
    with _toggles(on_set={"auto_promote"}, keystone=True):
        r = ap.auto_promote_if_clean(
            "example.com", _tpl(2), reviewed_dir=rd,
            clean_fn=lambda: {"clean": True, "boundary": [], "drift": 1, "lines": ["x"]})
    assert r["ok"] and r.get("auto_promoted") is True, r
    live = _live(rd)
    assert live and live["status"] == "enabled", live
    shutil.rmtree(d, ignore_errors=True)


def test_auto_promote_boundary_stages_for_review_never_enables():
    d = tempfile.mkdtemp()
    rd = _setup(d, gold=_tpl(1))
    with _toggles(on_set={"auto_promote"}, keystone=True):
        r = ap.auto_promote_if_clean(
            "example.com", _tpl(2), reviewed_dir=rd,
            clean_fn=lambda: {"clean": False, "boundary": ["new api host(s): ['api.x']"]})
    assert r["ok"] and r.get("auto_promoted") is False, r
    assert r.get("needs_confirm") is True, r
    assert _live(rd) is None, "a boundary candidate must NEVER be auto-enabled"
    shutil.rmtree(d, ignore_errors=True)
