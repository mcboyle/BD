"""O1826 C13 -- session_keeper's fetch and httpx probes, and pause_site_keepers.

M161/M162. The Playwright navigate probe was moved to word-boundary login
detection (_LOGIN_URL_RE, _LOGIN_FORM_JS) because the substring tests missed
Devise's /users/sign_in and every page whose button says "Sign in". The fetch
and httpx probes kept their own copies of those substring tests. So the same
logged-out site read DEAD through Playwright and ALIVE or INCONCLUSIVE through
the fallbacks:
  * httpx 302 -> /users/sign_in: "sign_in" holds neither "login" nor "signin",
    so the probe fell through to INCONCLUSIVE instead of DEAD;
  * 200 with a password field and "Sign in" (no "login"): ALIVE on both.

M163. pause_site_keepers ran each keeper's Chromium teardown while holding the
global _state_lock. Every keeper thread needs that lock to record its state,
so one slow browser close stalled all of them.

NEGATIVE CONTROLS. A member page still reads ALIVE, and a change-password page
("Signed in as ...", "Design intent") still reads ALIVE: the
password-AND-sign-in conjunction and the word boundaries must survive. Pause
still tears down every keeper for the site and leaves other sites alone.
"""
from __future__ import annotations

import threading

import pytest

from bulk_downloader import session_keeper as sk


BD_GATE_SCOPE = "module"


_BASE = "https://c13.test"
_CFG = {"login_url": _BASE + "/login", "success_url": _BASE + "/home",
        "keep_alive_check_url": _BASE + "/account", "password": "p",
        "keep_alive_enabled": True}

_SIGN_IN_PAGE = ('<html><body><form action="/session">'
                 '<input type="password" name="pw">'
                 '<button>Sign in</button></form></body></html>')
# Carries a sign-in word but no password field: pins the conjunction, since
# the word alone must not read as a logout.
_MEMBER_PAGE = ('<html><body><h1>Your library</h1>'
                '<p>Last log-in: 2026-10-03</p>'
                '<a href="/logout">Log out</a></body></html>')
# Mirrors the canaries in tests/test_heartbeat_detects_a_logged_out_page.py:
# a password field without a sign-in word, plus "Signed in" and "Design intent"
# which only a substring test would read as a sign-in word.
_CHANGE_PASSWORD_PAGE = ('<html><body><h1>Design intent review</h1>'
                         '<p>Signed in as tester.</p><form>'
                         '<input type="password" name="current">'
                         '<button>Change password</button>'
                         '</form></body></html>')

# r2: the Python twin must read what _LOGIN_FORM_JS reads (body.textContent and
# a real password <input>), not raw markup. Each logged-in change-password page
# below carries a sign-in word ONLY where the DOM hides it from textContent --
# Chromium reads every one ALIVE (.review/r2/twin_probe.out). A raw-HTML scan
# read them DEAD, and DEAD on every beat is a relogin storm on a live session.
_CHANGE_PW_FORM = ('<form><input type="password" name="current">'
                   '<button>Change password</button></form>')
_HIDDEN_SIGNIN_MEMBER_PAGES = {
    "head_meta": ('<html><head><meta name="description" content="Sign-in and '
                  'security settings"></head><body><h1>Security</h1>'
                  + _CHANGE_PW_FORM + '</body></html>'),
    "head_title": ('<html><head><title>Sign in &amp; security</title></head>'
                   '<body>' + _CHANGE_PW_FORM + '</body></html>'),
    "attribute": ('<html><body><a href="/users/sign_in" hidden></a>'
                  + _CHANGE_PW_FORM + '</body></html>'),
    "comment": ('<html><body><!-- Sign in modal removed -->'
                + _CHANGE_PW_FORM + '</body></html>'),
    "template": ('<html><body><template><input type="password"></template>'
                 '<p>Sign in to another account</p></body></html>'),
}
# r2: login pages Chromium reads DEAD that a 'type="password"' substring and a
# raw-markup word scan both missed.
_MARKUP_VARIANT_SIGN_IN_PAGES = {
    "single_quoted": ("<html><body><form><input type='password' name='pw'>"
                      "<button>Sign in</button></form></body></html>"),
    "unquoted": ("<html><body><form><input type=password name=pw>"
                 "<button>Log in</button></form></body></html>"),
    "uppercase": ('<html><body><form><INPUT TYPE="PASSWORD" name="pw">'
                  '<button>Sign In</button></form></body></html>'),
    "spaced": ('<html><body><form><input type = "password" name="pw">'
               '<button>Sign in</button></form></body></html>'),
    "nbsp_entity": ('<html><body><form><input type="password" name="pw">'
                    '<button>Log&nbsp;in</button></form></body></html>'),
    "markup_split": ('<html><body><form><input type="password" name="pw">'
                     '<button>Sign<span> in</span></button></form>'
                     '</body></html>'),
}
# r3: Chromium runs _LOGIN_FORM_JS with scripting on, so <noscript> content is
# raw text: its tags are not elements, and a head <noscript> does not close
# <head>. Parsed as markup, a noscript password input counted as real, and a
# tracking-pixel <img>/<iframe> in a head <noscript> opened the body, so the
# head <script>/<title> after it read as body text -> DEAD on a live session.
# Chromium reads every page below JS=False (.review/r3/js_oracle.out).
_FB_PIXEL = ('<noscript><img height="1" width="1" style="display:none" '
             'src="https://www.facebook.com/tr?id=1&ev=PageView&noscript=1"/>'
             '</noscript>')
