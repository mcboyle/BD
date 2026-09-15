"""Row 723 (residual) -- a real-Chrome -> bundled-Chromium degradation in a
LOGIN FLOW is filed under the site that ran it and reaches that site's run
record.

v3.66.1507 (cut/row723-chrome-fallback-run-record) taught the runner's own
launcher and ``cloak.persistent_context`` to note the degradation in a
drainable ledger and gave BrowserMixin ``_surface_pending_channel_fallbacks``
to drain it. Three residuals stayed open on 822b7330, each measured here:

  * ``login_impl/submit.py`` (do_login) and ``login_impl/manual.py``
    (ManualLoginSession) retry the launch without the channel and write ONE
    stderr line -- no ledger note, no site id.  A degradation that leaves no
    note is indistinguishable from a launch that worked (A2).
  * a site config carries no ``site_id`` key (it is not a CFG_FIELD), so even
    ``persistent_context`` files its note under ``""`` -- a key no site's
    drain can match.  The owner of the flow is the runner (or the keeper),
    which knows its site: it declares it with ``cloak.owning_site``.
  * ``_surface_pending_channel_fallbacks`` had no product caller.

Offline: no browser, no network, no site. Every launch entry point is
monkeypatched; the login form is a duck-typed page.
"""
import contextlib
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import bulk_downloader.cloak as cloak

BD_GATE_SCOPE = "module"

# Documented zero-entropy fixture values -- not secrets, not sites.
_SITE = "row723-login-site"
_OTHER = "row723-other-site"
LOGIN_URL = "https://login.example.invalid/login"
_CHROME_MISSING = ("Chromium distribution 'chrome' is not found at "
                   "/opt/google/chrome/chrome")


def _login_notes(site_id):
    """Ledger notes filed under `site_id` by a LOGIN flow (drains them)."""
    return [n for n in cloak.drain_channel_fallbacks(site_id)
            if "login" in n["flow"]]


def _chrome_absent_launch(monkeypatch, browser, *, retry_also_fails=False):
    """cloak.launch_browser: raise iff channel="chrome"; the bundled retry
    succeeds unless asked to fail. Returns the exact call log."""
    calls = []

    def _launch(**kw):
        calls.append(dict(kw))
        if kw.get("channel") == "chrome":
            raise RuntimeError(_CHROME_MISSING)
        if retry_also_fails:
            raise RuntimeError("bundled Chromium failed too")
        return browser, None, "fixture"

    monkeypatch.setattr(cloak, "launch_browser", _launch)
    return calls


def _chrome_absent_persistent(monkeypatch, ctx, *, retry_also_fails=False):
    calls = []

    def _open(**kw):
        calls.append(dict(kw))
        if kw.get("channel") == "chrome":
            raise RuntimeError(_CHROME_MISSING)
        if retry_also_fails:
            raise RuntimeError("bundled Chromium failed too")
        return ctx, None, "fixture"

    monkeypatch.setattr(cloak, "open_persistent_context", _open)
    return calls


# ── do_login (login_impl/submit.py) ────────────────────────────────────────

class _Stop(Exception):
    """Raised by the fixture page the moment the flow touches it: the launch
    arms have run, nothing after them is this row's."""


def _drive_do_login(monkeypatch, tmp_path, *, config_extra=None,
                    retry_also_fails=False):
    """Drive the real do_login up to and including its launch retry."""
    from bulk_downloader.login_impl import submit

    def _stop(*a, **kw):
        raise _Stop("login flow reached the page")
    ctx = SimpleNamespace(new_page=_stop, cookies=lambda: [], close=lambda: None)
    browser = SimpleNamespace(new_context=lambda **kw: ctx, close=lambda: None)
    calls = _chrome_absent_launch(monkeypatch, browser,
                                  retry_also_fails=retry_also_fails)
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    config = {"login_url": LOGIN_URL, "username": "fixture",
              "password": "zero-entropy-password", "wait": 0,
              "use_real_chrome": True, "use_stealth": False,
              "login_evidence_dir": str(tmp_path / "evidence")}
    config.update(config_extra or {})
    # do_login's outer handler turns any exception into (False, "login error:
    # <text>", []) -- the fixture's stop marker rides that text.
    result = submit.do_login(config, allow_manual_takeover=False)
    assert result[0] is False and result[1].startswith("login error: "), result
    return calls, result[1]


