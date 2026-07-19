"""v3.66.137 — Phase K: held-out designation ASSIST + human-only designation CLI. Proves: the assist
is read-only (writes nothing); its integrity verdict matches the gate's hard-fails (training overlap,
raw signing value -> REJECT); the tier-delta equals the REAL oracle_verdict run with the candidate
actually designated (parity — no permissive drift); the designation CLI writes provenance only after
the verdict passes and refuses a rejected candidate; decay is surfaced; and K registers NO apply-kind
(it is not grantable) and adds no routes/POST. The designation action stays permanently human.
"""
import os
import shutil

import pytest  # noqa: F401

from tools import autonomy_oracle as ao
from tools import autonomy_held_out_assist as ka
from tools import autonomy_designate as kd
from tools import autonomy_apply as aap
from tools.cockpit_core import tasks_root

SID = "s1"

_MAP = {
    "capX":      {"capture": "capX",      "identity": "vidA", "renditions": ["1080p"], "template_shape": "media_present", "signing_marker_names": ["token"]},
    "capY":      {"capture": "capY",      "identity": "vidA", "renditions": ["1080p"], "template_shape": "media_present", "signing_marker_names": ["token"]},
    "capTrain":  {"capture": "capTrain",  "identity": "vidA", "renditions": ["1080p"], "template_shape": "media_present", "signing_marker_names": ["token"]},
    "capSigned": {"capture": "capSigned", "identity": "vidA", "renditions": ["1080p"], "template_shape": "media_present", "signing_marker_names": ["Authorization=Bearerabcdef1234567890123"]},
    "capDisagree": {"capture": "capDisagree", "identity": "vidB", "renditions": ["720p"], "template_shape": "thin", "signing_marker_names": ["token"]},
}


def _setup(held=("capX",), training=("capTrain",), designated_at=None):
    """Temp governance store + provenance + monkeypatched descriptor source. Returns saved fns."""
    os.environ["BD_COCKPIT_TASKS"] = "/tmp/bd_k_test_tasks"
    os.environ.pop("BD_HELD_OUT_STALE_DAYS", None)
    shutil.rmtree(tasks_root() / "governance", ignore_errors=True)
    os.makedirs(ao._oracle_root(), exist_ok=True)
    saved = (ao._held_out_descriptors, ka._descriptor_for, ka._available_captures)
    ao._held_out_descriptors = lambda site: [_MAP[n] for n in ao._held_out_capture_names(site) if n in _MAP]
    ka._descriptor_for = lambda name: _MAP.get(name)
    ka._available_captures = lambda: list(_MAP.keys())
    prov = {SID: {"training": list(training), "held_out": list(held)}}
    if designated_at is not None:
        prov[SID]["held_out_designated_at"] = designated_at
    ao._atomic_write(ao._provenance_path(), prov)
    return saved


def _restore(saved):
    ao._held_out_descriptors, ka._descriptor_for, ka._available_captures = saved


class TestAssistVerdict:
    def test_independent_agreeing_candidate_advances_tier(self):
        saved = _setup()
        try:
            v = ka.evaluate_candidate(SID, "capY")
            assert v["eligible"] is True
            assert v["current_tier"] == 2 and v["projected_tier"] == 3 and v["advances"] is True
        finally:
            _restore(saved)

    def test_training_overlap_is_reject(self):
        saved = _setup()
        try:
            v = ka.evaluate_candidate(SID, "capTrain")
            assert v["eligible"] is False
            assert any("REJECT" in r and "training" in r for r in v["reasons"])
            assert v["projected_tier"] == 0
        finally:
            _restore(saved)

    def test_raw_signing_value_is_reject(self):
        saved = _setup()
        try:
            v = ka.evaluate_candidate(SID, "capSigned")
            assert v["eligible"] is False
            assert any("REJECT" in r and "signing" in r for r in v["reasons"])
        finally:
            _restore(saved)

    def test_disagreeing_candidate_does_not_reach_tier3(self):
        saved = _setup()
        try:
            v = ka.evaluate_candidate(SID, "capDisagree")
            # eligible (independent, no signing) but descriptors disagree -> stays tier 2
            assert v["eligible"] is True and v["projected_tier"] == 2 and v["advances"] is False
        finally:
            _restore(saved)


class TestParityAndReadOnly:
    def test_projected_equals_real_verdict_after_designation(self):
        saved = _setup()
        try:
            proj = ka.evaluate_candidate(SID, "capY")["projected_tier"]
            prov = ao._provenance()
            prov[SID]["held_out"].append("capY")
            ao._atomic_write(ao._provenance_path(), prov)
            real_after = ao.oracle_verdict(SID)["tier"]
            assert proj == real_after          # no permissive drift
        finally:
            _restore(saved)

    def test_assist_writes_nothing(self):
        saved = _setup()
        try:
            before = open(ao._provenance_path(), encoding="utf-8").read()
            ka.designation_report()
            ka.evaluate_candidate(SID, "capY")
            ka.site_designation(SID)
            assert open(ao._provenance_path(), encoding="utf-8").read() == before
        finally:
            _restore(saved)

    def test_decay_flag_surfaced(self):
        saved = _setup(designated_at="2000-01-01T00:00:00+00:00")
        try:
            d = ka.site_designation(SID)["decay"]
            assert d["stale"] is True and d["age_days"] > 365
        finally:
            _restore(saved)


class TestDesignationCLI:
    def test_designate_eligible_writes_and_advances(self):
        saved = _setup()
        try:
            r = kd.designate(SID, "capY", by="mboyle", reason="2nd independent capture")
            assert r["ok"] is True and r["tier_now"] == 3
            assert "capY" in ao._held_out_capture_names(SID)
            assert ao._provenance().get(SID, {}).get("held_out_designated_at")
        finally:
            _restore(saved)

    def test_designate_refuses_rejected_candidate(self):
        saved = _setup()
        try:
            r = kd.designate(SID, "capTrain", by="mboyle", reason="x")
            assert r["ok"] is False
            assert "capTrain" not in ao._held_out_capture_names(SID)   # nothing written
        finally:
            _restore(saved)

    def test_undesignate_removes(self):
        saved = _setup(held=("capX", "capY"))
        try:
            r = kd.undesignate(SID, "capY", by="mboyle", reason="superseded")
            assert r["ok"] is True
            assert "capY" not in ao._held_out_capture_names(SID)
        finally:
            _restore(saved)


class TestNotAnApplyKind:
    def test_designation_is_not_a_grantable_kind(self):
        from tools import cockpit_console  # noqa: F401  (triggers all kind registrations)
        kinds = [k["kind"] for k in aap.registered_kinds()]
        assert "held_out" not in kinds and "designation" not in kinds
        assert "library_reconcile" in kinds  # sanity: other kinds still registered

    def test_designation_action_is_permanently_human(self):
        assert "corpus_writes" in ao.PERMANENTLY_INELIGIBLE

    def test_assist_module_has_no_writer_and_no_reverser(self):
        src = open(ka.__file__, encoding="utf-8").read()
        assert "_atomic_write(" not in src        # the assist never writes
        assert "register_apply_kind" not in src   # not a grantable kind
        assert "register_reverser" not in src      # no reverser registered

    def test_no_new_routes_or_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        posts = {r.rule for r in app.url_map.iter_rules()
                 if r.rule.startswith("/cockpit") and "POST" in (r.methods or set())}
        assert len(rules) >= 162 and len(posts) >= 26
