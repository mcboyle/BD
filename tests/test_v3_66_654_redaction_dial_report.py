"""v3.66.654 -- S3.3 (X-RED-1): redaction-dial report dev-loop gate.

tools/redaction_dial_report.py reports how the active redaction profile deviates from
safe defaults + flags reduced_redaction (captures local-only). Advisory; --check fails
when the dial is reduced below safe. No runtime change.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from bulk_downloader import redaction_profile as rp

_p = Path(__file__).resolve().parent.parent / "tools" / "redaction_dial_report.py"
_spec = importlib.util.spec_from_file_location("redaction_dial_report", _p)
rdr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rdr)


def test_dial_delta_reports_only_differences():
    defaults = {"a": "safe", "b": "safe", "c": "safe"}
    profile = {"a": "safe", "b": "keep", "c": "safe"}
    deltas = rdr.dial_delta(profile, defaults)
    assert deltas == [{"category": "b", "active": "keep", "default": "safe"}], deltas


def test_build_report_safe_defaults_not_reduced():
    prof = dict(rp._DEFAULTS)
    rep = rdr.build_report(profile=prof, defaults=dict(rp._DEFAULTS))
    assert rep["reduced_redaction"] is False, rep
    assert rep["deltas"] == [], rep


def test_build_report_reduced_when_url_kept():
    prof = dict(rp._DEFAULTS)
    prof["network_signed_urls"] = rp.KEEP_FULL   # dial down below safe
    rep = rdr.build_report(profile=prof, defaults=dict(rp._DEFAULTS))
    assert rep["reduced_redaction"] is True, rep
    cats = {d["category"] for d in rep["deltas"]}
    assert "network_signed_urls" in cats, rep


def test_main_check_fails_only_when_reduced():
    orig = rdr.build_report
    rdr.build_report = lambda *a, **k: {
        "profile": {}, "defaults": {}, "deltas": [], "reduced_redaction": True}
    try:
        assert rdr.main(["--check"]) == 1
        rdr.build_report = lambda *a, **k: {
            "profile": {}, "defaults": {}, "deltas": [], "reduced_redaction": False}
        assert rdr.main(["--check"]) == 0
        assert rdr.main([]) == 0
    finally:
        rdr.build_report = orig


def test_live_build_report_runs():
    # Smoke: resolves the real profile + defaults without raising.
    rep = rdr.build_report()
    assert set(rep) >= {"profile", "defaults", "deltas", "reduced_redaction"}
