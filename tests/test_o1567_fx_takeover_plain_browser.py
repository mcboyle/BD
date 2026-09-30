"""o1567 fx-takeover-plain-browser (FIXROWS-O1567.tsv; ORDER-O1567-FIXROWS.md "fx-takeover-plain-browser").

LIVE on test3 (.80): Cloudflare's Turnstile on freetour.adulttime.com refused even a HUMAN click in the app's takeover
window, but passed in a plain google-chrome on the same VM and IP (operator, 22:5xZ). Measured 23:0xZ: the takeover
window is the cloakbrowser chromium driven by Playwright over --remote-debugging-pipe, with a throwaway /tmp profile.
Operator requirement: the operator's ONLY action is the checkbox click; the app does the rest.

Contract:
  1. A takeover standing on a Cloudflare challenge page is replaced by a PLAIN browser: the same cloak binary and
     stealth flags, a pinned fingerprint seed, the site's manual profile, and no automation channel.
  2. The pass is read from that profile's own cookie store (a fresh cf_clearance). The automated login then runs
     INSIDE that browser: it attaches over the loopback CDP port no client used during the challenge, and fills the
     form with the stored credentials. Measured live (results/test3/FINDING-cf-clearance-handoff.md): the clearance
     is bound to the session that earned it, so carrying the cookie into another browser was challenged again.
     Only after the login is the browser closed. The manual profile is then synced into the runtime profiles, and a
     waiting queue is resumed.
  3. A takeover on any other page (a login form) keeps the old window.

Hermetic: a 127.0.0.1 site whose login URL answers a Cloudflare-shaped interstitial until the pass cookies are
present. As live, the pass is bound to the browser session: a persistent cf_clearance plus a session cookie that a
relaunch drops. A human pass is stood in for by the site setting them (GET /__human_pass): nothing here clicks a
checkbox. The plain browser is the real cloakbrowser binary (headless for the test); the login is the real do_login.
"""
from __future__ import annotations

import functools
import http.server
import threading
import time
from collections import Counter
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

BD_GATE_SCOPE = "module"

GOOD = "zero-entropy-password"      # documented fixture password; no vault reference
CHALLENGE_HTML = b"""<!doctype html><html><head><title>Just a moment...</title></head><body>
<h1>freetour.adulttime.test</h1><h2>Performing security verification</h2>
<div id="challenge-running">This website uses a security service to protect against malicious bots.</div>
<form id="challenge-form" action="/en/login?__cf_chl_f_tk=x" method="POST"><div class="cf-turnstile"></div></form>
</body></html>"""
FORM_HTML = b"""<!doctype html><html><head><title>Log in</title></head><body>
<form method="POST" action="/en/login"><input name="username" type="text"><input name="password" type="password">
<button type="submit">Log in</button></form></body></html>"""
MEMBERS_HTML = b"<!doctype html><html><head><title>Members</title></head><body><h1>members area</h1></body></html>"


def _cleared(cookie_header):
    """The clearance holds only with the session cookie of the browser that earned it."""
    return "cf_clearance=PASSED" in cookie_header and "cf_sess=BOUND" in cookie_header


def _handler(hits):
    class H(http.server.BaseHTTPRequestHandler):
        def _cookies(self):
            return self.headers.get("Cookie") or ""

        def _send(self, status, body=b"", headers=()):
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.partition("?")[0]
            if path == "/__human_pass":       # stands in for the human's checkbox pass
                hits["pass"] += 1
                return self._send(302, headers=(("Set-Cookie", "cf_clearance=PASSED; Path=/; Max-Age=3600"),
                                                ("Set-Cookie", "cf_sess=BOUND; Path=/"),
                                                ("Location", "/en/login")))
            if path == "/members" and "session=ok" in self._cookies():
                hits["members"] += 1
                return self._send(200, MEMBERS_HTML)
            if _cleared(self._cookies()):
                hits["form"] += 1
                return self._send(200, FORM_HTML)
            hits["challenge"] += 1
            return self._send(403, CHALLENGE_HTML)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(n).decode())
            if _cleared(self._cookies()) and form.get("password", [""])[0] == GOOD:
                hits["login"] += 1
                return self._send(302, headers=(("Set-Cookie", "session=ok; Path=/"), ("Location", "/members")))
            hits["challenge"] += 1
            return self._send(403, CHALLENGE_HTML)

        def log_message(self, *a):
            pass
    return H


