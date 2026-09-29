"""tpl95-whoreshub-1-detector-merge: ONE search-field detector for dl95-eporner-4 and tpl95-whoreshub-1.

Lane ebdac9378 (tpl95-whoreshub-1) added a second ``_SEARCH_FIELD_JS`` to login_impl/_common.py under the same name as
main's dl95-eporner-4 detector. The later module-level assignment wins, so eporner-4's ``_search_field_reason`` ran
whoreshub's rules, whose form-action regex reads the FULL action URL: a login form posting to
``/members/login?next=/search/`` was refused as a search box, the case eporner-4 exists to allow
(REDIFF-NEEDED-bd-integrator-A.md, LANE-3 exclusions; the lane's merge d197fa82 kept whoreshub's detector).

Fix: main's detector (action PATH only) gains whoreshub's extra spellings -- keyword names, a search form named by its
id/class, a "search" placeholder / aria-label -- and whoreshub's _try_fill skip rides main's existing one.
Hermetic: Chromium set_content, no network.
"""
from __future__ import annotations

import pytest

from bulk_downloader.login_impl import _common

BD_GATE_SCOPE = "module"


@pytest.fixture(scope="module")
def page():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


def _reason(page, html):
    page.set_content(f"<!doctype html><html><body>{html}</body></html>")
    return _common._search_field_reason(page.locator("#f"))


def test_there_is_one_detector():
    src = open(_common.__file__, encoding="utf-8").read()
    assert src.count("\n_SEARCH_FIELD_JS =") == 1, "WHORESHUB_EPORNER_TWO_SEARCH_DETECTORS"


@pytest.mark.parametrize("html", [
    '<form action="/members/login?next=/search/"><input type="email" name="email" id="f"></form>',
    '<form action="https://www.example.test/login/?return=/search/"><input type="text" name="login" id="f"></form>',
])
def test_a_login_form_whose_action_query_names_search_is_not_a_search_box(page, html):
    assert _reason(page, html) == "", f"WHORESHUB_DETECTOR_REFUSED_A_LOGIN_FORM: {html}"


@pytest.mark.parametrize("html,reason", [
    ('<form id="search_form" action="https://www.whoreshub.test/search/"><input type="text" class="search" '
     'name="q" placeholder="Search" id="f"></form>', "name=q"),
    ('<form action="/x"><input type="text" name="keywords" id="f"></form>', "name=keywords"),
    ('<form id="header-search" action="/x"><input type="text" name="k" id="f"></form>', "search form"),
    ('<form action="/x"><input type="text" name="k" placeholder="Search videos" id="f"></form>', "search placeholder"),
    ('<input type="text" name="k" aria-label="Search" id="f">', "search placeholder"),
])
def test_whoreshubs_search_spellings_are_refused_by_the_one_detector(page, html, reason):
    assert reason in _reason(page, html), html


def test_control_a_login_placeholder_is_not_search(page):
    assert _reason(page, '<form action="/login/"><input type="text" name="username" id="f" '
                         'placeholder="Please enter login here"></form>') == ""
