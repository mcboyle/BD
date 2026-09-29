"""O1567 fx-ma-rate-limited: a bare "403 Forbidden / Request is denied" page on a
LOGIN site was taken for a RATE LIMIT (24 h site cooldown, start blocked, no login).

MEASURED on test4 (10.0.70.85) 2026-09-29 20:11:37Z, site-ma-brazzers 1167e615 and
site-ma-bangbros 146f9c87 (journal ``journalctl -u bulkdownloader``):

    [1167e615][rate_limit] https://site-ma.brazzers.com/scene/11458247/:
      Rate limit at https://site-ma.brazzers.com/scene/11458247/
      (page text: ...  403 Forbidden  Request is denied...)

rl_1167e615.json until=+86400 s; every ``POST /api/sites/<id>/start`` then answered
{"blocked_by":"rate_limited"} and the queue rows stayed pending.  An unauthenticated
``curl`` of a scene URL returns the same bare page (title "Forbidden", body
"403 Forbidden Request is denied"): it is the logged-out wall of a member site, not
a throttle.  Same class as dl95-blacked-3 (members-only denial -> auth).

Rule (narrow): a page whose ONLY rate-limit evidence is a 403/forbidden/denied
phrase, whose whole visible text is that bare error, on a site that has a login_url,
is "auth" (login + requeue).  A site without a login_url, any throttle wording, or a
long page keeps "rl".
"""

BD_GATE_SCOPE = "module"

import pytest

from bulk_downloader.runner import SiteRunner

BARE = "Forbidden 403 Forbidden Request is denied"
LOGIN_URL = "https://site-ma.brazzers.com/login"


class _Loc:
    def __init__(self, text):
        self._t = text

    def inner_text(self, timeout=None):
        return self._t


class _Page:
    def __init__(self, text, url="https://site-ma.brazzers.com/scene/11458247/"):
        self.url = url
        self._t = text

    def locator(self, sel):
        return _Loc(self._t)

    def content(self):
        return "<html><body>" + self._t + "</body></html>"


def _check(text, login_url=LOGIN_URL):
    r = object.__new__(SiteRunner)
    r.config = {"login_url": login_url} if login_url else {}
    return SiteRunner._check_redirect(r, _Page(text), "u")


@pytest.mark.parametrize("text", [
    BARE,
    "403 Forbidden\nRequest is denied",
])
def test_a_bare_403_wall_on_a_login_site_is_auth_not_a_rate_limit(text):
    got = _check(text)
    assert got == "auth", (
        f"O1567: bare 403 wall {text!r} on a login site was classified {got!r}; "
        f"it must be 'auth' (login + requeue), not a 24 h site cooldown")


@pytest.mark.parametrize("text,login_url", [
    (BARE, ""),                                            # no login_url: a public site, real block
    ("403 Forbidden  Too many requests -- try again later", LOGIN_URL),
    ("403 Forbidden  Rate limit exceeded", LOGIN_URL),
    ("Error 403 Forbidden  Members only area " + "x " * 200, LOGIN_URL),  # long page
])
def test_control_real_blocks_stay_rate_limits(text, login_url):
    assert _check(text, login_url) == "rl", text


def test_control_a_normal_page_is_none():
    assert _check("HOME  VIDEOS  Download 1080p") is None
