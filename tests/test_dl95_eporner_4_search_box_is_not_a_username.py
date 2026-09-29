"""dl95-eporner-4 live (test2 6f85714f, eporner, 2026-09-29 01:47-01:50Z): a
re-login for an expired session opened eporner's login_url (the home page),
found no login field, and the LAST-DITCH username fallback

    form input:not([type='hidden']):not([type='submit']):not([type='button'])...

matched the header SEARCH box instead.  The account name was typed into it and
"submitted", and the evidence page was eporner's search results for the
account name:

  login: filled username via [form input:not([type=hidden]):not([type=submit])...]
  login: password field absent; clicked continue [button[type=submit]]
  Couldn't find password field ... tried 15 selectors -> manual takeover -> dead_letter

tpl95-whoreshub-1 (lane, 02:30Z) already skips search inputs in _try_fill via
_is_search_field.  This row adds what eporner still needs: a visible search box
does not count as "the username field is already visible" when a configured
login trigger decides whether to open the modal; role=searchbox is a search
input; only the form action's PATH is read (a login form posting to
/login?next=/search/ is not a search form); and a locator answering anything
but a string is not a search field (fail-open for test doubles).

The header markup is a REAL capture of https://www.eporner.com/login/
(tests/fixtures/dl95_eporner_4/, 2026-09-29, fetched on test2; no account
data).  Pages are rendered in a local headless chromium via ``page.route``;
NO LIVE SITE IS TOUCHED.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-eporner-4.test"
FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "dl95_eporner_4"
           / "eporner_login_header_search.html")
HEADER = FIXTURE.read_text(encoding="utf-8")
ACCOUNT = "fixture-account-name"

# The live page: the header search form and nothing else a filler can use.
HOME_HTML = f"<!doctype html><html><body>{HEADER}</body></html>"

# Positive control: the same header plus a real, visible login form.
LOGIN_FORM = """
<form id="loginForm" method="post" action="/login/">
  <input type="text" name="username" id="l_user">
  <input type="password" name="password" id="l_pass">
  <button type="submit">Log in</button>
</form>"""
WITH_FORM_HTML = f"<!doctype html><html><body>{HEADER}{LOGIN_FORM}</body></html>"

# A modal login behind a trigger link, as eporner's own
# EP.account.login.openModal('login', 'header') renders it.
MODAL_HTML = f"""<!doctype html><html><body>{HEADER}
<a href="/login/" id="open-login" onclick="document.getElementById('modal').style.display='block';return false;">Log in</a>
<div id="modal" style="display:none">{LOGIN_FORM}</div>
</body></html>"""

PAGES = {"/home": HOME_HTML, "/with-form": WITH_FORM_HTML, "/modal": MODAL_HTML}

CHROME = os.environ.get("BD_PW_CHROME", "")


def _launch(p):
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if CHROME and os.path.exists(CHROME):
        try:
            return p.chromium.launch(headless=True, timeout=20000, args=args,
                                     executable_path=CHROME)
        except Exception:
            pass
    try:
        return p.chromium.launch(headless=True, timeout=20000, args=args)
    except Exception as e:
        pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip (T5): {e}")


def _serve(route, request):
    path = "/" + request.url.split("?", 1)[0].split("/", 3)[-1]
    route.fulfill(status=200, content_type="text/html",
                  body=PAGES.get(path, "<!doctype html><html><body>search results</body></html>"))


@contextmanager
def _page(path, html=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            if html is not None:
                page.route(ORIGIN + "/**", lambda route, _req: route.fulfill(
                    status=200, content_type="text/html", body=html))
            else:
                page.route(ORIGIN + "/**", _serve)
            page.goto(ORIGIN + path, wait_until="load")
            yield page
        finally:
            browser.close()


def _fallbacks():
    from bulk_downloader.login_impl.submit import USER_FIELD_FALLBACKS
    return list(USER_FIELD_FALLBACKS)


def _search_value(page):
    return page.locator("#srch").input_value()


def test_precondition_the_real_header_search_is_what_the_generic_fallback_matches():
    """The captured eporner search input is visible and the last-ditch
    fallback selector reaches it (so the RED below is the live mechanism)."""
    with _page("/home") as page:
        assert page.locator("#srch").is_visible()
        generic = _fallbacks()[-1]
        assert generic.startswith("form input:not(")
        assert page.locator(generic).first.get_attribute("id") == "srch"


def test_the_account_name_is_never_typed_into_the_search_box():
    from bulk_downloader.login_impl._common import _try_fill
    with _page("/home") as page:
        ok, info = _try_fill(page, _fallbacks(), ACCOUNT, "username")
        assert _search_value(page) == "", "the account name was typed into the site search box"
        assert ok is False, info


def test_the_failure_names_the_search_field_it_refused():
    """Distinctive diagnostic: 'nothing matched' and 'only a search box
    matched' are different situations and the message says which."""
    from bulk_downloader.login_impl._common import _try_fill
    with _page("/home") as page:
        ok, info = _try_fill(page, _fallbacks(), ACCOUNT, "username")
        assert ok is False
        assert "search box" in info and "type=search" in info, info


