"""A2 (headline) -- self-healing refresh of approved templates (v3.66.475).

_refresh already does a drift-gated swap, but it skips the FULL gate and the
post-write re-verify. A2 adds self_heal(): normalize -> full gate -> A0 backup +
write (drift-gated) -> RE-VERIFY -> keep, or AUTO-RESTORE on regression. For an
already-enabled host with every gate green and a good backup there is NO operator
checkpoint; any uncertainty (gate fail / drift over tolerance / post-write
regression) stages for review or auto-restores -- never leaving a broken live.

The gate + verify are injectable: they have their own suites, so these tests
pin the A2 ORCHESTRATION (the flow + the safety net), not the gate logic.

Contract (RED-first -- self_heal does not exist yet):
  1. enabled host + gates green + drift OK -> self_healed, live updated, no checkpoint.
  2. gate failure -> staged_for_review, live UNTOUCHED, not self_healed.
  3. host not enabled -> not handled, live untouched.
  4. post-write verify regression -> AUTO-RESTORE: live byte-identical to pre-write.
  5. drift over tolerance -> staged_for_review (uncertainty), live untouched.
  6. auto_refresh toggle off -> skipped, no write.

Zero-arg + tempfile; runs under run_tests.py AND pytest.
"""
import contextlib
import json
import shutil
import tempfile
from pathlib import Path

from bulk_downloader import lifecycle_drift as ld
from bulk_downloader import lifecycle_automation as la


def _tpl(v, status="enabled", extra=None):
    t = {"host": "example.com", "status": status, "version": str(v),
         "selectors": {"player": {"play_button": f".p{v}"}},
         "api": {}, "network_patterns": []}
    if extra:
        t.update(extra)
    return t


def _setup(d, live, gold=None):
    rd = Path(d) / "templates" / "reviewed"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "example.com.template.json").write_text(json.dumps(live), "utf-8")
    if gold is not None:
        (rd / "example.com.template.json.bak").write_text(json.dumps(gold), "utf-8")
    return rd


def _live(rd):
    return json.loads((rd / "example.com.template.json").read_text("utf-8"))


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


def test_self_heal_green_keeps_in_service_no_checkpoint():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(1), gold=_tpl(1))
    cand = _tpl(1, extra={"refreshed_marker": True})   # drift 0 vs gold
    with _toggles(on_set={"auto_refresh"}):
        r = ld.self_heal("example.com", cand, reviewed_dir=rd,
                         gate_fn=lambda t: [], verify_fn=lambda t: True, normalize=False)
    assert r["ok"] and r.get("self_healed") is True, r
    assert r.get("checkpoint") is False, r
    assert _live(rd)["status"] == "enabled"
    assert _live(rd).get("refreshed_marker") is True, _live(rd)
    shutil.rmtree(d, ignore_errors=True)


def test_self_heal_gate_failure_stages_for_review_live_untouched():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(1), gold=_tpl(1))
    before = (rd / "example.com.template.json").read_bytes()
    cand = _tpl(2)
    with _toggles(on_set={"auto_refresh"}):
        r = ld.self_heal("example.com", cand, reviewed_dir=rd,
                         gate_fn=lambda t: ["unsafe selector"], normalize=False)
    assert r["ok"] and r.get("staged_for_review") is True, r
    assert r.get("self_healed") is not True
    assert (rd / "example.com.template.json").read_bytes() == before, "live must be untouched"
    shutil.rmtree(d, ignore_errors=True)


def test_self_heal_not_enabled_is_not_handled():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(1, status="disabled"))
    before = (rd / "example.com.template.json").read_bytes()
    with _toggles(on_set={"auto_refresh"}):
        r = ld.self_heal("example.com", _tpl(1), reviewed_dir=rd,
                         gate_fn=lambda t: [], normalize=False)
    assert r.get("handled") is False, r
    assert (rd / "example.com.template.json").read_bytes() == before
    shutil.rmtree(d, ignore_errors=True)


def test_self_heal_regression_auto_restores():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(1), gold=_tpl(1))
    before = (rd / "example.com.template.json").read_bytes()
    cand = _tpl(1, extra={"BAD": "would-regress"})     # drift 0 -> swaps
    with _toggles(on_set={"auto_refresh"}):
        # verify_fn fails -> post-write regression -> auto-restore from A0 backup
        r = ld.self_heal("example.com", cand, reviewed_dir=rd,
                         gate_fn=lambda t: [], verify_fn=lambda t: False, normalize=False)
    assert r["ok"] and r.get("self_healed") is False, r
    assert r.get("restored") is True, r
    # live must be byte-identical to the pre-write template (the candidate rolled back)
    assert (rd / "example.com.template.json").read_bytes() == before, _live(rd)
    assert "BAD" not in _live(rd)
    shutil.rmtree(d, ignore_errors=True)


def test_self_heal_drift_over_tolerance_stages_for_review():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(1), gold=_tpl(1))
    before = (rd / "example.com.template.json").read_bytes()
    cand = _tpl(9)   # selectors differ from gold -> drift > 0
    with _toggles(on_set={"auto_refresh"}):
        r = ld.self_heal("example.com", cand, reviewed_dir=rd, max_drift=0,
                         gate_fn=lambda t: [], normalize=False)
    assert r["ok"] and r.get("staged_for_review") is True, r
    assert r.get("self_healed") is not True
    assert (rd / "example.com.template.json").read_bytes() == before, "live untouched over tolerance"
    shutil.rmtree(d, ignore_errors=True)


def test_self_heal_toggle_off_is_skipped():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(1), gold=_tpl(1))
    before = (rd / "example.com.template.json").read_bytes()
    r = ld.self_heal("example.com", _tpl(1), reviewed_dir=rd,
                     gate_fn=lambda t: [], normalize=False)
    assert r.get("skipped"), r
    assert (rd / "example.com.template.json").read_bytes() == before
    shutil.rmtree(d, ignore_errors=True)
