"""v3.66.76 — fragility skeleton-split: the first validation->correction loop.

Guards the model change (skeleton fragility split by role) and the corpus
mechanics that record it: a structured `correction` record with six fields, and
`resolves`, which retires a prior pending entry by reference (append-only — the
falsified entry is never edited). Recognition-only.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import capture_workbench as wb
from bulk_downloader import validation_corpus as vc


# ── the model change: skeleton fragility is now role-split ─────────
class TestFragilitySplit:
    def test_identity_kind_validates(self):
        assert wb._assumption_kind("assume:skeleton:content_id") == \
            "skeleton_identity"
        assert wb._FRAGILITY["skeleton_identity"][
            "perturbation"]["different_title"] == "validates"

    def test_rendition_kind_may_invalidate(self):
        assert wb._assumption_kind("assume:skeleton:rendition") == \
            "skeleton_rendition"
        assert wb._FRAGILITY["skeleton_rendition"][
            "perturbation"]["different_title"] == "may_invalidate"

    def test_numbered_slots_route_by_role(self):
        assert wb._assumption_kind("assume:skeleton:content_id3") == \
            "skeleton_identity"
        assert wb._assumption_kind("assume:skeleton:rendition2") == \
            "skeleton_rendition"

    def test_no_dead_skeleton_key_breaks_lookup(self):
        # every kind _assumption_kind can return must exist in _FRAGILITY
        for nid in ("assume:skeleton:content_id", "assume:skeleton:rendition",
                    "assume:skeleton:other", "assume:src_unknown:x",
                    "assume:goal_selection", "assume:n2_floor",
                    "assume:title_invariant", "weird:node"):
            assert wb._assumption_kind(nid) in wb._FRAGILITY


# ── the corpus mechanics: structured correction + resolution ───────
def _entry(**over):
    base = dict(version="3.66.76", subject="t", category="perturbation_rule",
                prediction="p", observation="o", outcome="confirmed", evidence="e")
    base.update(over)
    return base


_GOOD_CORRECTION = {
    "old_assumption": "old", "falsifying_evidence": "fe",
    "corrected_assumption": "ca", "validation_evidence": "ve",
    "downstream_layers": "dl", "observed_effect": "oe"}


class TestCorrectionRecord:
    def test_complete_correction_validates(self):
        vc.validate_entry(_entry(id="X", date="d", conclusion_class="model_correction",
                                 correction=_GOOD_CORRECTION))

    def test_incomplete_correction_rejected(self):
        bad = dict(_GOOD_CORRECTION)
        del bad["observed_effect"]
        with pytest.raises(ValueError):
            vc.validate_entry(_entry(id="X", date="d", correction=bad))

    def test_correction_must_be_dict(self):
        with pytest.raises(ValueError):
            vc.validate_entry(_entry(id="X", date="d", correction="nope"))

    def test_resolves_must_be_list(self):
        with pytest.raises(ValueError):
            vc.validate_entry(_entry(id="X", date="d", resolves="VC-0005"))


class TestResolution:
    def test_resolved_entry_drops_from_pending(self):
        path = tempfile.mkstemp(suffix=".jsonl")[1]
        os.remove(path)
        try:
            vc.append_entry(_entry(subject="broken", outcome="falsified",
                                   model_change="pending — fix"), path)  # VC-0001
            # a later correction resolves it
            vc.append_entry(_entry(subject="fix", conclusion_class="model_correction",
                                   resolves=["VC-0001"],
                                   correction=_GOOD_CORRECTION,
                                   model_change="shipped"), path)
            s = vc.summarize(vc.load_corpus(path))
            pend = {p["id"] for p in s["pending_corrections"]}
            assert "VC-0001" not in pend          # resolved
            assert s["resolved_corrections"]       # surfaced
        finally:
            os.remove(path)


class TestSeededCorrection:
    def test_vc0005_resolved_by_a_correction(self):
        s = vc.summarize(vc.load_corpus())
        pend = {p["id"] for p in s["pending_corrections"]}
        assert "VC-0005" not in pend, "the fragility correction resolves VC-0005"
        assert any("VC-0005" in r["resolves"] for r in s["resolved_corrections"])

    def test_correction_entry_has_six_fields(self):
        entries = vc.load_corpus()
        corr = vc.query(entries, subject="skeleton_fragility_role_split")
        assert corr and corr[0].get("correction")
        for f in vc.CORRECTION_FIELDS:
            assert corr[0]["correction"].get(f), f

    def test_bros_oversplit_now_resolved(self):
        # at v3.66.76 the bros over-split was the next pending correction; v3.66.77
        # resolved it (VC-0016 resolves VC-0012). Assert the debt is now retired.
        s = vc.summarize(vc.load_corpus())
        assert not any(p["subject"] == "segment_role_oversplits_sharded_paths"
                       for p in s["pending_corrections"])
        assert any("VC-0012" in r["resolves"] for r in s["resolved_corrections"])
