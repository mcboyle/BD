"""A login that covers no part of the page BD is about to load must say so.

WHY THIS EXISTS. The 2026-09-03 campaign lane on test6 filed
``TEMPLATE-LOGIN-WALL-SESSION-DOES-NOT-CARRY`` (HIGH). On brazzers the
credential login returned ``OK -- 19 cookies``, the job ran, and the scene page
rendered a login wall. ``record.json`` names both halves of the shape:
``settings.login_host = site-ma.brazzers.com`` and the requested URL is on
``www.brazzers.com``. Those are SIBLING subdomains. A session cookie the login
host issued without a ``Domain`` attribute is host-only, and RFC 6265 5.1.3
forbids sending it to a sibling -- so the browser was right, the jar was right,
and the only thing wrong was that nothing in BD could SAY so.

WHAT WAS MEASURED BEFORE WRITING THIS. A local fixture host was driven through
BD's real carry chain -- ``pw_to_json`` -> ``json.dumps``/``json.loads`` (the
disk shape) -> ``cookies.normalize_stored_cookie`` -> ``add_cookies`` into a
fresh context. Both directions came back clean: a ``Domain=.example.test``
cookie survives and IS offered to ``www.example.test``; a host-only cookie
survives and is correctly withheld. Chromium reports an unspecified
``SameSite`` as ``Lax`` (not ``None``), so ``normalize_stored_cookie``'s
``"None"`` default never fires on a harvested cookie and no cookie is lost for
want of ``Secure``. THE CARRY IS NOT BROKEN. That is why this cut adds a
diagnostic and not a wider cookie scope: widening would forge a ``Set-Cookie``
scope the site never issued.

WHERE THE SILENCE WAS. ``runner_auth._check_cookies_or_relogin(url)`` is the
one seam that already holds both the jar and the target URL
(``runner.py:_process_one`` calls it as dispatch branch ``cookie_relogin``),
and its whole predicate is ``if ei["expired"] <= 0 or ei["session"] != 0:
return True`` -- a question with no host in it. Nineteen live session cookies
scoped to a host the worker will never visit satisfy it exactly as well as a
real session does.

REPORT, DO NOT REFUSE, and the tests below pin that too. Zero applicable
cookies is not proof of a logged-out session: a site may authenticate from
localStorage or a bearer token, and the worker's persistent profile can hold
cookies that never reached the flat jar -- the same lane measured 38
persistent-profile hits on dfxtra, whose jar held nine. Turning this into a
refusal would convert a missing diagnostic into a false refusal on every such
site.

WHAT THIS GATE DOES NOT CLAIM. dfxtra's wall is NOT this shape: its
``login_host`` and its scene host are both ``www.dfxtra.com``, so no scoping
rule can drop anything, and its cause stays open (the sibling
``LOGIN-FALSE-SUCCESS-ON-GENERIC-SESSION-COOKIE`` row is the live candidate).
This gate covers the cross-host shape and says nothing about the same-host one.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# CI-SHARD-CLAIM template-selectors: this gate drives Chromium against a local
# fixture host, so it must run on a shard that installs a browser.
BD_GATE_SCOPE = "repo-wide"
pytestmark = pytest.mark.capture_serial


# Zero-entropy fixture values (A4): nothing here resembles a real credential
# or a real session token, and no live site is contacted anywhere in this file.
_COOKIE_NAME = "fixture_session"
_COOKIE_VALUE = "fixture-session-value"
_LOGIN_HOST = "login.example.test"
_SCENE_HOST = "www.example.test"
_SCENE_URL = f"https://{_SCENE_HOST}/video/1"
_LOGIN_URL = f"https://{_LOGIN_HOST}/auth/signin"


def _jar(domain, *, name=_COOKIE_NAME, path="/", secure=False, extra=0):
    """A jar in BD's in-memory shape -- what ``normalize_stored_cookie``
    produces and what ``add_cookies`` consumes. No ``expires`` key means a
    session cookie, which is what a real login mints."""
    jar = [{"name": name, "value": _COOKIE_VALUE, "domain": domain,
            "path": path, "sameSite": "Lax", "secure": secure,
            "httpOnly": True}]
    for i in range(extra):
        # the extras share the primary cookie's scope, so a path-scoped jar
        # is path-scoped as a whole (19 cookies, 0 apply) rather than 1 of 19
        jar.append({"name": f"pref_{i}", "value": f"v{i}", "domain": domain,
                    "path": path, "sameSite": "Lax", "secure": False,
                    "httpOnly": False})
    return jar


def _runner(cfg=None):
    from bulk_downloader import db
    from bulk_downloader.runner import SiteRunner
    db.db_init()
    base = {"name": "ScopeFixture", "login_url": _LOGIN_URL}
    base.update(cfg or {})
    return SiteRunner("scope_fixture_site", base)


# ── preconditions ────────────────────────────────────────────────────────────

def test_precondition_the_expiry_predicate_waves_a_subdomain_only_jar_through():
    """Prove the seam is REACHED and that nothing else refuses first.

    Without this, a green diagnostic test could be green because
    ``_check_cookies_or_relogin`` bailed early for an unrelated reason. The
    brazzers jar is live session cookies, so the existing predicate returns
    True at its first branch and the worker proceeds to render the wall."""
    from bulk_downloader.cookies import cookies_expiry_info

    jar = _jar(_LOGIN_HOST, extra=18)
    assert len(jar) == 19, "fixture must build the measured 19-cookie shape"
    ei = cookies_expiry_info(jar)
    assert ei["expired"] == 0 and ei["session"] == 19, (
        f"fixture jar graded {ei!r}; it must read as live session cookies, "
        f"which is what makes `expired <= 0 or session != 0` return True and "
        f"the login wall reachable")


def test_precondition_no_cookie_in_the_jar_is_scoped_to_the_scene_host():
    """The fixture must actually build the cross-host shape it claims."""
    jar = _jar(_LOGIN_HOST, extra=18)
    assert {c["domain"] for c in jar} == {_LOGIN_HOST}
    assert not any(c["domain"] in (f".{_SCENE_HOST}", _SCENE_HOST,
                                   ".example.test") for c in jar)


# ── the defect ───────────────────────────────────────────────────────────────

def test_a_jar_scoped_to_the_login_subdomain_names_the_uncovered_scene_host(capsys):
    """RED on base. 19 live cookies for ``login.example.test``, a scene on
    ``www.example.test``, and BD proceeds without a word."""
    r = _runner()
    r.set_cookies(_jar(_LOGIN_HOST, extra=18))
    before = r._event_seq

    proceed = r._check_cookies_or_relogin(_SCENE_URL)

    err = capsys.readouterr().err
    assert f"session does not cover {_SCENE_HOST}" in err, (
        f"stderr was {err!r}. A jar of 19 live cookies scoped to "
        f"{_LOGIN_HOST} covers nothing on {_SCENE_HOST}, so the page will "
        f"render as a login wall while login reported success. BD must name "
        f"that before the navigation, not leave the operator a screenshot "
        f"with no cause attached.")
    assert proceed is True, (
        "the diagnostic must REPORT, never refuse: the persistent profile or "
        "a token-based session can still authenticate this page")
    kinds = [e.get("kind") for e in r.get_events(after_seq=before)]
    assert "session_scope" in kinds, (
        f"event kinds {kinds!r} carry no session_scope row; a stderr line "
        f"alone does not reach the operator's event feed")


def test_the_diagnostic_names_the_jars_actual_scope_not_just_the_host(capsys):
    """A refusal that cannot be acted on is barely better than a silent one
    (A7). The line must carry the count and where the jar IS scoped, so the
    operator can go straight to the site's login_url."""
    r = _runner()
    r.set_cookies(_jar(_LOGIN_HOST, extra=18))
    r._check_cookies_or_relogin(_SCENE_URL)
    err = capsys.readouterr().err

    assert "19 cookie(s)" in err, f"no jar size in {err!r}"
    assert _LOGIN_HOST in err.split("session does not cover", 1)[1], (
        f"the line does not say where the jar IS scoped: {err!r}")


