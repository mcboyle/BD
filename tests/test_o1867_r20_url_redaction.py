"""O1867 R20: redact_url_credentials must not keep userinfo or fragment tokens.

login_impl/replay.py redact_url_credentials is the one redactor every login
status, evidence and runner_auth log line goes through.  It replaced only
credential-named QUERY values, so a URL read with userinfo
(https://user:pw@host) or an OAuth implicit-grant redirect
(#access_token=...) reached those sinks in cleartext.

RED (base): the userinfo, fragment and SPA-route fragment cases keep their
secret, the unparsable URL is returned as read, and the member probe's
cross-host message (_probe_member_url, O1856 option C) carries the landed
URL unredacted, and the query is re-encoded (q=a:b -> q=a%3Ab, flag ->
flag=).  GREEN: each secret is gone and every other byte is kept.
NEGATIVE CONTROL (passes on base and candidate): a URL with no credential in
its query or fragment is kept byte-for-byte.

r2 (cx8 r1 REFUTE F1/F2).  F1: a raised error carries the URL it was given
(Page.goto: net::ERR_UNSAFE_PORT at https://user:pw@host/...), and every
replay.py sink formatted str(e) raw into the verify result, its summary, a
member-state reason, the evidence HTML or a log line; app_sites_auth returns
that result as-is.  F2: the rebuild dropped an empty '?' or '#'.  RED (r1
tree a3b837b0): every exception sink keeps the secrets and 'x?' / 'x#' lose
their delimiter.  GREEN: each sink redacts BEFORE it truncates, every other
byte kept.  NEGATIVE CONTROL: an error that names no URL is kept verbatim.

r3 (cx8 r2 REFUTE F1).  member_state_check formatted the configured
success_url and member indicator raw -- a selector can name a URL
(a[href="https://user:pw@host/cb#access_token=..."]) -- and that reason is
the member_why submit._no_nav_verdict writes into login info and stderr; the
cookies-only verify reason formatted the same indicator raw.  RED (r2 tree
6e1cf54a): each of those reasons, the info and the stderr line keep the
secrets.  GREEN: redacted at the source.  NEGATIVE CONTROL: a configured URL
or selector with no credential is shown byte-for-byte.
"""
from __future__ import annotations

import contextlib
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bulk_downloader.login_impl import replay  # noqa: E402
from bulk_downloader.login_impl.replay import (  # noqa: E402
    _probe_member_url,
    redact_url_credentials,
)

BD_GATE_SCOPE = "module"

# Fake credential fixtures, assembled at runtime so the source carries no
# secret-shaped literal for the train's gitleaks gate.
_PW = "O1867R20-" + "PW"
_USER = "O1867R20-" + "USER"
_TOK = "O1867R20-" + "TOK"
_QPW = "O1867R20-" + "QPW"


def _redacted(url):
    return redact_url_credentials(f"landed at {url} (status 200)")


def test_query_credential_value_is_redacted():
    out = _redacted(f"https://site.example/login?user=alice&password={_QPW}"
                    "&next=members")
    assert _QPW not in out, out
    assert "password=<REDACTED>" in out, out
    assert "next=members" in out, out


@pytest.mark.parametrize("userinfo", [f"{_USER}:{_PW}", _USER])
def test_userinfo_is_redacted(userinfo):
    out = _redacted(f"https://{userinfo}@site.example:8443/members?page=2")
    assert _USER not in out and _PW not in out, out
    assert "https://<REDACTED>@site.example:8443/members?page=2" in out, out


@pytest.mark.parametrize("fragment", [
    f"access_token={_TOK}&token_type=bearer&state=s1",
    f"/callback?id_token={_TOK}&state=s1",
])
def test_credential_fragment_param_is_redacted(fragment):
    out = _redacted(f"https://site.example/cb#{fragment}")
    assert _TOK not in out, out
    assert "token=<REDACTED>&" in out, out
    assert "state=s1" in out, out


def test_unparsable_url_is_withheld_not_returned_as_read():
    out = _redacted(f"https://{_USER}:{_PW}@[site.example/members")
    assert _PW not in out and _USER not in out, out
    assert out == "landed at <REDACTED> (status 200)", out


