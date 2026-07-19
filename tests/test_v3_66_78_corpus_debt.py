"""v3.66.78 — corpus debt report (correction / capability / validation debt).

The corpus as a planning artifact: it separates the three KINDS of debt because
they are different work. Tests pin the categorization, the 'addressed' logic (a
shipped fix counts even without a resolver pointer), the five-question answers,
and the clean-correction checkpoint on the seeded corpus.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import validation_corpus as vc


def _entry(**over):
    base = dict(version="3.66.78", subject="t", category="assumption",
                prediction="p", observation="o", outcome="confirmed", evidence="e")
    base.update(over)
    return base


def _corpus(*entries):
    path = tempfile.mkstemp(suffix=".jsonl")[1]
    os.remove(path)
    for e in entries:
        vc.append_entry(e, path)
    return path


class TestCategorization:
    def test_three_categories_separated(self):
        path = _corpus(
            _entry(subject="wrong", outcome="falsified", model_change="pending — fix"),
            _entry(subject="gap", conclusion_class="capability_gap",
                   model_change="pending — cap"),
            _entry(subject="untested_rule", outcome="untested"),
            _entry(subject="good", outcome="confirmed"))
        try:
            r = vc.debt_report(vc.load_corpus(path))
            assert [e["subject"] for e in r["correction_debt"]] == ["wrong"]
            assert [e["subject"] for e in r["capability_debt"]] == ["gap"]
            assert [e["subject"] for e in r["validation_debt"]] == ["untested_rule"]
        finally:
            os.remove(path)

    def test_capability_gap_not_in_correction_debt(self):
        path = _corpus(_entry(subject="gap", conclusion_class="capability_gap",
                              outcome="confirmed", model_change="pending — cap"))
        try:
            r = vc.debt_report(vc.load_corpus(path))
            assert not r["correction_debt"]
            assert len(r["capability_debt"]) == 1
        finally:
            os.remove(path)


class TestAddressedLogic:
    def test_shipped_fix_clears_correction_debt(self):
        # a falsification with a SHIPPED (non-pending) model change is addressed,
        # even without a resolver pointer (the VC-0002 case)
        path = _corpus(_entry(subject="shipped", outcome="falsified",
                              model_change="v3.66.69 the fix shipped"))
        try:
            r = vc.debt_report(vc.load_corpus(path))
            assert not r["correction_debt"]
        finally:
            os.remove(path)

    def test_pending_fix_remains_debt(self):
        path = _corpus(_entry(subject="todo", outcome="falsified",
                              model_change="pending — not done"))
        try:
            assert vc.debt_report(vc.load_corpus(path))["correction_debt"]
        finally:
            os.remove(path)

    def test_resolved_pointer_clears_debt(self):
        path = _corpus(
            _entry(subject="broken", outcome="falsified", model_change="pending"),
            _entry(subject="fix", resolves=["VC-0001"],
                   model_change="shipped"))
        try:
            assert not vc.debt_report(vc.load_corpus(path))["correction_debt"]
        finally:
            os.remove(path)

    def test_orphaned_evidence_detected(self):
        # a falsification with NO model change at all is evidence without action
        path = _corpus(_entry(subject="orphan", outcome="falsified"))
        try:
            r = vc.debt_report(vc.load_corpus(path))
            assert [e["subject"] for e in r["evidence_without_action_item"]] == \
                ["orphan"]
        finally:
            os.remove(path)


class TestLiveCorpusDebt:
    """Invariants of the debt report against the LIVE (evolving) corpus.

    The .78 checkpoint snapshot (zero correction debt, one capability gap) was a
    point-in-time fact; the nubile captures (v3.66.79) reopened correction debt
    and added a capability gap. The corpus state is data that changes per capture,
    so these tests assert the report's LOGIC is sound — not a frozen state.
    """
    def test_correction_debt_items_are_genuinely_open_problems(self):
        r = vc.debt_report(vc.load_corpus())
        for e in r["correction_debt"]:
            assert e["outcome"] in ("falsified", "partial")
            assert e["conclusion_class"] != "capability_gap"

    def test_capability_debt_items_are_capability_gaps(self):
        r = vc.debt_report(vc.load_corpus())
        for e in r["capability_debt"]:
            assert e["conclusion_class"] == "capability_gap"
        # The HLS gap (VC-0014, hls_manifest_classification_gap) was an open
        # capability gap at .78; it was RESOLVED by VC-0029 (hls_manifest_preference,
        # a confirmed model_correction with a resolves-pointer to VC-0014), so it is
        # no longer in capability_debt. Confirm the resolution holds rather than
        # asserting the (now-closed) gap is still open.
        corpus = vc.load_corpus()
        resolved = {rid for e in corpus
                    for rid in ([e.get("resolves")] if isinstance(e.get("resolves"), str)
                                else (e.get("resolves") or []))}
        assert "VC-0014" in resolved  # the HLS gap is closed by a later entry
        assert not any(e["subject"] == "hls_manifest_classification_gap"
                       for e in r["capability_debt"])

    def test_validation_debt_is_untested_assertions(self):
        r = vc.debt_report(vc.load_corpus())
        # The untested fragility axes. At .78 there were >=3; the corpus has since
        # worked debt down. As of v3.66.96, VC-0017 (player_config) was retired by
        # VC-0035 on a real N=3 same-title perturbation capture, leaving exactly
        # VC-0018 (workflow) still untested — it requires its own real perturbation
        # capture and cannot be retired synthetically. This is a deliberate-state
        # tripwire: if it changes, update it as part of that deliberate corpus change.
        ids = {e["id"] for e in r["validation_debt"]}
        assert ids == {"VC-0018"}, ids
        for e in r["validation_debt"]:
            assert e["outcome"] == "untested"

    def test_checkpoint_flag_is_consistent_with_the_debt(self):
        # the flag must AGREE with the computed debt, whatever the state is
        r = vc.debt_report(vc.load_corpus())
        cp = r["checkpoint"]
        clean = (not r["correction_debt"]
                 and not r["evidence_without_action_item"])
        assert cp["at_clean_correction_checkpoint"] is clean
        assert cp["no_pending_corrections"] is (not r["correction_debt"])
        assert cp["open_capability_gaps"] == len(r["capability_debt"])