# ── negative controls ────────────────────────────────────────────────────────

def test_a_parent_domain_jar_is_never_reported_as_uncovered(capsys):
    """The over-fire control. A ``Domain=.example.test`` cookie IS offered to
    ``www.example.test`` -- measured against Chromium below -- so a diagnostic
    here would be a false alarm on every correctly-configured site."""
    r = _runner()
    r.set_cookies(_jar(".example.test", extra=18))
    proceed = r._check_cookies_or_relogin(_SCENE_URL)
    err = capsys.readouterr().err
    assert "session does not cover" not in err, (
        f"a parent-domain jar covers the scene host; diagnostic fired anyway: "
        f"{err!r}")
    assert proceed is True


def test_an_empty_jar_is_not_reported_as_an_uncovered_host(capsys):
    """"No session captured" is a different, already-visible state. Reporting
    it here would bury the cross-host case in noise from every fresh site."""
    r = _runner()
    r.set_cookies([])
    r._check_cookies_or_relogin(_SCENE_URL)
    assert "session does not cover" not in capsys.readouterr().err


def test_a_url_with_no_host_is_unmeasurable_not_uncovered(capsys):
    """A2's third state. Without a host the question cannot be asked, and an
    unmeasurable claim must not be published as a finding."""
    r = _runner()
    r.set_cookies(_jar(_LOGIN_HOST))
    r._check_cookies_or_relogin("not-a-url")
    assert "session does not cover" not in capsys.readouterr().err