_GTM = ('<noscript><iframe src="https://www.googletagmanager.com/ns.html?'
        'id=GTM-C13" height="0" width="0" style="display:none"></iframe>'
        '</noscript>')
_NOSCRIPT_MEMBER_PAGES = {
    "body_noscript_fallback_form": (
        '<html><body><h1>Your library</h1>\n<noscript><form action="/session">'
        '<input type="password" name="pw"><button>Sign in</button></form>'
        '</noscript>\n<a href="/logout">Log out</a></body></html>'),
    "head_fb_pixel_then_script": (
        '<html><head><title>Security</title>' + _FB_PIXEL
        + '<script>var next = "/users/sign_in";</script></head><body>'
        + _CHANGE_PW_FORM + '</body></html>'),
    "head_gtm_then_title": (
        '<html><head>' + _GTM + '<title>Log in and security</title></head>'
        '<body>' + _CHANGE_PW_FORM + '</body></html>'),
}
_NOSCRIPT_TWIN_PAGES = dict(_NOSCRIPT_MEMBER_PAGES, **{
    "body_noscript_password": (
        '<html><body><noscript><input type="password" name="pw"></noscript>'
        '<p>Sign in history</p></body></html>'),
    "head_noscript_password": (
        '<html><head><noscript><input type="password" name="pw"></noscript>'
        '</head><body><p>Sign in history</p></body></html>'),
})
# A sign-in page whose <head> carries a <title> and a <script> before the form:
# the head-text counter must close, or the whole body reads as head text.
_TITLED_SIGN_IN_PAGE = ('<html><head><title>Example</title>'
                        '<script>var a = 1;</script></head><body>'
                        '<form action="/session"><input type="password" '
                        'name="pw"><button>Sign in</button></form>'
                        '</body></html>')


class _FakePage:
    def __init__(self, fetch_result):
        self._fetch_result = fetch_result
        self.fetch_evals = 0

    def evaluate(self, js):
        assert "fetch(" in js, "only the in-page fetch probe is scripted"
        self.fetch_evals += 1
        return dict(self._fetch_result)


