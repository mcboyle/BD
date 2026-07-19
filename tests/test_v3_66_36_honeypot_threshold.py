"""Tests for per-site honeypot threshold learning — P5-2b (v3.66.36).

Covers the pure learner, the (safely-degrading) trap-score collector, the
additive db_log honeypot_score persistence + migration, and the
per-site override at the provider_resolve threshold seam (default off).
"""
import sqlite3

import pytest

from bulk_downloader import honeypot_threshold as ht


def _history_schema(cx):
    """The v3.66.36 history shape (subset sufficient for the collector)."""
    cx.execute("""CREATE TABLE history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id TEXT, status TEXT, file_size INTEGER,
        honeypot_score REAL DEFAULT NULL)""")


# ── _percentile ─────────────────────────────────────────────────────
class TestPercentile:
    def test_single(self):
        assert ht._percentile([0.7], 0.1) == 0.7

    def test_min_max(self):
        vals = [0.1, 0.5, 0.9]
        assert ht._percentile(vals, 0.0) == 0.1
        assert ht._percentile(vals, 1.0) == 0.9

    def test_interpolation(self):
        # midpoint of [0.2, 0.8] at q=0.5 → 0.5
        assert ht._percentile([0.2, 0.8], 0.5) == pytest.approx(0.5)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            ht._percentile([], 0.1)


# ── learn_threshold ─────────────────────────────────────────────────
class TestLearnThreshold:
    def test_below_min_samples_returns_default(self):
        assert ht.learn_threshold([0.9, 0.95], default=0.8, min_samples=5) == 0.8

    def test_empty_returns_default(self):
        assert ht.learn_threshold([], default=0.8) == 0.8

    def test_quantile_used_when_enough(self):
        # All traps scored ~0.9; 10th-percentile sits near the low end.
        scores = [0.88, 0.90, 0.91, 0.93, 0.95, 0.97]
        thr = ht.learn_threshold(scores, default=0.8, min_samples=5, quantile=0.10)
        assert ht.THRESHOLD_FLOOR <= thr <= ht.THRESHOLD_CEIL
        # Learned threshold should be at/below the lowest trap, tightening
        # relative to the 0.8 default for this trap-heavy site.
        assert thr <= 0.9

    def test_clamped_to_floor(self):
        # Traps that scored low → clamp up to the floor, never below.
        scores = [0.1, 0.12, 0.15, 0.18, 0.2, 0.22]
        thr = ht.learn_threshold(scores, default=0.8, min_samples=5)
        assert thr == ht.THRESHOLD_FLOOR

    def test_clamped_to_ceil(self):
        scores = [0.98, 0.99, 0.99, 1.0, 1.0, 1.0]
        thr = ht.learn_threshold(scores, default=0.8, min_samples=5)
        assert thr <= ht.THRESHOLD_CEIL

    def test_non_numeric_and_out_of_range_filtered(self):
        scores = ["x", None, 1.5, -0.2, 0.9, 0.9, 0.9, 0.9, 0.9]
        # Only the five 0.9s are usable → meets min_samples.
        thr = ht.learn_threshold(scores, default=0.8, min_samples=5)
        assert thr == pytest.approx(0.9)

    def test_insufficient_usable_after_filter_returns_default(self):
        scores = ["x", None, 1.5, 0.9, 0.9]  # only two usable
        assert ht.learn_threshold(scores, default=0.77, min_samples=5) == 0.77


# ── trap_scores_for_site (collector) ────────────────────────────────
class TestCollector:
    def test_returns_only_scored_traps(self):
        cx = sqlite3.connect(":memory:")
        cx.row_factory = sqlite3.Row
        _history_schema(cx)
        rows = [
            ("siteA", "done", 1 * 1024 * 1024, 0.9),    # trap + score ✓
            ("siteA", "done", 2 * 1024 * 1024, 0.85),   # trap + score ✓
            ("siteA", "done", 1 * 1024 * 1024, None),   # trap, no score ✗
            ("siteA", "done", 500 * 1024 * 1024, 0.9),  # big file, not tiny ✗
            ("siteA", "failed", 1 * 1024 * 1024, 0.9),  # not done ✗
            ("siteB", "done", 1 * 1024 * 1024, 0.99),   # other site ✗
        ]
        cx.executemany(
            "INSERT INTO history(site_id,status,file_size,honeypot_score) "
            "VALUES(?,?,?,?)", rows)
        got = sorted(ht.trap_scores_for_site("siteA", conn=cx))
        assert got == [0.85, 0.9]

    def test_missing_column_degrades_to_empty(self):
        cx = sqlite3.connect(":memory:")
        cx.execute("CREATE TABLE history(id INTEGER, site_id TEXT, "
                   "status TEXT, file_size INTEGER)")  # no honeypot_score
        assert ht.trap_scores_for_site("siteA", conn=cx) == []

    def test_no_site_returns_empty(self):
        assert ht.trap_scores_for_site("", conn=None) == []