def test_the_diagnostic_is_emitted_once_per_host_not_once_per_url(capsys):
    """The wall recurs on every URL of that host; a per-URL line buries it."""
    r = _runner()
    r.set_cookies(_jar(_LOGIN_HOST, extra=18))
    for n in range(4):
        r._check_cookies_or_relogin(f"https://{_SCENE_HOST}/video/{n}")
    err = capsys.readouterr().err
    assert err.count("session does not cover") == 1, (
        f"expected exactly one line over four URLs on one host, got "
        f"{err.count('session does not cover')}: {err!r}")

    r._check_cookies_or_relogin("https://other.example.test/video/9")
    err2 = capsys.readouterr().err
    assert err2.count("session does not cover other.example.test") == 1, (
        f"a SECOND uncovered host must still be named once: {err2!r}")


def test_a_path_scoped_cookie_does_not_cover_a_sibling_path(capsys):
    """Domain is not the only scope. ``/en/members`` is not offered to
    ``/en/video/1``, and a prefix test alone would wrongly offer it to
    ``/en/membersonly``."""
    r = _runner()
    r.set_cookies(_jar(_SCENE_HOST, path="/en/members", extra=18))
    r._check_cookies_or_relogin(f"https://{_SCENE_HOST}/en/video/1")
    assert f"session does not cover {_SCENE_HOST}" in capsys.readouterr().err

    r2 = _runner()
    r2.set_cookies(_jar(_SCENE_HOST, path="/en/members", extra=18))
    r2._check_cookies_or_relogin(f"https://{_SCENE_HOST}/en/members/scene/1")
    assert "session does not cover" not in capsys.readouterr().err


def test_the_check_never_breaks_the_download_path(capsys, monkeypatch):
    """A diagnostic that can raise is worse than the silence it replaces."""
    r = _runner()
    r.set_cookies(_jar(_LOGIN_HOST, extra=18))
    from bulk_downloader import session_scope

    def _explode(*a, **k):
        raise RuntimeError("scope check exploded")

    monkeypatch.setattr(session_scope, "applicable_cookies", _explode)
    # precondition: the patched seam really is the one the runner reaches
    with pytest.raises(RuntimeError):
        session_scope.uncovered_host_diagnostic(r.cookies, _SCENE_URL)
    assert r._check_cookies_or_relogin(_SCENE_URL) is True
    assert "session scope check raised" in capsys.readouterr().err


def test_a_host_only_cookie_on_the_apex_does_not_cover_a_subdomain(capsys):
    """RFC 6265 5.1.3: a cookie set WITHOUT a Domain attribute on
    ``example.test`` is host-only and is NOT sent to ``www.example.test``.
    Pinned in both directions so an `endswith` widening is caught."""
    from bulk_downloader.session_scope import applicable_cookies, domain_matches
    assert domain_matches("example.test", "example.test") is True
    assert domain_matches("example.test", "www.example.test") is False
    assert domain_matches(".example.test", "www.example.test") is True
    jar = _jar("example.test", extra=3)
    assert applicable_cookies(jar, "https://www.example.test/video/1") == []
    assert len(applicable_cookies(jar, "https://example.test/video/1")) == 4


