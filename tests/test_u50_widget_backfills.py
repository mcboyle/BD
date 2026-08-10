"""U50 — backfill three primary-value widget keys.

The widget audit (v3.63.6 unit #5) found three widgets that showed
'—' on every deployment because the backend never emitted their
primary key:

  • avg_size_fmt    — required by the `avg_size` widget (performance)
  • eta_clear_fmt   — required by the `eta_clear` widget (system)
  • cookies_oldest_days + cookies_oldest_site — required by the
                       `cookies` widget (system)

These are *primary value* fields, not decorative slots (`*_extra`,
`*_delta`, `*_spark`). Decorative slots stay unfilled — they need
historical baselines or charting data this collector doesn't have
to invent.

v3.63.6 unit #7 ships the three collectors. Each fails open: if the
required data isn't available (no completions, queue empty, no
cookies), the key stays absent and the renderer shows '—'.

These tests verify both the present-data path and the absent-data
fail-open behaviour via fake-subprocess / in-process mocking.
"""
from __future__ import annotations

import importlib
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_API_PY = _REPO_ROOT / "bulk_downloader" / "app_widgets_api.py"


def _own_empty_schema():
    """Create THIS test's database schema, complete, in clean_workdir's tmpdir.

    db_init() alone is not the whole schema: `library` (and friends) are
    created by migrations, not by db_init -- measured here as
    "no such table: library" from the scoped-library collector when this
    helper only called db_init(). Both calls, always.
    """
    from bulk_downloader import db, migrations
    db.db_init()
    migrations.apply_pending(backup_first=False)


# ── The OTHER half of this file's denominator: live runner state ──
#
# MEASURED ON THE DEPLOY BOX, v3.66.1007 capture (2026-08-10). Both eta_clear
# tests below failed there -- `assert 'now' is None` and `assert 'now' == '30m'`
# -- while passing in isolation in every container. The queue depth they patch
# into `db_stats` never reached the forecast:
#
#   app_widgets_api.py  "Live runner state is authoritative for queue/rate
#                        headlines."  ->  `out["queue_depth"] = queue_depth`
#
# That branch runs whenever `app_state.runners` is non-empty, and it OVERWRITES
# the db_stats value with the live count. So one runner left in that
# module-global dict by any earlier file sharing the xdist worker forces
# queue_depth = 0, and the forecast collapses to "now".
#
# @922 fixed the SAME class in this file for the database ("depends on
# whichever tables an EARLIER file happened to create") and stopped at the
# schema. `runners` is the half it missed; the box found it when installing an
# unrelated dev module changed the collected count and xdist reshuffled which
# files share a worker.
#
# Reproduced in-container before any fix: runners populated -> queue_depth=0,
# eta_clear_fmt='now'; runners empty -> queue_depth=5, eta_clear_fmt=None.


class _AmbientRunner:
    """A runner of the shape an earlier file leaves in `app_state.runners`.

    Answers the production contract `_runner_live_counts` reads --
    `get_status(light=True)["counts"]` -- so the live branch treats it as real.
    """

    def __init__(self, pending):
        self._pending = pending

    def get_status(self, light=False):
        return {"counts": {"pending": self._pending,
                           "running": 1 if self._pending else 0}}

    def _current_throughput_bps(self):
        return 0.0


