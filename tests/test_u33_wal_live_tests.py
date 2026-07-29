"""U33 — L20 (WAL no-lock under parallel workers) + L21 (WAL
checkpoint under load), the two INV-004 live tests.

The checks need the running deployment on `stash`. Unit-testable
here: registration, graceful degradation against an unreachable /
DB-less target, and — using a real sandbox WAL DB — that the shared
_wal_parallel_read load harness genuinely runs concurrent readers
without lock errors.
"""
import tempfile

import live_tests.checks as checks  # noqa: F401 (registers the checks)
import live_tests.harness as h
from bulk_downloader import db


# Read the harness tuple rather than restating it: a local copy goes stale
# the moment the verdict vocabulary grows (it did -- h.NA was added and every
# copy of this line silently began rejecting a valid level).
_LEVELS = h._LEVELS


def _get_test(test_id):
    for t in h.registry():
        if t.id == test_id:
            return t
    return None


# ── registration ───────────────────────────────────────────────────

def test_l20_and_l21_are_registered():
    ids = {t.id for t in h.registry()}
    assert "L20" in ids and "L21" in ids


def test_l20_l21_are_not_disruptive():
    assert _get_test("L20").disruptive is False
    assert _get_test("L21").disruptive is False


# ── graceful degradation (no DB / unreachable target) ──────────────

def test_l20_missing_db_is_warn_not_fail():
    # the test cannot conclude without a DB -> WARN, never a crash or
    # a false FAIL
    ctx = h.Context("http://localhost:1", "/tmp/u33_no_such_dir")
    level, detail = _get_test("L20").fn(ctx)
    assert level == h.WARN
    assert "no DB" in detail


def test_l21_missing_db_is_warn_not_fail():
    ctx = h.Context("http://localhost:1", "/tmp/u33_no_such_dir")
    level, detail = _get_test("L21").fn(ctx)
    assert level == h.WARN
    assert "no DB" in detail


# ── the shared parallel-worker load harness ────────────────────────

def test_wal_parallel_read_runs_concurrent_readers(clean_workdir):
    # a real WAL-mode DB in the sandbox: parallel readers must see
    # zero lock errors — that is exactly what INV-004 guarantees
    db.db_init()
    db.db_log("s1", "Site", "https://example.com/a", "done")
    ctx = h.Context("http://localhost:1", str(clean_workdir))
    lock_errors, other_errors, total = checks._wal_parallel_read(
        ctx, threads=6, iterations=20)
    assert lock_errors == []
    assert other_errors == []
    # 6 threads x 20 iterations all completed
    assert total == 120


def test_wal_parallel_read_handles_a_bad_db_path():
    # a connection that cannot open must be recorded, not raised
    ctx = h.Context("http://localhost:1", "/tmp/u33_no_such_dir")
    lock_errors, other_errors, total = checks._wal_parallel_read(
        ctx, threads=3, iterations=5)
    # every worker fails to connect -> recorded as other_errors
    assert total == 0
    assert len(other_errors) >= 1


# ── L20/L21 against a real sandbox WAL DB ──────────────────────────

def test_l20_passes_on_a_real_wal_db(clean_workdir):
    db.db_init()
    db.db_log("s1", "Site", "https://example.com/a", "done")
    ctx = h.Context("http://localhost:1", str(clean_workdir))
    level, detail = _get_test("L20").fn(ctx)
    # a healthy sandbox WAL DB with parallel readers must PASS
    assert level == h.PASS
    assert "lock errors" in detail


def test_l21_bounded_wal_sidecar_is_pass_or_warn(clean_workdir):
    db.db_init()
    ctx = h.Context("http://localhost:1", str(clean_workdir))
    level, detail = _get_test("L21").fn(ctx)
    # a fresh sandbox DB has a tiny WAL sidecar — never a FAIL
    assert level in (h.PASS, h.WARN)


# ── L20/L21 run through the harness ────────────────────────────────

def test_l20_l21_run_via_harness(tmp_path):
    rdir = tmp_path / "results"
    # no DB at tmp_path -> both WARN -> exit 0 (nothing FAILED)
    code = h.run_all("http://localhost:1", str(tmp_path),
                     only=["L20", "L21"], results_dir=rdir)
    assert code == 0
    assert (rdir / "L20.log").is_file()
    assert (rdir / "L21.log").is_file()
