"""O1826 C12 / M113: Verify login must not report "cookies_only" success
just because the page it reads has no password field.

verify_login_replay loads the manual-login profile, opens login_url and,
when success_url does not match, used to call the profile logged in as soon
as no ``input[type=password]`` was counted.  An error page (5xx body, a
"session expired" wall, a CDN block page) has no password field either, so
Verify answered "Cookies alone reach the member area" with nothing proving
membership -- even when the member_url probe right after it bounced to a
login form.

A missing login form is now only an invitation to look for a POSITIVE member
signal (row 708): the declared member indicator (learned or configured) on
the page.  None -> replay_ok False with an UNKNOWN reason.  O1856: the
member_url probe still runs and is reported, but is no member signal -- it
accepts any same-host page without a form, an error page included.

RED (base): the error page, the bounced member probe and a same-host member
error page all report replay_ok True / cookies_only.  GREEN: not verified,
UNKNOWN.  NEGATIVE CONTROL: a page carrying the declared indicator still
passes cookies_only, with the probe reported beside it.

The browser is a fake page behind cloak.persistent_context: only what
_cookies_alone and _probe_member_url read (url, locator(...).count()).
"""
from __future__ import annotations

import contextlib
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BD_GATE_SCOPE = "module"

_LOGIN_URL = "https://site.example/login"
_MEMBER_URL = "https://site.example/members/videos"
_SUCCESS_URL = "https://site.example/dashboard"
_INDICATOR = "a.logout"
_PASSWORD = "input[type='password']"
# Fake credential fixtures, assembled at runtime so the source carries no
# secret-shaped literal for the train's gitleaks gate.
_FX_HUNTER = "O1826C12-" + "hunter2"
_FX_SECRETTOK = "O1826C12-" + "SECRETTOK"
_FX_LANDTOK = "O1826C12-" + "LAND-TOK"


class _Locator:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    def count(self):
        n = self._page.sites[self._page.url].get(self._selector, 0)
        if isinstance(n, Exception):
            raise n
        return n


class _Page:
    """One tab over a fixed map url -> {selector: count}. goto may land on a
    different url (a redirect), as the live site would."""

    def __init__(self, sites, redirects=None):
        self.sites = sites
        self.redirects = redirects or {}
        self.url = "about:blank"
        self.visited = []

    def set_default_timeout(self, ms):
        pass

    def goto(self, url, **kw):
        self.visited.append(url)
        self.url = self.redirects.get(url, url)

    def wait_for_timeout(self, ms):
        pass

    def locator(self, selector):
        return _Locator(self, selector)


class _Ctx:
    def __init__(self, page):
        self.pages = [page]

    def close(self):
        pass


def _fake_profile(monkeypatch, page):
    pytest.importorskip("playwright.sync_api")
    from bulk_downloader import cloak
    from bulk_downloader import stealth
    from bulk_downloader.login_impl import submit

    @contextlib.contextmanager
    def _persistent(**kw):
        yield _Ctx(page), "fake"

    def _no_worker_login(*a, **kw):
        raise AssertionError("verify ran do_login; the page showed no login form")

    monkeypatch.setattr(cloak, "persistent_context", _persistent)
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    monkeypatch.setattr(stealth, "apply_to_page",
                        lambda *a, **kw: (False, "test"))
    monkeypatch.setattr(submit, "do_login", _no_worker_login)


def _config(**extra):
    cfg = {"login_url": _LOGIN_URL, "success_url": _SUCCESS_URL,
           "use_real_chrome": False}
    cfg.update(extra)
    return cfg


def _verify(cfg, member_url=None):
    from bulk_downloader.login import verify_login_replay
    return verify_login_replay(cfg, "/nonexistent-profile", member_url=member_url,
                               timeout=1.0)


