"""Tests for lifecycle_drift (A5 sub-waves 3+4): drift wiring + lifecycle responses.

Synthetic fixtures only. The load-bearing assertions:
  * detection (sweep, validation_gate) never mutates;
  * the auto_* wrappers are OFF by default → no mutation (the safety floor);
  * quarantine makes a template not-usable AND is reachable only with toggle+keystone;
  * a flagged template STAYS usable (advisory only);
  * repair lands at `reviewed` (never auto re-enables) and respects the drift gate;
  * every mutation snapshotted gold first (rollback-able).
"""
import contextlib
import json
from pathlib import Path

from bulk_downloader import lifecycle_drift as ld
from bulk_downloader import lifecycle_automation as la


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
            "selectors": {"player": {"play_button": f".p{v}"}},
            "api": {}, "network_patterns": []}


def _setup(tmp_path, live, gold=None):
    rd = tmp_path / "templates" / "reviewed"
    rd.mkdir(parents=True)
    if live is not None:
        (rd / "example.com.template.json").write_text(json.dumps(live), "utf-8")
    if gold is not None:
        (rd / "example.com.template.json.bak").write_text(json.dumps(gold), "utf-8")
    return rd


def _live(rd):
    p = rd / "example.com.template.json"
    return json.loads(p.read_text("utf-8"))


class TestDetectionReadOnly:
    def test_sweep_reports_drift(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(2), gold=_tpl(1))
        before = (rd / "example.com.template.json").read_bytes()
        s = ld.sweep(reviewed_dir=rd)
        assert s["ok"] and s["checked"] == 1
        assert s["rows"][0]["drift"] > 0
        # read-only
        assert (rd / "example.com.template.json").read_bytes() == before

    def test_sweep_skips_non_enabled(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1, status="quarantined"))
        s = ld.sweep(reviewed_dir=rd)
        assert s["checked"] == 0  # quarantined is not swept

    def test_validation_gate_flags_offender(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(2), gold=_tpl(1))
        g = ld.validation_gate(reviewed_dir=rd, max_drift=0)
        assert g["rc"] == 1 and g["offenders"]

    def test_validation_gate_clean_when_no_drift(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1), gold=_tpl(1))
        g = ld.validation_gate(reviewed_dir=rd, max_drift=0)
        assert g["rc"] == 0