class _FakeHttpxResponse:
    def __init__(self, status_code, headers=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


class _FakeHttpxClient:
    def __init__(self, response):
        self._response = response
        self.gets = []

    def __call__(self, *_args, **_kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, url):
        self.gets.append(url)
        return self._response


def _keeper(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    keeper = sk.SessionKeeper("c13", 0, dict(_CFG), lambda *_a: (False, "no"))
    monkeypatch.setattr(keeper, "_persist_cookies", lambda: None)
    return keeper


def _httpx_probe(monkeypatch, tmp_path, response):
    import httpx
    from bulk_downloader import download_egress as _de

    keeper = _keeper(monkeypatch, tmp_path)
    # Stub the cookie read and the VPN proxy resolution so neither refuses
    # first and manufactures a DEAD that is not about the response.
    monkeypatch.setattr(keeper, "_load_cookies", lambda: {"sid": "c13"})
    monkeypatch.setattr(_de, "effective_download_proxy",
                        lambda *_a, **_k: None)
    client = _FakeHttpxClient(response)
    monkeypatch.setattr(httpx, "Client", client)
    verdict, detail = keeper._heartbeat_httpx_fallback()
    assert client.gets == [_CFG["keep_alive_check_url"]], client.gets
    return verdict, detail


def _fetch_probe(monkeypatch, tmp_path, body):
    keeper = _keeper(monkeypatch, tmp_path)
    page = _FakePage({"status": 200, "type": "basic",
                      "url": _BASE + "/account", "body": body})
    keeper._page = page
    verdict, detail = keeper._heartbeat_in_page_fetch()
    assert page.fetch_evals == 1, page.fetch_evals
    return verdict, detail


# -- M162: the httpx redirect test ---------------------------------------------

@pytest.mark.parametrize("location", [
    "/users/sign_in",
    _BASE + "/users/sign_in?return_to=%2Faccount",
    "/session/new",
])
def test_httpx_redirect_to_a_sign_in_route_is_dead(monkeypatch, tmp_path,
                                                   location):
    verdict, detail = _httpx_probe(monkeypatch, tmp_path, _FakeHttpxResponse(
        302, headers={"location": location}))
    assert verdict is sk.DEAD, (
        f"302 -> {location} read {verdict!r} ({detail!r}); the navigate probe "
        f"calls this redirect a logout, the httpx fallback must too")


def test_httpx_redirect_elsewhere_stays_inconclusive(monkeypatch, tmp_path):
    verdict, detail = _httpx_probe(monkeypatch, tmp_path, _FakeHttpxResponse(
        302, headers={"location": "/maintenance"}))
    assert verdict is sk.INCONCLUSIVE, (verdict, detail)


# -- M161/M162: the body test, on both fallbacks -------------------------------

def test_httpx_sign_in_page_is_dead(monkeypatch, tmp_path):
    verdict, detail = _httpx_probe(monkeypatch, tmp_path,
                                   _FakeHttpxResponse(200, text=_SIGN_IN_PAGE))
    assert verdict is sk.DEAD, (
        f"a 200 with a password field and 'Sign in' read {verdict!r} "
        f"({detail!r}) through the httpx fallback")


def test_in_page_fetch_sign_in_page_is_dead(monkeypatch, tmp_path):
    verdict, detail = _fetch_probe(monkeypatch, tmp_path, _SIGN_IN_PAGE)
    assert verdict is sk.DEAD, (
        f"a 200 with a password field and 'Sign in' read {verdict!r} "
        f"({detail!r}) through the in-page fetch")


@pytest.mark.parametrize("body", [_MEMBER_PAGE, _CHANGE_PASSWORD_PAGE],
                         ids=["member", "change_password"])
def test_httpx_logged_in_pages_stay_alive(monkeypatch, tmp_path, body):
    verdict, detail = _httpx_probe(monkeypatch, tmp_path,
                                   _FakeHttpxResponse(200, text=body))
    assert verdict is sk.ALIVE, (verdict, detail)


@pytest.mark.parametrize("body", [_MEMBER_PAGE, _CHANGE_PASSWORD_PAGE],
                         ids=["member", "change_password"])
def test_in_page_fetch_logged_in_pages_stay_alive(monkeypatch, tmp_path, body):
    verdict, detail = _fetch_probe(monkeypatch, tmp_path, body)
    assert verdict is sk.ALIVE, (verdict, detail)


@pytest.mark.parametrize("probe", ["httpx", "fetch"])
@pytest.mark.parametrize("case", sorted(_HIDDEN_SIGNIN_MEMBER_PAGES))
def test_sign_in_word_outside_body_text_stays_alive(monkeypatch, tmp_path,
                                                    probe, case):
    body = _HIDDEN_SIGNIN_MEMBER_PAGES[case]
    if probe == "httpx":
        verdict, detail = _httpx_probe(monkeypatch, tmp_path,
                                       _FakeHttpxResponse(200, text=body))
    else:
        verdict, detail = _fetch_probe(monkeypatch, tmp_path, body)
    assert verdict is sk.ALIVE, (
        f"{probe}: logged-in page ({case}) read {verdict!r} ({detail!r}); its "
        f"sign-in word is not in body.textContent, so _LOGIN_FORM_JS reads it "
        f"ALIVE -- the raw-HTML twin would relogin a live session every beat")


@pytest.mark.parametrize("probe", ["httpx", "fetch"])
@pytest.mark.parametrize("case", sorted(_MARKUP_VARIANT_SIGN_IN_PAGES))
def test_sign_in_page_markup_variants_are_dead(monkeypatch, tmp_path, probe,
                                               case):
    body = _MARKUP_VARIANT_SIGN_IN_PAGES[case]
    if probe == "httpx":
        verdict, detail = _httpx_probe(monkeypatch, tmp_path,
                                       _FakeHttpxResponse(200, text=body))
    else:
        verdict, detail = _fetch_probe(monkeypatch, tmp_path, body)
    assert verdict is sk.DEAD, (
        f"{probe}: sign-in page ({case}) read {verdict!r} ({detail!r}); "
        f"_LOGIN_FORM_JS reads it as a login form")


@pytest.mark.parametrize("case", sorted(_NOSCRIPT_TWIN_PAGES))
def test_twin_reads_noscript_as_raw_text_like_the_js(case):
    assert sk._body_has_login_form(_NOSCRIPT_TWIN_PAGES[case]) is False, (
        f"twin read {case} as a login form; with scripting on Chromium parses "
        f"<noscript> as raw text and _LOGIN_FORM_JS returns false")


@pytest.mark.parametrize("probe", ["httpx", "fetch"])
@pytest.mark.parametrize("case", sorted(_NOSCRIPT_MEMBER_PAGES))
def test_noscript_member_pages_stay_alive(monkeypatch, tmp_path, probe, case):
    body = _NOSCRIPT_MEMBER_PAGES[case]
    if probe == "httpx":
        verdict, detail = _httpx_probe(monkeypatch, tmp_path,
                                       _FakeHttpxResponse(200, text=body))
    else:
        verdict, detail = _fetch_probe(monkeypatch, tmp_path, body)
    assert verdict is sk.ALIVE, (
        f"{probe}: logged-in page ({case}) read {verdict!r} ({detail!r}); a "
        f"<noscript> parsed as markup relogins a live session every beat")


@pytest.mark.parametrize("probe", ["httpx", "fetch"])
def test_titled_head_sign_in_page_is_dead(monkeypatch, tmp_path, probe):
    if probe == "httpx":
        verdict, detail = _httpx_probe(
            monkeypatch, tmp_path,
            _FakeHttpxResponse(200, text=_TITLED_SIGN_IN_PAGE))
    else:
        verdict, detail = _fetch_probe(monkeypatch, tmp_path,
                                       _TITLED_SIGN_IN_PAGE)
    assert verdict is sk.DEAD, (
        f"{probe}: sign-in page with a head <title>/<script> read {verdict!r} "
        f"({detail!r}); the head text never closed, so the form text was lost")


# -- M163: pause_site_keepers and _state_lock ---------------------------------

def _lock_free_from_another_thread(timeout=1.0) -> bool:
    got = []

    def probe():
        ok = sk._state_lock.acquire(timeout=timeout)
        if ok:
            sk._state_lock.release()
        got.append(ok)

    t = threading.Thread(target=probe)
    t.start()
    t.join(timeout + 5)
    return bool(got and got[0])


class _FakeKeeper:
    def __init__(self):
        self.torn_down = 0
        self.lock_free_during_teardown = None

    def _teardown_browser(self):
        self.torn_down += 1
        self.lock_free_during_teardown = _lock_free_from_another_thread()


def test_lock_probe_can_say_held():
    # Positive control: the probe reports False while the lock is held.
    with sk._state_lock:
        assert _lock_free_from_another_thread(timeout=0.2) is False
    assert _lock_free_from_another_thread(timeout=0.2) is True


def test_pause_tears_down_outside_the_state_lock(monkeypatch):
    keepers = {("c13", 0): _FakeKeeper(), ("c13", 1): _FakeKeeper(),
               ("other", 0): _FakeKeeper()}
    monkeypatch.setattr(sk, "_keepers", dict(keepers))

    assert sk.pause_site_keepers("c13") == 2

    for key in (("c13", 0), ("c13", 1)):
        assert keepers[key].torn_down == 1, key
        assert keepers[key].lock_free_during_teardown is True, (
            f"{key}: _state_lock was held across the browser teardown, so "
            f"every keeper's _set_state blocked behind a Chromium close")
    assert keepers[("other", 0)].torn_down == 0
