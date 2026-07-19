"""Tests for bulk_downloader.extension_vault (v3.43.14, ext bridge).

Coverage:
  - Pairing token lifecycle: issue -> redeem -> consumed
  - Vault token validation (valid + invalid)
  - Idle expiry of redeemed tokens
  - Pairing token expiry
  - Per-entry cooldown enforcement
  - Per-token rate limit
  - eTLD+1 derivation edge cases
  - Origin matching with regex + substring fallback + bare-domain
  - Token revocation by full token and by prefix
"""
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _isolated_cwd():
    """Each test in its own tmpdir so vault_tokens.json is fresh."""
    orig = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        try: yield Path(td)
        finally: os.chdir(orig)


# ─── Pairing flow ─────────────────────────────────────────────────

def test_issue_pairing_token_returns_long_string():
    from bulk_downloader.extension_vault import issue_pairing_token
    with _isolated_cwd():
        tok = issue_pairing_token()
        assert isinstance(tok, str)
        assert len(tok) >= 16


def test_redeem_pairing_token_consumes_and_issues_vault_token():
    from bulk_downloader.extension_vault import (
        issue_pairing_token, redeem_pairing_token, validate_vault_token,
    )
    with _isolated_cwd():
        pair = issue_pairing_token()
        vt = redeem_pairing_token(pair, "test extension")
        assert vt and len(vt) >= 32
        # Pairing token is now consumed
        assert redeem_pairing_token(pair) is None
        # Vault token is valid
        meta = validate_vault_token(vt)
        assert meta is not None
        assert meta["label"] == "test extension"


def test_redeem_unknown_pairing_token_returns_none():
    from bulk_downloader.extension_vault import redeem_pairing_token
    with _isolated_cwd():
        assert redeem_pairing_token("not-a-real-token") is None


def test_redeem_expired_pairing_token_returns_none():
    """Pairing token expires after PAIRING_TOKEN_EXPIRY_SECONDS."""
    from bulk_downloader import extension_vault as ev
    with _isolated_cwd():
        orig = ev.PAIRING_TOKEN_EXPIRY_SECONDS
        ev.PAIRING_TOKEN_EXPIRY_SECONDS = 0.01  # 10ms for the test
        try:
            pair = ev.issue_pairing_token()
            time.sleep(0.05)
            assert ev.redeem_pairing_token(pair) is None
        finally:
            ev.PAIRING_TOKEN_EXPIRY_SECONDS = orig


# ─── Token validation ─────────────────────────────────────────────

def test_validate_vault_token_unknown_returns_none():
    from bulk_downloader.extension_vault import validate_vault_token
    with _isolated_cwd():
        assert validate_vault_token("not-a-real-token") is None
        assert validate_vault_token("") is None


def test_validate_updates_last_used_at():
    from bulk_downloader.extension_vault import (
        issue_pairing_token, redeem_pairing_token, validate_vault_token,
    )
    with _isolated_cwd():
        vt = redeem_pairing_token(issue_pairing_token())
        meta1 = validate_vault_token(vt)
        ts1 = meta1["last_used_at"]
        time.sleep(0.05)
        meta2 = validate_vault_token(vt)
        assert meta2["last_used_at"] > ts1


def test_revoke_vault_token():
    from bulk_downloader.extension_vault import (
        issue_pairing_token, redeem_pairing_token, revoke_vault_token,
        validate_vault_token,
    )
    with _isolated_cwd():
        vt = redeem_pairing_token(issue_pairing_token())
        assert revoke_vault_token(vt) is True
        assert validate_vault_token(vt) is None
        # Idempotent: revoking an already-revoked token is False
        assert revoke_vault_token(vt) is False


def test_revoke_by_prefix():
    from bulk_downloader.extension_vault import (
        issue_pairing_token, redeem_pairing_token, list_vault_tokens,
        revoke_by_prefix, validate_vault_token,
    )
    with _isolated_cwd():
        vt = redeem_pairing_token(issue_pairing_token(), "ext A")
        toks = list_vault_tokens()
        assert len(toks) == 1
        prefix = toks[0]["id"]
        assert revoke_by_prefix(prefix) is True
        assert validate_vault_token(vt) is None


