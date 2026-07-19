"""v3.66.80 — failure taxonomy axis.

The corpus now records what KIND of mistake each finding is, across five classes:
candidate_loss, candidate_over_generation, bad_candidate_selection,
capability_boundary, insufficient_evidence. Tests pin resolution precedence,
id-keyed backfill (VC-0005/VC-0006 share a subject and must resolve differently),
validation, the summarize distribution, and the framework-level finding.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import validation_corpus as vc


def _entry(**over):
    base = dict(id="VC-9999", date="2026-05-30", version="3.66.80", subject="t",
                category="assumption", prediction="p", observation="o",
                outcome="confirmed", evidence="e")
    base.update(over)
    return base


class TestResolution:
    def test_explicit_field_wins(self):
        e = _entry(failure_class="candidate_loss")
        assert vc.failure_class(e) == "candidate_loss"

    def test_backfill_by_id(self):
        e = next(x for x in vc.load_corpus() if x["id"] == "VC-0020")
        assert vc.failure_class(e) == "candidate_loss"

    def test_confirmation_has_no_failure_class(self):
        e = next(x for x in vc.load_corpus() if x["id"] == "VC-0009")
        assert vc.failure_class(e) is None  # a validation is not a failure

    def test_shared_subject_resolves_by_id_not_subject(self):
        # VC-0005 (falsified) and VC-0006 (confirmed) share a subject
        c = vc.load_corpus()
        v5 = next(x for x in c if x["id"] == "VC-0005")
        v6 = next(x for x in c if x["id"] == "VC-0006")
        assert v5["subject"] == v6["subject"]
        assert vc.failure_class(v5) == "bad_candidate_selection"
        assert vc.failure_class(v6) is None  # the confirmation is not a failure


class TestValidation:
    def test_rejects_unknown_failure_class(self):
        with pytest.raises(ValueError):
            vc.validate_entry(_entry(failure_class="not_a_class"))

    def test_accepts_known_failure_class(self):
        for fc in vc.FAILURE_CLASSES:
            vc.validate_entry(_entry(failure_class=fc))


class TestDistribution:
    def test_every_failure_bearing_entry_is_classified(self):
        s = vc.summarize(vc.load_corpus())
        assert s["unclassified_failures"] == []

    def test_all_five_classes_present(self):
        s = vc.summarize(vc.load_corpus())
        for fc in vc.FAILURE_CLASSES:
            assert fc in s["by_failure_class"], fc

    def test_candidate_generation_failed_in_both_directions(self):
        # the audit's central finding: loss AND over-generation both occurred
        s = vc.summarize(vc.load_corpus())
        assert s["by_failure_class"]["candidate_loss"] >= 1
        assert s["by_failure_class"]["candidate_over_generation"] >= 1

    def test_correction_shares_class_with_its_failure(self):
        c = vc.load_corpus()
        # VC-0016 fixes VC-0012 (bros) — both candidate_over_generation
        v12 = next(x for x in c if x["id"] == "VC-0012")
        v16 = next(x for x in c if x["id"] == "VC-0016")
        assert vc.failure_class(v12) == vc.failure_class(v16) == \
            "candidate_over_generation"


class TestFrameworkFinding:
    def test_recorded(self):
        f = vc.query(vc.load_corpus(),
                     subject="candidate_generation_is_lossy_upstream_of_uncertainty")
        assert f and vc.conclusion_class(f[0]) == "framework_level"

    def test_not_counted_as_a_failure(self):
        # the meta-finding establishes the taxonomy; it is not itself a failure
        f = vc.query(vc.load_corpus(),
                     subject="candidate_generation_is_lossy_upstream_of_uncertainty")[0]
        assert vc.failure_class(f) is None
