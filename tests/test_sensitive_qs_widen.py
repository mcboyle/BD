"""DEC-1 — widen SENSITIVE_QS_KEY with nonce/otp/tk/ak/sk + state.

The canonical sensitive query-string key matcher is
``bulk_downloader.capture_redact.SENSITIVE_QS_KEY`` (imported by capture_bodies,
capture_ingest, capture_synth, temporal_harness, capture_artifact_redact, and
re-exported via tools/scrub_recon.py) — NOT tools/capture_scrub.py, which is a
separate standalone over-redactor with its own pattern set. (The DEC-1 text said
capture_scrub.py; the real source of truth is capture_redact.py.)

T7/210 added ``code`` and ``k`` as ANCHORED exact-match (``^(?:code|k)$``) so the
substring arm never catches geocode/zipcode/encode or any key merely containing
``k``. The new keys MUST follow the same anchoring: ``tk/ak/sk/state`` as
substrings would mask benign keys like task=, ask=, make=, estate=, statement=.

Operator decision (DEC-1): include ``state`` deliberately (OAuth/CSRF nonce class;
errs F2-safe). Spot-check, not a gate, that no template's replay URL depends on a
state= value.

Pure module test — no app, no db. Zero-arg functions per the custom runner.
"""
from __future__ import annotations

from bulk_downloader.capture_redact import redact_query, SENSITIVE_QS_KEY, PLACEHOLDER


def _masked(url, key):
    """True iff ``key``'s value is redacted to PLACEHOLDER in redact_query(url)."""
    out = redact_query(url)
    return f"{key}={PLACEHOLDER}" in out


# ── new keys: their values must be masked (exact key match) ──────────────

def test_nonce_value_masked():
    assert _masked("https://x.test/p?nonce=abc123", "nonce")


def test_otp_value_masked():
    assert _masked("https://x.test/p?otp=998877", "otp")


def test_tk_value_masked():
    assert _masked("https://x.test/p?tk=deadbeef", "tk")


def test_ak_value_masked():
    assert _masked("https://x.test/p?ak=AKIAEXAMPLE", "ak")


def test_sk_value_masked():
    assert _masked("https://x.test/p?sk=secretvalue", "sk")


def test_state_value_masked():
    # DEC-1 explicitly includes state (OAuth/CSRF nonce class).
    assert _masked("https://x.test/cb?state=xyzCSRF", "state")


def test_combined_oauth_callback_masks_all_three():
    url = "https://x.test/cb?state=S1&otp=123456&nonce=N9&foo=keepme"
    out = redact_query(url)
    assert f"state={PLACEHOLDER}" in out
    assert f"otp={PLACEHOLDER}" in out
    assert f"nonce={PLACEHOLDER}" in out
    # benign key preserved verbatim
    assert "foo=keepme" in out


# ── anchoring guard: substring look-alikes MUST be preserved ─────────────

def test_substring_lookalikes_not_masked():
    # each of these CONTAINS a new key as a substring but is a benign key
    for key, val in [
        ("task", "build"),       # contains 'tk'? no — guards 'sk'/'ak' style intent
        ("ask", "yes"),          # contains 'sk'
        ("make", "v3"),          # contains 'ak'
        ("estate", "manor"),     # contains 'state'
        ("statement", "q3"),     # contains 'state'
        ("network", "lan"),      # contains 'tk'
        ("announce", "now"),     # contains 'nonce'
        ("pronounce", "later"),  # contains 'nonce'
    ]:
        url = f"https://x.test/p?{key}={val}"
        out = redact_query(url)
        assert f"{key}={val}" in out, f"benign key '{key}' was wrongly masked: {out}"


def test_regex_anchors_new_keys_exactly():
    # direct matcher checks — exact keys hit, substring carriers miss
    for hit in ["nonce", "otp", "tk", "ak", "sk", "state"]:
        assert SENSITIVE_QS_KEY.search(hit), f"{hit} should match"
    for miss in ["ask", "task", "make", "estate", "statement", "announce"]:
        # these must NOT match via the NEW anchored keys. (They could still
        # match an unrelated substring arm in principle, but none of these
        # contain token/key/sig/secret/etc., so they must be clean.)
        assert not SENSITIVE_QS_KEY.search(miss), f"{miss} should NOT match"


# ── regression: previously-landed keys still work ───────────────────────

def test_t7_code_k_still_masked():
    assert _masked("https://x.test/p?code=ABCDEF", "code")
    assert _masked("https://x.test/p?k=tok123", "k")


def test_geocode_still_preserved():
    out = redact_query("https://x.test/p?geocode=40.7,-74.0&zipcode=10001")
    assert "geocode=40.7,-74.0" in out
    assert "zipcode=10001" in out


# ── P3-T12: challenge-RESPONSE query tokens are secrets — mask the value ──
# cf_challenge_response / __cf_chl_tk (Cloudflare) and *captcha-response
# (hCaptcha/reCaptcha) carry "you passed the challenge" tokens whose retention
# could enable replay; the redaction floor must scrub them like any signed
# token. Substring arm: 'challenge' / 'cf_chl' / 'captcha'.

def test_cf_challenge_response_masked():
    assert _masked("https://challenges.test/chl?cf_challenge_response=ABC123XYZ", "cf_challenge_response")


def test_cf_chl_tk_masked():
    assert _masked("https://challenges.test/chl?__cf_chl_tk=DEADBEEF", "__cf_chl_tk")


def test_hcaptcha_response_masked():
    assert _masked("https://x.test/verify?h-captcha-response=tok_abc", "h-captcha-response")


def test_recaptcha_response_masked():
    assert _masked("https://x.test/verify?g-recaptcha-response=tok_xyz", "g-recaptcha-response")


def test_challenge_keys_match_but_benign_lookalikes_do_not():
    for hit in ["cf_challenge_response", "__cf_chl_tk", "h-captcha-response",
                "g-recaptcha-response", "challenge_token"]:
        assert SENSITIVE_QS_KEY.search(hit), f"{hit} should match"
    # benign keys must stay clean (no 'challenge'/'cf_chl'/'captcha' substring)
    for miss in ["change", "chalk", "channel", "cha", "capture", "chapter"]:
        assert not SENSITIVE_QS_KEY.search(miss), f"{miss} should NOT match"
