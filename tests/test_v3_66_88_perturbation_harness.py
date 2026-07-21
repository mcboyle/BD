"""v3.66.88 — perturbation-capture harness (VC-0017 / VC-0018).

Tests the harness machinery and, above all, the enforced boundary: a synthetic
perturbation validates the machinery but can never move debt, confidence,
sensitivity, or a corpus conclusion. The machinery is validated by checking that it
re-derives the skeleton, detects per-kind change, and maps each change against the
fragility prediction — including faithfully reporting tension rather than smoothing
it over. Recognition-only.
"""
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import perturbation_harness as ph
from capture_test_fixtures import capture_fixture_lane

_FIXTURES = capture_fixture_lane()


def _load(p):
    p = os.fspath(p)
    if p.endswith(".json"):
        return json.load(open(p))
    with zipfile.ZipFile(p) as z:
        n = [x for x in z.namelist() if x.endswith("capture.json")][0]
        return json.loads(z.read(n))


def _cap():
    if not _FIXTURES.has("capA.json"):
        pytest.skip("capture not present")
    return _load(_FIXTURES.path("capA.json"))


class TestBoundaryEnforced:
    def test_synthetic_never_resolves_debt(self):
        rep = ph.validate_machinery(_cap())
        assert rep["retires_debt"] is False
        for axis in rep["axes"].values():
            assert axis["resolves_debt"] is False

    def test_synthetic_touches_nothing_downstream(self):
        rep = ph.validate_machinery(_cap())
        for axis in rep["axes"].values():
            assert axis["affects_confidence"] is False
            assert axis["affects_sensitivity"] is False
            assert axis["affects_corpus_conclusion"] is False

    def test_synthetic_evidence_tagged(self):
        rep = ph.validate_machinery(_cap())
        assert rep["evidence"] == "synthetic"
        for axis in rep["axes"].values():
            assert axis["evidence"] == "synthetic"

    def test_real_evidence_leaves_decisions_pending_not_forced(self):
        # the real path must NOT pre-decide the downstream effects; they are None
        # (awaiting the real outcome), never silently False like synthetic
        base = _cap()
        out = ph.perturbation_run(base, base, "player_config", evidence="real")
        assert out["resolves_debt"] is None
        assert out["affects_confidence"] is None


class TestMachineryWorks:
    def test_both_axes_reported(self):
        rep = ph.validate_machinery(_cap())
        assert set(rep["axes"]) == {"player_config", "workflow"}

    def test_every_fragility_kind_carries_its_axis_prediction(self):
        rep = ph.validate_machinery(_cap())
        kinds = {k["kind"] for k in rep["axes"]["player_config"]["per_kind"]}
        # the harness reports against the full fragility map, not a subset
        assert {"goal_selection", "skeleton_identity", "skeleton_rendition",
                "title_invariant", "n2_floor", "src_unknown"} <= kinds

    def test_detects_change_and_maps_to_prediction(self):
        # the workflow synthetic perturbation changes the goal path shape;
        # the harness must observe goal_selection changed and call it consistent
        # with the likely_invalidates prediction
        rep = ph.validate_machinery(_cap())
        gs = next(k for k in rep["axes"]["workflow"]["per_kind"]
                  if k["kind"] == "goal_selection")
        assert gs["observed"] == "changed"
        assert "consistent" in gs["outcome"]

    def test_reports_tension_when_observation_contradicts_prediction(self):
        # machinery must surface disagreement, not hide it — a harness that only
        # ever agreed with itself would be the broken one
        rep = ph.validate_machinery(_cap())
        all_outcomes = [k["outcome"] for ax in rep["axes"].values()
                        for k in ax["per_kind"]]
        assert any("TENSION" in o for o in all_outcomes)

    def test_unknown_axis_errors(self):
        base = _cap()
        out = ph.perturbation_run(base, base, "cosmic_rays", evidence="synthetic")
        assert "error" in out
