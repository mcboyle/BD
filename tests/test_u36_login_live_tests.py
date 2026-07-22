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


_LEVELS = (h.PASS, h.WARN, h.FAIL)


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


def test_l7_no_db_is_warn():
    level, detail = _get_test("L7").fn(_dead_ctx())
    assert level == h.WARN
    assert "no DB" in detail


def test_both_return_valid_tuples():
    for tid in ("L6", "L7"):
        res = _get_test(tid).fn(_dead_ctx())
        assert isinstance(res, tuple) and len(res) == 2
        assert res[0] in _LEVELS
        assert isinstance(res[1], str) and res[1]


# ── L7 against a real sandbox DB (read-only over session_history) ──

def test_l7_warns_with_empty_session_history(clean_workdir):
    db.db_init()
    ctx = h.Context("http://localhost:1", str(clean_workdir))
    level, detail = _get_test("L7").fn(ctx)
    assert level == h.WARN
    assert "no login events" in detail


def test_l7_passes_when_a_fallback_event_exists(clean_workdir):
    db.db_init()
    db.session_event_record("s1", None, "login_template_fallback",
                            "template failed; opened manual login")
    ctx = h.Context("http://localhost:1", str(clean_workdir))
    level, detail = _get_test("L7").fn(ctx)
    assert level == h.PASS
    assert "login_template_fallback" in detail


def test_l7_warns_with_logins_but_no_fallback(clean_workdir):
    db.db_init()
    # login activity exists, but the Phase B fallback never fired
    db.session_event_record("s1", None, "login", "ok")
    ctx = h.Context("http://localhost:1", str(clean_workdir))
    level, detail = _get_test("L7").fn(ctx)
    # never fired is not a failure — the templates may all just work
    assert level == h.WARN


def test_l7_ignores_fallbacks_from_removed_sites(clean_workdir):
    db.db_init()
    db.session_event_record("removed", None, "login_template_fallback",
                            "orphaned site event")
    db.session_event_record("current", None, "login", "ok")
    ctx = _AuthHealthContext([], {"current": {"state": "idle"}},
                             clean_workdir / "downloader_history.db")
    level, detail = _get_test("L7").fn(ctx)
    assert level == h.WARN
    assert "no Phase B fallback" in detail


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
    def __init__(self, cookie_file, *, durable_site="s1"):
        self.cookie_file = str(cookie_file)
        self.durable_site = durable_site
        self.messages = []

    def get(self, path, timeout=15):
        if path == "/api/sites/v2":
            return True, 200, {
                "sites": [{"site_id": "s1", "auth_state": "idle"}],
            }, 1.0
        if path == "/api/auth_health/status":
            return True, 200, {
                "sites": [{"site_id": self.durable_site, "status": "green"}],
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