def test_one_unrelated_applicable_cookie_does_not_silence_the_login_hosts_scope(capsys):
    """The subject is the LOGIN HOST's cookies. A parent-domain consent cookie
    applies to the scene URL, but every cookie the login minted stays home,
    and that is exactly the brazzers shape -- it must still be named."""
    r = _runner()
    jar = _jar(_LOGIN_HOST, extra=18)
    jar.append({"name": "consent", "value": "yes", "domain": ".example.test",
                "path": "/", "sameSite": "Lax", "secure": False,
                "httpOnly": False})
    r.set_cookies(jar)
    r._check_cookies_or_relogin(_SCENE_URL)
    err = capsys.readouterr().err
    assert f"session does not cover {_SCENE_HOST}" in err, err
    assert "19 of the 20 cookie(s)" in err, err


def test_without_a_known_login_host_the_whole_jar_is_the_subject():
    """When no login host is known the diagnostic judges the whole jar: a jar
    scoped to another host speaks; a jar with one applicable cookie is
    silent (the pre-login-host rule, kept for callers with no login_url)."""
    from bulk_downloader.session_scope import uncovered_host_diagnostic
    jar = _jar(_LOGIN_HOST, extra=18)
    out = uncovered_host_diagnostic(jar, _SCENE_URL)
    assert f"session does not cover {_SCENE_HOST}" in out, out
    jar.append({"name": "consent", "value": "yes", "domain": ".example.test",
                "path": "/", "sameSite": "Lax", "secure": False,
                "httpOnly": False})
    assert uncovered_host_diagnostic(jar, _SCENE_URL) == ""


# ── ground truth: Chromium's own answer, on a local fixture host ─────────────

