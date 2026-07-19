"""v3.66.73 — validation corpus (permanent append-only ledger).

Pins the corpus contract: entries validate against the schema, append is
append-only with auto id/date, the failure-accumulation summary answers the
intended questions, and the SEEDED corpus that ships in the repo is well-formed
and internally consistent (e.g. the role-dependent perturbation rule appears
both confirmed and falsified). Recognition-only — no signing values stored.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import validation_corpus as vc


def _entry(**over):
    base = dict(version="3.66.73", subject="t", category="assumption",
                prediction="p", observation="o", outcome="confirmed",
                evidence="e")
    base.update(over)
    return base


# ── schema / validation ────────────────────────────────────────────
class TestSchema:
    def test_valid_entry_passes(self):
        vc.validate_entry(_entry(id="VC-9999", date="2026-01-01"))

    def test_missing_required_field_raises(self):
        e = _entry(id="VC-9999", date="2026-01-01")
        del e["prediction"]
        with pytest.raises(ValueError):
            vc.validate_entry(e)

    def test_bad_category_raises(self):
        with pytest.raises(ValueError):
            vc.validate_entry(_entry(id="X", date="d", category="nonsense"))

    def test_bad_outcome_raises(self):
        with pytest.raises(ValueError):
            vc.validate_entry(_entry(id="X", date="d", outcome="maybe"))


# ── append / load / query (in a temp file) ─────────────────────────
class TestAppendLoad:
    def _tmp(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.remove(path)  # start absent
        return path

    def test_append_assigns_id_and_date(self):
        path = self._tmp()
        try:
            e = vc.append_entry(_entry(), path)
            assert e["id"] == "VC-0001" and e["date"]
            e2 = vc.append_entry(_entry(subject="t2"), path)
            assert e2["id"] == "VC-0002"
        finally:
            os.remove(path)

    def test_append_is_append_only(self):
        path = self._tmp()
        try:
            vc.append_entry(_entry(subject="first"), path)
            vc.append_entry(_entry(subject="second"), path)
            entries = vc.load_corpus(path)
            assert [e["subject"] for e in entries] == ["first", "second"]
        finally:
            os.remove(path)

    def test_query_filters(self):
        path = self._tmp()
        try:
            vc.append_entry(_entry(outcome="confirmed"), path)
            vc.append_entry(_entry(outcome="falsified", subject="f"), path)
            ents = vc.load_corpus(path)
            assert len(vc.query(ents, outcome="falsified")) == 1
            assert len(vc.query(ents, outcome="confirmed")) == 1
        finally:
            os.remove(path)

    def test_missing_file_is_empty_corpus(self):
        assert vc.load_corpus("/nonexistent/corpus.jsonl") == []


# ── summary answers the intended questions ─────────────────────────
class TestSummary:
    def _corpus(self):
        path = tempfile.mkstemp(suffix=".jsonl")[1]
        os.remove(path)
        vc.append_entry(_entry(subject="a", outcome="falsified",
                               basis_kind="shape_heuristic"), path)
        vc.append_entry(_entry(subject="rule", category="perturbation_rule",
                               outcome="confirmed", basis_kind="shape_heuristic"),
                        path)
        vc.append_entry(_entry(subject="rule", category="perturbation_rule",
                               outcome="falsified", basis_kind="shape_heuristic",
                               model_change="pending — fix it"), path)
        return path

    def test_falsifications_and_basis_grouping(self):
        path = self._corpus()
        try:
            s = vc.summarize(vc.load_corpus(path))
            assert s["by_outcome"]["falsified"] == 2
            assert s["falsification_by_basis_kind"]["shape_heuristic"] == 2
        finally:
            os.remove(path)

    def test_perturbation_rule_ledger_keeps_both_outcomes(self):
        path = self._corpus()
        try:
            s = vc.summarize(vc.load_corpus(path))
            led = s["perturbation_rule_ledger"]["rule"]
            assert led["confirmed"] == 1 and led["falsified"] == 1
        finally:
            os.remove(path)

    def test_pending_correction_surfaced(self):
        path = self._corpus()
        try:
            s = vc.summarize(vc.load_corpus(path))
            subjects = {p["subject"] for p in s["pending_corrections"]}
            assert "rule" in subjects
        finally:
            os.remove(path)


# ── the SHIPPED seed corpus is well-formed and consistent ──────────
class TestSeededCorpus:
    def test_seed_corpus_loads_and_validates(self):
        entries = vc.load_corpus()  # the repo's validation_corpus.jsonl
        assert len(entries) >= 5, "seed corpus should carry the real events"
        for e in entries:
            vc.validate_entry(e)  # every shipped entry is well-formed

    def test_seed_records_the_filename_correction(self):
        entries = vc.load_corpus()
        filename = vc.query(entries, subject="filename_promotion")
        assert filename and filename[0]["outcome"] == "falsified"
        assert "identity/rendition" in (filename[0].get("model_change") or "")

    def test_seed_records_role_dependent_perturbation_rule(self):
        # the same rule must appear confirmed (identity) AND falsified (rendition)
        s = vc.summarize(vc.load_corpus())
        led = s["perturbation_rule_ledger"].get(
            "fragility_skeleton_different_title_validates")
        assert led and led["confirmed"] >= 1 and led["falsified"] >= 1


# ── posture ────────────────────────────────────────────────────────
class TestPosture:
    def test_no_signing_values_in_seed_corpus(self):
        raw = ""
        path = vc.default_corpus_path()
        if os.path.exists(path):
            raw = open(path, encoding="utf-8").read()
        # the corpus stores prose metadata, never captured token/expires values
        assert "token=" not in raw and "expires=" not in raw
