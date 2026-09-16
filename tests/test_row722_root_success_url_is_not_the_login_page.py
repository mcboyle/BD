"""Row 722 live (site-ma.bangbros.com, 2026-09-15 14:4xZ): success_url
https://site-ma.bangbros.com/ is a substring prefix of the LOGIN URL
https://site-ma.bangbros.com/login, so the run reported "page already at
success URL after fill" with nothing submitted ("✓ OK — 5 cookies").

A root success URL means the root page; and the login page is never the
success page. submit.py now uses replay.success_url_reached everywhere.
"""
from __future__ import annotations

import inspect

import pytest

from bulk_downloader.login_impl import replay, submit

BD_GATE_SCOPE = "module"

LOGIN = "https://site-ma.bangbros.test/login"


@pytest.mark.parametrize("success,final,expect", [
    ("https://site-ma.bangbros.test/", "https://site-ma.bangbros.test/login", False),   # THE ROW
    ("https://site-ma.bangbros.test/", "https://site-ma.bangbros.test/store", False),
    ("https://site-ma.bangbros.test/", "https://site-ma.bangbros.test/", True),
    ("https://site-ma.bangbros.test/", "https://site-ma.bangbros.test/?ref=x", True),
    ("https://site-ma.bangbros.test/videos", "https://site-ma.bangbros.test/videos/123", True),
    ("https://site-ma.bangbros.test/login", "https://site-ma.bangbros.test/login", False),  # login page never
    ("/members", "https://x.test/members/home", True),
    ("/members", "https://x.test/login?return=/members", False),
])
def test_success_url_reached(success, final, expect):
    assert replay.success_url_reached(success, final, LOGIN) is expect, (success, final)


def test_the_row_a_root_success_url_no_longer_matches_the_login_page():
    assert replay.success_url_reached("https://site-ma.bangbros.test/", LOGIN, LOGIN) is False, (
        "root success_url matched the login page: the run declares OK before submitting")


def test_submit_uses_the_structural_predicate_not_substring():
    src = inspect.getsource(submit.do_login)
    assert "success in cur_after_fill" not in src
    assert "success not in cur" not in src
    assert src.count("success_url_reached(") >= 3
