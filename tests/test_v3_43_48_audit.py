"""v3.43.48 audit regression tests — final all-LOC pass.

The fourth and final audit milestone, closing the gap on modules
that had never been systematically walked: login.py, backup.py,
resume.py, plex_deep.py, stash_deep.py, watch_folder.py, db.py,
user_templates.py, push.py, ui_events.py, rate_limit.py,
selftest.py, session_keeper.py, aiassist.py (re-pass),
integrity.py, all extension JS, and templates/index.html.

After this pass every LOC in the codebase has had at least one
systematic audit.

Findings fixed:
  16. (low-med) backup.py zip extraction had no per-member or total
      size cap. Zip bomb could decompress to TB. Added 256MB
      per-member + 10GB total caps.
  17. (low-med) user_templates.py let users save regex patterns
      of arbitrary length. Patterns like '(a+)+$' compile fine
      then hang routing calls (ReDoS). Added 500-char cap.
  18. (low) ui_events.py redaction needle list missed api_key,
      apikey, bearer, private_key, secret_key, credential,
      passphrase variants.
  19. (med) extension/options.js interpolated server-supplied
      r.label / r.error into innerHTML without escape.
  20. (med) extension/options.js interpolated silent_origins
      strings into innerHTML + data-* attributes without escape.

Deferred:
  21. (low) No CSP header on index.html; 112 inline event
      handlers. Future hardening item — same-origin app is the
      primary defense.
"""
from __future__ import annotations

import json
# [SAST 3:13pm 13 may] removed unused: import os
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
# [SAST 3:13pm 13 may] removed unused: from unittest import mock

# [SAST 3:13pm 13 may] removed unused: import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Finding 16: zip-bomb defense in restore ────────────────────────


def test_restore_caps_member_size():
    """A zip with a single huge member must be rejected (or skipped)
    rather than read entirely into memory."""
    from bulk_downloader.backup import restore_backup
    with tempfile.TemporaryDirectory() as tmp:
        bad_zip = Path(tmp) / "bomb.zip"
        # Build a zip with one large member — uncompressed declared
        # size is what we check. 300MB > 256MB cap.
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            # Don't actually write 300MB; we just need a member that
            # decompresses to > MAX_MEMBER_BYTES. The cap check is
            # the early `info.file_size > MAX_MEMBER_BYTES` branch.
            # Simulate by writing a file then patching file_size on
            # the ZipInfo. Simpler: write a 300MB-of-zeros member.
            # That's actually big — use 257MB to be just over the cap.
            data = b"\x00" * (257 * 1024 * 1024)
            zf.writestr("huge.bin", data)
        bad_zip.write_bytes(buf.getvalue())
        target = Path(tmp) / "restore_target"
        result = restore_backup(bad_zip, target_dir=target, dry_run=True)
        # Restore returns ok=True but with warnings about the skipped
        # member, not OOM
        assert result["ok"] is True
        warnings = " ".join(result.get("warnings", []))
        assert "oversized" in warnings.lower() or "exceed" in warnings.lower()


def test_restore_normal_size_still_works():
    """A small legitimate backup still restores cleanly after the cap."""
    from bulk_downloader.backup import create_backup, restore_backup
    with tempfile.TemporaryDirectory() as tmp:
        # Create a small backup
        src = Path(tmp) / "source"
        src.mkdir()
        (src / "sites_config.json").write_text(json.dumps({"site1": {"name": "Test"}}))
        backup_path = Path(tmp) / "backup.zip"
        # Use direct zipfile rather than the create_backup API (which
        # has its own quirks). Build a minimal valid bd backup.
        with zipfile.ZipFile(backup_path, "w") as zf:
            zf.writestr("_manifest.json", json.dumps({
                "bd_version": "v3.43.48", "created_at_iso": "2026-05-12T00:00:00"}))
            zf.writestr("sites_config.json", json.dumps({"site1": {"name": "Test"}}))
        target = Path(tmp) / "restored"
        r = restore_backup(backup_path, target_dir=target)
        assert r["ok"] is True
        assert r["files_restored"] >= 1
        assert (target / "sites_config.json").exists()


def test_restore_has_total_size_cap():
    """Source check: MAX_TOTAL_BYTES constant present in restore code."""
    src = (_REPO_ROOT / "bulk_downloader" / "backup.py").read_text(encoding="utf-8")
    assert "MAX_TOTAL_BYTES" in src
    assert "MAX_MEMBER_BYTES" in src
    # Per-member is 256MB; total is 10GB
    assert "256 * 1024 * 1024" in src
    assert "10 * 1024 * 1024 * 1024" in src


# ── Finding 17: regex pattern length cap ───────────────────────────