@pytest.mark.parametrize("url", [
    "https://site.example/members?page=2&sort=asc",
    "https://site.example/members#section-2",
    "https://site.example/#/videos?page=3&order=new",
])
def test_negative_control_noncredential_url_is_kept_verbatim(url):
    assert _redacted(url) == f"landed at {url} (status 200)"


def test_noncredential_query_bytes_are_not_reencoded():
    url = f"https://site.example/members?q=a:b&flag&token={_TOK}"
    assert _redacted(url) == ("landed at https://site.example/members"
                              "?q=a:b&flag&token=<REDACTED> (status 200)")


class _Locator:
    def count(self):
        return 0


class _CrossHostPage:
    """Lands on another host after goto -- the URL a redirect handed back."""

    def __init__(self, landed):
        self.url = ""
        self._landed = landed

    def goto(self, url, **_kw):
        self.url = self._landed

    def wait_for_timeout(self, _ms):
        pass

    def locator(self, _selector):
        return _Locator()


def test_member_probe_cross_host_message_is_redacted():
    landed = (f"https://{_USER}:{_PW}@sso.example/cb"
              f"#access_token={_TOK}&state=s1")
    ok, _ms, error = _probe_member_url(
        _CrossHostPage(landed), "https://site.example/members", 5)
    assert ok is False
    assert "redirected to a different host" in error, error
    for secret in (_USER, _PW, _TOK):
        assert secret not in error, error
    assert "sso.example/cb" in error, error


# ── r2 F2: an empty '?' or '#' is a byte of the URL as read ─────────────


@pytest.mark.parametrize("url", [
    "https://site.example/x?",
    "https://site.example/x#",
    "https://site.example/x?#",
    "HTTPS://Site.Example/x?",
])
def test_empty_query_and_fragment_delimiters_are_kept(url):
    assert _redacted(url) == f"landed at {url} (status 200)"


def test_empty_delimiters_kept_beside_a_redacted_credential():
    url = f"https://{_USER}:{_PW}@site.example/x?#"
    assert _redacted(url) == ("landed at https://<REDACTED>@site.example/x?#"
                              " (status 200)")


# cx8 r1 OWN-PROBES: shapes the rebuild must still redact.
@pytest.mark.parametrize("url", [
    "https://u:p:a@b@host.example:8443/x",
    "https://u:p%40a%3Ab@[::1]:8443/x",
    "https://u@例え.example/x",
    f"https://host.example/cb#/route?access_token={_TOK}&state=1",
    f"https://host.example/cb#password={_TOK};page=1",
    f"https://host.example/cb#%74oken={_TOK}&state=1",
])
def test_cx8_redaction_edges(url):
    out = redact_url_credentials(url)
    assert _TOK not in out and "u:p" not in out and "u@" not in out, out


# ── r2 F1: every exception sink in replay.py redacts the error text ──────

# A credentialed URL as Playwright quotes it in a raised navigation error:
# userinfo, a credential query value and an implicit-grant fragment token.
_SECRET_URL = (f"https://{_USER}:{_PW}@sso.example:6000/cb"
               f"?password={_QPW}&next=m#access_token={_TOK}&state=s1")
_SECRETS = (_USER, _PW, _QPW, _TOK)


def _goto_error():
    return RuntimeError(f"Page.goto: net::ERR_UNSAFE_PORT at {_SECRET_URL}\n"
                        f"Call log:\n  - navigating to \"{_SECRET_URL}\"")


def _assert_redacted(text):
    for secret in _SECRETS:
        assert secret not in text, text
    assert "<REDACTED>@sso.example:6000/cb" in text, text


class _RaisingLocator:
    def __init__(self, exc):
        self._exc = exc

    def count(self):
        if self._exc is not None:
            raise self._exc
        return 0


class _ErrPage:
    """A page whose every Playwright call named in *raises* fails with the
    error given; the rest answer as an empty, formless page."""

    def __init__(self, raises=None, url="https://site.example/login",
                 counts=None):
        self._raises = raises or {}
        self._url = url
        self._counts = counts or {}

    def _maybe(self, name):
        if name in self._raises:
            raise self._raises[name]

    @property
    def url(self):
        self._maybe("url")
        return self._url

    def set_default_timeout(self, _ms):
        pass

    def goto(self, url, **_kw):
        self._maybe(f"goto:{url}")
        self._url = url

    def wait_for_timeout(self, _ms):
        pass

    def locator(self, selector):
        if "locator" in self._raises:
            return _RaisingLocator(self._raises["locator"])
        n = self._counts.get(selector, 0)
        return type("_L", (), {"count": lambda _self: n})()

    def content(self):
        self._maybe("content")
        return "<html></html>"

    def screenshot(self, **_kw):
        self._maybe("screenshot")

    def evaluate(self, *_a):
        self._maybe("evaluate")
        return {"password_visible": False, "text": ""}


