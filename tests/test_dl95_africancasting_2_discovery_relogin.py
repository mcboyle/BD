"""dl95-africancasting-2 (O1513 A1-A finding A2): scene discovery vs a server-dropped session.

Measured on test2 v3.66.1706: the first africancasting scan returned NOT_LOGGED_IN with 0 discovered while
/api/sites/v2 said auth_state="ok"; after a runner run re-logged in, the same scan COMPLETED. The jar held
session cookies (no expiry), which _m2_auth_state reads as a live login, but the server had dropped them.

GREEN: a crawl that meets the login wall logs in with the stored credentials (runner.login_async, gated and
bounded like runner_auth._check_cookies_or_relogin) and walks once more; a jar that still meets the wall is
reported auth_state="expired" until a login replaces it.

Hermetic: the browser page and the page walk are replaced (scene_crawler._runner_page / crawl_with_page);
the run row lives in a tmp sqlite. No site, browser or network is touched.
"""
from __future__ import annotations

import contextlib
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bulk_downloader import scene_crawler as crawler  # noqa: E402
from bulk_downloader.app import _m2_auth_state  # noqa: E402

LISTING = "https://members.example.test/videos"
STALE = [{"name": "PHPSESSID", "value": "stale", "domain": "members.example.test", "path": "/", "expires": -1}]
FRESH = [{"name": "PHPSESSID", "value": "fresh", "domain": "members.example.test", "path": "/", "expires": -1}]


def _fp(jar):
    return tuple(sorted((c["domain"], c["path"], c["name"], c["value"]) for c in jar))


class _Runner:
    def __init__(self, cookies, *, creds=True, login_ok=True, new_jar=FRESH):
        self.cookies = list(cookies)
        self.config = {"username": "u", "password": "p"} if creds else {}
        self.login_ok, self.new_jar = login_ok, new_jar
        self.login_calls = []

    def login_async(self, on_done=None, allow_manual=True):
        self.login_calls.append({"allow_manual": allow_manual})
        if self.login_ok:
            self.cookies = list(self.new_jar)
        on_done(self.login_ok)


def _result(state, discovered=0):
    return crawler._run_base(
        state=state, effective_url=LISTING, pages_walked=1, page_urls=[LISTING], scroll_growth_steps=0,
        scroll_settle_state=crawler.SETTLE_SETTLED, scroll_late_growth_steps=0, shapes=set(), scenes=[],
        discovered=discovered, queued=discovered, title_pages_fetched=0, zero_scenes_found=False)


@pytest.fixture
def walk(monkeypatch):
    """Fake page walk: the server accepts only the jars in `live`; records the jar each walk carried."""
    seen = {"jars": [], "live": {_fp(FRESH)}}

    @contextlib.contextmanager
    def fake_runner_page(runner, _site_id):
        yield list(runner.cookies)

    def fake_crawl(page_jar, **kw):
        seen["jars"].append(page_jar)
        live = _fp(page_jar) in seen["live"]
        result = _result(crawler.STATE_COMPLETED if live else crawler.STATE_NOT_LOGGED_IN, discovered=2 if live else 0)
        crawler._finish_run(kw.get("run_id"), result, kw.get("db_path"))  # as the real walk does
        return result

    monkeypatch.setattr(crawler, "_runner_page", fake_runner_page)
    monkeypatch.setattr(crawler, "crawl_with_page", fake_crawl)
    return seen


def _crawl(runner, tmp_path):
    db = str(tmp_path / "crawl.sqlite")
    started = crawler.start_background_crawl(
        site_id="africancasting", listing_url=LISTING, site_config={}, runner=runner,
        enqueue_fn=lambda *_: None, db_path=db)
    deadline = time.time() + 10
    while time.time() < deadline:
        st = crawler.crawl_status(site_id="africancasting", run_id=started["run_id"], db_path=db)
        if st["state"] != crawler.STATE_RUNNING:
            return st
        time.sleep(0.02)
    pytest.fail("crawl thread did not finish in 10s")


def test_precondition_stale_session_jar_reads_ok_before_any_crawl():
    # the finding's shape: a session-cookie jar is "ok" by cookie bookkeeping alone
    assert _m2_auth_state(_Runner(STALE), {}) == "ok"


def test_stale_session_with_stored_creds_logs_in_and_completes(walk, tmp_path):
    runner = _Runner(STALE)
    st = _crawl(runner, tmp_path)
    assert runner.login_calls == [{"allow_manual": False}]
    assert [_fp(j) for j in walk["jars"]] == [_fp(STALE), _fp(FRESH)]
    assert (st["state"], st["discovered"], st.get("relogin")) == (crawler.STATE_COMPLETED, 2, "ok")
    assert _m2_auth_state(runner, {}) == "ok"


def test_stale_session_without_creds_reports_not_logged_in_and_auth_expired(walk, tmp_path):
    runner = _Runner(STALE, creds=False)
    st = _crawl(runner, tmp_path)
    assert runner.login_calls == []
    assert (st["state"], st.get("relogin")) == (crawler.STATE_NOT_LOGGED_IN, "no_credentials")
    assert _m2_auth_state(runner, {}) == "expired"


def test_failed_login_is_tried_once_and_the_dead_jar_reads_expired(walk, tmp_path):
    runner = _Runner(STALE, login_ok=False)
    st = _crawl(runner, tmp_path)
    assert len(runner.login_calls) == 1 and len(walk["jars"]) == 1
    assert (st["state"], st.get("relogin")) == (crawler.STATE_NOT_LOGGED_IN, "failed")
    assert _m2_auth_state(runner, {}) == "expired"


def test_login_that_yields_another_dead_jar_marks_that_jar(walk, tmp_path):
    other = [dict(FRESH[0], value="also-dead")]
    runner = _Runner(STALE, new_jar=other)
    st = _crawl(runner, tmp_path)
    assert (st["state"], st.get("relogin")) == (crawler.STATE_NOT_LOGGED_IN, "ok")
    assert _m2_auth_state(runner, {}) == "expired"


def test_expired_mark_is_keyed_on_the_jar_and_clears_when_it_walks_again(walk, tmp_path):
    runner = _Runner(STALE, creds=False)
    _crawl(runner, tmp_path)
    assert _m2_auth_state(runner, {}) == "expired"
    runner.cookies = list(FRESH)  # any login replaces the jar
    assert _m2_auth_state(runner, {}) == "ok"
    runner.cookies = list(STALE)
    assert _m2_auth_state(runner, {}) == "expired"
    walk["live"].add(_fp(STALE))  # the server accepts that jar again
    st = _crawl(runner, tmp_path)
    assert st["state"] == crawler.STATE_COMPLETED and "relogin" not in st
    assert _m2_auth_state(runner, {}) == "ok"


def test_jar_fingerprint_ignores_order_and_sees_a_changed_value():
    from bulk_downloader.cookies import jar_fingerprint
    a = {"name": "a", "value": "1", "domain": "d", "path": "/"}
    b = {"name": "b", "value": "2", "domain": "d", "path": "/"}
    assert jar_fingerprint([a, b]) == jar_fingerprint([b, a])
    assert jar_fingerprint([a, b]) != jar_fingerprint([a, dict(b, value="3")])