class TestAutoWrappersDefaultOff:
    def test_auto_flag_off_is_noop(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        before = (rd / "example.com.template.json").read_bytes()
        with _toggles(on_set=set()):
            r = ld.auto_flag_if_enabled("example.com", "drift", reviewed_dir=rd)
        assert r.get("skipped")
        assert (rd / "example.com.template.json").read_bytes() == before

    def test_auto_quarantine_off_is_noop(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        before = (rd / "example.com.template.json").read_bytes()
        with _toggles(on_set=set()):
            r = ld.auto_quarantine_if_enabled("example.com", "drift", reviewed_dir=rd)
        assert r.get("skipped")
        assert (rd / "example.com.template.json").read_bytes() == before

    def test_auto_quarantine_blocked_without_keystone_even_if_toggled(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        before = (rd / "example.com.template.json").read_bytes()
        with _toggles(on_set={"auto_quarantine"}, keystone=False):
            r = ld.auto_quarantine_if_enabled("example.com", "drift", reviewed_dir=rd)
        assert r.get("skipped")  # double-gate holds
        assert (rd / "example.com.template.json").read_bytes() == before

    def test_auto_repair_off_is_noop(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        with _toggles(on_set=set()):
            r = ld.auto_repair_if_enabled("example.com", _tpl(1), reviewed_dir=rd)
        assert r.get("skipped")


class TestFlagMechanism:
    def test_flag_keeps_template_usable(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        r = ld._flag("example.com", "drift seen", reviewed_dir=rd)
        assert r["ok"] and r["still_usable"]
        t = _live(rd)
        assert t["needs_review"] is True
        assert t["status"] == "enabled"          # STAYS usable
        assert la.is_usable(t["status"]) is True

    def test_auto_flag_on_acts(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        with _toggles(on_set={"auto_flag"}):
            r = ld.auto_flag_if_enabled("example.com", "drift", reviewed_dir=rd)
        assert r["ok"] and r.get("flagged") == "example.com"
        assert _live(rd)["needs_review"] is True


class TestQuarantineMechanism:
    def test_quarantine_makes_unusable_and_snapshots(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        r = ld._quarantine("example.com", "drift", reviewed_dir=rd)
        assert r["ok"] and r["usable"] is False
        t = _live(rd)
        assert t["status"] == "quarantined"
        assert la.is_usable(t["status"]) is False
        # gold snapshot taken (recovery point)
        assert (rd / "example.com.template.json.bak").is_file()

    def test_auto_quarantine_on_with_keystone_acts(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        with _toggles(on_set={"auto_quarantine"}, keystone=True):
            r = ld.auto_quarantine_if_enabled("example.com", "drift", reviewed_dir=rd)
        assert r.get("quarantined") == "example.com"
        assert _live(rd)["status"] == "quarantined"

    def test_quarantine_illegal_from_candidate(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1, status="candidate"))
        r = ld._quarantine("example.com", reviewed_dir=rd)
        assert r["ok"] is False and "illegal" in r["error"]


class TestRepairMechanism:
    def test_repair_lands_at_reviewed_not_enabled(self, tmp_path):
        # quarantined template; a fresh capture identical to gold repairs it.
        rd = _setup(tmp_path, live=_tpl(1, status="quarantined"), gold=_tpl(1))
        r = ld._repair("example.com", _tpl(1), reviewed_dir=rd, max_drift=0)
        assert r["ok"] and r["repaired"] is True
        t = _live(rd)
        assert t["status"] == "reviewed"        # NOT auto re-enabled
        assert la.is_usable(t["status"]) is False

    def test_repair_rejected_when_drift_exceeds_tolerance(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1, status="quarantined"), gold=_tpl(1))
        before = (rd / "example.com.template.json").read_bytes()
        r = ld._repair("example.com", _tpl(9), reviewed_dir=rd, max_drift=0)
        assert r["ok"] and r["repaired"] is False
        # live untouched (still quarantined v1)
        assert (rd / "example.com.template.json").read_bytes() == before


class TestRefreshMechanism:
    def test_refresh_keeps_enabled_when_within_tolerance(self, tmp_path):
        # enabled template, fresh capture identical to gold → refresh, STAYS enabled.
        rd = _setup(tmp_path, live=_tpl(1), gold=_tpl(1))
        r = ld._refresh("example.com", _tpl(1), reviewed_dir=rd, max_drift=0)
        assert r["ok"] and r["refreshed"] is True
        t = _live(rd)
        assert t["status"] == "enabled"           # stays in service
        assert la.is_usable(t["status"]) is True

    def test_refresh_refuses_non_enabled(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1, status="quarantined"), gold=_tpl(1))
        r = ld._refresh("example.com", _tpl(1), reviewed_dir=rd, max_drift=0)
        assert r["ok"] is False and "enabled" in r["error"]

    def test_refresh_rejected_on_drift_leaves_live_untouched(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1), gold=_tpl(1))
        before = (rd / "example.com.template.json").read_bytes()
        r = ld._refresh("example.com", _tpl(9), reviewed_dir=rd, max_drift=0)
        assert r["ok"] and r["refreshed"] is False
        assert (rd / "example.com.template.json").read_bytes() == before

    def test_auto_refresh_off_is_noop(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1), gold=_tpl(1))
        with _toggles(on_set=set()):
            r = ld.auto_refresh_if_enabled("example.com", _tpl(1), reviewed_dir=rd)
        assert r.get("skipped")

    def test_auto_refresh_blocked_without_keystone(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1), gold=_tpl(1))
        with _toggles(on_set={"auto_refresh"}, keystone=False):
            r = ld.auto_refresh_if_enabled("example.com", _tpl(1), reviewed_dir=rd)
        assert r.get("skipped")  # keystone double-gate


class TestSweepAndRespond:
    def test_responses_off_is_read_only(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(2), gold=_tpl(1))   # drifted
        before = (rd / "example.com.template.json").read_bytes()
        with _toggles(on_set=set()):
            r = ld.sweep_and_respond(reviewed_dir=rd)
        assert r["needing_attention"] == 1
        assert r["responses"][0]["action"] == "none"
        assert (rd / "example.com.template.json").read_bytes() == before

    def test_quarantine_takes_precedence_over_flag(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(2), gold=_tpl(1))
        with _toggles(on_set={"auto_flag", "auto_quarantine"}, keystone=True):
            r = ld.sweep_and_respond(reviewed_dir=rd)
        assert r["responses"][0]["action"] == "quarantine"
        assert _live(rd)["status"] == "quarantined"
        assert "needs_review" not in _live(rd)   # not also flagged

    def test_flag_only_when_quarantine_off(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(2), gold=_tpl(1))
        with _toggles(on_set={"auto_flag"}, keystone=True):
            r = ld.sweep_and_respond(reviewed_dir=rd)
        assert r["responses"][0]["action"] == "flag"
        t = _live(rd)
        assert t["needs_review"] is True and t["status"] == "enabled"


class TestScheduledSweepGating:
    def test_scheduled_sweep_noop_when_drift_sweep_off(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(2), gold=_tpl(1))
        before = (rd / "example.com.template.json").read_bytes()
        with _toggles(on_set=set()):
            r = ld.scheduled_sweep(reviewed_dir=rd)
        assert r.get("skipped") == "drift_sweep disabled"
        assert (rd / "example.com.template.json").read_bytes() == before

    def test_scheduled_sweep_runs_when_on(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(2), gold=_tpl(1))
        with _toggles(on_set={"drift_sweep"}):   # sweep on, responses off
            r = ld.scheduled_sweep(reviewed_dir=rd)
        assert r["ok"] and r.get("swept") == 1


class TestSchedulerRegistrationInert:
    def test_drift_sweep_task_registered_and_inert_by_default(self):
        from bulk_downloader import bg_scheduler as bg
        bg.register_default_tasks()
        st = bg.status()
        # the task is registered...
        names = {t.get("name") for t in st.get("tasks", [])} if isinstance(st.get("tasks"), list) else set(st.keys())
        # status() shape varies; assert via the task registry directly
        assert "lifecycle.drift_sweep" in bg._tasks
        # ...and firing it with drift_sweep off is a no-op (returns skipped)
        from bulk_downloader import lifecycle_drift as ld2
        assert ld2.scheduled_sweep().get("skipped") == "drift_sweep disabled"
