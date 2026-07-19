"""RED-first guard for F-TOOLSO04-01.

capture_scrub's redact-then-verify-then-refuse backstop had a coverage hole: an
all-ALPHABETIC or all-NUMERIC high-entropy token of length >= token_min evaded
BOTH the scrub masker (which required a digit AND a letter) AND the independent
scan_residual verify pass (which had no opaque check at all), so it survived
redaction and passed the CLEAN verify -- defeating the tool's 'proven clean'
guarantee. The fix adds a Shannon-entropy opaque predicate so the masker redacts
such a token and the verifier flags any residual, WITHOUT masking benign
structured text (CamelCase / long class names / words stay below the bar).

Pre-fix: the all-alpha token survives the masker AND scan_residual doesn't flag
it -> these fail. Benign controls pass both before and after.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "tools"))
import capture_scrub as cs  # noqa: E402

# 29-char all-ALPHA, all-distinct (max-entropy) token, no digit -- the evasion class
_ALPHA_TOK = "AbcdefghijklmnopqrstuvwxyzXYZ"
# 26-char all-NUMERIC run, no letter (entropy can't exceed ~3.3, so length-gated)
_NUM_TOK = "80412739650182734906581273"


def test_masker_redacts_all_alpha_high_entropy_token():
    out = cs.scrub_string(_ALPHA_TOK, "safe", 24)
    assert _ALPHA_TOK not in out, f"all-alpha high-entropy token survived masker: {out}"


def test_masker_redacts_all_numeric_long_run():
    out = cs.scrub_string(_NUM_TOK, "safe", 24)
    assert _NUM_TOK not in out, f"all-numeric long run survived masker: {out}"


def test_scan_residual_flags_residual_opaque_token():
    # if such a token somehow survives into the output, the INDEPENDENT verify
    # pass must flag it so the tool refuses (exit 2) instead of writing CLEAN.
    hits = cs.scan_residual({"note": _ALPHA_TOK})
    assert any("opaque" in k for _, k in hits), f"verifier missed opaque token: {hits}"


# ── benign structured strings must NOT be over-masked/over-flagged ───────

def test_benign_camelcase_preserved():
    cam = "VideoThumbnailSummaryComponent"          # entropy ~4.06, below the bar
    assert cam in cs.scrub_string(cam, "safe", 24), "benign CamelCase wrongly masked"
    assert cs.scan_residual({"comp": cam}) == [], "benign CamelCase wrongly flagged"


def test_benign_short_and_ua_preserved():
    # short values and UA version strings are untouched (regression)
    assert cs.scan_residual({"ct": "text/html"}) == []
    ua = "Mozilla Chrome/137.0.0.0 Safari"
    out = cs.scrub_string(ua, "safe", 24)
    assert "Mozilla" in out and "Safari" in out


def test_mixed_digit_alpha_token_still_masked():
    mixed = "abc123DEF456ghi789JKL012mno"            # existing behaviour preserved
    assert mixed not in cs.scrub_string(mixed, "safe", 24)
