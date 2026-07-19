"""v3.66.71 — sensitivity analysis (robustness over hand-authored modeling).

The graph STRUCTURE is fixed; the weights and fragility rules are authored. This
layer sweeps the authored choices over the fixed structure and asks of every
ordering whether it survives. Built against the filename/rendition case: a
conclusion that only ranks where it does because of a constant or a perturbation
rule must be flagged CONTINGENT, before a capture disproves it. These tests pin:

  * the approved sweep regimes are all present (±1 node weight, all-equal,
    rank-reversal, fragility-band reorder);
  * an ordering forced purely by graph structure (goal_selection's downstream
    dominance) is ROBUST; finer skeleton-slot orderings are CONTINGENT;
  * the hand-authored 'different_title -> validates' rule on an inferred
    assumption is flagged plainly (the grounding case);
  * recognition-only (no signing values).
"""
import os
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
    def cap(expires, token, session):
        goal = (f"https://dd.example.test/53eb2252/3840x2160_60FPS_S.mp4"
                f"?expires={expires}&token={token}")
        return {"host": "site.test", "network_log": [
            _entry(1, "https://site.test/play", body='{"session":"%s"}' % session),
            _entry(2, f"https://api.site.test/cfg?session={session}"),
            _entry(9, goal)]}
    return wb.build_workbench(synthesize(cap("1700000000", "TOKA", "sA"),
                                         cap("1700009999", "TOKB", "sB")))


def _sens():
    return _same_title_draft().to_dict()["sensitivity"]


# ── sweep coverage ─────────────────────────────────────────────────
class TestSweepRegimes:
    def test_all_approved_node_weight_regimes_present(self):
        regimes = set(_sens()["robustness"]["node_weight_regimes"])
        assert "all_equal" in regimes
        assert "rank_reversal" in regimes
        # ±1 jitter present for at least one node type
        assert any(r.startswith("plus1:") for r in regimes)
        assert any(r.startswith("minus1:") for r in regimes)

    def test_fragility_reorder_regimes_present(self):
        regimes = set(_sens()["robustness"]["fragility_regimes"])
        assert "all_equal_fragility" in regimes
        assert "fragility_reversal" in regimes


# ── robust vs contingent ───────────────────────────────────────────
class TestRobustness:
    def test_goal_selection_is_robust(self):
        # goal_selection sits beneath the most downstream nodes -> its top rank
        # is a structural fact, robust to any weighting
        s = _sens()
        assert "assume:goal_selection" in s["robust_conclusions"]
        row = next(r for r in s["robustness"]["by_downstream_weight"]
                   if r["assumption"] == "assume:goal_selection")
        assert row["robust"] is True
        assert row["rank_range"][0] == row["rank_range"][1] == 1

    def test_skeleton_slot_ordering_is_contingent(self):
        # the finer ordering of the skeleton slots (which fed the half-wrong
        # filename recommendation) is an artifact of the node weights
        s = _sens()
        cont = {c["assumption"] for c in s["contingent_conclusions"]}
        assert any(a.startswith("assume:skeleton:") for a in cont)
        # and a contingent row names the regime that moved it
        rend = next((r for r in s["robustness"]["by_downstream_weight"]
                     if r["assumption"] == "assume:skeleton:rendition"), None)
        assert rend is not None and rend["robust"] is False
        assert rend["moved_under"]   # non-empty: names which regimes flip it

    def test_every_row_has_rank_range(self):
        for r in _sens()["robustness"]["by_downstream_weight"]:
            assert "rank_range" in r and len(r["rank_range"]) == 2
            assert r["rank_range"][0] <= r["baseline_rank"] <= r["rank_range"][1]


# ── the grounding case: the hand-authored perturbation rule ────────
class TestModelingDependencyFlags:
    def test_rendition_validates_rule_corrected_not_flagged(self):
        # v3.66.76: rendition's 'different_title -> validates' was falsified
        # (VC-0005) and corrected to may_invalidate, so it must NO LONGER be
        # flagged as a validates-on-inferred liability. The identity slot keeps
        # the (now validated, VC-0006) validates rule and is still flagged —
        # validated, but still hand-authored.
        deps = _sens()["modeling_dependencies"]
        by = {d["assumption"]: d for d in deps}
        assert "assume:skeleton:rendition" not in by, \
            "rendition validates rule was corrected; should not be flagged"
        cid = by.get("assume:skeleton:content_id")
        assert cid is not None and cid["basis"] == "shape_heuristic"
        assert cid["hand_authored_rule"].get("different_title") == "validates"
        assert "HAND-AUTHORED" in cid["flag"]

    def test_robust_goal_selection_not_in_validates_flags(self):
        # goal_selection's perturbation is 'may_invalidate', not 'validates',
        # so it should NOT be flagged as a validates-rule dependency
        deps = {d["assumption"] for d in _sens()["modeling_dependencies"]}
        # goal_selection may appear for other reasons, but not via a validates
        # rule — assert its rule isn't a validates claim
        s = _sens()
        gs = next((d for d in s["modeling_dependencies"]
                   if d["assumption"] == "assume:goal_selection"), None)
        assert gs is None  # goal_selection has no validates/resolves rule


# ── posture ────────────────────────────────────────────────────────
class TestPosture:
    def test_no_signing_values_in_sensitivity_output(self):
        blob = str(_sens())
        assert "TOKA" not in blob and "1700000000" not in blob