def test_row723_the_login_submit_retry_files_its_degradation_under_the_site(
        monkeypatch, tmp_path):
    cloak.reset_cache_for_tests()
    calls, stopped = _drive_do_login(monkeypatch, tmp_path,
                                     config_extra={"site_id": _SITE})
    # Fixture shape first: the chrome attempt failed and the bundled retry ran.
    assert "reached the page" in stopped, stopped
    assert [c.get("channel") for c in calls] == ["chrome", None], calls
    assert len(calls) == 2, calls   # exact: one channel attempt + one retry
    notes = _login_notes(_SITE)
    assert len(notes) == 1, (
        "the login submit retry left no note in the ledger for the site, "
        f"so its run record can never learn of the degradation: {notes!r}")
    assert notes[0]["recovered"] is True and notes[0]["channel"] == "chrome"
    assert _CHROME_MISSING[:30] in notes[0]["error"], notes[0]


def test_row723_a_login_submit_retry_that_also_fails_is_filed_not_dropped(
        monkeypatch, tmp_path):
    cloak.reset_cache_for_tests()
    calls, err = _drive_do_login(monkeypatch, tmp_path,
                                 config_extra={"site_id": _SITE},
                                 retry_also_fails=True)
    assert "bundled Chromium failed too" in err, err
    assert len(calls) == 2, calls
    notes = _login_notes(_SITE)
    assert len(notes) == 1, f"a failed bundled retry left no note: {notes!r}"
    assert notes[0]["recovered"] is False, notes[0]


def test_row723_the_owning_site_names_a_flow_whose_config_has_no_site_id(
        monkeypatch, tmp_path):
    """A real site config has no site_id key; the runner that owns the flow
    declares the site around it."""
    cloak.reset_cache_for_tests()
    with cloak.owning_site(_SITE):
        calls, stopped = _drive_do_login(monkeypatch, tmp_path)
    assert "reached the page" in stopped and len(calls) == 2, (stopped, calls)
    assert cloak.ledger_site_id({}) == "", "the declaration must not outlive its block"
    notes = _login_notes(_SITE)
    assert len(notes) == 1 and notes[0]["site_id"] == _SITE, notes
    assert cloak.drain_channel_fallbacks("") == [], "nothing may be filed under no site"


# ── ManualLoginSession (login_impl/manual.py) ─────────────────────────────

def _drive_manual_launch(monkeypatch, *, persistent, retry_also_fails=False,
                         config_extra=None):
    """Run ManualLoginSession._launch on the calling thread (no worker
    thread, no browser) up to its first page touch."""
    from bulk_downloader.login_impl import manual

    def _stop(*a, **kw):
        raise _Stop("manual flow reached the page")
    ctx = SimpleNamespace(pages=[], new_page=_stop,
                          add_init_script=lambda *a, **k: None)
    browser = SimpleNamespace(new_context=lambda **kw: ctx, close=lambda: None)
    if persistent:
        calls = _chrome_absent_persistent(monkeypatch, ctx,
                                          retry_also_fails=retry_also_fails)
        monkeypatch.setattr(cloak, "launch_browser",
                            lambda **kw: (browser, None, "fixture"))
    else:
        calls = _chrome_absent_launch(monkeypatch, browser,
                                      retry_also_fails=retry_also_fails)
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    config = {"login_url": LOGIN_URL, "use_real_chrome": True,
              "use_stealth": False}
    config.update(config_extra or {})
    session = manual.ManualLoginSession.__new__(manual.ManualLoginSession)
    manual.ManualLoginSession.__init__  # exists; not run (it starts a thread)
    session._config = config
    session._banner_js = ""
    session._manual_profile_dir = "/tmp/row723-manual-profile-never-opened" if persistent else None
    session._headless = True
    session._owning_site = getattr(cloak, "ledger_site_id",
                                   lambda c: (c or {}).get("site_id", ""))(config)
    with pytest.raises((_Stop, RuntimeError)) as info:
        session._launch()
    return calls, info.value


