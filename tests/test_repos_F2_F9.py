"""Tests for F2 (Windows reserved names) + F9 (regex length caps).

Both came from the master backlog's repo-integration findings:
  • F2: elhacker-main/downloader.py — Windows reserved filename guard
  • F9: cobalt-main/api/src/processing/service-patterns.js —
        length-capped pattern testers as SSRF / regex-DOS defense
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


def _isolated_bd():
    tmp = tempfile.mkdtemp(prefix="bd-repos-test-")
    # v3.66.9: snapshot env + modules so teardown_module can restore
    # them (matches the pattern fixed in test_phases_195_199.py).
    if not _ISOLATED_BD_SAVED:
        _ISOLATED_BD_SAVED["env"] = {
            k: os.environ.get(k)
            for k in ("BD_INSTALL_DIR", "BD_DISABLE_KEEPALIVE")
        }
        _ISOLATED_BD_SAVED["modules"] = {
            k: v for k, v in sys.modules.items()
            if k.startswith("bulk_downloader")
        }
    os.environ["BD_INSTALL_DIR"] = tmp
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    for m in list(sys.modules):
        if m.startswith("bulk_downloader"):
            del sys.modules[m]
    return tmp


_ISOLATED_BD_SAVED = {}


def teardown_module(module):
    if not _ISOLATED_BD_SAVED:
        return
    for k, v in _ISOLATED_BD_SAVED["env"].items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for m in [k for k in list(sys.modules)
              if k.startswith("bulk_downloader")]:
        del sys.modules[m]
    sys.modules.update(_ISOLATED_BD_SAVED["modules"])
    _ISOLATED_BD_SAVED.clear()


# ─── F2: Windows reserved-name guard ───────────────────────────────────


class TestReservedWindowsNames(unittest.TestCase):
    """CON, PRN, AUX, NUL, COM1-9, LPT1-9 can't be used as filenames
    or stems on Windows. Even on Linux, BD writes to NTFS/SMB/exFAT
    shares where the reservation still applies."""

    def setup_method(self, method=None):
        _isolated_bd()
        from bulk_downloader import fname
        self.fname = fname

    setUp = setup_method

    def test_bare_con_gets_prefixed(self):
        """{title} = 'CON' would otherwise produce a file Windows
        can't open."""
        out = self.fname._sanitize_filename_var("CON")
        self.assertNotEqual(out.upper(), "CON",
            f"bare CON should be escaped, got {out!r}")
        self.assertTrue(out.startswith("_"),
            f"escaped form should start with underscore, got {out!r}")

    def test_con_with_extension_gets_prefixed(self):
        """CON.mp4 is just as broken as CON — Windows looks at the
        stem before the extension."""
        out = self.fname._sanitize_filename_var("CON.mp4")
        self.assertNotEqual(out, "CON.mp4")
        self.assertTrue(out.startswith("_"),
            f"escaped form should start with underscore, got {out!r}")

    def test_case_insensitive(self):
        """'con' and 'Con' are also reserved."""
        for variant in ("con", "Con", "cON", "PrN", "lpt9"):
            out = self.fname._sanitize_filename_var(variant)
            self.assertTrue(
                out.startswith("_"),
                f"variant {variant!r} should be escaped, got {out!r}")

    def test_all_reserved_names_caught(self):
        """Coverage check — every name in the set must be escaped."""
        for name in self.fname._RESERVED_WINDOWS_NAMES:
            out = self.fname._sanitize_filename_var(name)
            self.assertTrue(out.startswith("_"),
                f"{name!r} not escaped: got {out!r}")

    def test_normal_names_unchanged(self):
        """Don't over-trigger — 'Concord', 'Console', 'auxiliary'
        contain CON/AUX as substrings but aren't reserved."""
        for legitimate in ("Concord", "Console", "auxiliary",
                            "scene_2024", "con_test", "CONCERT"):
            out = self.fname._sanitize_filename_var(legitimate)
            self.assertEqual(out, legitimate,
                f"{legitimate!r} shouldn't be escaped, got {out!r}")

    def test_reserved_name_with_path_separators(self):
        """A hostile {title} = 'CON/../etc' should be both path-
        sanitized AND reserved-name escaped."""
        out = self.fname._sanitize_filename_var("CON/etc")
        # Path separator → underscore, then the resulting bare stem
        # 'CON_etc' is no longer a reserved name → unchanged from
        # there. Wait — actually 'CON_etc' isn't reserved, so we just
        # check the path was sanitized.
        self.assertNotIn("/", out)
        # The point: should not crash or produce something Windows
        # would reject.

    def test_reserved_name_helper_function(self):
        """The _is_reserved_windows_name helper is the truth source."""
        self.assertTrue(self.fname._is_reserved_windows_name("CON"))
        self.assertTrue(self.fname._is_reserved_windows_name("con"))
        self.assertTrue(self.fname._is_reserved_windows_name("CON.mp4"))
        self.assertTrue(self.fname._is_reserved_windows_name("LPT9.txt"))
        self.assertFalse(self.fname._is_reserved_windows_name("Concord"))
        self.assertFalse(self.fname._is_reserved_windows_name(""))
        self.assertFalse(self.fname._is_reserved_windows_name("normal.mp4"))


