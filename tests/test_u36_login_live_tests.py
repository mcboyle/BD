"""U36 — L6 (real-templated-auto-login) + L7 (phase-b-fallback), the
two login-subsystem live tests.

Both are deliberately READ-ONLY: they inspect login state/config the
app already records, and never fire a real login (which could trip a
captcha or a billing upsell — lesson I1). So both are disruptive=False.
Unit-testable here: registration, the non-disruptive flag, graceful
degradation, and L7's session_history logic against a sandbox DB.
"""
import live_tests.checks as checks  # noqa: F401 (registers the checks)
import live_tests.harness as h
from bulk_downloader import db
import time


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

def test_l6_l7_registered():
    ids = {t.id for t in h.registry()}
    assert "L6" in ids and "L7" in ids


def test_login_tests_are_not_disruptive():
    # they are read-only — they never fire a real login, so they are
    # safe to run any time (no captcha / no billing-upsell risk)
    assert _get_test("L6").disruptive is False
    assert _get_test("L7").disruptive is False


# ── graceful degradation ───────────────────────────────────────────

def _dead_ctx():
    return h.Context("http://localhost:1", "/tmp/u36_no_dir")


def test_l6_unreachable_is_warn():
    level, detail = _get_test("L6").fn(_dead_ctx())
    assert level == h.WARN
    assert "not testable" in detail or "unreachable" in detail


class _AuthHealthContext:
    def __init__(self, auth_sites, configured_sites=None, db_path=None):
        self.auth_sites = auth_sites
        self.configured_sites = configured_sites or {"s1": {"state": "idle"}}
        self.db_path = db_path

    def get(self, path, timeout=15):
        if path == "/api/auth_health/status":
            return True, 200, {"sites": self.auth_sites}, 1.0
        if path == "/api/status":
            return True, 200, self.configured_sites, 1.0
        return False, 404, {}, 1.0

    def log(self, _message):
        pass

    def ro_db(self):
        import sqlite3
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)


def test_l6_accepts_auth_health_list_and_green_status():
    ctx = _AuthHealthContext([{"site_id": "s1", "status": "green"}])
    level, detail = _get_test("L6").fn(ctx)
    assert level == h.PASS
    assert "healthy authenticated" in detail


def test_l6_ignores_an_auth_health_row_whose_site_no_longer_exists():
    """A green row outlives its site, and must not carry L6's verdict.

    Nothing prunes the auth_health table. DELETE /api/sites/<sid> stops the
    runner, drops the account pool, deletes the queue rows and removes the
    site from s_cfg -- and leaves the auth_health row untouched (driven
    against the real handler: it answers {'ok': True} while the row survives
    a subsequent GET /api/auth_health/status). There is no DELETE, prune or
    sweep of that table anywhere in bulk_downloader/ or tools/.

    Site ids are uuid4().hex[:8], so a site is never re-created under its old
    id and the row is orphaned permanently. Every capture that probes a
    seeded fixture-login site therefore leaves ANOTHER green corpse behind.
    From the second capture onward the first one satisfies L6's
    `if healthy: PASS` branch before this run's own result is even reached,
    and L6 can no longer fail -- CLAUDE.md 0, and self-inflicted.

    The denominator must be sites the deployment currently HAS.
    """
    ctx = _AuthHealthContext(
        [{"site_id": "deleted_two_captures_ago", "status": "green"},
         {"site_id": "s1", "status": "yellow"}],
        configured_sites={"s1": {"state": "idle"}})
    level, detail = _get_test("L6").fn(ctx)
    assert level == h.WARN, (
        f"L6 returned {level} on a green auth-health row for "
        f"'deleted_two_captures_ago', a site that is not in /api/status. The "
        f"only current site reports yellow. A verdict carried by a deleted "
        f"site's corpse can never fail: {detail}"
    )


def test_l6_will_not_pass_when_it_cannot_tell_live_sites_from_orphans():
    """Unknown is a third state, and it fails.

    If the configured-site list cannot be read, every stored row is
    indistinguishable from an orphan, so a PASS would be an assertion about a
    denominator L6 could not see.
    """
    class _NoStatus(_AuthHealthContext):
        def get(self, path, timeout=15):
            if path == "/api/status":
                return False, 500, None, 1.0
            return super().get(path, timeout)

    ctx = _NoStatus([{"site_id": "s1", "status": "green"}])
    level, detail = _get_test("L6").fn(ctx)
    assert level == h.WARN, (
        f"L6 returned {level} while it could not enumerate configured sites, "
        f"so it could not tell a live row from an orphan: {detail}"
    )