def test_positive_control_a_real_login_form_beside_the_search_box_is_filled():
    from bulk_downloader.login_impl._common import _try_fill
    from bulk_downloader.login_impl.submit import PASS_FIELD_FALLBACKS
    with _page("/with-form") as page:
        ok, used = _try_fill(page, _fallbacks(), ACCOUNT, "username")
        assert ok, used
        assert page.locator("#l_user").input_value() == ACCOUNT
        assert _search_value(page) == ""
        ok, used = _try_fill(page, list(PASS_FIELD_FALLBACKS), "pw-fixture", "password")
        assert ok, used
        assert page.locator("#l_pass").input_value() == "pw-fixture"


def test_a_configured_trigger_fires_when_only_the_search_box_is_visible():
    """Pre-fix the search box counted as 'username field is already visible',
    so a login_trigger on a site like eporner could never open the modal."""
    from bulk_downloader.login_impl._common import (
        _fire_login_trigger_if_needed,
        _try_fill,
    )
    with _page("/modal") as page:
        needed, fired, detail = _fire_login_trigger_if_needed(
            page, "a#open-login", _fallbacks())
        assert (needed, fired) == (True, True), detail
        assert page.locator("#l_user").is_visible()
        ok, used = _try_fill(page, _fallbacks(), ACCOUNT, "username")
        assert ok, used
        assert page.locator("#l_user").input_value() == ACCOUNT
        assert _search_value(page) == ""


def test_a_visible_login_field_still_skips_the_trigger():
    from bulk_downloader.login_impl._common import _fire_login_trigger_if_needed
    with _page("/with-form") as page:
        assert _fire_login_trigger_if_needed(page, "a#nowhere", _fallbacks()) == (
            False, False, "username field is already visible")


def test_eporner_fixture_full_fill_path_without_trigger_refuses_search_and_cannot_find_username():
    """RED without the fix: on the real eporner header+modal fixture, when
    login_trigger is not configured (or empty), the trigger never fires,
    the modal remains hidden, and username fill fails without typing into
    the search box."""
    from bulk_downloader.login_impl._common import (
        _fire_login_trigger_if_needed,
        _try_fill,
    )

    with _page("/home") as page:
        needed, fired, _detail = _fire_login_trigger_if_needed(page, "", _fallbacks())
        assert (needed, fired) == (False, False)
        # Modal is still hidden
        assert not page.locator("#modal_user").is_visible()
        # Filling username fails cleanly
        ok, _info = _try_fill(page, _fallbacks(), ACCOUNT, "username")
        assert ok is False
        assert _search_value(page) == ""


def test_eporner_fixture_full_fill_path_with_template_default_trigger_succeeds():
    """GREEN with the fix: eporner's template default login_trigger
    (a[data-nav-header='login_open'], a[href='/login/']) fires against the
    eporner header, opens the modal, and the full username and password
    fill path completes into the real login form while leaving the search
    box completely empty."""
    import bulk_downloader.site_templates as st
    from bulk_downloader.login_impl._common import (
        _fire_login_trigger_if_needed,
        _try_fill,
    )
    from bulk_downloader.login_impl.submit import PASS_FIELD_FALLBACKS

    tpl = st.get("eporner")
    assert tpl is not None
    trigger = (tpl.get("config_defaults") or {}).get("login_trigger")
    assert trigger, "eporner site template must specify login_trigger"

    with _page("/home") as page:
        assert not page.locator("#modal_user").is_visible()
        assert _search_value(page) == ""

        # Step 1: fire trigger
        needed, fired, detail = _fire_login_trigger_if_needed(page, trigger, _fallbacks())
        assert (needed, fired) == (True, True), detail
        assert page.locator("#modal_user").is_visible()

        # Step 2: fill username
        ok, used = _try_fill(page, _fallbacks(), ACCOUNT, "username")
        assert ok is True, f"Username fill failed: {used}"
        assert page.locator("#modal_user").input_value() == ACCOUNT

        # Step 3: fill password
        ok, used = _try_fill(page, list(PASS_FIELD_FALLBACKS), "secretpass123", "password")
        assert ok is True, f"Password fill failed: {used}"
        assert page.locator("#modal_pass").input_value() == "secretpass123"

        # Search box was NEVER touched
        assert _search_value(page) == "", "account name was typed into search box"



