"""SAUCE — classify_login must learn the submit button on saucedemo-style
sites where the post-login URL has the login URL as a prefix.

Bug: the submit picker used `login_url in click_url` (substring). With
login_url="https://www.saucedemo.com/", the post-login record
".../inventory.html" CONTAINS that prefix, so the loop overwrote sub_rec
with the post-nav <div> (no id/name/text) → synthesize_selectors → []. The
real <input type=submit> click was clobbered and the miner learned NO submit
selector.

Fix: prefer the LAST submit-SHAPED click (button / input[type=submit|button|
image] / role=button / submit-keyword class or text) among the on-login
clicks, falling back to prior behaviour only when none is submit-shaped.
Username already worked (this also pins that it keeps working).

Pure module test — no app/db. Zero-arg functions per the custom runner.
"""
from __future__ import annotations

from bulk_downloader.learn import classify_login


def _saucedemo_harvest():
    return {
        "inputs": [
            {"tag": "input", "type": "text", "id": "user-name",
             "name": "user-name", "url": "https://www.saucedemo.com/",
             "ts": 1, "_input_value": "standard_user", "placeholder": "Username"},
            {"tag": "input", "type": "password", "id": "password",
             "name": "password", "url": "https://www.saucedemo.com/",
             "ts": 2, "_input_value": "secret", "secret": True},
        ],
        "clicks": [
            {"tag": "input", "type": "submit", "id": "login-button",
             "name": "login-button", "text": "", "cls": "submit-button btn_action",
             "url": "https://www.saucedemo.com/", "ts": 3},
            # post-login navigation — URL has login_url as a prefix
            {"tag": "div", "id": "", "name": "",
             "url": "https://www.saucedemo.com/inventory.html", "ts": 4},
        ],
    }


def test_saucedemo_submit_is_learned():
    res = classify_login(_saucedemo_harvest(), login_url="https://www.saucedemo.com/")
    sub = res["submit_btn"]
    assert sub, "submit_btn must not be empty (the <input type=submit> click was clobbered)"
    # the input[type=submit] click → these are the expected synthesized forms
    assert "#login-button" in sub
    assert "input[type='submit']" in sub
    # the post-nav <div> must NOT have been chosen
    assert not any(s.startswith("div") for s in sub)


def test_saucedemo_username_still_learned():
    res = classify_login(_saucedemo_harvest(), login_url="https://www.saucedemo.com/")
    assert "#user-name" in res["user_field"]


def test_post_nav_div_does_not_clobber_submit_generic():
    """Generic form of the bug: any site where login_url is a prefix of the
    post-login URL. A non-submit post-nav element must not win."""
    h = {
        "inputs": [],
        "clicks": [
            {"tag": "button", "type": "submit", "id": "go", "text": "Sign In",
             "url": "https://site.test/login", "ts": 1},
            {"tag": "div", "id": "dash", "url": "https://site.test/login/home", "ts": 2},
        ],
    }
    sub = classify_login(h, login_url="https://site.test/login")["submit_btn"]
    assert "#go" in sub
    assert not any("dash" in s for s in sub)


def test_login_url_prefix_tolerance_preserved():
    """The login.example.com → members.example.com substring tolerance the
    original code intended must still hold when the submit click itself is on
    a URL that merely contains login_url."""
    h = {
        "inputs": [],
        "clicks": [
            {"tag": "input", "type": "submit", "id": "submit-btn",
             "url": "https://login.example.com/auth?next=members", "ts": 1},
        ],
    }
    sub = classify_login(h, login_url="https://login.example.com/auth")["submit_btn"]
    assert "#submit-btn" in sub


def test_no_login_url_prefers_submit_shaped_before_nav():
    """Fallback branch (no login_url): among URL-changing clicks, the
    submit-shaped one should win over an incidental nav click."""
    h = {
        "inputs": [],
        "clicks": [
            # incidental nav link clicked first, changes URL
            {"tag": "a", "id": "forgot", "text": "Forgot password",
             "url": "https://site.test/login", "ts": 1},
            {"tag": "input", "type": "submit", "id": "real-submit",
             "url": "https://site.test/login", "ts": 2},
            {"tag": "div", "id": "", "url": "https://site.test/welcome", "ts": 3},
        ],
    }
    sub = classify_login(h, login_url="")["submit_btn"]
    assert "#real-submit" in sub