def test_user_template_rejects_long_pattern():
    """A 600-char regex pattern must be rejected at save-time before
    it can be applied to every routing call."""
    from bulk_downloader.user_templates import _validate_pattern
    long_pat = "a" * 600
    ok, msg = _validate_pattern(long_pat)
    assert ok is False
    assert "too long" in msg.lower() or "500" in msg


def test_user_template_accepts_normal_pattern():
    from bulk_downloader.user_templates import _validate_pattern
    ok, _ = _validate_pattern("example\\.com")
    assert ok is True


def test_user_template_rejects_invalid_regex():
    """Original behavior preserved — invalid regex still rejected."""
    from bulk_downloader.user_templates import _validate_pattern
    ok, msg = _validate_pattern("(unclosed group")
    assert ok is False


def test_user_template_rejects_non_string_pattern():
    """Defense in depth — non-string inputs are rejected cleanly."""
    from bulk_downloader.user_templates import _validate_pattern
    for bad in (None, 12345, [], {}):
        ok, _ = _validate_pattern(bad)
        assert ok is False


# ── Finding 18: ui_events expanded redaction needles ──────────────


def test_ui_events_redacts_api_key():
    """A storage_write event with key='api_key' must be redacted."""
    from bulk_downloader.ui_events import _redact
    event = {
        "category": "storage_write",
        "data": {"key": "api_key", "value": "sk-secret-12345"},
    }
    redacted = _redact(event)
    assert redacted["data"]["value"] == "[redacted]"


def test_ui_events_redacts_apikey_variants():
    """Variants: apikey, api-key, bearer, private_key, passphrase."""
    from bulk_downloader.ui_events import _redact
    for variant in ("apikey", "api-key", "bearer_token",
                      "private_key", "privatekey", "passphrase",
                      "credential", "passwd"):
        event = {
            "category": "storage_write",
            "data": {"key": variant, "value": "secret-value"},
        }
        redacted = _redact(event)
        assert redacted["data"]["value"] == "[redacted]", (
            f"variant {variant!r} not redacted")


def test_ui_events_passes_normal_keys():
    """Non-sensitive keys aren't redacted."""
    from bulk_downloader.ui_events import _redact
    event = {
        "category": "storage_write",
        "data": {"key": "theme_preference", "value": "dark"},
    }
    redacted = _redact(event)
    assert redacted["data"]["value"] == "dark"


# ── Finding 19: extension options.js escapes pair result ──────────


def test_options_js_has_esc_helper():
    """options.js must define an esc() helper so the inner-HTML
    interpolations can use it."""
    src = (_REPO_ROOT / "extension" / "options.js").read_text(encoding="utf-8")
    assert "function esc(" in src
    # The escape itself must cover the standard HTML entities
    assert "&amp;" in src
    assert "&lt;" in src
    assert "&gt;" in src
    assert "&quot;" in src


def test_options_js_escapes_pair_label():
    """The pair-result render must escape r.label."""
    src = (_REPO_ROOT / "extension" / "options.js").read_text(encoding="utf-8")
    # Post-fix uses esc() on r.label
    assert "esc(r.label" in src
    # And the bare-interpolation pattern (no esc wrap) must not exist
    # in the active code. We look for the line that BUILDS the html
    # — the older form was `+\n          (r.label || ...)` with no
    # esc call. Searching for that exact concatenation.
    assert "          (r.label || " not in src


def test_options_js_escapes_pair_error():
    src = (_REPO_ROOT / "extension" / "options.js").read_text(encoding="utf-8")
    assert "esc(r?.error" in src
    assert "          (r?.error || " not in src


# ── Finding 20: extension options.js escapes silent_origins ───────


def test_options_js_escapes_silent_origin_text():
    """silent_origins origin strings must be escaped in text content
    (the `<span>${o}</span>` site)."""
    src = (_REPO_ROOT / "extension" / "options.js").read_text(encoding="utf-8")
    # Pre-fix bare interpolation
    assert "<span>${o}</span>" not in src
    # Post-fix uses esc()
    assert "<span>${esc(o)}</span>" in src


def test_options_js_escapes_silent_origin_attribute():
    """The data-origin attribute interpolation must also escape."""
    src = (_REPO_ROOT / "extension" / "options.js").read_text(encoding="utf-8")
    assert 'data-origin="${o}"' not in src
    assert 'data-origin="${esc(o)}"' in src


# ── End-to-end: every audit milestone test file still passes ──────


def test_every_audit_milestone_test_file_present():
    """Sanity check: each audit pass left its regression-test file
    behind for future re-runs."""
    tests_dir = _REPO_ROOT / "tests"
    for fname in ("test_v3_43_19_audit_fixes.py",
                    "test_v3_43_27_audit_fixes.py",
                    "test_v3_43_46_audit.py",
                    "test_v3_43_47_audit.py",
                    "test_v3_43_48_audit.py"):
        path = tests_dir / fname
        assert path.exists(), f"missing audit test file: {fname}"