def test_error_page_without_password_field_is_not_cookies_only_success(
        monkeypatch):
    # login_url answers a 5xx-style error body: no password field, no member
    # marker, no success_url redirect, and no member_url to probe.
    page = _Page({_LOGIN_URL: {"body": 1}})
    _fake_profile(monkeypatch, page)
    res = _verify(_config())
    assert res["replay_ok"] is False, res
    assert res["replay_method"] != "cookies_only", res
    assert "UNKNOWN" in res["replay_error"], res
    assert "no member indicator is declared" in res["replay_error"], res


def test_member_probe_bounced_to_login_is_not_cookies_only_success(monkeypatch):
    # The login_url page has no form, but the member area bounces to one:
    # the probe's failure is reported, and nothing proves membership.
    page = _Page({_LOGIN_URL: {"body": 1},
                  _MEMBER_URL: {_PASSWORD: 1}})
    _fake_profile(monkeypatch, page)
    res = _verify(_config(), member_url=_MEMBER_URL)
    assert res["member_probe_ok"] is False, res
    assert res["replay_ok"] is False, res
    assert res["replay_method"] != "cookies_only", res
    assert "UNKNOWN" in res["replay_error"], res
    assert "bounced to a login form" in res["member_probe_error"], res
    assert "no member indicator is declared" in res["replay_error"], res


def test_member_indicator_on_page_is_cookies_only_success(monkeypatch):
    # NEGATIVE CONTROL: the declared member indicator is a positive signal.
    page = _Page({_LOGIN_URL: {_INDICATOR: 1}})
    _fake_profile(monkeypatch, page)
    res = _verify(_config(member_indicator=_INDICATOR))
    assert res["replay_ok"] is True, res
    assert res["replay_method"] == "cookies_only", res
    assert res["replay_error"] == "", res


def test_declared_indicator_absent_from_error_page_is_not_success(monkeypatch):
    # A site that DECLARES its member indicator, verified against an error
    # page that shows neither a form nor the indicator: the indicator must
    # be PRESENT, not merely declared.
    page = _Page({_LOGIN_URL: {"body": 1}})
    _fake_profile(monkeypatch, page)
    res = _verify(_config(member_indicator=_INDICATOR))
    assert res["replay_ok"] is False, res
    assert res["replay_method"] != "cookies_only", res
    assert "UNKNOWN" in res["replay_error"], res


def test_unreadable_indicator_is_not_success(monkeypatch):
    # A malformed / unreadable indicator selector is no measurement, and an
    # unavailable measurement is never membership (fail closed).
    page = _Page({_LOGIN_URL: {_INDICATOR: ValueError("bad selector")}})
    _fake_profile(monkeypatch, page)
    res = _verify(_config(member_indicator=_INDICATOR))
    assert res["replay_ok"] is False, res
    assert res["replay_method"] != "cookies_only", res
    assert "UNKNOWN" in res["replay_error"], res


def test_learned_indicator_on_page_is_cookies_only_success(monkeypatch):
    # NEGATIVE CONTROL: a template-LEARNED indicator (learned.login) counts
    # the same as a configured one, as in member_state_check.
    page = _Page({_LOGIN_URL: {_INDICATOR: 1}})
    _fake_profile(monkeypatch, page)
    res = _verify(_config(
        learned={"login": {"member_indicator": _INDICATOR}}))
    assert res["replay_ok"] is True, res
    assert res["replay_method"] == "cookies_only", res


def test_unreadable_password_count_still_runs_the_worker_login(monkeypatch):
    # Unchanged path: when the password field cannot be counted, verify
    # does not judge the page at all -- it runs the worker's do_login.
    from bulk_downloader.login_impl import submit
    page = _Page({_LOGIN_URL: {_PASSWORD: RuntimeError("page closed")}})
    _fake_profile(monkeypatch, page)
    calls = []

    def _refused_login(*a, **kw):
        calls.append(kw)
        return False, "O1826C12 worker login refused", []

    monkeypatch.setattr(submit, "do_login", _refused_login)
    res = _verify(_config(member_indicator=_INDICATOR))
    assert len(calls) == 1, res
    assert res["replay_ok"] is False, res
    assert res["replay_error"] == "O1826C12 worker login refused", res


