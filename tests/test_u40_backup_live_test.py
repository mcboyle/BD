"""U40 — L23 (backup-restore-roundtrip), a single live test.

L23 copies the live DB (the WAL trio: .db + .db-wal + .db-shm) to a
scratch dir, restores the copy, runs integrity_check, and compares
row counts against the live DB. It is read-only against the
deployment (the live DB is only copied FROM), so it runs fully here
against a real sandbox DB — not just registration-checked.
"""
import live_tests.checks as checks  # noqa: F401 (registers the checks)
import live_tests.harness as h
from bulk_downloader import db


_LEVELS = (h.PASS, h.WARN, h.FAIL)


def _get_test(test_id):
    for t in h.registry():
        if t.id == test_id:
            return t
    return None


# ── registration ───────────────────────────────────────────────────

def test_l23_is_registered():
    assert _get_test("L23") is not None


def test_l23_is_not_disruptive():
    # it copies the DB and works on the copy — the live DB is never
    # written, and the destructive /api/backup/restore is never fired
    assert _get_test("L23").disruptive is False


# ── graceful degradation ───────────────────────────────────────────

def test_l23_no_db_is_warn():
    ctx = h.Context("http://localhost:1", "/tmp/u40_no_dir")
    level, detail = _get_test("L23").fn(ctx)
    assert level == h.WARN
    assert "no DB" in detail


def test_l23_returns_a_valid_tuple():
    ctx = h.Context("http://localhost:1", "/tmp/u40_no_dir")
    res = _get_test("L23").fn(ctx)
    assert isinstance(res, tuple) and len(res) == 2
    assert res[0] in _LEVELS


# ── L23 fully exercised against a real sandbox DB ──────────────────

def test_l23_roundtrip_passes_on_a_real_db(clean_workdir):
    db.db_init()
    for i in range(5):
        db.db_log("s1", "Site", f"https://example.com/v{i}", "done",
                  filename=f"file{i}.mp4")
    ctx = h.Context("http://localhost:1", str(clean_workdir))
    level, detail = _get_test("L23").fn(ctx)
    # a healthy DB must roundtrip cleanly
    assert level == h.PASS
    assert "integrity_check" in detail
    assert "match live" in detail


def test_l23_roundtrip_preserves_row_counts(clean_workdir):
    # populate, roundtrip, and confirm the test reports matching
    # counts (no mismatch warning)
    db.db_init()
    for i in range(12):
        db.db_log("s1", "Site", f"https://example.com/r{i}", "done")
    ctx = h.Context("http://localhost:1", str(clean_workdir))
    level, detail = _get_test("L23").fn(ctx)
    assert level == h.PASS
    # PASS (not WARN) means restored counts matched the live DB
    assert "match live" in detail


def test_l23_handles_an_empty_but_valid_db(clean_workdir):
    db.db_init()  # schema present, zero rows
    ctx = h.Context("http://localhost:1", str(clean_workdir))
    level, detail = _get_test("L23").fn(ctx)
    # an empty DB still roundtrips soundly
    assert level == h.PASS


# ── harness integration ────────────────────────────────────────────

def test_l23_runs_via_harness(tmp_path):
    rdir = tmp_path / "results"
    # no DB at tmp_path -> WARN -> exit 0
    code = h.run_all("http://localhost:1", str(tmp_path),
                     only=["L23"], results_dir=rdir)
    assert code == 0
    assert (rdir / "L23.log").is_file()
