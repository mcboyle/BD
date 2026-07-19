"""v3.43.47 audit regression tests — Tier-1/Tier-2 modules.

The second wave of audit fixes, covering modules that had never
been systematically reviewed before (runner.py, learn.py, hooks.py,
password_import.py, app.js, secrets_store.py, extension_vault.py).

Findings fixed:
  8. (low) learn.py — suggestedName/suggestedPattern interpolated
     into value="..." attribute without escape. Site hostname is
     attacker-influenced.
  10. (low-med) /api/secrets/import_file accepted unbounded upload
      into memory. 10GB CSV → 10GB RAM consumed.
  11. (med) app.js backup/restore innerHTML interpolation of
      server-supplied error strings without escape.
  12. (med) app.js Plex section / Jellyfin library titles
      interpolated into innerHTML without escape. Media-server
      titles can contain HTML metacharacters.
  14. (info) secrets_store PBKDF2 iterations default 200,000 ->
      600,000 (OWASP 2023 SHA-256 guidance).

Deferred (documented in findings table; acceptable risk):
  9.  hooks.py webhook SSRF — operator-trusted, LAN-legit
  13b sites_config.json sid shape validation — disk-edit only
  15. extension_vault timing-safe compare — 32-byte token space
"""
from __future__ import annotations

import json
from pathlib import Path
# [SAST 3:13pm 13 may] removed unused: from unittest import mock

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell):
    route handlers now live in app_<group>.py, so source-level route-body assertions
    read the aggregate rather than app.py alone."""
    pkg = _REPO_ROOT / "bulk_downloader"
    parts = [_APP_PY.read_text(encoding="utf-8")]
    parts += [p.read_text(encoding="utf-8") for p in sorted(pkg.glob("app_*.py"))]
    return "\n".join(parts)


_APP_JS = _REPO_ROOT / "bulk_downloader" / "static" / "app.js"
_LEARN_PY = _REPO_ROOT / "bulk_downloader" / "learn.py"


def _learn_impl_src():
    """Concatenated source of the decomposed learn_impl/ package. learn.py is now an
    ADD-only re-export shim (DECOMP-LEAF cut 5); RECORDER_JS/TEACH_OVERLAY_JS and the
    function bodies live in learn_impl/*.py, so source/structure tests read the package."""
    from pathlib import Path as _P
    import bulk_downloader.learn_impl as _pkg
    _d = _P(_pkg.__file__).parent
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_d.glob("*.py")))
_SECRETS_PY = _REPO_ROOT / "bulk_downloader" / "secrets_store.py"


# ── Finding 8: learn.py escapes suggestedName / suggestedPattern ────


def test_learn_escapes_suggested_name():
    """The save-template modal interpolates suggestedName into a
    value="..." attribute. After the fix, both occurrences go
    through esc()."""
    src = _learn_impl_src()
    # The attribute interpolations must use esc()
    assert 'value="${esc(suggestedName)}"' in src, (
        "suggestedName interpolated into value=... without esc()")


def test_learn_escapes_suggested_pattern():
    src = _learn_impl_src()
    assert 'value="${esc(suggestedPattern)}"' in src, (
        "suggestedPattern interpolated into value=... without esc()")


def test_learn_no_unescaped_hostname_interpolation():
    """Regression: neither bare ${suggestedName} nor
    ${suggestedPattern} should appear in an attribute context."""
    src = _learn_impl_src()
    # The two bad patterns from before the fix
    assert 'value="${suggestedName}"' not in src
    assert 'value="${suggestedPattern}"' not in src


# ── Finding 10: password import size cap ────────────────────────────


def test_password_import_has_size_cap():
    """/api/secrets/import_file must reject uploads > 10MB."""
    src = _APP_SRC()
    pos = src.find("def api_secrets_import_file")
    body = src[pos:pos + 2500]
    # The constant must be present in some form
    assert "10 * 1024 * 1024" in body or "10*1024*1024" in body, (
        "no 10MB constant in api_secrets_import_file")
    # Must return 413 on overflow
    assert "413" in body, "no 413 return for oversized payload"


def test_password_import_caps_both_paths():
    """Both multipart upload AND JSON content paths must respect
    the cap."""
    src = _APP_SRC()
    pos = src.find("def api_secrets_import_file")
    body = src[pos:pos + 2500]
    # Both branches should check size
    assert body.count("MAX_IMPORT_BYTES") >= 2, (
        "size cap not applied on both upload paths")
    # Multipart branch uses bounded read
    assert "MAX_IMPORT_BYTES + 1" in body or "MAX_IMPORT_BYTES+1" in body


def test_password_import_small_input_still_works():
    """The cap must not break legitimate small inputs."""
    from bulk_downloader import password_import as pi
    # A 50-byte Bitwarden-shaped JSON
    valid_bw = json.dumps({"items": [{
        "name": "Test", "type": 1,
        "login": {"username": "u", "password": "p",
                    "uris": [{"uri": "https://x.com"}]},
    }]}).encode("utf-8")
    fmt, records = pi.import_passwords(valid_bw)
    assert "bitwarden" in fmt.lower()
    assert len(records) == 1


# ── Finding 11: app.js backup/restore innerHTML escapes ────────────


# ── Finding 12: Plex / Jellyfin innerHTML escapes ──────────────────


# ── Finding 14: PBKDF2 600k for new vaults ─────────────────────────


def test_pbkdf2_default_is_600k():
    """New vaults must use 600,000 PBKDF2 iterations (OWASP 2023)."""
    src = _SECRETS_PY.read_text(encoding="utf-8")
    # The _load_or_init() fresh-init path stamps this value
    assert '"iterations": 600000' in src, (
        "PBKDF2 default not bumped to 600k")
    # The old default must be gone from initialization
    # (it can still appear in a doc reference about backwards compat,
    # but not as the fresh-init value)
    init_pos = src.find("# Fresh init:")
    assert init_pos > 0
    init_body = src[init_pos:init_pos + 800]
    assert '"iterations": 200000' not in init_body, (
        "old 200k default still in fresh-init block")


def test_existing_vault_unchanged():
    """A loaded vault keeps its stamped iterations — the change is
    only for newly-created vaults."""
    src = _SECRETS_PY.read_text(encoding="utf-8")
    # _derive_key reads iterations from _data, not from a constant
    pos = src.find("def _derive_key")
    body = src[pos:pos + 400]
    assert "self._data" in body
    assert "iterations" in body
    # No hardcoded constant in _derive_key
    assert "600000" not in body
    assert "200000" not in body


def test_secrets_store_round_trip():
    """End-to-end: set + get works with the new defaults."""
    from bulk_downloader import secrets_store as ss
    import tempfile  # [SAST 3:13pm 13 may] dropped: os
    if not getattr(ss, "_CRYPTO_AVAILABLE", True):
        pytest.skip("cryptography not installed")
    # Use a tempdir to isolate from the real vault
    with tempfile.TemporaryDirectory() as tmp:
        orig = ss.SECRETS_FILE
        try:
            ss.SECRETS_FILE = Path(tmp) / "secrets.json"
            backend = ss.MasterPasswordBackend()
            assert backend.unlock("test-password-abc")
            backend.set("test-key", "test-secret-value")
            assert backend.get("test-key") == "test-secret-value"
            # Verify the stamped iteration count
            data = json.loads(ss.SECRETS_FILE.read_text(encoding="utf-8"))
            assert data["iterations"] == 600000
        finally:
            ss.SECRETS_FILE = orig


# ── Misc regression: app.js helper still works ──────────────────────


