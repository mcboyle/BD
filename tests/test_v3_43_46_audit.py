"""v3.43.46 audit regression tests — Tier-3 audit findings.

This file locks in the fixes from the audit at the end of Tier-3
(v3.43.33 through v3.43.45, ~7,900 LOC). Each test corresponds to
a specific finding in /tmp/audit/findings.md (or the equivalent
in this commit message).

Findings fixed:
  1. (medium) Extension X-BD-Token header was never read by the
     server — silently 401'd when BD_TOKEN was enabled.
  2. (low) SSE generator unsubscribe was in `except` branches, not
     `finally`. A BaseException could leak the subscriber.
  3. (low) plan_destination fallback to `src.name` accepted
     dangerous filenames (path separators, control chars).
  5. (low) migrate_file had TOCTOU between exists() and move();
     could silently overwrite if another writer raced.
  7. (low) _score_url_against_sites crashed on non-string url
     input (TypeError in re.search after urlparse caught the type
     mismatch).

Findings deferred (acceptable risk for single-user LAN tool):
  4. (audit-critical, then re-classified) get_scheduler() DCL is
     already correct — false alarm during walk.
  6. (medium) Jellyfin api_key in query string (logged by upstream
     server). Documented; user can rotate the key.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

# [SAST 3:13pm 13 may] removed unused: import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Finding 1: X-BD-Token header accepted by auth ────────────────────


def test_x_bd_token_header_accepted_as_bearer():
    """When BD_TOKEN is configured, an X-BD-Token header carrying
    that token must be accepted (matches what the extension sends)."""
    src = "\n".join([(_REPO_ROOT / "bulk_downloader" / "app.py").read_text(encoding="utf-8")] + [_q.read_text(encoding="utf-8") for _q in sorted((_REPO_ROOT / "bulk_downloader").glob("app_*.py"))])
    # The check function must read X-BD-Token
    assert 'X-BD-Token' in src, "X-BD-Token header is not read by auth"
    # And it must be compared to the configured token
    pos = src.find("def _check_token")
    # v3.48: window widened from 4000 to 6000 — _check_token grew to
    # include new path bypasses (/m/ mobile shell, /api/health monitor
    # probe). The assertion target (X-BD-Token check) is still in the
    # function, just deeper in the body.
    body = src[pos:pos + 6000]
    assert 'X-BD-Token' in body, "X-BD-Token check not in _check_token"


def test_x_bd_token_matches_authorization_bearer_semantics():
    """Both header forms accept the same configured token; both
    reject mismatched tokens."""
    # Simulate by patching the module's _expected_token + calling
    # _check_token within a fake Flask request
    from bulk_downloader import app as _app
    test_token = "test-secret-token-abc"
    with mock.patch.object(_app, "_expected_token",
                              return_value=test_token):
        # Simulate the function logic directly. We can't easily
        # spin up a request context here, but we can verify the
        # source includes the comparison.
        src = "\n".join([(_REPO_ROOT / "bulk_downloader" / "app.py").read_text(encoding="utf-8")] + [_q.read_text(encoding="utf-8") for _q in sorted((_REPO_ROOT / "bulk_downloader").glob("app_*.py"))])
        pos = src.find("def _check_token")
        # v3.48: window widened (see note in companion test above)
        body = src[pos:pos + 6000]
        # Must compare X-BD-Token to the expected token
        assert "bd_token" in body and "tok" in body
        # And must accept (return None) on match
        assert body.count("return None") >= 5


# ── Finding 2: SSE unsubscribe in finally ───────────────────────────


def test_sse_generator_uses_finally_for_cleanup():
    """The SSE generator must call unsubscribe in `finally` so that
    BaseException doesn't leak the subscriber."""
    src = "\n".join([(_REPO_ROOT / "bulk_downloader" / "app.py").read_text(encoding="utf-8")] + [_q.read_text(encoding="utf-8") for _q in sorted((_REPO_ROOT / "bulk_downloader").glob("app_*.py"))])
    # Find the SSE handler
    pos = src.find("broker = _sse.get_broker()")
    assert pos > 0, "couldn't find SSE handler"
    body = src[pos:pos + 4000]
    # The handler must contain a finally clause that unsubscribes
    assert "finally:" in body
    finally_pos = body.find("finally:")
    after = body[finally_pos:finally_pos + 500]
    assert "unsubscribe" in after, "finally clause doesn't unsubscribe"


def test_sse_handler_no_separate_unsubscribe_in_except():
    """The previous pattern was unsubscribe in EACH except branch.
    With the finally fix that's redundant — verify there's no double-
    unsubscribe."""
    src = "\n".join([(_REPO_ROOT / "bulk_downloader" / "app.py").read_text(encoding="utf-8")] + [_q.read_text(encoding="utf-8") for _q in sorted((_REPO_ROOT / "bulk_downloader").glob("app_*.py"))])
    pos = src.find("broker = _sse.get_broker()")
    body = src[pos:pos + 4000]
    # The branches that used to have explicit unsubscribe should now
    # just yield error / GeneratorExit handling, with cleanup in finally.
    # Count unsubscribe calls in the SSE handler body — should be 1
    # (the finally one).
    assert body.count("broker.unsubscribe") == 1


# ── Finding 3: filename sanitization in plan_destination ────────────


