"""o1698 login-prefill-origin-guard -- do_login never types the configured
credentials into a page on an unrelated origin.

do_login loaded login_url and filled username + password into whatever page
the browser landed on. A login_url that 302-redirects to an UNRELATED host
got the configured password typed into (and POSTed by) that host's form;
row 774's cross-origin check refuses only AFTER the submit, when the
password has already left. The cloudflare re-submit path re-fills the form
the same way on whatever page the challenge cleared onto.

r2 (OGS1-C / OGS2-D refute): the guard ran once, before the username. The
page can still move before a password is typed -- between the username and
the password fill (F1b), and on the staged second screen a Continue click
opens (F1) -- and a manual takeover autofilled any page on the post-redirect
host (A3). Each of those fills is gated by the same predicate now; an
unreadable (None) page URL is refused, not a TypeError (F2).

Fix: the row-774 predicate, applied BEFORE the first fill and before the
re-submit fills: the page must be on the login page's origin, the same
registrable domain (row 722), or a declared success_url origin -- unless a
captured multi-step flow ran (v3.66.302 declares its own origins).

Fixture pages only; no live site, no login started.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bulk_downloader.login_impl import _common, submit

BD_GATE_SCOPE = "module"

FOREIGN = "https://login.unrelated-host.test/signin"


class _Locator:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n

    @property
    def first(self):
        return self

    def wait_for(self, **kw):
        return None


def _jar(names):
    # Documented zero-entropy cookie fixtures, never live session values.
    return [{"name": name, "value": "0" * 16} for name in names]


def _run(monkeypatch, tmp_path, *, login_url, landed, success_url=None, flow_ran=False,
         allow_manual=False, challenge_to=None, after_username=None, staged_to=None,
         wait_redirect=None, wait_field="password", wait_state="attached",
         wait_resubmit=False, staged_refusal_error=False):
    """Drive the real do_login; replace only the browser/UI boundaries.

    The page is on `landed` once login_url is loaded (the redirect already
    happened). With `challenge_to`, the first submit is answered by a
    cloudflare challenge that clears onto `challenge_to` with the login form
    back -- the re-submit path. With `after_username`, the page moves there
    once the username is typed. With `staged_to`, no password field exists
    until the Continue click, which moves the page there (two-step login).
    """
    from bulk_downloader import cloak, interstitial, learn, stealth
    from bulk_downloader.login_impl import _common, submit

    calls = {"submit": 0, "fill": [], "values": [], "close": 0, "cleared": False,
             "url": landed, "continued": False, "redirects": 0, "clear": []}
    before = _jar(["pref_a", "pref_b"])
    jar = [dict(c) for c in before]

    class Page:
        @property
        def viewport_size(self):
            return {"width": 1280, "height": 800}

        @property
        def url(self):
            return calls["url"]

        def goto(self, url, **kwargs):
            assert url == login_url

        def title(self):
            return "fixture"

        def content(self):
            return "<html><body>fixture page</body></html>"

        def locator(self, selector):
            if selector in ("#fixture-user", "#fixture-pass"):
                return Field(self, selector)
            return _Locator(1 if selector == "input[type=password]:visible" else 0)

        def wait_for_load_state(self, *a, **kw):
            return None

        def evaluate(self, *a, **kw):
            return None

    class Field:
        def __init__(self, page, selector):
            self.page, self.selector = page, selector
            self.role = "username" if selector == "#fixture-user" else "password"

        @property
        def first(self):
            return self

        def nth(self, index):
            assert index == 0
            return self

        def count(self):
            return 1

        def transition(self, state):
            if (wait_redirect and self.role == wait_field and state == wait_state
                    and bool(calls["submit"]) == wait_resubmit and not calls["redirects"]):
                calls["url"] = wait_redirect
                calls["redirects"] += 1

        def wait_for(self, state=None, **kwargs):
            self.transition(state)
            if staged_to and self.role == "password" and not calls["continued"]:
                if "password" not in calls["fill"]:
                    calls["fill"].append("password")
                raise TimeoutError("fixture: no password field before Continue")

        def fill(self, value):
            assert value == "", "fixture click succeeds; nonempty fallback is unexpected"
            calls["clear"].append(self.role)
            self.transition("clear")

        def click(self, **kwargs):
            self.transition("click")

    page = Page()
    ctx = SimpleNamespace(new_page=lambda: page, cookies=lambda: jar,
                          add_init_script=lambda *a: None)

    def close():
        calls["close"] += 1

    browser = SimpleNamespace(new_context=lambda **kw: ctx, close=close)
    monkeypatch.setattr(cloak, "launch_browser", lambda **kw: (browser, None, "fixture"))
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    monkeypatch.setattr(learn, "install_recorder", lambda page: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda *a: None)
    monkeypatch.setattr(interstitial, "dismiss_gates", lambda *a, **kw: [])
    monkeypatch.setattr(submit.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(submit, "replay_saved_login_flow", lambda *a: {"ran": flow_ran, "steps": 2})
    monkeypatch.setattr(submit, "_fire_login_trigger_if_needed", lambda *a, **kw: (False, False, ""))
    monkeypatch.setattr(submit, "_wait_captcha_tokens", lambda *a, **kw: (None, 0))
    monkeypatch.setattr(submit, "_try_check_remember_me", lambda page: None)
    def challenged(page):
        return bool(challenge_to) and calls["submit"] == 1 and not calls["cleared"]

    monkeypatch.setattr(submit, "_is_cloudflare_challenge_page", challenged)

    def clear(page, *a, **kw):
        # do_login also calls this on the freshly loaded login page (no
        # challenge there: nothing to clear); only the post-submit one clears.
        if calls["submit"] and challenge_to:
            calls["cleared"] = True
            calls["url"] = challenge_to
        return True

    monkeypatch.setattr(submit, "clear_cloudflare_challenge", clear)

    def type_value(page, selector, value, refusal=None):
        role = "username" if selector == "#fixture-user" else "password"
        label = role
        if calls["submit"]:
            label += " (re-submit)"
        elif role == "password" and calls["continued"]:
            label += " (after continue)"
        calls["fill"].append(label)
        calls["values"].append(value)
        if role == "username" and after_username:
            calls["url"] = after_username

    def click(page, selectors, what):
        if staged_to and what.startswith("continue"):
            calls["url"] = staged_to
            calls["continued"] = True
            return True, "fixture continue"
        return False, "no fixture control"

    def submit_form(page, selectors, password_selectors, *a, **kw):
        calls["submit"] += 1
        return bool(challenge_to), "fixture submit"

    if staged_refusal_error:
        original_refusal = submit._prefill_origin_refusal

        def staged_check(page, *args):
            if calls["continued"]:
                raise RuntimeError("fixture: staged origin verifier unavailable")
            return original_refusal(page, *args)

        monkeypatch.setattr(submit, "_prefill_origin_refusal", staged_check)

    monkeypatch.setattr(_common, "_type_field_value", type_value)
    monkeypatch.setattr(_common, "_is_honeypot_field", lambda loc: (False, ""))
    monkeypatch.setattr(_common, "_is_search_field", lambda loc: (False, ""))
    monkeypatch.setattr(submit, "_try_click", click)
    monkeypatch.setattr(submit, "_submit_login", submit_form)

    # Documented zero-entropy password; no vault reference or real credential.
    config = {"login_url": login_url, "username": "fixture",
              "password": "zero-entropy-password", "wait": 0,
              "user_field": "#fixture-user", "pass_field": "#fixture-pass",
              "use_real_chrome": False, "use_stealth": False,
              "use_stealth_library": False,
              "login_evidence_dir": str(tmp_path / "evidence")}
    if success_url:
        config["success_url"] = success_url
    result = submit.do_login(config, allow_manual_takeover=allow_manual)
    return result, calls


def _typed_password(calls):
    return "zero-entropy-password" in calls["values"]


# ── THE ROW ──

@pytest.mark.parametrize("allow_manual", [False, True], ids=["auto", "manual-takeover"])
def test_unrelated_origin_after_goto_is_never_filled(monkeypatch, tmp_path, allow_manual):
    result, calls = _run(monkeypatch, tmp_path, login_url="https://www.site.test/login",
                         landed=FOREIGN, allow_manual=allow_manual)
    assert not calls["fill"] and not calls["submit"], (
        f"O1698_PREFILL_CROSS_ORIGIN: login_url https://www.site.test/login landed on "
        f"{FOREIGN} and do_login filled {calls['fill']} / submitted {calls['submit']}x "
        f"(password typed: {_typed_password(calls)})")
    assert result[0] in (False, "MANUAL_PENDING"), result
    assert "cross-origin" in result[1] and "unrelated-host.test" in result[1], result[1]


def test_re_submit_after_a_challenge_never_fills_an_unrelated_origin(monkeypatch, tmp_path):
    result, calls = _run(monkeypatch, tmp_path, login_url="https://www.site.test/login",
                         landed="https://www.site.test/login", challenge_to=FOREIGN)
    assert calls["fill"][:2] == ["username", "password"], calls  # the first fill is legit
    assert calls["fill"] == ["username", "password"] and calls["submit"] == 1, (
        f"O1698_RESUBMIT_CROSS_ORIGIN: challenge cleared onto {FOREIGN}; the re-submit "
        f"filled {calls['fill'][2:]} and submitted {calls['submit']}x")
    assert result[0] is False and "cross-origin" in result[1], result


# ── CONTROLS: the declared and same-brand origins still fill ──

@pytest.mark.parametrize("login_url,landed,success_url,flow_ran", [
    ("https://www.site.test/login", "https://www.site.test/login?next=/", None, False),
    ("https://vip4k.com/login", "https://login.vip4k.com/login", None, False),
    ("https://www.site.test/login", "https://auth.idp-host.test/u/login",
     "https://auth.idp-host.test/members", False),
    ("https://www.site.test/login", FOREIGN, None, True),
], ids=["same-origin", "same-brand", "declared-success-origin", "captured-flow"])
def test_control_declared_origins_still_fill(monkeypatch, tmp_path, login_url, landed,
                                             success_url, flow_ran):
    _result, calls = _run(monkeypatch, tmp_path, login_url=login_url, landed=landed,
                          success_url=success_url, flow_ran=flow_ran)
    assert calls["fill"] == ["username", "password"] and calls["submit"] == 1, (
        f"O1698_OVERREFUSE: {login_url} -> {landed} not filled: {calls}")


def test_control_same_origin_re_submit_still_fills(monkeypatch, tmp_path):
    result, calls = _run(monkeypatch, tmp_path, login_url="https://www.site.test/login",
                         landed="https://www.site.test/login",
                         challenge_to="https://www.site.test/login?retry=1")
    assert calls["fill"] == ["username", "password", "username (re-submit)",
                             "password (re-submit)"] and calls["submit"] == 2, (calls, result[1])


# ── r2: every credential fill, not only the first ──

@pytest.mark.parametrize("allow_manual", [False, True], ids=["auto", "manual-takeover"])
def test_staged_continue_onto_an_unrelated_origin_never_gets_the_password(monkeypatch, tmp_path,
                                                                          allow_manual):
    result, calls = _run(monkeypatch, tmp_path, login_url="https://www.site.test/login",
                         landed="https://www.site.test/login", staged_to=FOREIGN,
                         allow_manual=allow_manual)
    assert calls["fill"][:2] == ["username", "password"], calls  # premise: first screen had no field
    assert not _typed_password(calls) and not calls["submit"], (
        f"O1698_R2_STAGED_CROSS_ORIGIN: Continue moved the page to {FOREIGN} and the staged "
        f"retry typed the password (fills {calls['fill']}, submitted {calls['submit']}x)")
    assert result[0] in (False, "MANUAL_PENDING"), result
    assert "cross-origin" in result[1] and "unrelated-host.test" in result[1], result[1]


@pytest.mark.parametrize("allow_manual", [False, True], ids=["auto", "manual-takeover"])
def test_a_redirect_between_username_and_password_never_gets_the_password(monkeypatch, tmp_path,
                                                                          allow_manual):
    result, calls = _run(monkeypatch, tmp_path, login_url="https://www.site.test/login",
                         landed="https://www.site.test/login", after_username=FOREIGN,
                         allow_manual=allow_manual)
    assert calls["fill"][:1] == ["username"], calls  # the username fill was on the login page
    assert calls["fill"] == ["username"] and not calls["submit"], (
        f"O1698_R2_BETWEEN_FILLS_CROSS_ORIGIN: the page moved to {FOREIGN} after the username; "
        f"do_login still filled {calls['fill'][1:]} (password typed: {_typed_password(calls)})")
    assert result[0] in (False, "MANUAL_PENDING"), result
    assert "cross-origin" in result[1] and "unrelated-host.test" in result[1], result[1]


def test_an_unreadable_page_url_is_refused_not_a_type_error(monkeypatch, tmp_path):
    from bulk_downloader.login_impl import submit
    for unreadable in (None, 7):
        try:
            why = submit._prefill_origin_refusal(SimpleNamespace(url=unreadable),
                                                 "https://www.site.test/login", None)
        except TypeError as e:
            pytest.fail(f"O1698_R2_URL_NONE: page.url={unreadable!r} raised {e!r} instead of a refusal")
        assert why and "cross-origin" in why, (unreadable, why)
    try:
        result, calls = _run(monkeypatch, tmp_path, login_url="https://www.site.test/login",
                             landed=None)
    except TypeError as e:
        pytest.fail(f"O1698_R2_URL_NONE: do_login on a page.url=None page raised {e!r}")
    assert not calls["fill"] and result[0] is False and "cross-origin" in result[1], (calls, result)


@pytest.mark.parametrize("kw", [
    {"staged_to": "https://www.site.test/login/password"},
    {"staged_to": "https://login.site.test/password"},
    {"after_username": "https://www.site.test/login?step=2"},
], ids=["staged-same-origin", "staged-same-brand", "between-fills-same-origin"])
def test_control_second_fill_on_a_declared_origin_still_fills(monkeypatch, tmp_path, kw):
    result, calls = _run(monkeypatch, tmp_path, login_url="https://www.site.test/login",
                         landed="https://www.site.test/login", **kw)
    assert _typed_password(calls) and calls["submit"] == 1, (
        f"O1698_R2_OVERREFUSE: {kw} -> password not typed / not submitted: {calls} {result[1]}")




# r3: drive the real _try_fill through delayed attachment/visibility.
@pytest.mark.parametrize("allow_manual", [False, True], ids=["auto", "manual"])
@pytest.mark.parametrize("state", ["attached", "visible", "clear", "click"])
def test_probe_redirect_between_username_and_password(monkeypatch, tmp_path, allow_manual, state):
    result, calls = _run(
        monkeypatch, tmp_path, login_url="https://www.site.test/login",
        landed="https://www.site.test/login", wait_redirect=FOREIGN,
        wait_state=state, allow_manual=allow_manual)
    assert calls["redirects"] == 1 and calls["values"][:1] == ["fixture"], calls
    assert not _typed_password(calls) and calls["submit"] == 0, (
        f"OGS1C_BETWEEN_CROSS_ORIGIN: fills={calls['fill']} submits={calls['submit']}")
    assert result[0] == ("MANUAL_PENDING" if allow_manual else False), result
    assert "cross-origin" in result[1], result
    assert calls["close"] == (0 if allow_manual else 1), calls
    assert not calls["continued"], "refusal must not enter Continue recovery"


@pytest.mark.parametrize("role,resubmit", [
    ("username", False), ("username", True), ("password", True),
])
def test_every_do_login_fill_rechecks_after_wait(monkeypatch, tmp_path, role, resubmit):
    site = "https://www.site.test/login"
    result, calls = _run(
        monkeypatch, tmp_path, login_url=site, landed=site,
        challenge_to=site if resubmit else None,
        wait_redirect=FOREIGN, wait_field=role, wait_state="visible",
        wait_resubmit=resubmit)
    assert calls["redirects"] == 1, calls
    assert calls["values"] == (["fixture", "zero-entropy-password"] +
                                (["fixture"] if role == "password" else [])
                                if resubmit else []), (
        f"O1698_R3_FILL_WAIT: role={role} resubmit={resubmit} calls={calls}")
    assert calls["submit"] == int(resubmit), calls
    assert result[0] is False and "cross-origin" in result[1], result


@pytest.mark.parametrize("state", ["attached", "visible", "clear", "click"])
def test_control_same_origin_wait_transition_still_fills(monkeypatch, tmp_path, state):
    site = "https://www.site.test/login"
    _result, calls = _run(monkeypatch, tmp_path, login_url=site, landed=site,
                         wait_redirect=site + "?step=2", wait_state=state)
    assert calls["redirects"] == 1 and calls["submit"] == 1, calls
    assert calls["values"] == ["fixture", "zero-entropy-password"], calls


def test_page_url_read_failure_is_refused():
    from bulk_downloader.login_impl import submit

    class ClosedPage:
        @property
        def url(self):
            raise RuntimeError("fixture page closed")

    why = submit._prefill_origin_refusal(ClosedPage(), "https://www.site.test/login", None)
    assert why and "cross-origin" in why, why

# ── r2 A3: the manual-takeover autofill (real ManualLoginSession, headless Chromium) ──
# login_url is on 127.0.0.1; it 302-redirects to the SAME server addressed as
# `localhost` -- another origin and no shared registrable domain. The form
# beacons field LENGTHS (never values) to the server.

_TAKEOVER_FORM = """<html><body><h1>Log In</h1><div id="slot"></div>
<script>
const form = () => { document.getElementById('slot').innerHTML =
  '<form><input type="text" name="username" autocomplete="username" id="u">' +
  '<input type="password" name="password" autocomplete="current-password" id="p"></form>'; };
