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


# ── the seeded site must be the one under test ─────────────────────
#
# Found on test4 2026-07-28. With a seeded fixture site present and its
# queue populated, L11 still reported on '08c75e90' -- an operator site --
# because _pipeline_setup returns site_ids[0] despite a docstring promising
# it "Prefers a site whose URL points at the local fixture site". The check
# was answering about a subject it had not selected.

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CHECKS_PATH = _REPO_ROOT / "live_tests" / "checks.py"
_HARNESS_PATH = _REPO_ROOT / "live_tests" / "harness.py"
_SEED_PATH = _REPO_ROOT / "tools" / "live_seed.py"


class _StubCtx:
    """Stand-in exposing only what _pipeline_setup consumes: a GET."""

    def __init__(self, body):
        self._body = body
        self.logs = []

    def get(self, path, timeout=15):
        return True, 200, self._body, 1.0

    def log(self, msg):
        self.logs.append(msg)


def test_pipeline_setup_prefers_the_seeded_site_over_an_operator_site():
    """The seeded site is the subject; an operator site is not.

    Ordering matters here: the seeded site is deliberately NOT first, because
    returning site_ids[0] is exactly the defect. Picking an operator site is
    also the unsafe direction -- these checks are disruptive=True, and the
    fixture exists so no real site is touched.
    """
    body = {
        "aaa11111": {"name": "Real Operator Site", "state": "idle",
                     "config": {}},
        "bbb22222": {"name": "bdseed fixture site", "state": "idle",
                     "config": {}},
    }
    sid, _info = checks._pipeline_setup(_StubCtx(body))
    assert sid == "bbb22222", (
        f"_pipeline_setup chose {sid!r}, an unmarked operator site, while a "
        f"seeded fixture site was configured. Observed on test4: L11 reported "
        f"'site 08c75e90 has no completed downloads' while the seeder had "
        f"just populated its own site. A check that selects the wrong subject "
        f"reports truthfully about the wrong thing."
    )


def test_pipeline_setup_still_degrades_when_nothing_is_seeded():
    """No fixture site is a fallback, not a crash."""
    body = {"aaa11111": {"name": "Real Operator Site", "state": "idle",
                         "config": {}}}
    sid, _info = checks._pipeline_setup(_StubCtx(body))
    assert sid == "aaa11111", (
        "with no seeded site present the previous behaviour must stand; "
        f"got {sid!r}"
    )


def test_the_seed_marker_agrees_with_the_seeders_own():
    """checks.py cannot import the seeder, so its marker copy is pinned here.

    live_tests must stay read-only, so it may not import tools/live_seed.py
    (pinned by test_live_seed.py). That forces a second copy of the marker,
    and section 5's rule applies: the copy nobody updated is the one that
    runs. AST, not grep -- the string appears in prose in both files.
    """
    def _marker(path, name):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        return node.value.value
        return None

    seeder = _marker(_SEED_PATH, "SEED_MARKER")
    mirror = _marker(_CHECKS_PATH, "SEED_MARKER")
    assert seeder is not None, "tools/live_seed.py no longer defines SEED_MARKER"
    assert mirror == seeder, (
        f"live_tests/checks.py pins SEED_MARKER={mirror!r} but the seeder "
        f"writes {seeder!r}; the checks would stop recognising seeded sites "
        f"silently, and every seeded PASS would quietly become a WARN."
    )


# ── no helper may advertise a capability the suite cannot have ─────

def test_no_private_helper_is_defined_but_never_referenced():
    """A dead helper documents a capability the package does not have.

    _await_job sat here unused beneath a header claiming these tests "QUEUE A
    REAL JOB and let it run". They cannot: Context exposes get/log/ro_db and
    no write verb at all, which is the safety property that makes the suite
    safe to point at production. The dead helper and the docstring together
    cost a session's investigation before the source said otherwise.
    """
    tree = ast.parse(_CHECKS_PATH.read_text(encoding="utf-8"))
    defined = {n.name for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name.startswith("_")}
    used = {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    dead = sorted(defined - used)
    assert not dead, (
        f"defined but never referenced in live_tests/checks.py: {dead}. "
        f"Either wire it to a caller or delete it -- an unused helper reads "
        f"as a capability the suite has."
    )


def test_the_live_context_exposes_no_write_verb():
    """The read-only invariant, pinned.

    This is what makes the seeder live in tools/ and what makes it impossible
    for a check to start a download itself. If a write verb is ever added here
    it must be a deliberate, reviewed decision -- not a quiet consequence of
    making one check more capable.
    """
    tree = ast.parse(_HARNESS_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Context":
            methods = {n.name for n in node.body
                       if isinstance(n, ast.FunctionDef)}
            break
    else:
        raise AssertionError("live_tests.harness no longer defines Context")
    writes = methods & {"post", "put", "patch", "delete"}
    assert not writes, (
        f"Context grew {sorted(writes)}; the live suite is no longer "
        f"structurally read-only and is no longer safe to point at "
        f"production. tests/test_live_seed.py's rationale depends on this."
    )
