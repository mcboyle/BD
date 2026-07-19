"""v3.66.89 — offline capture-ingestion tool.

Tests the ingestion core and the CLI report generation, with particular weight on the two
boundaries that matter most: the recognition-only posture (no signing value ever reaches an
output) and the corpus boundary (a suggested entry is never written and never retires debt).
Recognition-only; reuses existing analysis logic rather than duplicating it.
"""
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import capture_ingest as ci

U = "/mnt/user-data/uploads/"
_SERIES = [U + "ultrafilms_title2.wacz", U + "ultrafilms_title14_later.wacz"]


def _have(paths):
    return all(os.path.exists(p) for p in paths)


def _skip_unless(paths):
    if not _have(paths):
        pytest.skip("capture artifacts not present")


class TestLoadingAndNormalization:
    def test_loads_json_and_wacz(self):
        _skip_unless([U + "capA.json", U + "ultrafilms_title2.wacz"])
        j = ci.load_capture(U + "capA.json")
        w = ci.load_capture(U + "ultrafilms_title2.wacz")
        assert "network_log" in j and "network_log" in w

    def test_normalization_masks_every_url(self):
        _skip_unless([U + "capA.json"])
        m = ci.normalize_capture(ci.load_capture(U + "capA.json"), source_name="capA")
        # no request URL may carry a raw query value on a sensitive key
        for r in m["requests"]:
            assert ci.posture_scan(r["url"]) == [], f"raw signing value in {r['url']}"
        # the goal URL is masked too
        if m["goal_url"]:
            assert ci.posture_scan(m["goal_url"]) == []

    def test_normalization_records_capability_presence(self):
        _skip_unless([U + "capA.json"])
        m = ci.normalize_capture(ci.load_capture(U + "capA.json"))
        caps = m["capabilities"]
        # the model records each capability explicitly as bool, present or not
        assert set(caps) == {"has_responses", "has_headers", "has_initiator"}
        assert all(isinstance(v, bool) for v in caps.values())

    def test_signing_markers_are_names_only(self):
        _skip_unless([U + "capA.json"])
        m = ci.normalize_capture(ci.load_capture(U + "capA.json"))
        for r in m["requests"]:
            for mk in r["signing_markers"]:
                assert set(mk) <= {"name", "location"}  # never a 'value' key


class TestAnalysisReuse:
    def test_single_capture_uses_goal_skeleton(self):
        _skip_unless([U + "capA.json"])
        res = ci.analyze_captures([U + "capA.json"])
        a = res["per_capture"][0]["analysis"]
        assert a["identity_slots"]            # goal_skeleton found an identity slot
        assert res["temporal"] is None        # one capture: no temporal

    def test_same_identity_series_runs_temporal(self):
        _skip_unless(_SERIES)
        res = ci.analyze_captures(_SERIES)
        assert res["same_identity"] is True
        assert res["temporal"] is not None
        # identity/rendition/structural confirmed on this real series
        ax = res["temporal"]["axes"]
        assert ax["identity"]["outcome"] == "confirmed"
        assert ax["structural"]["outcome"] == "confirmed"

    def test_scrubbed_signing_is_untested_not_absent(self):
        _skip_unless(_SERIES)
        res = ci.analyze_captures(_SERIES)
        assert res["temporal"]["axes"]["signing"]["outcome"] == "untested"

    def test_perturbation_real_path_leaves_verdict_pending(self):
        _skip_unless([U + "capA.json", U + "ultrafilms_title14_later.wacz"])
        out = ci.analyze_perturbation(U + "capA.json",
                                      U + "ultrafilms_title14_later.wacz",
                                      "player_config")
        # real evidence must NOT pre-force the verdict (None = data decides)
        assert out["resolves_debt"] is None
        assert out["affects_confidence"] is None


class TestPostureBoundary:
    def test_posture_scan_flags_raw_value(self):
        assert ci.posture_scan("expires=SECRET123 token=abcd") != []

    def test_posture_scan_passes_masked(self):
        assert ci.posture_scan("expires=<masked> token=REDACTED foo=bar") == []

    def test_no_signing_value_in_any_report(self, tmp_path):
        _skip_unless(_SERIES)
        from tools.offline_capture_analyze import (
            _capture_inventory, _offline_analysis, _validation_readiness,
            _drift_report, _build_suggested_entry, _posture_verify)
        res = ci.analyze_captures(_SERIES)
        sug = _build_suggested_entry(res, None)
        reports = {
            "capture_inventory.md": _capture_inventory(res),
            "offline_analysis.md": _offline_analysis(res),
            "validation_readiness.md": _validation_readiness(res, None, sug),
            "drift_report.md": _drift_report(res, None) or "",
        }
        assert _posture_verify(reports) == []


class TestCorpusBoundary:
    def test_suggested_entry_has_no_resolves(self):
        _skip_unless(_SERIES)
        from tools.offline_capture_analyze import _build_suggested_entry
        sug = _build_suggested_entry(ci.analyze_captures(_SERIES), None)
        assert sug is not None
        assert "resolves" not in sug          # cannot retire debt

    def test_suggested_entry_is_schema_shaped(self):
        _skip_unless(_SERIES)
        from tools.offline_capture_analyze import _build_suggested_entry, _corpus_compat_check
        sug = _build_suggested_entry(ci.analyze_captures(_SERIES), None)
        compat = _corpus_compat_check(sug)
        assert compat["schema_shaped"] is True
        assert compat["retires_debt"] is False

    def test_tool_run_does_not_write_corpus(self, tmp_path):
        _skip_unless(_SERIES)
        # capture the corpus file's state, run the CLI, confirm it is untouched
        from bulk_downloader import validation_corpus as vc
        before = len(vc.load_corpus())
        from tools.offline_capture_analyze import main
        rc = main(_SERIES + ["--out", str(tmp_path)])
        assert rc == 0
        assert len(vc.load_corpus()) == before   # corpus unchanged
        # and the reviewable suggestion exists as a file, not a corpus row
        assert (tmp_path / "corpus_candidate_entry.json").exists()


class TestReportProduction:
    def test_all_expected_reports_written(self, tmp_path):
        _skip_unless(_SERIES)
        from tools.offline_capture_analyze import main
        rc = main(_SERIES + ["--out", str(tmp_path)])
        assert rc == 0
        for name in ("capture_inventory.md", "offline_analysis.md",
                     "validation_readiness.md", "drift_report.md",
                     "corpus_candidate_entry.json"):
            assert (tmp_path / name).exists(), f"missing {name}"

    def test_perturbation_triple_required_together(self, tmp_path):
        from tools.offline_capture_analyze import main
        rc = main(["--baseline", U + "capA.json", "--out", str(tmp_path)])  # no axis
        assert rc == 2  # incomplete triple is rejected
