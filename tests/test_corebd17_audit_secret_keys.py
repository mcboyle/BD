"""RED-first guard for F-COREBD17-03.

audit-log redaction (``audit._is_secret_key`` / ``_redact``) must classify the
bare-name I0008 secret-keyword floor -- authorization/code/state/challenge/
captcha/nonce/otp/csrf/bearer/session/jwt -- as secret, not just the keys that
happen to contain a hand-maintained ``_SECRET_KEY_MARKERS`` substring. The fix
sources the floor from the single canonical SoT
``capture_artifact_redact._kv_key_is_secret`` so the two can never drift.

Pre-fix: the floor keys fall through the marker tuple and their values are
written to the audit before/after columns in plaintext -> these tests fail.
"""
from bulk_downloader import audit as A

# Bare-name floor keys the hand-maintained marker tuple misses.
_FLOOR_KEYS = ["authorization", "code", "state", "challenge", "captcha",
               "nonce", "otp", "csrf", "bearer", "session", "jwt"]


def test_is_secret_key_covers_i0008_floor():
    missed = [k for k in _FLOOR_KEYS if not A._is_secret_key(k)]
    assert not missed, f"audit._is_secret_key misses I0008 floor keys: {missed}"


def test_redact_strips_bare_name_secret_values():
    rec = {k: f"LEAK-{k}" for k in _FLOOR_KEYS}
    out = A._redact(rec)
    leaked = {k: out[k] for k in _FLOOR_KEYS if out[k] not in ("[redacted]", "")}
    assert not leaked, f"audit._redact leaked bare-name secrets: {leaked}"


def test_redact_does_not_over_redact_benign_keys():
    # Control: genuinely non-secret keys must survive unchanged -- fail-closed
    # must not degrade into redact-everything.
    rec = {"url": "https://example.com/keep", "count": 7, "note": "done"}
    out = A._redact(rec)
    assert out == {"url": "https://example.com/keep", "count": 7, "note": "done"}


def test_existing_marker_coverage_not_regressed():
    # Regression guard: pre-existing marker coverage must still hold.
    rec = {"plex_token": "T", "qb_password": "P", "session_cookie": "C"}
    out = A._redact(rec)
    assert out == {"plex_token": "[redacted]", "qb_password": "[redacted]",
                   "session_cookie": "[redacted]"}