# ── learned_drop_threshold (integration) ────────────────────────────
class TestLearnedDropThreshold:
    def test_falls_back_to_default_without_data(self):
        cx = sqlite3.connect(":memory:")
        cx.row_factory = sqlite3.Row
        _history_schema(cx)
        assert ht.learned_drop_threshold("siteA", default=0.8, conn=cx) == 0.8

    def test_learns_from_data(self):
        cx = sqlite3.connect(":memory:")
        cx.row_factory = sqlite3.Row
        _history_schema(cx)
        cx.executemany(
            "INSERT INTO history(site_id,status,file_size,honeypot_score) "
            "VALUES(?,?,?,?)",
            [("siteA", "done", 1024 * 1024, s)
             for s in (0.7, 0.72, 0.74, 0.76, 0.78, 0.8)])
        thr = ht.learned_drop_threshold("siteA", default=0.9, conn=cx)
        # Site historically traps at ~0.7-0.8 → learned threshold tighter
        # than the 0.9 default.
        assert thr < 0.9
        assert thr >= ht.THRESHOLD_FLOOR


# ── enabled() gate ──────────────────────────────────────────────────
class TestEnabled:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv(ht._ENV_FLAG, raising=False)
        assert ht.enabled() is False

    def test_on_when_set(self, monkeypatch):
        monkeypatch.setenv(ht._ENV_FLAG, "1")
        assert ht.enabled() is True


# ── db_log persistence (additive) + migration ───────────────────────
class TestPersistence:
    def _tmp_db(self, monkeypatch, tmp_path):
        from bulk_downloader import db as _db
        dbfile = str(tmp_path / "history.db")
        monkeypatch.setattr(_db, "_resolve_db_path", lambda: dbfile)
        _db.db_init()
        return _db

    def test_db_log_default_none_is_compatible(self, monkeypatch, tmp_path):
        _db = self._tmp_db(monkeypatch, tmp_path)
        # Legacy call shape (no honeypot_score) still works.
        _db.db_log("siteA", "Site A", "http://x/1", "done", "f.mp4", 1234, "ok")
        with _db.db_conn() as cx:
            row = cx.execute("SELECT honeypot_score FROM history "
                             "WHERE url='http://x/1'").fetchone()
        assert row["honeypot_score"] is None

    def test_db_log_stamps_score(self, monkeypatch, tmp_path):
        _db = self._tmp_db(monkeypatch, tmp_path)
        _db.db_log("siteA", "Site A", "http://x/2", "done", "f.mp4",
                   2 * 1024 * 1024, "ok", honeypot_score=0.87)
        # Round-trips, and the collector picks it up as a confirmed trap.
        with _db.db_conn() as cx:
            scores = ht.trap_scores_for_site("siteA", conn=cx)
        assert scores == [0.87]

    def test_migration_v7_registered(self):
        from bulk_downloader import migrations as _m
        versions = {mm["version"] for mm in _m._MIGRATIONS}
        assert 7 in versions


# ── provider_resolve seam (per-site override, default off) ──────────
class TestResolveSeam:
    def test_per_site_off_uses_global(self, monkeypatch):
        from bulk_downloader import provider_resolve as pr
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "0.8")
        monkeypatch.delenv(ht._ENV_FLAG, raising=False)
        assert pr._honeypot_drop_threshold(site_id="siteA") == 0.8

    def test_per_site_on_overrides_with_learned(self, monkeypatch):
        from bulk_downloader import provider_resolve as pr
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "0.8")
        monkeypatch.setenv(ht._ENV_FLAG, "1")
        monkeypatch.setattr(ht, "learned_drop_threshold",
                            lambda sid, default, **kw: 0.62)
        assert pr._honeypot_drop_threshold(site_id="siteA") == 0.62

    def test_per_site_inert_when_global_off(self, monkeypatch):
        from bulk_downloader import provider_resolve as pr
        # Global off (None) → per-site has no base to refine, stays None.
        monkeypatch.delenv("BD_HONEYPOT_SCORE_THRESHOLD", raising=False)
        monkeypatch.setenv(ht._ENV_FLAG, "1")
        assert pr._honeypot_drop_threshold(site_id="siteA") is None

    def test_no_site_id_uses_global(self, monkeypatch):
        from bulk_downloader import provider_resolve as pr
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "0.8")
        monkeypatch.setenv(ht._ENV_FLAG, "1")
        # No site_id → cannot personalize, returns global.
        assert pr._honeypot_drop_threshold() == 0.8