def test_l7_no_db_is_not_exercisable():
    # No DB -> the fallback cannot be observed. N/A, not WARN: a missing
    # DB is already FAILed by L22/L26 (whose subject IS the DB); a second
    # unclearable WARN here would be a false alarm (CLAUDE.md sec0 inverse).
    level, detail = _get_test("L7").fn(_dead_ctx())
    assert level == h.NA
    assert "no DB" in detail


def test_both_return_valid_tuples():
    for tid in ("L6", "L7"):
        res = _get_test(tid).fn(_dead_ctx())
        assert isinstance(res, tuple) and len(res) == 2
        assert res[0] in _LEVELS
        assert isinstance(res[1], str) and res[1]


# ── L7 against a real sandbox DB (read-only over session_history) ──

def test_l7_empty_session_history_is_not_exercisable(clean_workdir):
    db.db_init()
    ctx = h.Context("http://localhost:1", str(clean_workdir))
    level, detail = _get_test("L7").fn(ctx)
    assert level == h.NA
    assert "no login events" in detail


def test_l7_passes_when_a_fallback_event_exists(clean_workdir):
    db.db_init()
    db.session_event_record("s1", None, "login_template_fallback",
                            "template failed; opened manual login")
    ctx = h.Context("http://localhost:1", str(clean_workdir))
    level, detail = _get_test("L7").fn(ctx)
    assert level == h.PASS
    assert "login_template_fallback" in detail


def test_l7_logins_but_no_fallback_is_observed_na(clean_workdir):
    db.db_init()
    # login activity exists, but the Phase B fallback never fired
    db.session_event_record("s1", None, "login", "ok")
    ctx = h.Context("http://localhost:1", str(clean_workdir))
    level, detail = _get_test("L7").fn(ctx)
    # events exist but none is a fallback -> observed, nothing to judge.
    # N/A, not WARN (never-fired is not a fault). The message must NOT
    # claim the takeover "is wired": start_manual_login can never open
    # from inside login_async's own _run thread, so an event proves
    # recording, not takeover (runner_auth re-entrancy -- separate cut).
    assert level == h.NA
    assert "no failure to assess" in detail
    assert "wired" not in detail.lower()


def test_l7_ignores_fallbacks_from_removed_sites(clean_workdir):
    db.db_init()
    db.session_event_record("removed", None, "login_template_fallback",
                            "orphaned site event")
    db.session_event_record("current", None, "login", "ok")
    ctx = _AuthHealthContext([], {"current": {"state": "idle"}},
                             clean_workdir / "downloader_history.db")
    level, detail = _get_test("L7").fn(ctx)
    # the removed-site fallback is filtered out by the site scope; the
    # current site shows login activity but no fallback -> observed N/A
    # (branch D), not WARN. The old "no Phase B fallback" phrase is gone.
    assert level == h.NA
    assert "no failure to assess" in detail


def test_l7_unreadable_db_is_fail(clean_workdir):
    # REGRESSION GUARD (passes on pristine): a corrupt session_history is
    # a real, observable problem -- the ONE L7 state that must stay FAIL.
    # N/A must not swallow it. Mutation M6 flips it to N/A; this catches it.
    dbp = clean_workdir / "downloader_history.db"
    dbp.write_text("not a database")
    ctx = h.Context("http://localhost:1", str(clean_workdir))
    level, detail = _get_test("L7").fn(ctx)
    assert level == h.FAIL
    assert "could not read session_history" in detail


def test_l7_na_messages_are_textually_distinct(clean_workdir):
    # The three N/A states must not collapse into one message -- collapsing
    # observed vs not-exercisable was the real defect in the L12 cut.
    nodb = _get_test("L7").fn(_dead_ctx())              # branch A (no DB)

    db.db_init()
    empty_ctx = h.Context("http://localhost:1", str(clean_workdir))
    notexerc = _get_test("L7").fn(empty_ctx)            # branch E (no events)

    db.session_event_record("s1", None, "login", "ok")
    obs_ctx = h.Context("http://localhost:1", str(clean_workdir))
    observed = _get_test("L7").fn(obs_ctx)              # branch D (observed)

    assert nodb[0] == notexerc[0] == observed[0] == h.NA
    details = {nodb[1], notexerc[1], observed[1]}
    assert len(details) == 3, f"N/A messages collapsed: {sorted(details)}"


