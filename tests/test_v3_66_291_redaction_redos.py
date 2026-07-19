"""v3.66.291 — ReDoS guard for the capture export-boundary redaction scrub.

A long contiguous ``[A-Za-z0-9_-]`` DOM string (JWPlayer framework class chains,
concatenated ids, base64/data blobs) drove ``_KV_SECRET_RE`` — and the email /
userinfo patterns — into catastrophic / quadratic backtracking, pinning
``write_wacz`` at 100% CPU indefinitely (observed in the field: an ultrafilms 8K
capture whose ``capture_session.py`` hung 19+ minutes at 100% CPU, flat RSS, zero
syscalls). The fix bounds every greedy quantifier in the value detectors so the
scrub is linear-time, while preserving exact secret-matching.

These tests prove BOTH halves:
  * the scrub is linear-time on pathological input (no hang), and
  * real secrets are still redacted / flagged and structure survives
    (no coverage regression — the F2 floor is unchanged).

RED on v3.66.290 (the timing tests time out); GREEN on v3.66.291.
"""

import signal

from bulk_downloader import capture_artifact_redact as R
from bulk_downloader.wacz_export import build_wacz_bytes
from bulk_downloader.capture_redact import PLACEHOLDER


class _Timeout(Exception):
    pass


def _run_under(budget_s, fn, *a, **k):
    """Run ``fn`` under a wall-clock alarm; raise ``_Timeout`` if it overruns.

    A true ReDoS spins in the C ``re`` engine and would otherwise hang the whole
    run; the SIGALRM fires between bytecode/​interpreter checkpoints so a pure-CPU
    regex IS interruptible here. Always restores the prior handler.
    """
    def _boom(*_):
        raise _Timeout
    old = signal.signal(signal.SIGALRM, _boom)
    signal.setitimer(signal.ITIMER_REAL, budget_s)
    try:
        return fn(*a, **k)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


# Strings of the shape a real 8K / JWPlayer capture carries: long contiguous
# word-character runs (keyword-laden, base64-ish, framework class chains) with no
# clean key=value delimiter — exactly what made the old patterns backtrack.
_PATHO = [
    "abc_def_token_key_sig_session_auth" * 600,            # keyword-laden run, no '='
    "eyJ" + "AbCdEf0123456789_-" * 1500,                   # jwt-prefixed base64 run
    ("k" * 50 + "tokenkey" + "v" * 50) * 300,              # mixed kv-ish run, no '='
    "jw-icon jw-reset jw-button-color jw-display " * 2000,  # framework class chain
]

# The old bug took far longer than this (effectively unbounded); the fix is
# sub-50ms. A 2s budget is generous headroom that still fails loudly on a hang.
_BUDGET = 2.0


def test_redact_value_is_linear_on_pathological_strings():
    for s in _PATHO:
        try:
            _run_under(_BUDGET, R.redact_value, s)
        except _Timeout:
            assert False, (
                f"redact_value did not finish in {_BUDGET}s on a {len(s)}-char "
                f"string — catastrophic regex backtracking (ReDoS)")


def test_value_findings_is_linear_on_pathological_strings():
    for s in _PATHO:
        try:
            _run_under(_BUDGET, R._value_findings, s)
        except _Timeout:
            assert False, (
                f"_value_findings did not finish in {_BUDGET}s on a {len(s)}-char "
                f"string — catastrophic regex backtracking (ReDoS)")


def test_build_wacz_does_not_hang_on_pathological_capture():
    # A capture whose DOM text/cssText carries the pathological strings: the
    # export-boundary scrub (redact_capture + scan_floor_secrets) runs inside
    # build_wacz_bytes and must not hang write_wacz.
    cap = {
        "url": "https://example.com/x",
        "captured_at": "2026-01-01T00:00:00Z",
        "title": "t",
        "network_log": [],
        "dom_log": [
            {"type": "full_snapshot", "text": _PATHO[0]},
            {"type": 2, "_cssText": _PATHO[3]},
            {"type": 2, "text": _PATHO[2]},
        ],
    }
    try:
        blob = _run_under(_BUDGET, build_wacz_bytes, cap)
    except _Timeout:
        assert False, (
            f"build_wacz_bytes hung (>{_BUDGET}s) on a capture carrying a "
            f"pathological DOM string — the redaction scrub ReDoS'd")
    assert isinstance(blob, (bytes, bytearray)) and len(blob) > 0


def test_real_secrets_still_redacted_no_coverage_regression():
    # key=value secret: value scrubbed, key (selector structure) preserved.
    out_kv = R.redact_value("session_token=abc123XYZ")
    assert "abc123XYZ" not in out_kv and "session_token=" in out_kv

    # JWT: the token body is removed.
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.SECRETSIGVALUE"
    assert "SECRETSIGVALUE" not in R.redact_value(jwt)

    # email: scrubbed.
    assert "john.doe@example.com" not in R.redact_value(
        "reach me at john.doe@example.com please")

    # URL-authority userinfo: credentials stripped, host kept.
    out_ui = R.redact_value("https://user:pass@host.example.com/x")
    assert "user:pass@" not in out_ui and "host.example.com" in out_ui

    # A non-secret key is STRUCTURE and must survive verbatim.
    assert R.redact_value("color=red") == "color=red"


def test_secret_inside_a_large_string_is_still_caught():
    # Coverage: a real secret embedded in a large (realistic) DOM blob is still
    # flagged by the scanner AND scrubbed by redact_value — windowing/​bounding
    # must not let a secret slip past inside a big string.
    big = "jw-icon jw-reset " * 500
    s = big + " session_token=DEADBEEFsecret " + big
    assert "kv_secret" in R._value_findings(s)
    assert "DEADBEEFsecret" not in R.redact_value(s)

    s2 = big + " a.user@example.org " + big
    assert "email" in R._value_findings(s2)
    assert "a.user@example.org" not in R.redact_value(s2)