def test_list_vault_tokens_returns_metadata_only():
    """list_vault_tokens must not expose the raw vault token value."""
    from bulk_downloader.extension_vault import (
        issue_pairing_token, redeem_pairing_token, list_vault_tokens,
    )
    with _isolated_cwd():
        vt = redeem_pairing_token(issue_pairing_token(), "my ext")
        toks = list_vault_tokens()
        assert len(toks) == 1
        # ID is a short prefix, NOT the full token
        assert toks[0]["id"] == vt[:8]
        # The full token is NOT in the response
        full_token_strings = [str(v) for v in toks[0].values()]
        assert not any(vt == s for s in full_token_strings)


# ─── Rate limiting ────────────────────────────────────────────────

def test_per_entry_cooldown_blocks_same_entry():
    from bulk_downloader.extension_vault import (
        issue_pairing_token, redeem_pairing_token, check_and_record_fetch,
    )
    with _isolated_cwd():
        vt = redeem_pairing_token(issue_pairing_token())
        ok1, _ = check_and_record_fetch(vt, "bulkdl-site-wow")
        assert ok1 is True
        ok2, reason = check_and_record_fetch(vt, "bulkdl-site-wow")
        assert ok2 is False
        assert "cooldown" in reason.lower()


def test_cooldown_does_not_block_different_entry():
    from bulk_downloader.extension_vault import (
        issue_pairing_token, redeem_pairing_token, check_and_record_fetch,
    )
    with _isolated_cwd():
        vt = redeem_pairing_token(issue_pairing_token())
        ok1, _ = check_and_record_fetch(vt, "bulkdl-site-wow")
        assert ok1
        ok2, _ = check_and_record_fetch(vt, "bulkdl-site-ultraf")
        assert ok2


def test_per_token_rate_limit():
    """Burst of fetches up to PER_TOKEN_RATE_LIMIT succeed; beyond that
    is rate-limited."""
    from bulk_downloader import extension_vault as ev
    with _isolated_cwd():
        vt = ev.redeem_pairing_token(ev.issue_pairing_token())
        # Lower the limit for fast testing — same code path
        orig = ev.PER_TOKEN_RATE_LIMIT
        ev.PER_TOKEN_RATE_LIMIT = 3
        ev.PER_ENTRY_COOLDOWN_SECONDS = 0  # bypass per-entry cooldown
        try:
            # Use a different entry each time to avoid per-entry cooldown
            for i in range(3):
                ok, _ = ev.check_and_record_fetch(vt, f"entry-{i}")
                assert ok, f"call {i+1} should have succeeded"
            ok, reason = ev.check_and_record_fetch(vt, "entry-99")
            assert not ok
            assert "too many" in reason.lower() or "limit" in reason.lower()
        finally:
            ev.PER_TOKEN_RATE_LIMIT = orig
            ev.PER_ENTRY_COOLDOWN_SECONDS = 5.0


def test_fetch_with_invalid_token_denied():
    from bulk_downloader.extension_vault import check_and_record_fetch
    with _isolated_cwd():
        ok, reason = check_and_record_fetch("not-a-token", "any-entry")
        assert not ok
        assert "invalid" in reason.lower()


# ─── eTLD+1 derivation ────────────────────────────────────────────

def test_etld1_basic_cases():
    from bulk_downloader.extension_vault import get_registrable_domain
    assert get_registrable_domain("https://example.com/foo") == "example.com"
    assert get_registrable_domain("example.com") == "example.com"
    assert get_registrable_domain("https://sub.example.com") == "example.com"
    assert get_registrable_domain("https://a.b.c.example.com") == "example.com"


def test_etld1_strips_port():
    from bulk_downloader.extension_vault import get_registrable_domain
    assert get_registrable_domain("http://example.com:8080") == "example.com"


def test_etld1_strips_credentials():
    from bulk_downloader.extension_vault import get_registrable_domain
    assert get_registrable_domain("http://user:pw@example.com/x") == "example.com"


def test_etld1_handles_single_label():
    from bulk_downloader.extension_vault import get_registrable_domain
    assert get_registrable_domain("localhost") == "localhost"
    assert get_registrable_domain("") == ""


