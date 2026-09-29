"""dl95-blacked-3: a logged-out members-only gate was taken for a RATE LIMIT.

MEASURED on test2 2026-09-29 (build 1bf8eab4, site blacked 3c8aba9b, login
site, auth_state unknown; evidence
``harness-work/UIUX-20260928/download-95/B6-B/p1/blacked/`` and the journal
slice ``harness-work/FIX/dl95-blacked-3-journal.txt``):

    [3c8aba9b][rate_limit] https://www.blacked.com/videos/hot-bestie-foursome:
      Rate limit at https://www.blacked.com/videos/hot-bestie-foursome
      (page text: ...ORMERS LIVE SEX SEARCH LOGIN GET ACCESS ACCESS DENIED
      You must be a member to watch this vid...)

The job was re-queued "Rate limit", the WHOLE SITE went rate_limited (Home
attention "blacked -- Rate limited") and no login was attempted.

``_check_redirect`` runs ``RL_RE`` over the body BEFORE the in-place login-wall
check, and ``RL_RE`` carries the bare denial phrase ``access denied``.  The
page says why access is denied -- you are not a member -- so it is an auth
wall: ``_handle_auth_required`` (login + requeue), never a 24-hour cooldown.

The rule is narrow on purpose: a page whose ONLY rate-limit evidence is the
bare denial phrase, and which also says the content is for members, is
"auth".  A denial with no membership wording, or a real throttle phrase next to
a "Members only" nav link, stays "rl".
"""

# The gate parses a module-level ASSIGNMENT, not a docstring line.
BD_GATE_SCOPE = "module"

import pytest

from bulk_downloader.runner import SiteRunner

# The measured page text around the match, as the journal quoted it.
BLACKED_GATE = ("VIDEOS MODELS PERFORMERS LIVE SEX SEARCH LOGIN GET ACCESS "
                "ACCESS DENIED  You must be a member to watch this video. "
                "Join now for instant access.")


class _Loc:
    def __init__(self, text):
        self._t = text

    def inner_text(self, timeout=None):
        return self._t


class _Page:
    def __init__(self, text, url="https://www.blacked.example/videos/hot-bestie-foursome"):
        self.url = url
        self._t = text

    def locator(self, sel):
        return _Loc(self._t)

    def content(self):
        return "<html><body>" + self._t + "</body></html>"


def _check(text):
    r = object.__new__(SiteRunner)
    return SiteRunner._check_redirect(r, _Page(text), "u")


def test_a_members_only_access_denied_page_is_auth_not_a_rate_limit():
    got = _check(BLACKED_GATE)
    assert got == "auth", (
        f"DL95-BK3: a members-only 'ACCESS DENIED -- You must be a member' "
        f"gate was classified {got!r}; it must be 'auth' (login + requeue), "
        f"not a site-wide rate-limit cooldown")


@pytest.mark.parametrize("text", [
    "ACCESS DENIED  This video is for members only.",
    "Access denied. Members-only content -- please log in.",
])
def test_other_members_only_denials_are_auth_too(text):
    assert _check(text) == "auth", text


@pytest.mark.parametrize("text", [
    # A denial that does not say membership is the reason stays a block.
    "Access Denied\nYou don't have permission to access this server.",
    # A real throttle phrase beside a members-only nav link stays a block.
    "HOME  MEMBERS ONLY  Too many requests -- try again later. ACCESS DENIED",
    "Error 403 Forbidden  Members only area",
])
def test_control_real_blocks_stay_rate_limits(text):
    assert _check(text) == "rl", text


def test_control_a_members_only_nav_link_alone_is_a_normal_page():
    assert _check("HOME  VIDEOS  MEMBERS ONLY  Download 1080p") is None