def test_unknown_reason_redacts_credentials_in_the_page_url(monkeypatch):
    # The UNKNOWN reason names the page read; it reaches replay_error and the
    # wizard's summary card, so a credential-bearing query must be redacted
    # (as member_state_check does), never echoed.
    landed = (_LOGIN_URL + "?next=/m&password=" + _FX_HUNTER
              + "&token=" + _FX_SECRETTOK)
    page = _Page({landed: {"body": 1}}, redirects={_LOGIN_URL: landed})
    _fake_profile(monkeypatch, page)
    res = _verify(_config())
    assert res["replay_ok"] is False, res
    assert "UNKNOWN" in res["replay_error"], res
    for field in ("replay_error", "summary"):
        assert _FX_HUNTER not in res[field], res
        assert _FX_SECRETTOK not in res[field], res
    assert "password=" in res["replay_error"], res


def test_unknown_reason_names_the_login_landing_page_not_the_probe_page(
        monkeypatch):
    # The member_url probe navigates the same page.  The reason must name the
    # login_url landing page that was judged (no form, no indicator), not the
    # credentialed login form the probe bounced to -- both redacted.
    landed = _LOGIN_URL + "?next=/m&token=" + _FX_LANDTOK
    bounce = _LOGIN_URL + "?return=/members&password=O1826C12-PROBE-PW"
    page = _Page({landed: {"body": 1}, bounce: {_PASSWORD: 1}},
                 redirects={_LOGIN_URL: landed, _MEMBER_URL: bounce})
    _fake_profile(monkeypatch, page)
    res = _verify(_config(member_indicator=_INDICATOR), member_url=_MEMBER_URL)
    assert res["replay_ok"] is False, res
    assert res["member_probe_ok"] is False, res
    assert page.url == bounce, res
    assert "no login form at " + _LOGIN_URL + "?next=" in res["replay_error"], res
    assert "return=" not in res["replay_error"], res
    for field in ("replay_error", "summary"):
        assert _FX_LANDTOK not in res[field], res
        assert "O1826C12-PROBE-PW" not in res[field], res


def test_same_host_member_error_page_without_indicator_is_not_success(
        monkeypatch):
    # O1856: no indicator declared, and member_url serves a same-host error
    # page with no form.  The probe reports ok (no form, same host) but that
    # is the M113 heuristic one hop away: UNKNOWN, with the probe reported.
    page = _Page({_LOGIN_URL: {"body": 1}, _MEMBER_URL: {"body": 1}})
    _fake_profile(monkeypatch, page)
    res = _verify(_config(), member_url=_MEMBER_URL)
    assert res["member_probe_ok"] is True, res
    assert res["replay_ok"] is False, res
    assert res["replay_method"] != "cookies_only", res
    assert "no member indicator is declared" in res["replay_error"], res


def test_indicator_on_page_passes_with_the_member_probe_reported(monkeypatch):
    # NEGATIVE CONTROL: the indicator decides; the member_url probe still
    # runs and its result is reported beside the cookies_only pass.
    page = _Page({_LOGIN_URL: {_INDICATOR: 1}, _MEMBER_URL: {_PASSWORD: 1}})
    _fake_profile(monkeypatch, page)
    res = _verify(_config(member_indicator=_INDICATOR), member_url=_MEMBER_URL)
    assert res["replay_ok"] is True, res
    assert res["replay_method"] == "cookies_only", res
    assert res["member_probe_ok"] is False, res
    assert page.visited == [_LOGIN_URL, _MEMBER_URL], res


def test_success_url_redirect_is_unchanged(monkeypatch):
    # The success_url path never read the password count; it stays a pass.
    page = _Page({_SUCCESS_URL: {"body": 1}},
                 redirects={_LOGIN_URL: _SUCCESS_URL})
    _fake_profile(monkeypatch, page)
    res = _verify(_config())
    assert res["replay_ok"] is True, res
    assert res["replay_method"] == "cookies_only", res