def test_member_probe_navigation_error_is_redacted():
    page = _ErrPage({"goto:https://site.example/members": _goto_error()})
    ok, _ms, error = _probe_member_url(page, "https://site.example/members", 5)
    assert ok is False and "navigation to member URL failed" in error, error
    _assert_redacted(error)


def test_member_probe_locator_error_is_redacted():
    page = _ErrPage({"locator": _goto_error()})
    ok, _ms, error = _probe_member_url(page, "https://site.example/members", 5)
    assert ok is False and "probe failed" in error, error
    for secret in _SECRETS:
        assert secret not in error, error


def test_error_is_redacted_before_it_is_cut():
    # A long message puts the [:150] cut INSIDE the userinfo: cutting first
    # leaves "https://O1867R20-USER:O18..." with no '@' left to redact.
    exc = RuntimeError("x" * 120 + f" at {_SECRET_URL}")
    page = _ErrPage({"goto:https://site.example/members": exc})
    _ok, _ms, error = _probe_member_url(page, "https://site.example/members", 5)
    assert "O1867R20" not in error, error


def test_error_without_url_is_kept_verbatim():
    # NEGATIVE CONTROL: redaction touches URLs only.
    exc = RuntimeError("Timeout 5000ms exceeded.")
    page = _ErrPage({"goto:https://site.example/members": exc})
    _ok, _ms, error = _probe_member_url(page, "https://site.example/members", 5)
    assert error == "navigation to member URL failed: Timeout 5000ms exceeded."


def test_member_state_reason_final_url_unreadable_is_redacted(tmp_path):
    page = _ErrPage({"url": _goto_error()})
    matched, why, _path = replay.member_state_check(
        page, {"success_url": "https://site.example/home",
               "login_evidence_dir": str(tmp_path)})
    assert matched is False and "final URL unreadable" in why, why
    _assert_redacted(why)


def test_member_state_reason_indicator_unreadable_is_redacted(tmp_path):
    page = _ErrPage({"locator": _goto_error()})
    matched, why, _path = replay.member_state_check(
        page, {"member_indicator": "a.logout",
               "login_evidence_dir": str(tmp_path)})
    assert matched is False and "unreadable" in why, why
    _assert_redacted(why)


def test_evidence_html_and_screenshot_errors_are_redacted(tmp_path, capsys):
    page = _ErrPage({"content": _goto_error(), "screenshot": _goto_error()})
    path = replay.write_login_evidence(
        page, {"login_evidence_dir": str(tmp_path)},
        "https://site.example/home", "login-test")
    html = pathlib.Path(path).read_text(encoding="utf-8")
    assert "page content unavailable" in html, html
    _assert_redacted(html)
    err = capsys.readouterr().err
    assert "evidence screenshot unavailable" in err, err
    _assert_redacted(err)


def test_evidence_write_error_is_redacted(monkeypatch, capsys):
    def _boom(_config):
        raise _goto_error()
    monkeypatch.setattr(replay, "_login_evidence_dir", _boom)
    assert replay.write_login_evidence(
        _ErrPage(), {}, "https://site.example/home", "login-test") is None
    err = capsys.readouterr().err
    assert "could not keep the page read as evidence" in err, err
    _assert_redacted(err)


def test_pre_submit_screenshot_error_is_redacted(tmp_path, capsys):
    page = _ErrPage({"screenshot": _goto_error()})
    assert replay.keep_pre_submit_screenshot(
        page, {"login_evidence_dir": str(tmp_path)}) is None
    err = capsys.readouterr().err
    assert "pre-submit screenshot unavailable" in err, err
    _assert_redacted(err)


def test_login_surface_error_is_redacted(capsys):
    assert replay._read_login_surface(
        _ErrPage({"evaluate": _goto_error()})) is None
    err = capsys.readouterr().err
    assert "post-submit surface unreadable" in err, err
    _assert_redacted(err)


