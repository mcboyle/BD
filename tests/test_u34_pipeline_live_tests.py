"""U34 — L11 (end-to-end download) + L13 (library fast-path) + L14
(stash-dedup-skip), the three core-pipeline live tests.

These QUEUE OR DEPEND ON real downloads against the live deployment,
so all three are disruptive=True. Unit-testable here: registration,
the disruptive flag, graceful degradation, and — for L14, which is
read-only over history — real behaviour against a sandbox DB.
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

def test_l11_l13_l14_registered():
    ids = {t.id for t in h.registry()}
    assert {"L11", "L13", "L14"} <= ids


def test_pipeline_tests_are_disruptive():
    # they queue / depend on real downloads -> must be opt-in
    for tid in ("L11", "L13", "L14"):
        assert _get_test(tid).disruptive is True


# ── graceful degradation ───────────────────────────────────────────

def _dead_ctx():
    return h.Context("http://localhost:1", "/tmp/u34_no_dir",
                     disruptive=True)


def test_l11_unreachable_is_warn():
    level, detail = _get_test("L11").fn(_dead_ctx())
    assert level == h.WARN
    assert "not testable" in detail


class _TopLevelStatusContext:
    """Match the deployed /api/status and extractor-matrix schemas."""

    def get(self, path, timeout=15):
        if path == "/api/status":
            return True, 200, {"site-a": {"state": "idle"}}, 1.0
        if path == "/api/sites/site-a/queue/counts":
            return True, 200, {"done": 1, "failed": 0}, 1.0
        if path == "/api/dev/extractor_matrix":
            return True, 200, {
                "extractors": [
                    {"name": "gallery-dl", "library_installed": True},
                    {"name": "missing", "library_installed": False},
                ]
            }, 1.0
        return False, 404, {}, 1.0

    def log(self, _message):
        pass


def test_l11_accepts_deployed_top_level_status_schema():
    level, detail = _get_test("L11").fn(_TopLevelStatusContext())
    assert level == h.PASS
    assert "1 completed" in detail


def test_l13_accepts_deployed_extractor_matrix_schema():
    level, detail = _get_test("L13").fn(_TopLevelStatusContext())
    assert level == h.PASS
    assert "1 of 2" in detail


def test_l13_unreachable_is_warn():
    level, detail = _get_test("L13").fn(_dead_ctx())
    assert level == h.WARN


def test_l14_no_db_is_warn():
    level, detail = _get_test("L14").fn(_dead_ctx())
    assert level == h.WARN
    assert "no DB" in detail


def test_all_three_return_valid_tuples():
    for tid in ("L11", "L13", "L14"):
        res = _get_test(tid).fn(_dead_ctx())
        assert isinstance(res, tuple) and len(res) == 2
        assert res[0] in _LEVELS
        assert isinstance(res[1], str) and res[1]


# ── L14 against a real sandbox DB (it is read-only over history) ───

def test_l14_warns_with_no_completed_downloads(clean_workdir):
    db.db_init()
    ctx = h.Context("http://localhost:1", str(clean_workdir),
                    disruptive=True)
    level, detail = _get_test("L14").fn(ctx)
    assert level == h.WARN
    assert "no completed downloads" in detail


def test_l14_passes_when_a_dedup_skip_is_recorded(clean_workdir):
    db.db_init()
    # a completed download whose message records a dedup skip
    db.db_log("s1", "Site", "https://example.com/v1", "done",
              filename="movie.mp4", message="skipped: dedup match")
    ctx = h.Context("http://localhost:1", str(clean_workdir),
                    disruptive=True)
    level, detail = _get_test("L14").fn(ctx)
    assert level == h.PASS
    assert "dedup-skipped" in detail


def test_l14_passes_for_real_skipped_duplicate_queue_state(clean_workdir):
    db.db_init()
    db.db_log("s1", "Site", "https://example.com/v1", "done",
              filename="movie.mp4", message="ok")
    db.queue_upsert(
        "s1", "https://example.com/v1", status="skipped_duplicate",
        message="Duplicate of history #1 (movie.mp4)")
    ctx = h.Context("http://localhost:1", str(clean_workdir),
                    disruptive=True)
    level, detail = _get_test("L14").fn(ctx)
    assert level == h.PASS
    assert "dedup-skipped" in detail


def test_l14_warns_when_downloads_exist_but_no_dedup_skip(clean_workdir):
    db.db_init()
    db.db_log("s1", "Site", "https://example.com/v2", "done",
              filename="other.mp4", message="ok")
    ctx = h.Context("http://localhost:1", str(clean_workdir),
                    disruptive=True)
    level, detail = _get_test("L14").fn(ctx)
    # downloads exist, but none was a dedup skip -> WARN, not FAIL
    assert level == h.WARN


# ── harness integration ────────────────────────────────────────────

def test_pipeline_tests_skipped_without_disruptive_flag(tmp_path):
    # default run excludes disruptive tests -> none of L11/L13/L14 run
    rdir = tmp_path / "r"
    h.run_all("http://localhost:1", str(tmp_path), results_dir=rdir)
    for tid in ("L11", "L13", "L14"):
        assert not (rdir / f"{tid}.log").exists()


def test_pipeline_tests_run_when_named_explicitly(tmp_path):
    # naming IDs via --only runs them regardless of the disruptive flag
    rdir = tmp_path / "r"
    code = h.run_all("http://localhost:1", str(tmp_path),
                     only=["L11", "L13", "L14"], results_dir=rdir)
    # all WARN against a dead target -> exit 0
    assert code == 0
    for tid in ("L11", "L13", "L14"):
        assert (rdir / f"{tid}.log").is_file()