if (location.search.includes('late=1')) setTimeout(form, 2500); else form();
setInterval(() => { const u = document.getElementById('u'), p = document.getElementById('p');
  if (u) navigator.sendBeacon('/state', JSON.stringify(
    {host: location.hostname, u: u.value.length, p: p.value.length})); }, 200);
</script></body></html>"""


def _takeover_site(foreign):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    states = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            port = self.server.server_address[1]
            query = self.path.partition("?")[2]
            if self.path.startswith("/login") and "form=1" not in query:
                host = "localhost" if foreign else "127.0.0.1"
                self.send_response(302)
                self.send_header("Location", f"http://{host}:{port}/login?form=1&{query}")
                self.end_headers()
                return
            body = _TAKEOVER_FORM.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if self.path.startswith("/state"):
                states.append(json.loads(self.rfile.read(n) or b"{}"))
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, states


@pytest.mark.parametrize("foreign", [True, False], ids=["redirect-to-other-origin", "control-same-origin"])
@pytest.mark.parametrize("late", [False, True], ids=["form-at-open", "form-after-open"])
def test_takeover_autofill_never_fills_a_page_the_login_url_redirected_off_origin(capfd, foreign, late):
    import time

    from bulk_downloader.login_impl import manual

    server, states = _takeover_site(foreign)
    login_url = f"http://127.0.0.1:{server.server_address[1]}/login{'?late=1' if late else ''}"
    cfg = {"name": "o1698-takeover-probe", "login_url": login_url,
           "username": "fixture", "password": "zero-entropy-password",
           "use_real_chrome": False, "use_stealth": False, "browser_backend": "playwright"}
    session = manual.ManualLoginSession(cfg, "", headless=True)
    try:
        assert session.ready, f"premise: the takeover browser did not open ({session.error})"
        deadline = time.time() + 12
        while time.time() < deadline and not any(s["p"] for s in states):
            time.sleep(0.25)
        if foreign:
            time.sleep(2)  # a refused page is given the same time to be filled
    finally:
        session.cancel()
        server.shutdown()
    assert states, "premise: the login form never loaded (no beacon)"
    hosts = {s["host"] for s in states}
    assert hosts == {"localhost" if foreign else "127.0.0.1"}, f"premise: form served on {hosts}"
    filled = any(s["p"] for s in states)
    if foreign:
        assert not filled, (
            "O1698_R2_TAKEOVER_AUTOFILL_CROSS_ORIGIN: login_url 127.0.0.1 redirected to localhost and the "
            f"takeover autofilled the password there (late form={late}; last state {states[-1]})")
    else:
        assert filled, f"O1698_R2_OVERREFUSE: same-origin takeover form not autofilled: {states[-3:]}"
    assert "zero-entropy-password" not in capfd.readouterr().err


"""OGR2S-C security probe (r2): the origin is asked once, BEFORE _try_fill;
_try_fill then waits up to _MATCH_WAIT_MS per selector for the field to
attach. A redirect that commits inside that wait gets the password.
Stubbed page only; no browser, no real credentials."""
LOGIN = "https://www.site.test/login"
SECRET = "S3CRET-PROBE"


class _Loc:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def nth(self, i):
        return self

    def count(self):
        return 1 if self.page.field else 0

    def wait_for(self, state=None, timeout=None):
        p = self.page
        if state == "visible" and timeout == 6000 and not p.field:
            raise TimeoutError("staged wait: interstitial has no password field")
        if state == "attached" and p.flip_in_fill and not p.field:
            p.url, p.field = FOREIGN, True   # redirect commits during the wait
        if not p.field:
            raise TimeoutError("not attached")

    def fill(self, v):
        pass

    def click(self, timeout=None):
        pass


class _Page:
    def __init__(self, url, field, flip_in_fill=False):
        self.url, self.field, self.flip_in_fill = url, field, flip_in_fill

    def locator(self, sel):
        return _Loc(self)


@pytest.fixture
def typed(monkeypatch):
    rec = []
    monkeypatch.setattr(submit, "_try_click", lambda *a, **k: (True, "cont"))
    monkeypatch.setattr(_common, "_is_honeypot_field", lambda loc: (False, ""))
    monkeypatch.setattr(_common, "_is_search_field", lambda loc: (False, ""))
    monkeypatch.setattr(_common, "_type_field_value",
                        lambda page, sel, value, refusal=None: rec.append((page.url, value)))
    return rec


def _go(page):
    return submit._staged_password_retry(
        page, ["#c"], submit.PASS_FIELD_FALLBACKS, SECRET,
        refusal=lambda: submit._prefill_origin_refusal(page, LOGIN, None))


def test_control_same_origin_staged_fills(typed):
    ok, _ = _go(_Page(LOGIN + "/step2", field=True))
    assert ok and typed == [(LOGIN + "/step2", SECRET)]


def test_control_foreign_before_check_is_refused(typed):
    ok, why = _go(_Page(FOREIGN, field=True))
    assert not ok and "cross-origin" in why and typed == []


def test_probe_redirect_inside_try_fill_wait_never_gets_password(typed):
    ok, info = _go(_Page(LOGIN + "/interstitial", field=False, flip_in_fill=True))
    leaked = [u for u, v in typed if v == SECRET and not u.startswith("https://www.site.test")]
    assert not leaked, f"OGR2S_FILL_WAIT_RACE: password typed on {leaked} ok={ok} info={info}"


@pytest.mark.parametrize("raises", [False, True])
def test_refusal_is_terminal_after_skipping_a_decoy(monkeypatch, typed, raises):
    page = _Page(LOGIN, field=True)
    visited, cleared, checks = [], [], []

    class Match(_Loc):
        def __init__(self, index=0):
            super().__init__(page)
            self.index = index

        def count(self):
            return 3

        def nth(self, index):
            visited.append(index)
            return Match(index)

        def wait_for(self, state=None, timeout=None):
            if state == "visible":
                page.url = FOREIGN

        def fill(self, value):
            cleared.append(value)

    page.locator = lambda selector: Match()
    monkeypatch.setattr(_common, "_is_honeypot_field",
                        lambda loc: (loc.index == 0, "decoy"))

    def refuse():
        checks.append(page.url)
        if raises:
            raise RuntimeError("fixture URL unavailable")
        return submit._prefill_origin_refusal(page, LOGIN, None)

    ok, why = _common._try_fill(page, ["#first", "#later"], SECRET, "password",
                               refusal=refuse)
    assert not ok and "cross-origin" in why, why
    assert visited == [0, 1] and checks == [FOREIGN], (
        f"O1698_TERMINAL_REFUSAL: visited={visited} checks={checks}")
    assert cleared == typed == [], "O1698_TERMINAL_REFUSAL: field mutated"


@pytest.mark.parametrize("foreign", [False, True])
def test_click_failure_rechecks_before_fallback_fill(typed, foreign):
    page = _Page(LOGIN, field=True)
    fills = []

    class ClickFailure(_Loc):
        def click(self, timeout=None):
            page.url = FOREIGN if foreign else LOGIN + "?step=2"
            raise RuntimeError("fixture overlay intercepts click")

        def fill(self, value):
            fills.append(value)

    page.locator = lambda selector: ClickFailure(page)
    ok, why = _common._try_fill(
        page, ["#first", "#later"], SECRET, "password",
        refusal=lambda: submit._prefill_origin_refusal(page, LOGIN, None))
    assert ok is (not foreign), f"O1698_FALLBACK_REFUSAL: {why} {fills}"
    assert fills == ([""] if foreign else ["", SECRET]), (
        f"O1698_FALLBACK_REFUSAL: unexpected values {fills}")
    assert typed == []


def test_staged_preask_error_is_reported_as_refusal(monkeypatch, tmp_path):
    result, calls = _run(monkeypatch, tmp_path, login_url=LOGIN, landed=LOGIN,
                         staged_to=LOGIN + "/step2", staged_refusal_error=True)
    assert calls["continued"], "premise: reached staged pre-ask"
    assert not _typed_password(calls) and calls["submit"] == 0
    assert result[0] is False
    assert "cross-origin prefill refused" in result[1], (
        f"OG_FU1_STAGED_CHECK_ERROR: {result[1]}")
    assert "could not verify" in result[1]