# NO SINGLE LEAKED RUNNER CAN CONSTRAIN ALL THREE GUARDS, and the two attempts
# that tried are the argument for the third parameter. MEASURED with bd-mutate,
# removing each guard in turn:
#
#   ambient pending = 0   test_..._says_now_when_queue_empty  ESCAPED
#                         (a leak yields queue_depth 0 -> "now", the very
#                          answer that test asserts)
#   ambient pending = 7   test_..._absent_when_no_throughput  ESCAPED
#                         (a leak yields depth 7 at files_hour 0 -> no
#                          forecast -> key absent, which that test asserts)
#
# The blind spot MOVED rather than closing -- CLAUDE.md section 0's "the fix
# reproduces the shape of the defect", inside the repair for an instance of it.
# Running both shapes means every guard is constrained by its OWN assertion:
# an idle leak breaks the two tests that need depth, a busy leak breaks the one
# that needs an empty queue, and 3 of 3 mutants are caught.
_AMBIENT_STATES = {
    "no_ambient_runners": lambda: {},
    "an_idle_runner_left_behind": lambda: {"left_by_an_earlier_file":
                                           _AmbientRunner(0)},
    "a_busy_runner_left_behind": lambda: {"left_by_an_earlier_file":
                                          _AmbientRunner(7)},
}


def _set_runners(value):
    """Set `app_state.runners`, returning a restore callable.

    `app_state` is imported through importlib rather than held at module scope
    because the tests reload `app_widgets_api`, and `_collect_data` resolves
    app_state at CALL time -- binding it here would risk asserting against a
    different module object than the code under test reads.
    """
    app_mod = importlib.import_module("bulk_downloader.app_state")
    original = getattr(app_mod, "runners", None)
    app_mod.runners = value

    def _restore():
        if original is None:
            if hasattr(app_mod, "runners"):
                del app_mod.runners
        else:
            app_mod.runners = original

    return _restore


@pytest.fixture(params=sorted(_AMBIENT_STATES))
def ambient_runners(request):
    """Run each test under every shape of the state it was silently inheriting.

    PARAMETRIZED RATHER THAN MERELY CLEARED, deliberately. Clearing alone would
    turn the box's red green while proving nothing about immunity -- the next
    reshuffle would be free to break it again in a way no lane could see.
    Running the leaked shapes means the repair fails on pristine source, which
    is the only version of it that can be shown to work.
    """
    restore = _set_runners(_AMBIENT_STATES[request.param]())
    try:
        yield request.param
    finally:
        restore()


@contextmanager
def _no_live_runners():
    """Own the precondition the db_stats path needs, for the call under test.

    A test whose subject is the db_stats branch has to establish that the
    live-runner branch is not taken. Asserting over whatever the worker left
    behind is the CLAUDE.md section 0 shape: the denominator excluded the
    subject, and the check reported a plausible answer about the wrong path.
    """
    restore = _set_runners({})
    try:
        yield
    finally:
        restore()


# ── Source-grep contracts ─────────────────────────────────────────


def test_avg_size_collector_present():
    body = _API_PY.read_text(encoding="utf-8")
    # AVG(file_size) query is the canonical shape.
    assert re.search(r"AVG\(file_size\)", body), (
        "avg_size collector must query AVG(file_size) over history"
    )
    assert "avg_size_fmt" in body, (
        "avg_size collector must emit avg_size_fmt"
    )


def test_eta_clear_collector_present():
    body = _API_PY.read_text(encoding="utf-8")
    assert "eta_clear_fmt" in body
    assert "eta_clear_extra" in body


def test_cookies_oldest_collector_present():
    body = _API_PY.read_text(encoding="utf-8")
    assert "cookies_oldest_days" in body
    assert "cookies_oldest_site" in body
    # Reuses doctor.cookie_freshness rather than reimplementing
    # the cookie-file walk.
    assert "cookie_freshness" in body, (
        "cookies_oldest must reuse doctor.cookie_freshness, not "
        "reimplement the cookie-file walk"
    )


# ── eta_clear behaviour ─────────────────────────────────────────


