"""RED-first guard for F-COCKPIT02-02.

cockpit_core.posture_clean() returned [] (== CLEAN) on ANY exception, so a
scanner error / unavailability silently disabled the output-artifact leak
quarantine -- callers treat [] as leak-free and surface the artifact. The fix
fails CLOSED: on a scanner error it returns a non-empty sentinel so callers
quarantine the artifact instead of passing it through unscanned.

Uses a manual try/finally attribute patch (run_tests.py-safe -- no monkeypatch
fixture). posture_clean's lazy `from capture_ingest import posture_scan`
resolves the module attribute at call time, so patching it takes effect.

Pre-fix: the except branch returns [] -> this fails.
"""
from tools import cockpit_core as C
import bulk_downloader.capture_ingest as CI


def test_posture_clean_fails_closed_on_scanner_error():
    orig = CI.posture_scan

    def _boom(_text):
        raise RuntimeError("scanner unavailable")

    CI.posture_scan = _boom
    try:
        result = C.posture_clean("any text that should be scanned")
    finally:
        CI.posture_scan = orig
    assert result, ("posture_clean must fail CLOSED (return a non-empty leak "
                    "sentinel) when the scanner errors, so callers quarantine")


def test_posture_clean_passes_through_scanner_verdict():
    # Control: when the scanner works, posture_clean returns its verdict verbatim
    # -- the fix must fail closed ONLY on error, not always.
    orig = CI.posture_scan
    CI.posture_scan = lambda _t: []
    try:
        assert C.posture_clean("clean text") == []
    finally:
        CI.posture_scan = orig
    CI.posture_scan = lambda _t: ["leak:token"]
    try:
        assert C.posture_clean("leaky text") == ["leak:token"]
    finally:
        CI.posture_scan = orig