def test_l7_na_flows_through_run_all_without_failing(tmp_path):
    # run_all-enforcement lesson: every other L7 test calls the check
    # directly and never reaches run_all's `if level not in _LEVELS: FAIL`
    # guard (harness.py:280). If N/A left _LEVELS, L7's N/A would be
    # rewritten to FAIL on the box while this suite stayed green. This runs
    # L7 THROUGH the harness (no-DB home -> branch A -> N/A) and asserts the
    # summary records N/A and the run still exits 0.
    rdir = tmp_path / "results"
    home = tmp_path / "empty_home"          # no downloader_history.db here
    home.mkdir()
    code = h.run_all("http://localhost:1", str(home),
                     only=["L7"], results_dir=rdir)
    assert code == 0
    summary = (rdir / "SUMMARY.txt").read_text(encoding="utf-8")
    assert "N/A  L7" in summary, f"run_all did not record L7 N/A:\n{summary}"


# ── harness integration ────────────────────────────────────────────

def test_l6_l7_run_via_harness(tmp_path):
    rdir = tmp_path / "results"
    # non-disruptive -> run on a default run; WARN against a dead
    # target -> exit 0
    code = h.run_all("http://localhost:1", str(tmp_path),
                     only=["L6", "L7"], results_dir=rdir)
    assert code == 0
    assert (rdir / "L6.log").is_file()
    assert (rdir / "L7.log").is_file()


class _CookieJarContext:
    def __init__(self, cookie_file, *, durable_site="s1", checked_at=None):
        self.cookie_file = str(cookie_file)
        self.durable_site = durable_site
        self.checked_at = time.time() if checked_at is None else checked_at
        self.messages = []

    def get(self, path, timeout=15):
        if path == "/api/sites/v2":
            return True, 200, {
                "sites": [{"site_id": "s1", "auth_state": "idle"}],
            }, 1.0
        if path == "/api/auth_health/status":
            return True, 200, {
                "sites": [{
                    "site_id": self.durable_site,
                    "status": "green",
                    "last_check_ts": self.checked_at,
                }],
            }, 1.0
        if path == "/api/status":
            return True, 200, {
                "s1": {"config": {"cookie_file": self.cookie_file}},
            }, 1.0
        return False, 404, {}, 1.0

    def log(self, message):
        self.messages.append(message)


def test_l8_uses_durable_green_auth_after_service_restart(tmp_path):
    jar = tmp_path / "cookies.json"
    jar.write_text('[{"name":"session","value":"fixture"}]', encoding="utf-8")
    ctx = _CookieJarContext(jar)

    level, detail = _get_test("L8").fn(ctx)

    assert level == h.PASS
    assert "non-empty cookies" in detail
    assert any("durable" in message for message in ctx.messages)


def test_l8_ignores_durable_auth_for_removed_site(tmp_path):
    jar = tmp_path / "cookies.json"
    jar.write_text('[{"name":"session","value":"fixture"}]', encoding="utf-8")
    ctx = _CookieJarContext(jar, durable_site="removed")

    level, detail = _get_test("L8").fn(ctx)

    assert level == h.WARN
    assert "none report auth_state=ok" in detail


def test_l8_durable_auth_still_rejects_empty_cookie_file(tmp_path):
    jar = tmp_path / "cookies.json"
    jar.write_text("", encoding="utf-8")
    ctx = _CookieJarContext(jar)

    level, detail = _get_test("L8").fn(ctx)

    assert level == h.FAIL
    assert "empty" in detail


def test_l8_ignores_stale_durable_auth_health(tmp_path):
    jar = tmp_path / "cookies.json"
    jar.write_text('[{"name":"session","value":"fixture"}]', encoding="utf-8")
    ctx = _CookieJarContext(jar, checked_at=time.time() - (3 * 86400))

    level, detail = _get_test("L8").fn(ctx)

    assert level == h.WARN
    assert "none report auth_state=ok" in detail