@pytest.mark.parametrize("html,reason", [
    ('<form action="/search/"><input type="text" name="term" id="f"></form>', "search form"),
    ('<form action="/find"><input type="text" name="q" id="f"></form>', "name=q"),
    ('<div role="search"><form action="/x"><input type="text" name="k" id="f"></form></div>', "role=search"),
    ('<form action="/x"><input type="text" role="searchbox" name="k" id="f"></form>', "role=searchbox"),
    ('<form action="/x"><input type="search" name="k" id="f"></form>', "type=search"),
])
def test_each_search_spelling_is_refused(html, reason):
    from bulk_downloader.login_impl._common import _is_search_field
    with _page("/x", html=f"<!doctype html><html><body>{html}</body></html>") as page:
        is_search, why = _is_search_field(page.locator("#f"))
        assert is_search and reason in why, why


@pytest.mark.parametrize("html", [
    '<form action="/login/"><input type="text" name="login" id="f"></form>',
    '<form action="/members/login?next=/search/"><input type="email" name="email" id="f"></form>',
    '<form action="/signin"><input type="text" name="user" id="f" placeholder="Username or email"></form>',
    '<input type="text" name="username" id="f">',
])
def test_login_fields_are_not_mistaken_for_search(html):
    from bulk_downloader.login_impl._common import _is_search_field
    with _page("/x", html=f"<!doctype html><html><body>{html}</body></html>") as page:
        assert _is_search_field(page.locator("#f")) == (False, "")


def test_a_locator_that_cannot_evaluate_fails_open():
    """Fake/unsupported locators (unit-test doubles) are never refused."""
    from bulk_downloader.login_impl._common import _is_search_field

    class _NoEval:
        def evaluate(self, _js):
            raise RuntimeError("no browser")

    class _NotAString:
        def evaluate(self, _js):
            return object()

    assert _is_search_field(_NoEval()) == (False, "")
    assert _is_search_field(_NotAString()) == (False, "")


def test_existing_site_with_empty_login_trigger_gap_fills_at_runtime_and_opens_modal():
    """B6-B live note: an existing site whose login_trigger is '' and applied_template
    is None must gap-fill the trigger from template defaults at runtime so the modal
    opens and login succeeds."""
    from bulk_downloader.login_impl._common import (
        _fire_login_trigger_if_needed,
        _try_fill,
    )
    from bulk_downloader.login_impl.submit import PASS_FIELD_FALLBACKS

    # Existing site config as on live host .183
    cfg = {
        "login_url": "https://www.eporner.com/",
        "login_trigger": "",
        "applied_template": None,
    }

    with _page("/home") as page:
        assert not page.locator("#modal_user").is_visible()
        assert _search_value(page) == ""

        # Runtime gap-fill in _fire_login_trigger_if_needed:
        # trigger is empty string in cfg, but resolves to template default via config["login_url"]
        needed, fired, detail = _fire_login_trigger_if_needed(page, cfg["login_trigger"], _fallbacks(), config=cfg)
        assert (needed, fired) == (True, True), detail
        assert page.locator("#modal_user").is_visible()

        # Modal is open: user and pass fill succeeds, search untouched
        ok, used = _try_fill(page, _fallbacks(), ACCOUNT, "username")
        assert ok is True, f"Username fill failed: {used}"
        assert page.locator("#modal_user").input_value() == ACCOUNT

        ok, used = _try_fill(page, list(PASS_FIELD_FALLBACKS), "secretpass123", "password")
        assert ok is True, f"Password fill failed: {used}"
        assert page.locator("#modal_pass").input_value() == "secretpass123"
        assert _search_value(page) == ""


