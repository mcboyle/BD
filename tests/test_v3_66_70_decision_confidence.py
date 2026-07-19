"""v3.66.70 — decision confidence (weakest-link, band + audit trace).

Built against the filename falsification. A decision must be capped by its
LEAST stable load-bearing assumption (never an average, which would hide a
fragile dependency), and the band must always ship with the supporting
assumptions, their stability bands, inferred-vs-observed basis, and the
perturbation that would change them. These tests pin:

  * weakest-link aggregation (a fragile support caps the decision);
  * the band is never emitted without its supporting-assumption trace;
  * a decision resting only on goal_selection (heuristic, inferred) reads low;
  * the additional-capture recommendation is capped + flagged inferred BEFORE a
    second title is captured (the grounding case);
  * recognition-only output (no signing values).
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader.capture_synth import synthesize
from bulk_downloader import capture_workbench as wb


def _entry(seq, url, body=None):
    return {"seq": seq, "url": url, "method": "GET", "request_headers": {},
            "response_headers": {}, "response_status": 200,
            "response_body": body, "type": "xhr"}


def _same_title_draft():
    """A same-title capture pair mirroring the ultrafilms shape (content id
    constant, goal cdn/{content_id}/{rendition}.mp4 with signing) — the state
    BEFORE a second title is captured."""
    def cap(expires, token, session):
        goal = (f"https://dd.example.test/53eb2252/3840x2160_60FPS_S.mp4"
                f"?expires={expires}&token={token}")
        return {"host": "site.test", "network_log": [
            _entry(1, "https://site.test/play", body='{"session":"%s"}' % session),
            _entry(2, f"https://api.site.test/cfg?session={session}"),
            _entry(9, goal)]}
    return wb.build_workbench(synthesize(cap("1700000000", "TOKA", "sA"),
                                         cap("1700009999", "TOKB", "sB")))


def _decisions():
    return _same_title_draft().to_dict()["decision_confidence"]


# ── structure + method ─────────────────────────────────────────────
class TestStructure:
    def test_method_is_weakest_link(self):
        assert _decisions()["method"] == "weakest_link"

    def test_every_decision_has_band_and_trace(self):
        for d in _decisions()["decisions"]:
            assert d["confidence"] in ("low", "low-medium", "medium", "high")
            # the band is NEVER emitted without its supporting trace
            assert "supporting_assumptions" in d
            # if the decision is capped, the capping assumption appears in trace
            if d["capped_by"]:
                names = {s["assumption"] for s in d["supporting_assumptions"]}
                assert d["capped_by"] in names

    def test_trace_fields_present(self):
        dec = next(d for d in _decisions()["decisions"]
                   if d["supporting_assumptions"])
        s = dec["supporting_assumptions"][0]
        for f in ("assumption", "stability_band", "basis", "kind",
                  "would_invalidate"):
            assert f in s
        assert s["kind"] in ("inferred", "observed")


# ── weakest-link semantics ─────────────────────────────────────────
class TestWeakestLink:
    def test_decision_capped_by_least_stable_support(self):
        # a decision's band must equal the MIN band among its supports, not an
        # average — the whole point of rejecting averaging.
        order = {"low": 0, "low-medium": 1, "medium": 2, "high": 3}
        for d in _decisions()["decisions"]:
            sup = d["supporting_assumptions"]
            if not sup:
                continue
            weakest = min(order[s["stability_band"]] for s in sup)
            assert order[d["confidence"]] == weakest

    def test_goal_selection_dependent_decisions_are_low(self):
        # everything resting on the unverified goal pick (heuristic) caps low
        dec = {d["decision"]: d for d in _decisions()["decisions"]
               if d["decision"] in ("goal_classification",
                                     "new_provider_required")}
        assert dec["goal_classification"]["confidence"] == "low"
        assert dec["goal_classification"]["capped_by"] == "assume:goal_selection"
        # new_provider_required is structurally False but inherits the fragility
        assert dec["new_provider_required"]["confidence"] == "low"


# ── the grounding case: the half-wrong recommendation, pre-capture ──
class TestGroundingCase:
    def test_capture_recommendation_capped_and_inferred(self):
        rec = next(d for d in _decisions()["decisions"]
                   if d.get("category") == wb.CP_ADDITIONAL_CAPTURE)
        # it must NOT read settled before a second title confirms it
        assert rec["confidence"] in ("low", "low-medium", "medium")
        # and every supporting assumption is flagged inferred (none observed)
        assert rec["supporting_assumptions"]
        assert all(s["kind"] == "inferred"
                   for s in rec["supporting_assumptions"])
        # the skeleton slots it rests on are named in the trace
        names = {s["assumption"] for s in rec["supporting_assumptions"]}
        assert any(n.startswith("assume:skeleton:") for n in names)


# ── posture ────────────────────────────────────────────────────────
class TestPosture:
    def test_no_signing_values_in_confidence_output(self):
        blob = str(_decisions())
        assert "TOKA" not in blob and "1700000000" not in blob