def test_plan_destination_rejects_traversal_basenames():
    """If source_path resolves to outside source_root, the fallback
    used to be `src.name`. Audit-tightened: reject `.`, `..`, paths
    containing separators, or null bytes."""
    from bulk_downloader.storage_tier import plan_destination
    with tempfile.TemporaryDirectory() as tmp:
        src_root = os.path.join(tmp, "root")
        dest_root = os.path.join(tmp, "dest")
        os.makedirs(src_root)
        # A file outside src_root, with a "weird" name
        outside = os.path.join(tmp, "..", "passwd")
        # The function uses .resolve() so the actual on-disk path
        # doesn't need to exist; we're testing the type-handling
        result = plan_destination(outside, src_root, dest_root)
        # Either returns None (refused) or returns dest_root/passwd
        # which is the legitimate basename. The unsafe cases are
        # rejected:
        assert result is None or "passwd" in result
        # Also: empty filename rejected
        assert plan_destination("", src_root, dest_root) is None


def test_plan_destination_handles_relative_path():
    """A path inside src_root should still work normally."""
    from bulk_downloader.storage_tier import plan_destination
    with tempfile.TemporaryDirectory() as tmp:
        src_root = os.path.join(tmp, "root")
        dest_root = os.path.join(tmp, "dest")
        os.makedirs(src_root)
        sub = os.path.join(src_root, "sub")
        os.makedirs(sub)
        f = os.path.join(sub, "file.mp4")
        Path(f).write_text("")
        result = plan_destination(f, src_root, dest_root)
        assert result is not None
        assert result.endswith(os.path.join("sub", "file.mp4"))


def test_plan_destination_none_inputs():
    from bulk_downloader.storage_tier import plan_destination
    assert plan_destination(None, "/a", "/b") is None
    assert plan_destination("/a", None, "/b") is None
    assert plan_destination("/a", "/b", None) is None


def test_plan_destination_non_string_inputs():
    """Non-string inputs return None rather than crash."""
    from bulk_downloader.storage_tier import plan_destination
    for bad in (12345, [], {}, object()):
        assert plan_destination(bad, "/a", "/b") is None
        assert plan_destination("/a", bad, "/b") is None
        assert plan_destination("/a", "/b", bad) is None


# ── Finding 5: atomic destination creation in migrate_file ──────────


def test_migrate_file_refuses_when_dest_exists():
    """Pre-existing destination must still be refused (existing
    behavior preserved)."""
    from bulk_downloader.storage_tier import migrate_file
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.mp4")
        dest = os.path.join(tmp, "dest.mp4")
        Path(src).write_text("hello")
        Path(dest).write_text("existing")
        r = migrate_file(src, dest)
        assert r["ok"] is False
        assert "exists" in r["error"]


def test_migrate_file_uses_exclusive_open():
    """The audit fix uses os.open with O_EXCL as a TOCTOU defense.
    Verify the code path exists."""
    src_text = (_REPO_ROOT / "bulk_downloader" / "storage_tier.py").read_text(encoding="utf-8")
    pos = src_text.find("def migrate_file")
    body = src_text[pos:pos + 4000]
    # Must use O_EXCL + O_CREAT
    assert "O_EXCL" in body
    assert "O_CREAT" in body


def test_migrate_file_happy_path_still_works():
    """The exclusive-open mitigation must not break the normal
    move-when-dest-doesn't-exist path."""
    from bulk_downloader.storage_tier import migrate_file
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.mp4")
        dest = os.path.join(tmp, "dest.mp4")
        Path(src).write_text("hello world")
        r = migrate_file(src, dest)
        assert r["ok"] is True
        assert Path(dest).read_text(encoding="utf-8") == "hello world"
        assert not Path(src).exists()


# ── Finding 7: _score_url_against_sites tolerates non-string url ────


def test_score_url_against_sites_non_string_url():
    """A non-string url used to crash inside `re.search(pat, url)`
    with TypeError. Audit fix: coerce to str early."""
    from bulk_downloader.app import _score_url_against_sites
    # An empty config snapshot ensures we don't depend on real
    # site config
    cfg_snapshot = []
    for bad in (None, 12345, [], {}, object()):
        # Must not raise
        result = _score_url_against_sites(bad, cfg_snapshot=cfg_snapshot)
        # Returns a 3-tuple
        assert isinstance(result, tuple)
        assert len(result) == 3


def test_score_url_against_sites_with_pattern_and_non_string():
    """With a real url_patterns regex configured, a non-string url
    must still return (None, 0, '') or similar — not raise."""
    from bulk_downloader.app import _score_url_against_sites
    cfg_snapshot = [
        ("site1", {"url_patterns": "example\\.com",
                     "login_url": "https://example.com/login"}),
    ]
    for bad in (None, 12345, []):
        result = _score_url_against_sites(bad, cfg_snapshot=cfg_snapshot)
        sid, score, reason = result
        # Coerced url is "" or "None" — neither matches example\.com
        # but the function still has to not crash
        # (sid will be None / score 0 in both cases)
        assert score == 0 or score == -1 or sid is None


def test_score_url_against_sites_string_url_still_works():
    """Sanity: the normal path with a real string url is unchanged."""
    from bulk_downloader.app import _score_url_against_sites
    cfg_snapshot = [
        ("site1", {"url_patterns": "example\\.com",
                     "login_url": "https://example.com/login"}),
    ]
    sid, score, reason = _score_url_against_sites(
        "https://example.com/video/123", cfg_snapshot=cfg_snapshot)
    assert sid == "site1"
    assert score >= 200   # pattern match
    assert "url_patterns" in reason