def test_eta_clear_says_now_when_queue_empty(clean_workdir, ambient_runners):
    """queue_depth=0 → 'now' (queue empty). The widget intent is
    'how long until queue drains'; if it's already drained, 'now'
    is the most readable answer.

    clean_workdir + db_init: `_collect_data` reads `history` and `library`
    DIRECTLY -- the db_stats / dashboard_widgets patches below do not cover
    that path. Without our own initialized database the result depends on
    whichever tables an EARLIER file happened to create (serial pass) or not
    create (parallel worker: "no such table: history" -> eta falls back to
    "now"). Measured at v3.66.922 as the first real-capture refutation of the
    @921 promotion. Seeding our own empty schema makes the test's denominator
    its own, on any worker.
    """
    _own_empty_schema()
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)

        # This test's expected value is ALSO what a leaked runner produces, so
        # without owning the precondition it would pass for the wrong reason on
        # exactly the runs where its two siblings fail.
        with _no_live_runners():
            # db_stats may not seed queue_depth if history table is empty;
            # we can force the queue_depth = 0 by patching db_stats.
            with patch("bulk_downloader.db.db_stats", return_value={
                "counts": {"queued": 0, "pending": 0, "stuck": 0,
                           "failed_recent": 0, "retry_pending": 0,
                           "login_required": 0, "captcha": 0}}):
                out = app_widgets_api._collect_data(None)

        assert out.get("eta_clear_fmt") == "now", (
            f"empty queue should give eta='now', got {out.get('eta_clear_fmt')!r}"
        )
        assert out.get("eta_clear_extra") == "queue empty"
    finally:
        sys.path.pop(0)


def test_eta_clear_absent_when_no_throughput(clean_workdir, ambient_runners):
    """queue_depth > 0 but files_hour = 0 → can't forecast → key
    absent → renderer shows '—'. Better than rendering 'inf' or '∞'.
    """
    _own_empty_schema()  # see test_eta_clear_says_now_when_queue_empty
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)
        # Patch db_stats to seed queue_depth = 5 and ensure
        # dashboard_widgets.snapshot returns files_hour = 0. The patched depth
        # only reaches the forecast while no live runner outranks it.
        with _no_live_runners(), patch(
                "bulk_downloader.db.db_stats", return_value={
                    "counts": {"queued": 5, "pending": 0, "stuck": 0,
                               "failed_recent": 0, "retry_pending": 0,
                               "login_required": 0, "captcha": 0}}):
            with patch("bulk_downloader.dashboard_widgets.snapshot",
                       return_value={"done_today": 0, "done_hour": 0,
                                     "files_hour": 0, "throughput_fmt": None,
                                     "success_rate_pct": None,
                                     "bytes_today": "0 B",
                                     "avg_speed_fmt": None,
                                     "bandwidth_fmt": None}):
                out = app_widgets_api._collect_data(None)

            # Explicit None is the stable schema contract when forecast is
            # unavailable; the renderer still shows an honest em dash.
            assert out.get("eta_clear_fmt") is None
    finally:
        sys.path.pop(0)


def test_eta_clear_hours_format_for_short_queues(clean_workdir, ambient_runners):
    """A queue that drains in <1h gets 'Xm' (minutes); 1-24h gets
    'X.Yh' (hours); >24h gets 'X.Yd' (days). The human-readability
    convention matches eta_clear_fmt's display intent.

    files_hour is driven through REAL history rows, not the snapshot patch:
    `_collect_data` overlays canonical history over the snapshot whenever the
    history table exists ("canonical history overlays these"), so a patched
    snapshot files_hour is structurally unreachable once this test owns a real
    schema. Measured: with an empty seeded history the patched 60 was
    overwritten to 0 and eta came back None. Sixty done-rows in the last hour
    make the canonical path itself say 60/hr.
    """
    _own_empty_schema()  # see test_eta_clear_says_now_when_queue_empty
    from bulk_downloader import db
    for i in range(60):
        db.db_log("u50_site", "U50", f"https://example.invalid/{i}",
                  "done", filename=f"f{i}.mp4", file_size=1000)
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)
        # 30 queued at 60/hr → 0.5h → 30m
        with _no_live_runners(), patch(
                "bulk_downloader.db.db_stats", return_value={
                    "counts": {"queued": 30, "pending": 0, "stuck": 0,
                               "failed_recent": 0, "retry_pending": 0,
                               "login_required": 0, "captcha": 0}}):
            with patch("bulk_downloader.dashboard_widgets.snapshot",
                       return_value={"done_today": 0, "done_hour": 0,
                                     "files_hour": 0, "throughput_fmt": None,
                                     "success_rate_pct": None,
                                     "bytes_today": "0 B"}):
                out = app_widgets_api._collect_data(None)
        assert out.get("eta_clear_fmt") == "30m", (
            f"expected '30m' for 30/60hr, got {out.get('eta_clear_fmt')!r}"
        )
    finally:
        sys.path.pop(0)