def test_load_sites_config_gap_fills_empty_login_trigger_for_existing_site(tmp_path, monkeypatch):
    """B6-B live note: _load_sites_config on startup gap-fills login_trigger from
    matching template defaults for an existing site whose login_trigger is empty."""
    import json

    import bulk_downloader.app as app_mod

    sites_file = tmp_path / "sites_config.json"
    existing_sites = {
        "0c546602": {
            "name": "eporner",
            "login_url": "https://www.eporner.com/",
            "login_trigger": "",
            "applied_template": None,
        },
        "custom_site": {
            "name": "eporner_custom",
            "login_url": "https://www.eporner.com/",
            "login_trigger": "button.custom-login",
            "applied_template": None,
        },
    }
    sites_file.write_text(json.dumps(existing_sites), encoding="utf-8")

    monkeypatch.setattr(app_mod, "SITES_FILE", sites_file)
    monkeypatch.setattr(app_mod, "_SITES_FILE_RUNTIME_PUBLISHED_OBJECT", sites_file)
    monkeypatch.setattr(app_mod, "_SITES_FILE_EXISTED_AT_PUBLICATION", True)

    app_mod.s_cfg.clear()
    app_mod.runners.clear()
    app_mod._load_sites_config()

    # Empty trigger is gap-filled from eporner template defaults
    assert app_mod.s_cfg["0c546602"]["login_trigger"] == "a[data-nav-header='login_open'], a[href='/login/']"
    # Explicit custom trigger is preserved untouched
    assert app_mod.s_cfg["custom_site"]["login_trigger"] == "button.custom-login"



# tpl95-whoreshub-1-detector-merge (lane delta): main's dl95-eporner-4 cases,
# read through main's ``_search_field_reason`` and main's wording, kept beside
# the lane's ``_is_search_field`` cases above -- ONE detector answers both.
def test_the_failure_names_the_search_field_it_refused_in_mains_words():
    from bulk_downloader.login_impl._common import _try_fill
    with _page("/home") as page:
        ok, info = _try_fill(page, _fallbacks(), ACCOUNT, "username")
        assert ok is False
        assert "search field" in info and "type=search" in info, info


@pytest.mark.parametrize("html,reason", [
    ('<form action="/search/"><input type="text" name="term" id="f"></form>', "form action"),
    ('<form action="/search.php"><input type="text" name="term" id="f"></form>', "form action"),
    ('<form action="/find"><input type="text" name="searchterm" id="f"></form>', "name=searchterm"),
    ('<form action="/find"><input type="text" name="q" id="f"></form>', "name=q"),
    ('<div role="search"><form action="/x"><input type="text" name="k" id="f"></form></div>', "role=search"),
    ('<form action="/x"><input type="text" role="searchbox" name="k" id="f"></form>', "role=searchbox"),
    ('<form action="/x"><input type="search" name="k" id="f"></form>', "type=search"),
])
def test_each_search_spelling_is_refused_by_search_field_reason(html, reason):
    from bulk_downloader.login_impl._common import _search_field_reason
    with _page("/x", html=f"<!doctype html><html><body>{html}</body></html>") as page:
        assert reason in _search_field_reason(page.locator("#f"))


@pytest.mark.parametrize("html", [
    '<form action="/login/"><input type="text" name="login" id="f"></form>',
    '<form action="/members/login?next=/search/"><input type="email" name="email" id="f"></form>',
    '<form action="/signin"><input type="text" name="user" id="f" placeholder="Username or email"></form>',
    '<input type="text" name="username" id="f">',
])
def test_login_fields_are_not_mistaken_for_search_by_search_field_reason(html):
    from bulk_downloader.login_impl._common import _search_field_reason
    with _page("/x", html=f"<!doctype html><html><body>{html}</body></html>") as page:
        assert _search_field_reason(page.locator("#f")) == ""


def test_search_field_reason_fails_open():
    from bulk_downloader.login_impl._common import _search_field_reason

    class _NoEval:
        def evaluate(self, _js):
            raise RuntimeError("no browser")

    class _NotAString:
        def evaluate(self, _js):
            return object()

    assert _search_field_reason(_NoEval()) == ""
    assert _search_field_reason(_NotAString()) == ""