class _Handler(BaseHTTPRequestHandler):
    """Serves one page and one Set-Cookie header chosen by the path.

    No live site, no credential: the value is a fixed literal and the host
    only resolves because Chromium is launched with --host-resolver-rules."""

    def log_message(self, *a):  # keep the test output readable
        pass

    seen_cookie_headers = {}  # path -> Cookie header the browser actually SENT

    def do_GET(self):
        body = b"<html><body>fixture</body></html>"
        _Handler.seen_cookie_headers[self.path] = self.headers.get("Cookie", "")
        self.send_response(200)
        if self.path.startswith("/hostonly"):
            self.send_header("Set-Cookie",
                             f"{_COOKIE_NAME}={_COOKIE_VALUE}; Path=/")
        elif self.path.startswith("/parent"):
            self.send_header("Set-Cookie",
                             f"{_COOKIE_NAME}={_COOKIE_VALUE}; Path=/; "
                             f"Domain=.example.test")
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_chromium_agrees_with_the_products_scope_answer():
    """The pure matcher is only worth what the browser says it is.

    Drives a REAL Chromium against a local fixture host, harvests through BD's
    real ``pw_to_json`` -> disk JSON -> ``normalize_stored_cookie`` chain,
    re-injects into a FRESH context (the scene context), and compares the
    Cookie header Chromium ACTUALLY SENDS on a navigation (recorded by the
    fixture server) with ``session_scope.applicable_cookies`` on the identical
    jar. ``context.cookies([url])`` is NOT the oracle: it returns a host-only
    apex cookie for a sibling host that a navigation never receives. Both cases must
    agree, and the two cases must DIFFER from each other -- otherwise the
    comparison is over a constant and proves nothing."""
    from playwright.sync_api import sync_playwright

    from bulk_downloader.cookies import normalize_stored_cookie, pw_to_json
    from bulk_downloader.session_scope import applicable_cookies

    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    rules = (f"MAP *.example.test 127.0.0.1:{port},"
             f"MAP example.test 127.0.0.1:{port}")
    scene_http = f"http://{_SCENE_HOST}/video/1"
    login_http = f"http://{_LOGIN_HOST}/video/1"
    observed = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", f"--host-resolver-rules={rules}"])
            try:
                for case, path in (("hostonly", "/hostonly"),
                                   ("parent", "/parent")):
                    login_ctx = browser.new_context()
                    page = login_ctx.new_page()
                    page.goto(f"http://{_LOGIN_HOST}{path}",
                              wait_until="domcontentloaded")
                    harvested = login_ctx.cookies()
                    assert [c["name"] for c in harvested] == [_COOKIE_NAME], (
                        f"{case}: fixture minted {harvested!r}; the login "
                        f"context must hold exactly the fixture cookie or "
                        f"nothing downstream is about the intended shape")
                    login_ctx.close()

                    # BD's real save/load round trip, including the disk hop.
                    stored = json.loads(json.dumps(pw_to_json(harvested)))
                    jar = [normalize_stored_cookie(c) for c in stored]
                    assert len(jar) == 1 and jar[0]["value"] == _COOKIE_VALUE

                    scene_ctx = browser.new_context()
                    scene_ctx.add_cookies(jar)
                    # THE ORACLE IS THE COOKIE HEADER THE BROWSER SENDS on a
                    # real navigation, recorded by the fixture server --
                    # not context.cookies([url]), which the shape lens showed
                    # returns a host-only apex cookie for a sibling host that
                    # a navigation never receives.
                    _Handler.seen_cookie_headers.clear()
                    scene_page = scene_ctx.new_page()
                    scene_key = f"/video/1?case={case}&who=scene"
                    login_key = f"/video/1?case={case}&who=login"
                    scene_page.goto(f"http://{_SCENE_HOST}{scene_key}",
                                    wait_until="domcontentloaded")
                    scene_page.goto(f"http://{_LOGIN_HOST}{login_key}",
                                    wait_until="domcontentloaded")
                    sent = dict(_Handler.seen_cookie_headers)
                    scene_ctx.close()
                    # precondition: both navigations reached the fixture
                    assert scene_key in sent and login_key in sent, sent
                    chromium_scene = [_COOKIE_NAME] if (
                        f"{_COOKIE_NAME}=" in sent[scene_key]) else []
                    chromium_login = [_COOKIE_NAME] if (
                        f"{_COOKIE_NAME}=" in sent[login_key]) else []

                    observed[case] = {
                        "domain": jar[0]["domain"],
                        "chromium_scene": chromium_scene,
                        "chromium_login": chromium_login,
                        "bd_scene": [c["name"] for c in
                                     applicable_cookies(jar, scene_http)],
                        "bd_login": [c["name"] for c in
                                     applicable_cookies(jar, login_http)],
                    }
            finally:
                browser.close()
    finally:
        srv.shutdown()

    # The carry itself is intact in BOTH directions: the cookie always
    # survives the round trip and is always offered back to its own host.
    for case, o in observed.items():
        assert o["chromium_login"] == [_COOKIE_NAME], (
            f"{case}: the cookie did not survive BD's round trip into a fresh "
            f"context ({o!r}). If this ever fails the defect is a DROPPED "
            f"cookie in cookies.py, not a missing diagnostic.")
        assert o["bd_scene"] == o["chromium_scene"], (
            f"{case}: BD says {o['bd_scene']!r} reaches the scene host, "
            f"Chromium says {o['chromium_scene']!r}")
        assert o["bd_login"] == o["chromium_login"], (
            f"{case}: BD and Chromium disagree on the login host: {o!r}")

    # The comparison is not over a constant: the two cases genuinely differ,
    # and they differ in the direction the campaign measured.
    assert observed["hostonly"]["domain"] == _LOGIN_HOST
    assert observed["parent"]["domain"] == ".example.test"
    assert observed["hostonly"]["chromium_scene"] == [], (
        "a host-only login cookie must NOT reach the sibling scene host -- "
        "that absence is the brazzers login wall")
    assert observed["parent"]["chromium_scene"] == [_COOKIE_NAME], (
        "a parent-domain login cookie MUST reach the sibling scene host")


# ── CI wiring ────────────────────────────────────────────────────────────────

def test_this_gate_is_scheduled_in_ci_on_a_shard_that_has_chromium():
    """A gate CI does not run does not exist -- and a Chromium-driven gate on a
    shard without Chromium is the same thing with a longer traceback."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - measured, never skipped
        pytest.fail(f"UNKNOWN: PyYAML is unavailable ({exc}), so this gate "
                    "cannot check that CI schedules it on a browser shard")
    wf = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = wf["jobs"]["gate-suites"]
    me = "tests/" + Path(__file__).name
    shards = [s for s in job["strategy"]["matrix"]["include"]
              if me in s["suites"].split()]
    assert len(shards) == 1, (
        f"{me} appears in {len(shards)} gate-suites shards; it must be in "
        "exactly one or it runs nowhere / twice")
    name = shards[0]["name"]
    steps = [s for s in job["steps"]
             if "playwright install" in str(s.get("run", ""))]
    assert steps, "no step installs a browser on any gate shard"
    covered = [s for s in steps if name in str(s.get("if", ""))]
    assert covered, (
        f"shard {name!r} runs this gate but no 'playwright install' step's "
        f"condition names it, so every case would fail on a missing browser")
