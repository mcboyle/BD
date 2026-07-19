"""v3.66.75 — conclusion_class axis on the validation corpus.

The corpus now classifies each entry on a second axis (what KIND of validated
conclusion it is) and surfaces the four classes the operator tracks explicitly:
framework_level / site_specific / anomaly / capability_gap. Backfill keeps the
existing JSONL immutable (append-only preserved). Recognition-only — no values.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import validation_corpus as vc


def _entry(**over):
    base = dict(version="3.66.75", subject="t", category="assumption",
                prediction="p", observation="o", outcome="confirmed", evidence="e")
    base.update(over)
    return base


class TestConclusionClass:
    def test_explicit_field_wins(self):
        e = _entry(id="VC-9999", conclusion_class="capability_gap")
        assert vc.conclusion_class(e) == "capability_gap"

    def test_backfill_for_known_pre_field_entry(self):
        # an entry with no explicit field but a known id uses the backfill map
        assert vc.conclusion_class({"id": "VC-0010"}) == "framework_level"
        assert vc.conclusion_class({"id": "VC-0012"}) == "anomaly"
        assert vc.conclusion_class({"id": "VC-0013"}) == "site_specific"

    def test_unknown_unclassified(self):
        assert vc.conclusion_class({"id": "VC-7777"}) == "unclassified"

    def test_validate_rejects_bad_conclusion_class(self):
        with pytest.raises(ValueError):
            vc.validate_entry(_entry(id="X", date="d",
                                     conclusion_class="nonsense"))

    def test_validate_accepts_good_conclusion_class(self):
        vc.validate_entry(_entry(id="X", date="d",
                                 conclusion_class="anomaly"))


class TestFourBuckets:
    def _corpus(self):
        path = tempfile.mkstemp(suffix=".jsonl")[1]
        os.remove(path)
        vc.append_entry(_entry(subject="fl", conclusion_class="framework_level"),
                        path)
        vc.append_entry(_entry(subject="ss", conclusion_class="site_specific"),
                        path)
        vc.append_entry(_entry(subject="an", outcome="partial",
                               conclusion_class="anomaly",
                               model_change="pending — fix"), path)
        vc.append_entry(_entry(subject="cg", conclusion_class="capability_gap",
                               model_change="pending — new cap"), path)
        return path

    def test_four_buckets_populate(self):
        path = self._corpus()
        try:
            s = vc.summarize(vc.load_corpus(path))
            assert [e["subject"] for e in s["confirmed_framework_level"]] == ["fl"]
            assert [e["subject"] for e in s["confirmed_site_specific"]] == ["ss"]
            assert [e["subject"] for e in s["confirmed_anomalies"]] == ["an"]
            assert [e["subject"] for e in s["confirmed_capability_gaps"]] == ["cg"]
        finally:
            os.remove(path)

    def test_anomaly_counts_even_when_partial(self):
        # an anomaly is "confirmed" as an anomaly even if its outcome is partial
        path = self._corpus()
        try:
            s = vc.summarize(vc.load_corpus(path))
            assert s["confirmed_anomalies"][0]["outcome"] == "partial"
        finally:
            os.remove(path)

    def test_capability_gap_not_in_pending_corrections(self):
        # a capability gap (conservative-correct, confirmed) is NOT a correction
        path = self._corpus()
        try:
            s = vc.summarize(vc.load_corpus(path))
            subjects = {p["subject"] for p in s["pending_corrections"]}
            assert "cg" not in subjects   # it's a gap, not a wrong-ness
            assert "an" in subjects        # the anomaly is owed a fix
        finally:
            os.remove(path)


class TestSeededAxis:
    def test_seed_has_all_four_classes_populated(self):
        s = vc.summarize(vc.load_corpus())
        assert s["confirmed_framework_level"], "framework-level conclusions"
        assert s["confirmed_site_specific"], "site-specific conclusion"
        assert s["confirmed_anomalies"], "the bros over-split anomaly"
        assert s["confirmed_capability_gaps"], "the HLS capability gap"

    def test_hls_gap_is_capability_gap(self):
        entries = vc.load_corpus()
        hls = vc.query(entries, subject="hls_manifest_classification_gap")
        assert hls and vc.conclusion_class(hls[0]) == "capability_gap"

    def test_oversplit_is_anomaly(self):
        entries = vc.load_corpus()
        os_ = vc.query(entries, subject="segment_role_oversplits_sharded_paths")
        assert os_ and vc.conclusion_class(os_[0]) == "anomaly"