# ── the verify result and its summary: what the wizard renders ───────────

_LOGIN_URL = "https://site.example/login"
_MEMBER_URL = "https://site.example/members"
_SUCCESS_URL = "https://site.example/dashboard"


class _Ctx:
    def __init__(self, page):
        self.pages = [page]

    def close(self):
        pass


def _fake_profile(monkeypatch, page=None, *, launch_error=None,
                  login_info=None):
    pytest.importorskip("playwright.sync_api")
    from bulk_downloader import cloak
    from bulk_downloader import stealth
    from bulk_downloader.login_impl import submit

    @contextlib.contextmanager
    def _persistent(**_kw):
        if launch_error is not None:
            raise launch_error
        yield _Ctx(page), "fake"

    def _worker_login(*_a, **_kw):
        if login_info is None:
            raise AssertionError("verify ran do_login unexpectedly")
        return False, login_info, []

    monkeypatch.setattr(cloak, "persistent_context", _persistent)
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    monkeypatch.setattr(stealth, "apply_to_page",
                        lambda *a, **kw: (False, "test"))
    monkeypatch.setattr(submit, "do_login", _worker_login)


def _verify(member_url=None):
    return replay.verify_login_replay(
        {"login_url": _LOGIN_URL, "success_url": _SUCCESS_URL,
         "use_real_chrome": False},
        "/nonexistent-profile", member_url=member_url, timeout=1.0)


def _assert_result_redacted(res, field, phrase):
    assert phrase in res[field], res
    _assert_redacted(res[field])
    _assert_redacted(res["summary"])


def test_verify_login_navigation_error_result_and_summary_redacted(
        monkeypatch):
    _fake_profile(monkeypatch, _ErrPage({f"goto:{_LOGIN_URL}": _goto_error()}))
    res = _verify()
    assert res["replay_ok"] is False, res
    _assert_result_redacted(res, "replay_error", "navigation failed")


def test_verify_member_probe_error_result_and_summary_redacted(monkeypatch):
    # login_url redirects to success_url (cookies alone suffice), then the
    # member probe's goto raises.
    page = _ErrPage({f"goto:{_MEMBER_URL}": _goto_error()})
    page.goto = (lambda url, _orig=page.goto, **kw:
                 _orig(_SUCCESS_URL if url == _LOGIN_URL else url, **kw))
    _fake_profile(monkeypatch, page)
    res = _verify(member_url=_MEMBER_URL)
    assert res["member_probe_ok"] is False, res
    _assert_result_redacted(res, "member_probe_error",
                            "navigation to member URL failed")


def test_verify_worker_login_info_result_and_summary_redacted(monkeypatch):
    page = _ErrPage(counts={"input[type='password']": 1})
    _fake_profile(monkeypatch, page,
                  login_info=f"login failed at {_SECRET_URL}")
    res = _verify()
    assert res["replay_ok"] is False, res
    _assert_result_redacted(res, "replay_error", "login failed at")


def test_verify_infra_error_result_and_summary_redacted(monkeypatch):
    _fake_profile(monkeypatch, launch_error=_goto_error())
    res = _verify()
    assert res["replay_ok"] is False, res
    _assert_result_redacted(res, "replay_error", "verify infra error")


def test_app_sites_auth_login_verify_returns_redacted_result(
        fresh_app, monkeypatch):
    # Call site: app_sites_auth returns the runner's result as-is (POST and
    # the status poll), so the redaction must already be in it.
    from bulk_downloader import app as _app
    _fake_profile(monkeypatch, _ErrPage({f"goto:{_LOGIN_URL}": _goto_error()}))

    class _Runner:
        _last = None

        def verify_login_after_wizard(self, member_url=""):
            self._last = _verify(member_url or None)
            return self._last

        def get_last_verify_result(self):
            return self._last

    _app.s_cfg["o1867_sid"] = {"name": "o1867", "login_url": _LOGIN_URL}
    _app.runners["o1867_sid"] = _Runner()
    for resp in (fresh_app.post("/api/sites/o1867_sid/login_verify", json={}),
                 fresh_app.get("/api/sites/o1867_sid/login_verify_status")):
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        for secret in _SECRETS:
            assert secret not in body, body
        assert "navigation failed" in resp.get_json()["replay_error"], body


# ── r3: configured URLs in the member-state reason -> login info/stderr ──