def test_row723_the_manual_login_persistent_retry_files_under_the_site(monkeypatch):
    cloak.reset_cache_for_tests()
    calls, stopped = _drive_manual_launch(monkeypatch, persistent=True,
                                          config_extra={"site_id": _SITE})
    assert isinstance(stopped, _Stop), stopped
    assert [c.get("channel") for c in calls] == ["chrome", None], calls
    notes = _login_notes(_SITE)
    assert len(notes) == 1, f"the manual persistent retry left no note: {notes!r}"
    assert notes[0]["recovered"] is True and "manual" in notes[0]["flow"], notes[0]


def test_row723_the_manual_login_non_persistent_retry_files_under_the_site(monkeypatch):
    cloak.reset_cache_for_tests()
    calls, stopped = _drive_manual_launch(monkeypatch, persistent=False,
                                          config_extra={"site_id": _SITE})
    assert isinstance(stopped, _Stop), stopped
    assert [c.get("channel") for c in calls] == ["chrome", None], calls
    notes = _login_notes(_SITE)
    assert len(notes) == 1, f"the manual non-persistent retry left no note: {notes!r}"
    assert notes[0]["recovered"] is True, notes[0]


def test_row723_a_manual_persistent_retry_that_also_fails_is_filed_not_dropped(monkeypatch):
    """manual.py reverts to the non-persistent path when the bundled
    persistent retry fails too; that harder failure is recorded, not lost."""
    cloak.reset_cache_for_tests()
    calls, _ = _drive_manual_launch(monkeypatch, persistent=True,
                                    retry_also_fails=True,
                                    config_extra={"site_id": _SITE})
    assert [c.get("channel") for c in calls] == ["chrome", None], calls
    notes = _login_notes(_SITE)
    assert len(notes) == 1 and notes[0]["recovered"] is False, notes


# ── the runner owns its login flow and surfaces the note ───────────────────

class _Log:
    def warning(self, *a, **k):
        pass


def _auth_runner():
    from bulk_downloader import runner_auth, runner_browser

    class _Runner(runner_auth.AuthMixin, runner_browser.BrowserMixin):
        def __init__(self):
            self.site_id = _SITE
            self.config = {"login_url": LOGIN_URL, "auto_teach_first_run": False}
            self._login_thread = None
            self._manual_login_handle = None
            self._login_status = ""
            self.log = _Log()
            self.cookies = []
            self.events = []

        def set_cookies(self, cookies):
            self.cookies = list(cookies)

        def log_event(self, kind, message, url=None, extra=None):
            self.events.append({"kind": kind, "message": str(message),
                                "url": url, "extra": dict(extra or {})})

        # relogin / manual-fallback collaborators (no DB, no jobs, no threads)
        def _update_job(self, url, status, message, **extra):
            self.events.append({"kind": "job", "message": message, "url": url, "extra": {}})

        def _handle_failure(self, url, message, *a, **k):
            self.events.append({"kind": "failure", "message": message, "url": url, "extra": {}})

        def _report_uncovered_session_scope(self, url):
            pass

        def _manual_profile_dir(self):
            return "/tmp/row723-manual-profile-never-opened"

        def _start_owned_auxiliary_thread(self, attribute, thread):
            setattr(self, attribute, thread)   # the runner's publish-then-start contract
            thread.start()
            return True
    return _Runner()