@pytest.fixture
def site():
    hits = Counter()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(hits))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}", hits
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_plain_browser_carries_no_automation_channel(monkeypatch, tmp_path):
    from bulk_downloader import human_challenge as hc
    prof = tmp_path / "profiles" / "at" / "manual"
    argv = hc.plain_browser_argv("/opt/cloak/chrome", prof, "https://freetour.adulttime.com/en/login",
                                 seed=24680, user_agent="UA-fixture")
    assert argv[0] == "/opt/cloak/chrome" and argv[-1] == "https://freetour.adulttime.com/en/login"
    assert not [a for a in argv if a.startswith(("--remote-debugging", "--enable-automation"))], (
        f"O1567_TAKEOVER_PLAIN_HAS_CDP: {argv}")
    for flag in ("--fingerprint=24680", f"--user-data-dir={prof}", "--password-store=basic", "--user-agent=UA-fixture"):
        assert flag in argv, (flag, argv)
    assert not [a for a in argv if a.startswith("--headless")], "the live plain browser is headed"
    # the attach listener is loopback-only, and it is the only debugging flag
    argv_p = hc.plain_browser_argv("/opt/cloak/chrome", prof, "https://x.test/", seed=24680, debug_port=9333)
    assert [a for a in argv_p if a.startswith(("--remote-debugging", "--enable-automation"))] == [
        "--remote-debugging-port=9333", "--remote-debugging-address=127.0.0.1"], argv_p
    # positive control: the filter really removes an automation channel a flag builder hands it
    import cloakbrowser.browser as cbb
    monkeypatch.setattr(cbb, "build_args", lambda *a, **k: ["--remote-debugging-pipe", "--enable-automation",
                                                            "--fingerprint=24680"])
    argv2 = hc.plain_browser_argv("/opt/cloak/chrome", prof, "https://x.test/", seed=24680)
    assert "--remote-debugging-pipe" not in argv2 and "--enable-automation" not in argv2, argv2
    assert hc.domain_of("https://freetour.adulttime.com/en/login") == "adulttime.com"
    assert hc.domain_of("http://127.0.0.1:8080/x") == "127.0.0.1"


def _title_via(cdp_url):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        b = pw.chromium.connect_over_cdp(cdp_url)
        page = b.contexts[0].pages[-1]
        page.wait_for_load_state("domcontentloaded")
        return page.title()
    finally:
        pw.stop()


def test_the_login_after_a_pass_runs_inside_the_browser_that_passed(site, tmp_path, monkeypatch, capsys):
    from bulk_downloader import cloak, human_challenge as hc
    from bulk_downloader.login_impl import submit
    base, hits = site
    prof = tmp_path / "profiles" / "at" / "manual"
    launches = []
    real_launch = cloak.launch_browser

    def _headless_launch(**kw):
        launches.append(list(kw.get("args") or []))
        return real_launch(**{**kw, "headless": True})
    monkeypatch.setattr(cloak, "launch_browser", _headless_launch)
    monkeypatch.setattr(submit, "clear_cloudflare_challenge",
                        functools.partial(submit.clear_cloudflare_challenge, wait=1.0))
    config = {"name": "adulttime", "login_url": base + "/en/login", "username": "fixture", "password": GOOD,
              "success_url": "/members", "wait": 1, "use_stealth": False, "use_stealth_library": False,
              "browser_backend": "cloakbrowser", "login_evidence_dir": str(tmp_path / "evidence")}

    session = hc.PlainChallengeSession(base + "/__human_pass", prof, headless=True)
    session.start()
    try:
        ok, why = session.wait_for_pass(timeout=90, poll=1)
        assert ok, f"O1567_TAKEOVER_PASS_NOT_READ: {why}"
        cdp_url = session.cdp_url
        assert cdp_url and hits["pass"] >= 1, (cdp_url, hits)
        r1 = submit.do_login(dict(config, _human_clearance={"seed": session.seed, "cdp_url": cdp_url}))
        err = capsys.readouterr().err
        assert r1[0] is True, f"O1567_TAKEOVER_LOGIN_NOT_IN_PASSED_SESSION: {r1[:2]} hits={dict(hits)}"
        assert hits["login"] == 1 and hits["members"] >= 1, hits
        assert launches == [], f"a new browser was launched for the login: {launches}"
        assert "attaching to the human challenge browser that passed" in err, err[-600:]
        assert session.proc.poll() is None, "do_login must leave the challenge browser to its owner"
    finally:
        rc = session.close()
    assert rc == 0, rc

    # control: why the login cannot move to another browser -- the same profile relaunched is challenged again
    before = hits["challenge"]
    again = hc.PlainChallengeSession(base + "/en/login", prof, headless=True)
    again.start()
    try:
        deadline = time.time() + 30
        while again.cdp_url and hits["challenge"] == before and time.time() < deadline:
            time.sleep(0.5)
        assert hits["challenge"] > before and _title_via(again.cdp_url) == "Just a moment...", (
            "control: a relaunched profile must lose the session-bound pass", dict(hits))
    finally:
        again.close()


class _Runner:
    """The AuthMixin surface _maybe_start_human_challenge / _human_challenge_wait use."""

    def __init__(self, tmp_path, jobs=None):
        self.site_id = "f5d491e5"
        self.config = {"name": "adulttime", "login_url": "https://freetour.adulttime.test/en/login"}
        self.statuses, self.events, self.logins, self.starts = [], [], [], []
        self.jobs = jobs or {}
        self._lock = threading.Lock()
        self._state = "idle"
        self._prof = tmp_path / "profiles" / self.site_id / "manual"

    def _manual_profile_dir(self):
        return self._prof

    def _set_login_status(self, s):
        self.statuses.append(s)

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def login_async(self, on_done=None, allow_manual=True):
        self.logins.append((allow_manual, dict(getattr(self, "_pending_human_clearance", {}) or {}), on_done))

    def start(self):
        self.starts.append(True)