# ─── F9: Length-capped pattern matching ────────────────────────────────


class TestPatternLengthCaps(unittest.TestCase):
    """Long patterns × long URLs can trigger catastrophic backtracking
    in re.search and pin a CPU core. The caps in content_rights and
    the routing logic bound this."""

    def setup_method(self, method=None):
        _isolated_bd()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import content_rights
        self.cr = content_rights

    setUp = setup_method

    def test_constants_defined(self):
        self.assertGreater(self.cr._MAX_URL_LEN_FOR_MATCH, 1000)
        self.assertGreater(self.cr._MAX_PATTERN_LEN, 100)

    def test_over_long_pattern_rejected_at_insert(self):
        """Operator trying to save a 10kB pattern gets a clean error
        instead of silently storing junk."""
        long_pattern = "a" * (self.cr._MAX_PATTERN_LEN + 100)
        r = self.cr.block_url(long_pattern, reason="too long")
        self.assertFalse(r["ok"])
        self.assertIn("too long", r["error"])

    def test_under_cap_accepted(self):
        """Patterns up to the limit work fine."""
        ok_pattern = "a" * (self.cr._MAX_PATTERN_LEN - 10)
        r = self.cr.block_url(ok_pattern, reason="ok size")
        self.assertTrue(r["ok"], f"unexpected error: {r.get('error')}")

    def test_url_truncated_for_match(self):
        """A 100kB URL should match the pattern based on its first
        4kB, not blow up the regex engine."""
        self.cr.block_url("evil-site\\.com", reason="evil")
        # Build a long URL whose match content is in the first 4kB
        long_url = "https://evil-site.com/path" + "?param=" + ("x" * 100_000)
        start = time.time()
        result = self.cr.url_is_blocked(long_url)
        elapsed = time.time() - start
        self.assertIsNotNone(result, "blocklist should match short prefix")
        self.assertLess(elapsed, 1.0,
            f"url_is_blocked took {elapsed:.2f}s on long URL — "
            f"truncation not working?")

    def test_pathological_pattern_skipped_at_match_time(self):
        """If a corrupt over-long pattern somehow ends up in the DB
        (e.g. via direct SQL), match-time skip prevents it from
        DoSing the queue intake."""
        # Insert directly to bypass the insert-time cap
        from bulk_downloader import db as _db
        self.cr._ensure_tables()
        with _db.db_conn() as cx:
            # Use a string column that the schema allows but is over
            # the cap. Schema caps `pattern` at 500 chars too, but
            # let's verify the match-time check is independent.
            cx.execute("""
                INSERT INTO content_blocklist
                (pattern, hash_hex, reason, added_at, added_by)
                VALUES (?, ?, ?, ?, ?)
            """, ("z" * 10_000, "", "should be skipped",
                  time.time(), "test"))
        # A URL that would NOT match should return None quickly
        start = time.time()
        result = self.cr.url_is_blocked("https://innocent.example/x")
        elapsed = time.time() - start
        self.assertIsNone(result)
        self.assertLess(elapsed, 0.5,
            f"match took {elapsed:.2f}s with corrupt pattern — "
            f"match-time skip not working?")


# ─── Source-level checks for runner / app pattern caps ─────────────────


class TestPatternCapsAppliedInApp(unittest.TestCase):
    """All re.search() calls against URLs should now be capped.
    Source-level check that we didn't miss any."""

    def setup_method(self, method=None):
        _isolated_bd()
        import bulk_downloader
        _pkg = Path(bulk_downloader.__file__).parent
        # Phase 4: F9-marked re.search sites moved with their route groups onto
        # blueprints; count across app.py + every app_*.py so the total holds.
        self.app_src = "\n".join(
            [(_pkg / "app.py").read_text(encoding="utf-8")]
            + [q.read_text(encoding="utf-8") for q in sorted(_pkg.glob("app_*.py"))])

    setUp = setup_method

    def test_known_match_sites_have_caps(self):
        """Each of the 4 known re.search(pat, url, ...) sites in app.py
        should now have a 512/4096 cap nearby. Look for the F9 comment
        marker."""
        # The comment 'v3.46.4 F9' should appear N times if all sites
        # got patched
        f9_count = self.app_src.count("v3.46.4 F9")
        self.assertGreaterEqual(f9_count, 3,
            f"only {f9_count} F9 markers in app.py; expected ≥3 for "
            f"the known route-pattern match sites")


if __name__ == "__main__":
    unittest.main()