def _no_accounting(monkeypatch):
    """Grant every login attempt without a DB; pause/resume keepers are no-ops."""
    from bulk_downloader import session_keeper as _sk
    monkeypatch.setattr(_sk, "reserve_login_attempt", lambda *a, **k: {
        "granted": True, "status": "OK", "count": 1, "cap": 3, "reason": None},
        raising=False)
    monkeypatch.setattr(_sk, "pause_site_keepers", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(_sk, "resume_site_keepers", lambda *a, **k: None, raising=False)


def _degradation_events(runner):
    return [e for e in runner.events if e["extra"].get("degraded") is True]


def test_row723_the_runner_owns_its_login_flow_and_surfaces_the_note(monkeypatch):
    """login_async -> do_login: the flow sees the runner's site as its owner
    and, when it degrades, the note is in THIS runner's run record when the
    login settles -- not left for a later run to find."""
    from bulk_downloader import runner_auth
    cloak.reset_cache_for_tests()
    _no_accounting(monkeypatch)
    seen = []

    def fixture_do_login(config, allow_manual_takeover=False):
        owner = getattr(cloak, "ledger_site_id", lambda c: "")(config)
        seen.append(owner)
        cloak.note_channel_fallback(site_id=owner, flow="login", channel="chrome",
                                    error=_CHROME_MISSING, recovered=True)
        return True, "fixture ok", []
    monkeypatch.setattr(runner_auth, "do_login", fixture_do_login)

    r = _auth_runner()
    done = threading.Event()
    r.login_async(on_done=lambda ok: done.set(), allow_manual=False)
    assert done.wait(5), "login did not settle"
    r._login_thread.join(5)
    assert seen == [_SITE], (
        f"the runner did not declare itself the owner of its login flow: site seen {seen!r}")
    evs = _degradation_events(r)
    assert len(evs) == 1, (
        f"the login degradation is not in the runner's run record: {r.events!r}")
    assert evs[0]["extra"]["flow"] == "login" and evs[0]["extra"]["recovered"] is True
    assert cloak.drain_channel_fallbacks(_SITE) == [], "surfaced notes are drained"


def _browser_runner():
    from bulk_downloader.runner_browser import BrowserMixin

    class _Runner(BrowserMixin):
        def __init__(self):
            self.config = {"use_real_chrome": False, "browser_backend": "playwright",
                           "headless": True}
            self.site_id = _SITE
            self.events = []

        def log_event(self, kind, message, url=None, extra=None):
            self.events.append({"kind": kind, "message": str(message),
                                "url": url, "extra": dict(extra or {})})

        def _profile_dir(self, worker_idx=None):
            return "/tmp/row723-profile-does-not-launch"

        def _apply_persistent_cookie_file(self, ctx):
            pass

        def _install_stealth(self, ctx):
            pass
    return _Runner()


def test_row723_a_run_launch_surfaces_the_notes_a_keeper_login_left_behind(monkeypatch):
    """The keeper's login has no runner at hand: its note waits in the ledger
    and the site's next browser launch puts it in the run record, before the
    launch's own events. Another site's note stays for its own owner."""
    cloak.reset_cache_for_tests()
    cloak.note_channel_fallback(site_id=_SITE, flow="login", channel="chrome",
                                error=_CHROME_MISSING, recovered=True)
    cloak.note_channel_fallback(site_id=_OTHER, flow="login", channel="chrome",
                                error=_CHROME_MISSING, recovered=True)
    pw = _FakePW()
    ctx = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(cloak, "open_persistent_context",
                        lambda **kw: (ctx, pw, "fixture"))
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    r = _browser_runner()
    _browser, _ctx, got_pw, _backend = r._launch_browser()
    try:
        evs = _degradation_events(r)
        assert len(evs) == 1, (
            f"a pending login degradation was not surfaced by the site's launch: {r.events!r}")
        assert evs[0]["extra"]["flow"] == "login"
        assert len(cloak.drain_channel_fallbacks(_OTHER)) == 1, "another site's note is not ours"
        assert cloak.drain_channel_fallbacks(_SITE) == []
    finally:
        try:
            _ctx.close()
        finally:
            got_pw.stop()
    assert got_pw is pw and pw.stopped is True


# ── every other owner of a login flow (the H489 census of this cut) ────────

def _recording_manual_open(monkeypatch, seen):
    """login.open_manual_login_browser stand-in: records the owner the flow
    sees and files the degradation the real session would file."""
    from bulk_downloader import login as _login

    class _Handle:
        def snapshot_cookies(self, timeout=10):
            return []

        def cancel(self, timeout=10):
            pass

    def _open(config, **kw):
        owner = cloak.ledger_site_id(config)
        seen.append(owner)
        cloak.note_channel_fallback(site_id=owner, flow="manual login", channel="chrome",
                                    error=_CHROME_MISSING, recovered=True)
        return _Handle()
    monkeypatch.setattr(_login, "open_manual_login_browser", _open)


def test_row723_start_manual_login_owns_its_flow_and_surfaces_the_note(monkeypatch):
    cloak.reset_cache_for_tests()
    _no_accounting(monkeypatch)
    seen = []
    _recording_manual_open(monkeypatch, seen)
    r = _auth_runner()
    r.config["manual_use_persistent_profile"] = False
    ok, msg = r.start_manual_login()
    assert ok is True, (ok, msg)
    assert seen == [_SITE], f"start_manual_login did not declare its owner: {seen!r}"
    assert len(_degradation_events(r)) == 1, r.events
    assert cloak.drain_channel_fallbacks(_SITE) == []
    r._manual_snapshot_stop.set()


def test_row723_the_first_run_auto_teach_login_still_opens_the_manual_window(monkeypatch):
    """login_async, auto_teach_first_run with nothing learned -> start_manual_login
    (the runner_auth.py:262 call site is on the owned path)."""
    cloak.reset_cache_for_tests()
    _no_accounting(monkeypatch)
    seen = []
    _recording_manual_open(monkeypatch, seen)
    r = _auth_runner()
    r.config.update({"auto_teach_first_run": True, "manual_use_persistent_profile": False})
    outcomes = []
    r.login_async(on_done=outcomes.append)
    assert seen == [_SITE], seen
    assert r._manual_login_handle is not None and "Manual" in r._login_status, r._login_status
    assert len(_degradation_events(r)) == 1, r.events
    r._manual_snapshot_stop.set()


def test_row723_a_templated_login_failure_still_falls_back_to_the_manual_window(monkeypatch):
    """Phase B (v3.62.2): learned selectors + do_login False + allow_manual ->
    start_manual_login (the runner_auth.py:431 call site)."""
    from bulk_downloader import runner_auth
    cloak.reset_cache_for_tests()
    _no_accounting(monkeypatch)
    monkeypatch.setattr(runner_auth, "session_event_record", lambda *a, **k: None)
    monkeypatch.setattr(runner_auth, "do_login",
                        lambda config, allow_manual_takeover=False: (False, "template stale", []))
    seen = []
    _recording_manual_open(monkeypatch, seen)
    r = _auth_runner()
    r.config.update({"auto_teach_first_run": False,
                     "learned": {"login": {"user_field": "#u", "pass_field": "#p", "submit_btn": "#s"}},
                     "manual_use_persistent_profile": False})
    done = threading.Event()
    r.login_async(on_done=lambda ok: done.set(), allow_manual=True)
    assert done.wait(5), "login did not settle"
    r._login_thread.join(5)
    assert seen == [_SITE], seen
    assert r._manual_login_handle is not None, r._login_status
    assert any(e["kind"] == "login_template_fallback" for e in r.events), r.events
    r._manual_snapshot_stop.set()


def test_row723_an_expired_cookie_relogin_goes_through_the_owned_login(monkeypatch):
    """_check_cookies_or_relogin -> login_async (the runner_auth.py:1338 call site)."""
    from bulk_downloader import runner_auth
    cloak.reset_cache_for_tests()
    _no_accounting(monkeypatch)
    seen = []

    def fixture_do_login(config, allow_manual_takeover=False):
        seen.append(cloak.ledger_site_id(config))
        return True, "fixture ok", []
    monkeypatch.setattr(runner_auth, "do_login", fixture_do_login)
    r = _auth_runner()
    r.config.update({"username": "fixture", "password": "zero-entropy-password"})
    r.cookies = [{"name": "sid", "value": "x", "expires": 1.0}]   # expired, not a session cookie
    assert r._check_cookies_or_relogin("https://login.example.invalid/member") is True
    r._login_thread.join(5)
    assert seen == [_SITE], f"the relogin did not run under the runner's ownership: {seen!r}"


def test_row723_verify_after_wizard_owns_its_replay_and_returns_its_result(monkeypatch):
    from bulk_downloader import runner_auth
    cloak.reset_cache_for_tests()
    _no_accounting(monkeypatch)
    seen = []

    def fixture_verify(config, profile_dir, member_url=None, timeout=20.0):
        seen.append(cloak.ledger_site_id(config))
        cloak.note_channel_fallback(site_id=seen[-1], flow="login verify", channel="chrome",
                                    error=_CHROME_MISSING, recovered=True)
        return {"replay_ok": True, "summary": "fixture"}
    monkeypatch.setattr(runner_auth, "verify_login_replay", fixture_verify, raising=False)
    monkeypatch.setattr("bulk_downloader.login.verify_login_replay", fixture_verify)
    r = _auth_runner()
    result = r.verify_login_after_wizard()
    assert result == {"replay_ok": True, "summary": "fixture"}, result
    assert r.get_last_verify_result() == result
    assert seen == [_SITE], seen
    assert len(_degradation_events(r)) == 1, r.events


def test_row723_the_keeper_relogin_is_owned_by_its_site(monkeypatch, tmp_path):
    """SessionKeeper: a DEAD heartbeat -> _auto_relogin -> the registered
    callback (app._do_login_for_keeper) runs under the keeper's site. No
    runner is at hand, so the note waits for that site's next launch."""
    from bulk_downloader import session_keeper as sk
    cloak.reset_cache_for_tests()
    monkeypatch.chdir(tmp_path)
    seen = []

    def callback(site_id, account_idx, cfg):
        seen.append((site_id, cloak.ledger_site_id(cfg)))
        cloak.note_channel_fallback(site_id=cloak.ledger_site_id(cfg), flow="login",
                                    channel="chrome", error=_CHROME_MISSING, recovered=True)
        return True, "fixture relogin ok"
    cfg = {"login_url": LOGIN_URL, "keep_alive_check_url": LOGIN_URL, "password": "zero-entropy-password",
           "keep_alive_enabled": True}
    keeper = sk.SessionKeeper(_SITE, 0, cfg, callback)
    events = []
    monkeypatch.setattr(keeper, "_record_event", lambda et, d="": events.append((et, d)))
    monkeypatch.setattr(keeper, "_heartbeat", lambda: (sk.DEAD, "login page"))
    monkeypatch.setattr(keeper, "_teardown_browser", lambda: None)
    monkeypatch.setattr(keeper, "_persist_cookies", lambda: None)
    keeper._run_one_check()
    assert [e[0] for e in events][:1] == ["heartbeat_fail"], events   # the check really ran
    assert seen == [(_SITE, _SITE)], f"the keeper relogin was not owned by its site: {seen!r}"
    assert keeper.state["state"] == "connected", keeper.state
    assert len(cloak.drain_channel_fallbacks(_SITE)) == 1, "the note waits for the site's next launch"
    assert cloak.ledger_site_id({}) == "", "ownership ends with the relogin"


def test_row723_the_keeper_heartbeat_still_launches_and_runs_its_generation(monkeypatch, tmp_path):
    """Name-census neighbours of this cut (SessionKeeper._launch_browser /
    SessionKeeper._run share names with the runner seams it changes): the
    keeper still launches a dead browser before probing, and a generation
    still runs and unregisters itself."""
    from bulk_downloader import session_keeper as sk
    monkeypatch.chdir(tmp_path)
    cfg = {"login_url": LOGIN_URL, "keep_alive_check_url": LOGIN_URL, "password": "zero-entropy-password",
           "keep_alive_enabled": True}
    keeper = sk.SessionKeeper(_SITE, 0, cfg, lambda *a: (False, "no"))
    launched, navigated = [], []
    monkeypatch.setattr(keeper, "_browser_alive", lambda: False)
    monkeypatch.setattr(keeper, "_launch_browser", lambda: launched.append(1) or True)
    monkeypatch.setattr(keeper, "_heartbeat_navigate", lambda: navigated.append(1) or (sk.ALIVE, "fixture"))
    monkeypatch.setattr(keeper, "_heartbeat_httpx_fallback",
                        lambda: pytest.fail("a launch that succeeded must not fall back to httpx"))
    keeper._last_navigate_at = 0.0
    assert keeper._heartbeat() == (sk.ALIVE, "fixture")
    assert launched == [1] and navigated == [1], (launched, navigated)

    ran = []
    monkeypatch.setattr(keeper, "_run", lambda: ran.append(1))
    with sk._state_lock:
        sk._keepers[(_SITE, 0)] = keeper
    keeper._run_and_unregister()
    assert ran == [1], "the generation did not run"
    with sk._state_lock:
        assert (_SITE, 0) not in sk._keepers, "the generation did not unregister itself"


class _FakePW:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_row723_persistent_context_files_under_the_owning_site(monkeypatch):
    """cloak.persistent_context (verify / capture) with a config that has no
    site_id: both outcomes are filed under the declared owner."""
    cloak.reset_cache_for_tests()
    ctx = SimpleNamespace(close=lambda: None)
    pw = _FakePW()
    calls = []

    def _open(**kw):
        calls.append(dict(kw))
        if kw.get("channel") == "chrome":
            raise RuntimeError(_CHROME_MISSING)
        return ctx, pw, "fixture"
    monkeypatch.setattr(cloak, "open_persistent_context", _open)
    with cloak.owning_site(_SITE):
        with cloak.persistent_context(user_data_dir="/tmp/row723-never-opened",
                                      config={}, channel="chrome") as (got, backend):
            assert got is ctx and backend == "fixture"
    assert [c.get("channel") for c in calls] == ["chrome", None], calls
    notes = cloak.drain_channel_fallbacks(_SITE)
    assert len(notes) == 1 and notes[0]["recovered"] is True, notes
    assert notes[0]["flow"] == "persistent_context"
    assert pw.stopped is True

    def _open2(**kw):
        calls.append(dict(kw))
        raise RuntimeError(_CHROME_MISSING if kw.get("channel") == "chrome" else "bundled failed too")
    monkeypatch.setattr(cloak, "open_persistent_context", _open2)
    with cloak.owning_site(_SITE), pytest.raises(RuntimeError, match="bundled failed too"):
        with cloak.persistent_context(user_data_dir="/tmp/row723-never-opened",
                                      config={}, channel="chrome"):
            pass
    notes = cloak.drain_channel_fallbacks(_SITE)
    assert len(notes) == 1 and notes[0]["recovered"] is False, notes
    assert cloak.drain_channel_fallbacks("") == []


def test_row723_a_real_manual_session_carries_its_owner_onto_its_own_thread(monkeypatch):
    """ManualLoginSession is constructed on the runner's thread (inside
    owning_site) and launches on its OWN thread, where no declaration
    exists: the owner captured at construction is what _launch files under."""
    from bulk_downloader.login_impl import manual
    from bulk_downloader import interstitial
    cloak.reset_cache_for_tests()
    page = SimpleNamespace(goto=lambda *a, **k: None, evaluate=lambda *a, **k: None,
                           fill=lambda *a, **k: None, wait_for_timeout=lambda *a, **k: None,
                           is_closed=lambda: False)
    ctx = SimpleNamespace(pages=[], new_page=lambda: page, add_init_script=lambda *a, **k: None,
                          cookies=lambda: [], close=lambda: None)
    browser = SimpleNamespace(new_context=lambda **kw: ctx, close=lambda: None,
                              is_connected=lambda: True)
    calls = _chrome_absent_launch(monkeypatch, browser)
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    monkeypatch.setattr(interstitial, "dismiss_gates", lambda *a, **kw: [])
    config = {"login_url": LOGIN_URL, "use_real_chrome": True, "use_stealth": False,
              "name": "row723", "username": "", "password": ""}
    with cloak.owning_site(_SITE):
        session = manual.ManualLoginSession(config, "", manual_profile_dir=None, headless=True)
    try:
        assert session.error is None and session.ready, (session.error, session.ready)
        assert session._owning_site == _SITE
        assert [c.get("channel") for c in calls] == ["chrome", None], calls
        notes = _login_notes(_SITE)
        assert len(notes) == 1 and notes[0]["recovered"] is True, notes
    finally:
        session.cancel(timeout=5)


# ── negative controls ──────────────────────────────────────────────────────

def test_row723_no_degradation_leaves_no_note_and_no_event(monkeypatch, tmp_path):
    """Chrome present: one launch, nothing filed, nothing surfaced (base)."""
    from bulk_downloader.login_impl import submit
    cloak.reset_cache_for_tests()
    calls = []

    def _stop(*a, **kw):
        raise _Stop()
    ctx = SimpleNamespace(new_page=_stop, cookies=lambda: [], close=lambda: None)
    browser = SimpleNamespace(new_context=lambda **kw: ctx, close=lambda: None)
    monkeypatch.setattr(cloak, "launch_browser",
                        lambda **kw: calls.append(dict(kw)) or (browser, None, "fixture"))
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    with cloak.owning_site(_SITE):
        result = submit.do_login({"login_url": LOGIN_URL, "username": "fixture",
                                  "password": "zero-entropy-password", "wait": 0,
                                  "use_real_chrome": True, "use_stealth": False,
                                  "login_evidence_dir": str(tmp_path / "evidence")},
                                 allow_manual_takeover=False)
    assert result[0] is False and "login error" in result[1], result
    assert len(calls) == 1 and calls[0].get("channel") == "chrome", calls
    assert cloak.drain_channel_fallbacks(_SITE) == []
    r = _browser_runner()
    monkeypatch.setattr(cloak, "launch_browser", lambda **kw: (object(), None, "fixture"))
    r._launch_browser()
    assert _degradation_events(r) == []


def test_row723_a_caller_with_no_run_record_leaves_the_note_for_its_owner():
    """No event log -> nothing to surface into; the note is NOT drained."""
    from bulk_downloader.runner_browser import BrowserMixin
    cloak.reset_cache_for_tests()
    cloak.note_channel_fallback(site_id=_SITE, flow="login", channel="chrome",
                                error=_CHROME_MISSING, recovered=True)
    r = BrowserMixin.__new__(BrowserMixin)
    r.site_id = _SITE
    r.log_event = None   # a carrier with no run record
    assert r._surface_pending_channel_fallbacks() == 0
    assert len(cloak.drain_channel_fallbacks(_SITE)) == 1, "the note must survive for a real owner"


def test_row723_the_site_id_resolution_is_one_helper_in_cloak():
    """config site_id, then config sid, then the owning-site declaration,
    else '' -- and every login-flow site reads it from cloak, not its own."""
    assert cloak.ledger_site_id({"site_id": "a", "sid": "b"}) == "a"
    assert cloak.ledger_site_id({"sid": "b"}) == "b"
    with cloak.owning_site("c"):
        assert cloak.ledger_site_id({}) == "c"
        assert cloak.ledger_site_id({"site_id": "a"}) == "a"
        with cloak.owning_site("d"):
            assert cloak.ledger_site_id(None) == "d"
        assert cloak.ledger_site_id(None) == "c"
    assert cloak.ledger_site_id(None) == ""