def _bind(r):
    from bulk_downloader.runner_auth import AuthMixin
    for name in ("_maybe_start_human_challenge", "_human_challenge_wait"):
        setattr(r, name, getattr(AuthMixin, name).__get__(r))
    return r


def _takeover_on(html):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context()
    ctx.route("https://freetour.adulttime.test/**",
              lambda route, req: route.fulfill(status=200, content_type="text/html", body=html))
    ctx.new_page().goto("https://freetour.adulttime.test/en/login")
    return pw, browser, ctx


@pytest.mark.parametrize("page_html, plain", [(CHALLENGE_HTML, True), (FORM_HTML, False)])
def test_only_a_takeover_on_a_challenge_page_goes_to_the_plain_browser(tmp_path, monkeypatch, page_html, plain):
    from bulk_downloader import human_challenge as hc, session_keeper
    started, waits = [], []
    monkeypatch.setattr(hc, "plain_binary", lambda: "/opt/cloak/chrome")
    monkeypatch.setattr(hc.PlainChallengeSession, "start", lambda self: started.append(self.argv))
    monkeypatch.setattr(session_keeper, "pause_site_keepers", lambda sid: None)
    r = _bind(_Runner(tmp_path))
    monkeypatch.setattr(r, "_human_challenge_wait", lambda s: waits.append(s), raising=False)
    from bulk_downloader import runner_auth

    class _SyncThread:
        def __init__(self, target, args=(), **kw):
            self._t, self._a = target, args

        def start(self):
            self._t(*self._a)
    monkeypatch.setattr(runner_auth, "threading", SimpleNamespace(Thread=_SyncThread, Lock=threading.Lock))
    pw, browser, ctx = _takeover_on(page_html)
    try:
        took = r._maybe_start_human_challenge((pw, browser, ctx))
        assert took is plain, (took, r.statuses)
        if plain:
            assert not browser.is_connected(), "the CDP takeover window must be closed"
            assert started and f"--user-data-dir={r._prof}" in started[0] and waits
            assert "tick the Cloudflare box" in r.statuses[-1], r.statuses
        else:
            assert browser.is_connected() and not started, "a form takeover keeps its window"
    finally:
        if browser.is_connected():
            browser.close()
        pw.stop()


def _stub_session(passed=True, cdp_url="http://127.0.0.1:9333"):
    closes = []
    return SimpleNamespace(url="https://freetour.adulttime.test/en/login", domain="adulttime.test",
                           user_data_dir="/p/manual", seed=4242, cdp_url=cdp_url if passed else None,
                           wait_for_pass=lambda timeout=0: (True, "") if passed else (False, "no pass within 900s"),
                           close=lambda: closes.append(1) or 0, closes=closes)


def test_after_the_pass_the_app_logs_in_syncs_and_resumes(tmp_path, monkeypatch):
    from bulk_downloader import profile_sync
    synced = []

    def _sync(sid, ensure=()):
        synced.append((sid, list(ensure), len(sess.closes)))
        return {"synced": {"main": 1}}
    monkeypatch.setattr(profile_sync, "sync_manual_to_runtime", _sync)
    r = _bind(_Runner(tmp_path, jobs={"u1": {"status": "pending"}}))
    sess = _stub_session()
    r._human_challenge_wait(sess)
    assert len(r.logins) == 1, r.logins
    allow_manual, clearance, on_done = r.logins[0]
    assert allow_manual is False and clearance == {"seed": 4242, "cdp_url": "http://127.0.0.1:9333"}, r.logins[0]
    assert sess.closes == [], "the browser that passed must stay open for the login"
    on_done(True)
    assert synced == [("f5d491e5", ["main"], 1)] and r.starts == [True], (synced, r.starts)
    assert any("resuming the queue" in m for _k, m in r.events), r.events


def test_a_window_closed_after_the_pass_logs_in_nothing(tmp_path):
    r = _bind(_Runner(tmp_path, jobs={"u1": {"status": "pending"}}))
    sess = _stub_session(cdp_url=None)
    r._human_challenge_wait(sess)
    assert r.logins == [] and sess.closes == [1], (r.logins, sess.closes)
    assert "window was closed before the app could log in" in r.statuses[-1], r.statuses


def test_no_pass_logs_in_nothing(tmp_path, monkeypatch):
    r = _bind(_Runner(tmp_path, jobs={"u1": {"status": "pending"}}))
    sess = _stub_session(passed=False)
    r._human_challenge_wait(sess)
    assert r.logins == [] and r.starts == [] and sess.closes == [1], (r.logins, r.starts)
    assert r.statuses[-1] == "✗ Human check not passed: no pass within 900s", r.statuses