# ── avg_size behaviour ─────────────────────────────────────────


def test_avg_size_absent_when_no_history(clean_workdir):
    """No completed rows in the last 24h → key absent → renderer '—'.

    An empty `history` table -- OURS, created below, not an assumption
    about the ambient sandbox -- makes the collector's AVG return NULL;
    the `if r and r["avg_size"]` guard rejects None.
    """
    _own_empty_schema()  # see test_eta_clear_says_now_when_queue_empty
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)
        out = app_widgets_api._collect_data(None)
        # No avg_size_fmt key — collector saw no completed rows.
        assert "avg_size_fmt" not in out or out.get("avg_size_fmt") is None
    finally:
        sys.path.pop(0)


# ── cookies_oldest behaviour ──────────────────────────────────


def test_cookies_oldest_absent_when_no_sites(clean_workdir):
    """No sites configured → cookie_freshness returns empty list →
    no aged entries → both keys stay absent → renderer '—'.
    """
    _own_empty_schema()  # see test_eta_clear_says_now_when_queue_empty
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)
        import importlib as _il
        app_mod = _il.import_module("bulk_downloader.app_state")

        original_s_cfg = getattr(app_mod, "s_cfg", None)
        app_mod.s_cfg = {}
        try:
            out = app_widgets_api._collect_data(None)
        finally:
            if original_s_cfg is None:
                if hasattr(app_mod, "s_cfg"):
                    del app_mod.s_cfg
            else:
                app_mod.s_cfg = original_s_cfg

            assert out.get("cookies_oldest_days") is None
        assert "cookies_oldest_site" not in out
    finally:
        sys.path.pop(0)


def test_cookies_oldest_picks_max_age_site(clean_workdir):
    """With multiple sites of different cookie ages, the collector
    picks the OLDEST (= most due for renewal) and surfaces it.
    """
    _own_empty_schema()  # see test_eta_clear_says_now_when_queue_empty
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)
        import importlib as _il
        app_mod = _il.import_module("bulk_downloader.app_state")

        # Mock cookie_freshness to return three fake sites.
        fake_checks = [
            {"site": "site_a", "age_days": 3.2, "status": "ok"},
            {"site": "site_b", "age_days": 27.8, "status": "warn"},
            {"site": "site_c", "age_days": 10.0, "status": "ok"},
        ]
        original_s_cfg = getattr(app_mod, "s_cfg", None)
        # Need a truthy s_cfg so the collector enters the branch.
        app_mod.s_cfg = {"site_a": {"cookie_file": "x"}}
        try:
            with patch("bulk_downloader.doctor.cookie_freshness",
                       return_value=fake_checks):
                out = app_widgets_api._collect_data(None)
        finally:
            if original_s_cfg is None:
                if hasattr(app_mod, "s_cfg"):
                    del app_mod.s_cfg
            else:
                app_mod.s_cfg = original_s_cfg

        # site_b is the oldest at 27.8 days.
        assert out.get("cookies_oldest_days") == 27, (
            f"expected 27 (max age), got {out.get('cookies_oldest_days')!r}"
        )
        assert out.get("cookies_oldest_site") == "site_b", (
            f"expected 'site_b' (max-age site), got "
            f"{out.get('cookies_oldest_site')!r}"
        )
    finally:
        sys.path.pop(0)
