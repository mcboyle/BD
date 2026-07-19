"""v3.66.13 — P11 tests for tools/dd-score-audit.py.

Exercises the audit's main code paths:

  * _collect picks up every fixture and runs deep_detect on it.
  * _audit produces histogram, gaps, collisions, outliers
    with the shapes downstream tooling will rely on.
  * main() text mode and --json mode behave consistently.
  * --strict turns outliers into a non-zero exit.
  * --only filter actually narrows scope.

Like the other Phase-2 tests, this needs the bd_module_wipe marker
because deep_detect's internal state should be fresh per test.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.bd_module_wipe


REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_PATH = REPO_ROOT / "tools" / "dd-score-audit.py"


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "dd_score_audit", AUDIT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── _collect / _audit shape ──────────────────────────────────────────


def test_collect_returns_one_entry_per_fixture():
    audit_mod = _load_audit()
    reports = audit_mod._collect(only=None)
    # The committed corpus has at least 5 fixtures.
    assert len(reports) >= 5
    # Every value is a deep_detect-shaped dict.
    for name, report in reports.items():
        assert isinstance(report, dict)
        # download_candidates always present in a valid report
        assert "buckets" in report


def test_audit_histogram_shape():
    audit_mod = _load_audit()
    reports = audit_mod._collect(only=None)
    audit = audit_mod._audit(reports)

    # Histogram has one entry per source_type observed
    assert audit["histogram"], "expected at least one source_type"
    for source_type, stats in audit["histogram"].items():
        assert set(stats.keys()) == {
            "count", "min", "max", "median", "mean", "distinct"
        }
        assert stats["count"] >= 1
        assert stats["min"] <= stats["median"] <= stats["max"]
        assert stats["distinct"] == sorted(set(stats["distinct"]))


def test_audit_gaps_only_for_multi_candidate_fixtures():
    audit_mod = _load_audit()
    reports = audit_mod._collect(only=None)
    audit = audit_mod._audit(reports)
    # Gap entries exist only for fixtures with ≥2 download candidates
    for g in audit["gaps"]:
        report = reports[g["fixture"]]
        assert len(report["buckets"]["accepted"]) >= 2
        assert g["gap"] == g["best_score"] - g["next_score"]
        assert g["gap"] >= 0  # candidates are sorted desc by score


def test_audit_collisions_have_multiple_source_types():
    audit_mod = _load_audit()
    reports = audit_mod._collect(only=None)
    audit = audit_mod._audit(reports)
    # If any collisions are flagged, each must involve ≥2 distinct
    # source_types (else they're not collisions, they're within-type ties).
    for c in audit["collisions"]:
        assert len(c["types"]) >= 2


def test_audit_outliers_respect_thresholds():
    audit_mod = _load_audit()
    reports = audit_mod._collect(only=None)
    audit = audit_mod._audit(reports)
    floor = audit["thresholds"]["floor"]
    ceiling = audit["thresholds"]["ceiling"]
    for o in audit["outliers"]["floor"]:
        assert o["score"] <= floor
    for o in audit["outliers"]["ceiling"]:
        assert o["score"] > ceiling


# ── main() exit codes + output modes ─────────────────────────────────


def _run_main(audit_mod, monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["dd-score-audit"] + list(argv))
    try:
        rc = audit_mod.main(list(argv))
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_main_text_mode_exits_0(monkeypatch, capsys):
    audit_mod = _load_audit()
    rc, out, _ = _run_main(audit_mod, monkeypatch, capsys)
    assert rc == 0
    # Headings the human report always emits
    assert "Score histogram by source_type" in out
    assert "best vs runner-up gap" in out
    assert "Same-score collisions" in out
    assert "Outliers" in out


def test_main_json_mode_is_valid_json(monkeypatch, capsys):
    audit_mod = _load_audit()
    rc, out, _ = _run_main(audit_mod, monkeypatch, capsys, "--json")
    assert rc == 0
    payload = json.loads(out)
    assert payload["corpus_size"] >= 5
    assert "histogram" in payload
    assert "gaps" in payload
    assert "collisions" in payload
    assert "outliers" in payload
    assert "thresholds" in payload


def test_strict_exits_1_on_synthetic_outlier(monkeypatch, capsys):
    """Make _collect return a synthetic report with a score above the
    ceiling; --strict should turn that into exit 1."""
    audit_mod = _load_audit()
    fake_reports = {
        "synthetic_high": {
            "download_candidates": [
                {"source_type": "hls_manifest", "score": 9999,
                 "url": "https://x.example/a.m3u8"},
            ],
        },
    }
    monkeypatch.setattr(audit_mod, "_collect",
                        lambda only: fake_reports)
    rc, _, _ = _run_main(audit_mod, monkeypatch, capsys, "--strict")
    assert rc == 1


def test_strict_without_outliers_exits_0(monkeypatch, capsys):
    audit_mod = _load_audit()
    fake_reports = {
        "synthetic_ok": {
            "download_candidates": [
                {"source_type": "hls_manifest", "score": 100,
                 "url": "https://x.example/a.m3u8"},
            ],
        },
    }
    monkeypatch.setattr(audit_mod, "_collect",
                        lambda only: fake_reports)
    rc, _, _ = _run_main(audit_mod, monkeypatch, capsys, "--strict")
    assert rc == 0


def test_only_filter_narrows_corpus(monkeypatch, capsys):
    audit_mod = _load_audit()
    rc, out, _ = _run_main(audit_mod, monkeypatch, capsys,
                           "--only", "01_hls_master", "--json")
    assert rc == 0
    payload = json.loads(out)
    assert payload["corpus_size"] == 1


def test_corpus_missing_exits_2(monkeypatch, capsys):
    audit_mod = _load_audit()
    monkeypatch.setattr(audit_mod, "_collect",
                        lambda only: (_ for _ in ()).throw(
                            FileNotFoundError("no corpus at /nowhere")))
    rc, _, err = _run_main(audit_mod, monkeypatch, capsys)
    assert rc == 2
    assert "no corpus" in err
