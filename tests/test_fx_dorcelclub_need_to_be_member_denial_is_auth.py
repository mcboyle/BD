"""fx-dorcelclub-member-denial-auth (O1567, bd1 10.0.70.51, 2026-09-29 21:57Z).

Once fx-dorcelclub-frame-gate cleared dorcel's 18+/cookie wall, the members
download control was clicked and the logged-out session was answered with a
modal the app took for a RATE LIMIT:

    [8cab7bee][rate_limit] https://www.dorcelclub.com/en/scene/849534/...:
      Rate limit at ... (page text: ...ration for Tina Kay TINA KAY ALL VIDEOS
      ACCESS DENIED You need to be member to download this ...)

dl95-blacked-3 made a bare "access denied" beside members wording an auth
wall, but ``MEMBERS_ONLY_RE`` knew only "you must be a member". Dorcel's
"You need to be member" (no article) missed it, so the site went into the
24-hour cooldown and no login was attempted.
"""

BD_GATE_SCOPE = "module"

import pytest

from bulk_downloader.runner import SiteRunner
from tests.test_dl95_bk3_members_only_denial_is_auth_not_rate_limit import _Page

# The measured page text around the match, as the journal quoted it.
DORCEL_DENIAL = ("Illustration for Tina Kay TINA KAY ALL VIDEOS ACCESS DENIED "
                 "You need to be member to download this video.")


def _check(text):
    r = object.__new__(SiteRunner)
    return SiteRunner._check_redirect(
        r, _Page(text, url="https://www.dorcelclub.example/en/scene/849534/x"), "u")


def test_dorcel_need_to_be_member_denial_is_auth_not_a_rate_limit():
    got = _check(DORCEL_DENIAL)
    assert got == "auth", (
        f"FX_DORCEL_MEMBER_DENIAL_RATE_LIMITED: dorcel's 'ACCESS DENIED You need "
        f"to be member to download' wall was classified {got!r}; it must be "
        f"'auth' (login + requeue), not a site-wide rate-limit cooldown")


@pytest.mark.parametrize("text", [
    "ACCESS DENIED You need to be a member to watch this video.",
    "Access denied - you need to be a subscriber to view this scene.",
])
def test_need_to_be_a_member_variants_are_auth(text):
    assert _check(text) == "auth", text


@pytest.mark.parametrize("text", [
    # a real throttle phrase keeps the cooldown, whatever the members copy says
    "ACCESS DENIED Too many requests. You need to be member to download.",
    # a denial with no membership wording is still a block
    "ACCESS DENIED You need to be patient.",
])
def test_throttle_or_bare_denial_stays_rate_limited(text):
    assert _check(text) == "rl", text
