"""Security pinning tests — B3 (audit-log redaction) + B20 (IDN/Punycode
hostname normalization), v3.66.40.
"""
import json

import pytest

from bulk_downloader import audit
from bulk_downloader import extension_vault as ev


# ── B3: audit-log secret redaction ──────────────────────────────────
class TestB3AuditRedaction:
    def test_pwmgr_secret_fields_redacted(self):
        before = {
            "site_id": "siteA",
            "vault_token": "vt_live_abc123",
            "pairing_token": "pair_xyz",
            "csrf_token": "csrf_deadbeef",
            "master_password": "hunter2",
            "cookies": "sessionid=secret",
            "auth_token": "bearer_zzz",       # already in explicit set
        }
        out = audit._redact(before)
        assert out["site_id"] == "siteA"                # non-secret preserved
        for k in ("vault_token", "pairing_token", "csrf_token",
                  "master_password", "cookies", "auth_token"):
            assert out[k] == "[redacted]", k

    def test_secret_value_absent_from_serialized_json(self):
        s = audit._serialize({"vault_token": "vt_live_SHOULD_NOT_LEAK"})
        assert "vt_live_SHOULD_NOT_LEAK" not in s
        assert "[redacted]" in s

    def test_empty_secret_not_marked_redacted(self):
        out = audit._redact({"password": ""})
        assert out["password"] == ""   # empty stays empty, not "[redacted]"

    def test_nested_and_listed_secrets_redacted(self):
        before = {"accounts": [{"username": "u", "password": "p"}],
                  "nested": {"api_key": "k"}}
        out = audit._redact(before)
        assert out["accounts"][0]["username"] == "u"
        assert out["accounts"][0]["password"] == "[redacted]"
        assert out["nested"]["api_key"] == "[redacted]"

    def test_innocuous_keys_not_over_redacted(self):
        out = audit._redact({"url": "https://x/y", "name": "siteA",
                             "status": "done"})
        assert out == {"url": "https://x/y", "name": "siteA",
                       "status": "done"}

    def test_is_secret_key_markers(self):
        for k in ("vault_token", "X-BD-Token", "user_password",
                  "client_secret", "session_cookie", "ai_apikey"):
            assert audit._is_secret_key(k) is True, k
        for k in ("site_id", "url", "title", "status", 42, None):
            assert audit._is_secret_key(k) is False, k


# ── B20: IDN / Punycode hostname normalization ──────────────────────
class TestB20HostnameNormalization:
    def test_ascii_host_unchanged(self):
        assert ev.get_hostname("https://login.example.com/x") == "login.example.com"

    def test_unicode_host_normalized_to_punycode(self):
        # Cyrillic 'а' (U+0430) homograph of ASCII 'a'.
        host = ev.get_hostname("https://ex\u0430mple.com/login")
        assert host.isascii()                 # normalized to ASCII form
        assert host != "example.com"          # NOT confusable with the real host
        assert host.startswith("xn--")        # punycode label

    def test_homograph_host_does_not_match_ascii_pattern(self):
        entries = [{"id": "real", "patterns": [r"example\.com"]}]
        spoof = "https://ex\u0430mple.com/login"   # Cyrillic-a lookalike
        assert ev.entries_matching_origin(spoof, entries) == []
        # sanity: the genuine ASCII host still matches
        assert [e["id"] for e in ev.entries_matching_origin(
            "https://example.com/login", entries)] == ["real"]

    def test_malformed_idn_falls_back_without_raising(self):
        # A label that the idna codec rejects must not blow up matching;
        # get_hostname returns *something* and entries_matching_origin
        # returns a list rather than raising.
        weird = "https://\u0430.\u0430\u0430\u0430/x"
        ev.get_hostname(weird)  # must not raise
        assert isinstance(
            ev.entries_matching_origin(weird, [{"id": "a", "patterns": ["x"]}]),
            list)