_SECRET_SELECTOR = f'a[href="{_SECRET_URL}"]'
_SECRET_SUCCESS_URL = (f"{_MEMBER_URL}?access_token={_TOK}"
                       f"&password={_QPW}&next=m")
_INDICATOR_PAGES = {
    "present": lambda: _ErrPage(url=_MEMBER_URL,
                                counts={_SECRET_SELECTOR: 1}),
    "absent": lambda: _ErrPage(url=_MEMBER_URL),
    "error": lambda: _ErrPage({"locator": RuntimeError("Unexpected token")},
                              url=_MEMBER_URL),
}


def _assert_no_secret(text):
    for secret in _SECRETS:
        assert secret not in text, text


def test_member_state_reason_success_url_is_redacted(tmp_path):
    matched, why, _path = replay.member_state_check(
        _ErrPage(url=_MEMBER_URL),
        {"success_url": _SECRET_SUCCESS_URL,
         "login_evidence_dir": str(tmp_path)})
    assert matched is True, why
    _assert_no_secret(why)
    assert (f"success_url '{_MEMBER_URL}?access_token=<REDACTED>"
            "&password=<REDACTED>&next=m' matches") in why, why


@pytest.mark.parametrize("state, matched, phrase", [
    ("present", True, "present on the page read"),
    ("absent", False, "absent from the page read"),
    ("error", False, "unreadable (Unexpected token); UNKNOWN"),
])
def test_member_state_reason_indicator_is_redacted(tmp_path, state, matched,
                                                   phrase):
    ok, why, _path = replay.member_state_check(
        _INDICATOR_PAGES[state](),
        {"member_indicator": _SECRET_SELECTOR,
         "login_evidence_dir": str(tmp_path)})
    assert ok is matched and phrase in why, why
    _assert_redacted(why)


@pytest.mark.parametrize("config, state, confirmed", [
    ({"success_url": _SECRET_SUCCESS_URL}, "absent", True),
    ({"member_indicator": _SECRET_SELECTOR}, "present", True),
    ({"member_indicator": _SECRET_SELECTOR}, "absent", False),
    ({"member_indicator": _SECRET_SELECTOR}, "error", False),
])
def test_no_nav_login_info_and_stderr_carry_no_configured_secret(
        tmp_path, capsys, config, state, confirmed):
    # The sink cx8 ran: submit._no_nav_verdict returns member_why in the
    # login info AND writes it to stderr.
    pytest.importorskip("playwright.sync_api")
    from bulk_downloader.login_impl import submit
    verdict, info, _cookies = submit._no_nav_verdict(
        _INDICATOR_PAGES[state](),
        dict(config, login_evidence_dir=str(tmp_path)), [],
        "0 cookies", "o1867", lambda: None)
    err = capsys.readouterr().err
    assert (verdict is True) is confirmed, info
    assert info in err, err
    _assert_no_secret(info)
    _assert_no_secret(err)


def test_verify_cookies_only_unknown_reason_redacts_indicator(monkeypatch):
    _fake_profile(monkeypatch, _ErrPage())
    res = replay.verify_login_replay(
        {"login_url": _LOGIN_URL, "success_url": _SUCCESS_URL,
         "member_indicator": _SECRET_SELECTOR, "use_real_chrome": False},
        "/nonexistent-profile", timeout=1.0)
    assert res["replay_ok"] is False, res
    _assert_result_redacted(res, "replay_error",
                            "cookies-only login state UNKNOWN")


def test_configured_url_and_selector_without_secret_kept_verbatim(tmp_path):
    # NEGATIVE CONTROL: redaction touches credentials only.
    _ok, why, _path = replay.member_state_check(
        _ErrPage(url=_MEMBER_URL),
        {"success_url": _MEMBER_URL, "login_evidence_dir": str(tmp_path)})
    assert why == (f"success_url '{_MEMBER_URL}' matches the page read "
                   f"({_MEMBER_URL})"), why
    _ok, why, _path = replay.member_state_check(
        _ErrPage(url=_MEMBER_URL),
        {"member_indicator": 'a[href="https://site.example/logout?next=m"]',
         "login_evidence_dir": str(tmp_path)})
    assert why == ("member indicator 'a[href=\"https://site.example/logout"
                   "?next=m\"]' absent from the page read "
                   f"({_MEMBER_URL})"), why