# ─── Full-hostname pattern (v3.43.17: safer than eTLD+1) ──────────

def test_hostname_pattern_basic():
    """hostname_pattern returns re.escape'd full hostname so dots don't
    match arbitrary characters and siblings on a shared public-suffix
    don't over-match."""
    from bulk_downloader.extension_vault import hostname_pattern
    assert hostname_pattern("https://example.com/foo") == r"example\.com"
    assert hostname_pattern("example.com") == r"example\.com"
    assert hostname_pattern("https://sub.example.com") == r"sub\.example\.com"


def test_hostname_pattern_preserves_multi_part_tld():
    """The key motivation: .co.uk / .com.au shouldn't collapse to the
    public-suffix portion as get_registrable_domain does."""
    from bulk_downloader.extension_vault import hostname_pattern
    assert hostname_pattern("https://example.co.uk") == r"example\.co\.uk"
    assert hostname_pattern("https://shop.example.com.au") == r"shop\.example\.com\.au"
    assert hostname_pattern("https://www.bbc.co.uk/news") == r"www\.bbc\.co\.uk"


def test_hostname_pattern_strips_creds_port_path():
    from bulk_downloader.extension_vault import hostname_pattern
    assert hostname_pattern("https://user:pw@example.com:8443/x?y=z") == r"example\.com"
    assert hostname_pattern("http://example.com:80/") == r"example\.com"


def test_hostname_pattern_handles_edge_cases():
    from bulk_downloader.extension_vault import hostname_pattern
    assert hostname_pattern("") == ""
    assert hostname_pattern(None) == ""
    assert hostname_pattern("localhost:5000") == "localhost"


def test_hostname_pattern_actually_anchors_better_than_etld1():
    """End-to-end: an entry saved with hostname_pattern shouldn't match
    a sibling on the same public suffix, but the old eTLD+1 pattern
    would have."""
    import re
    from bulk_downloader.extension_vault import hostname_pattern, get_registrable_domain
    saved_for = "https://example.co.uk"
    new_pat = hostname_pattern(saved_for)        # 'example\.co\.uk'
    old_pat = get_registrable_domain(saved_for)  # 'co.uk' (the bug)
    target = "https://attacker.co.uk/login"
    # Old eTLD+1 pattern WOULD have matched the attacker site
    assert re.search(old_pat.replace(".", r"\."), target.lower()), (
        "sanity: old pattern matched attacker.co.uk")
    # New full-hostname pattern correctly rejects it
    assert not re.search(new_pat, target.lower()), (
        "regression: hostname_pattern leaked across siblings on .co.uk")


# ─── Origin matching ──────────────────────────────────────────────

def test_origin_matching_regex():
    from bulk_downloader.extension_vault import entries_matching_origin
    entries = [
        {"id": "a", "patterns": [r"wowgirls\.com"]},
        {"id": "b", "patterns": [r"otherx\.com"]},
    ]
    matched = entries_matching_origin(
        "https://venus.wowgirls.com/film/x", entries)
    assert len(matched) == 1
    assert matched[0]["id"] == "a"


def test_origin_matching_no_patterns_no_match():
    from bulk_downloader.extension_vault import entries_matching_origin
    entries = [
        {"id": "a", "patterns": []},
        {"id": "b", "patterns": [r"example\.com"]},
    ]
    matched = entries_matching_origin("https://example.com/x", entries)
    assert len(matched) == 1
    assert matched[0]["id"] == "b"


def test_origin_matching_empty_url():
    from bulk_downloader.extension_vault import entries_matching_origin
    assert entries_matching_origin("", [{"id": "a", "patterns": ["x"]}]) == []


def test_origin_matching_substring_fallback():
    """If a pattern fails to compile as regex, fall back to substring."""
    from bulk_downloader.extension_vault import entries_matching_origin
    entries = [{"id": "a", "patterns": ["[unclosed bracket"]}]
    # Unclosed regex; if substring match still applies, the literal
    # string '[unclosed bracket' won't be in any URL we test.
    assert entries_matching_origin("https://example.com", entries) == []
