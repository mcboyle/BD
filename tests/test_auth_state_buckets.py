"""_m2_auth_state must bucket from the whole expiry picture, not from `earliest`.

WHY THIS EXISTS. `/api/sites/v2` publishes a per-site `auth_state` built by
`bulk_downloader.app._m2_auth_state`, and that one string drives three things:
the seeder's readiness poll, live check L8's `active` set, and the
`login_expired` attention banner in `_m2_attention_for_site`. It was derived
from a single field of `cookies_expiry_info` -- `earliest` -- and `earliest` is
a LOSSY PROJECTION of that function's own result.

`cookies_expiry_info` assigns `earliest` only inside branches guarded by
`not (exp < now)`. Two completely different jars therefore report the identical
`earliest=None`:

  * a jar of SESSION cookies (no Expires, no Max-Age) -- a live, healthy login;
  * a jar in which EVERY cookie has already expired -- a dead login.

Reading `earliest` alone mapped both onto "unknown", and left "expired"
unreachable: enumerating 18304 jars over the dated/session/expired input space
produced only "ok" and "unknown", never "expired". So the `login_expired`
banner and `/api/sites/v2`'s issue-first sort key both hung off a branch that
could not fire. This is CLAUDE.md section 0 exactly -- the classifier could not
see the condition it was asked about, and answered anyway.

THE SESSION-COOKIE CASE IS NOT A FIXTURE ARTEFACT. A cookie with no Expires and
no Max-Age is what Flask's `session`, Django's default `sessionid`, PHP's
`PHPSESSID` (session.cookie_lifetime=0) and Rails' `_session_id` all issue out
of the box. BD's own code already treats it as a live session in
`runner_auth._check_cookies_or_relogin`, whose predicate is
`if ei["expired"] <= 0 or ei["session"] != 0: return True` -- a session cookie
explicitly means DO NOT re-login. `_m2_auth_state` was the only consumer of
`cookies_expiry_info` that ignored the `session` counter, and it contradicted
the runner on the same jar.

WHY THIS IS NOT VACUOUS. The fixture-derived test below does not assert
anything about the fixture. It drives `tools/fixture_site.py`'s real
`/formauth/login` handler, takes the Set-Cookie header that handler actually
emits, converts it through BD's real `pw_to_json`, and asserts on what BD's
real classifier returns. The subject is the classifier; the fixture is only the
input, and the same input arrives from the open internet every day. The
non-fixture tests here stand on their own without it.

AND IT MUST NOT OVER-CORRECT. An EMPTY jar stays "unknown". "ok" has to mean
"there is a usable cookie", never "we could not tell" -- otherwise this trades
one silent misreport for a louder one, and L8 (which verifies that every
auth_state=ok site has a non-empty cookie jar on disk) would start certifying
sites that never logged in.
"""
from __future__ import annotations

import itertools
import sys
import time
from http.cookies import SimpleCookie
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bulk_downloader.app import _m2_auth_state           # noqa: E402
from bulk_downloader.cookies import cookies_expiry_info, pw_to_json  # noqa: E402


class _Runner:
    """The only attribute _m2_auth_state reads off a runner."""

    def __init__(self, cookies):
        self.cookies = cookies


def _state(jar):
    return _m2_auth_state(_Runner(jar), {})


# BD's stored cookie shape (cookies.pw_to_json / load_cookies_from_file):
# an `expires` key is present ONLY for cookies that carry a real expiry.
def _session_cookie(name="sessionid"):
    return {"name": name, "value": "v", "domain": "example.test",
            "path": "/", "secure": False, "httpOnly": True,
            "sameSite": "Lax"}


def _dated_cookie(name, seconds_from_now):
    c = _session_cookie(name)
    c["expires"] = time.time() + seconds_from_now
    return c


# --------------------------------------------------------------------------
# 1. The real-internet case, stated without reference to any fixture.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "sessionid",     # Django
    "PHPSESSID",     # PHP, session.cookie_lifetime=0
    "session",       # Flask
    "_session_id",   # Rails
    "connect.sid",   # Express
])
def test_a_session_cookie_is_a_healthy_session_not_an_unknown_one(name):
    """No Expires and no Max-Age is the DEFAULT for every mainstream server-side
    session framework. A jar holding one is a logged-in site."""
    got = _state([_session_cookie(name)])
    assert got == "ok", (
        f"a jar holding a single {name!r} session cookie classified as {got!r}. "
        f"Session cookies carry no expiry by design, so cookies_expiry_info "
        f"reports earliest=None for them; reading earliest alone cannot tell a "
        f"live session from no session at all."
    )


def test_m2_auth_state_does_not_contradict_the_runners_own_relogin_predicate():
    """runner_auth._check_cookies_or_relogin already decides this same question
    on this same jar, and it says a session cookie means DO NOT re-login. Two
    consumers of cookies_expiry_info must not disagree about whether the site is
    logged in."""
    jar = [_session_cookie()]
    ei = cookies_expiry_info(jar)
    runner_treats_as_live = (ei["expired"] <= 0 or ei["session"] != 0)
    assert runner_treats_as_live, "precondition changed: re-read runner_auth"
    got = _state(jar)
    assert got == "ok", (
        f"runner_auth treats this jar as a live session (ei={ei}) and will not "
        f"re-login, while _m2_auth_state publishes {got!r} to /api/sites/v2. "
        f"The UI and the runner disagree about the same cookies."
    )


# --------------------------------------------------------------------------
# 2. The dead branch. "expired" must be reachable at all.
# --------------------------------------------------------------------------

def test_a_jar_whose_every_cookie_has_expired_reports_expired():
    jar = [_dated_cookie("sessionid", -86400),
           _dated_cookie("csrftoken", -10)]
    ei = cookies_expiry_info(jar)
    assert ei["expired"] == 2 and ei["earliest"] is None, (
        f"precondition changed: cookies_expiry_info={ei}")
    got = _state(jar)
    assert got == "expired", (
        f"every cookie in the jar is past its expiry (ei={ei}) yet auth_state "
        f"is {got!r}. cookies_expiry_info never sets `earliest` from an expired "
        f"cookie, so a classifier reading only `earliest` cannot see this."
    )


def test_every_bucket_is_reachable_over_the_input_space():
    """The section-0 assertion. A classifier with a bucket nothing can land in
    is not a classifier -- and `expired` was exactly that: the login_expired
    attention banner and the issue-first sort in /api/sites/v2 both key on it.
    Derive reachability, do not assert it."""
    now = time.time()
    offsets = [-10 ** 9, -86400 * 30, -3600, -60, -1, 1, 60, 3600,
               86400, 86400 * 30, 10 ** 9]
    produced = set()
    for n in (1, 2):
        for combo in itertools.product(offsets, repeat=n):
            produced.add(_state([
                {"name": f"c{i}", "value": "v", "expires": now + off}
                for i, off in enumerate(combo)]))
    produced.add(_state([_session_cookie()]))
    produced.add(_state([]))
    missing = {"ok", "expired", "unknown"} - produced
    assert not missing, (
        f"bucket(s) {sorted(missing)} are unreachable across the enumerated "
        f"cookie-jar space; only {sorted(produced)} can ever be produced"
    )


# --------------------------------------------------------------------------
# 3. The anti-over-correction guards. These must hold BEFORE and AFTER.
# --------------------------------------------------------------------------

def test_an_empty_jar_is_still_unknown():
    """Unknown is a third state and it fails. A site that never logged in must
    never read 'ok' -- L8 would then verify a cookie file that does not exist."""
    assert _state([]) == "unknown"
    assert _state(None) == "unknown"


def test_a_dated_cookie_still_in_date_is_still_ok():
    assert _state([_dated_cookie("sessionid", 3600)]) == "ok"
    assert _state([_dated_cookie("sessionid", 86400 * 30)]) == "ok"


def test_one_live_cookie_beside_an_expired_one_is_ok_not_expired():
    """'expired' means NOTHING usable is left, not 'something lapsed'. A stale
    tracking cookie beside a live session must not raise the banner."""
    jar = [_dated_cookie("old_promo", -86400), _dated_cookie("sessionid", 3600)]
    assert _state(jar) == "ok"
    jar = [_dated_cookie("old_promo", -86400), _session_cookie()]
    assert _state(jar) == "ok"


def test_a_runner_that_raises_is_unknown():
    class Broken:
        @property
        def cookies(self):
            raise RuntimeError("no runner")

    assert _m2_auth_state(Broken(), {}) == "unknown"


# --------------------------------------------------------------------------
# 4. End-to-end over the cookie tools/fixture_site.py actually issues.
#    The subject is BD's classifier; the fixture only supplies the input.
# --------------------------------------------------------------------------

def test_the_fixture_login_cookie_reads_ok_through_bds_own_conversion():
    """L6/L8 are seeded against tools/fixture_site.py. Its /formauth/login
    success path issues `fixture_session` with no expiry, so the jar BD ends up
    holding after a SUCCESSFUL login is session-only. Under the old classifier
    that site reported auth_state=unknown forever and L8 could never find an
    active site to verify -- a green login and a broken login were
    indistinguishable at the endpoint the seeder polls."""
    flask = pytest.importorskip("flask")
    assert flask
    from tools.fixture_site import make_app, USERNAME, PASSWORD

    client = make_app().test_client()
    resp = client.post("/formauth/login",
                       data={"username": USERNAME, "password": PASSWORD})
    assert resp.status_code == 302, (
        f"the fixture rejected the login (status={resp.status_code}); this test "
        f"is about classification, so a failed login makes it meaningless")

    morsel = SimpleCookie()
    morsel.load(resp.headers["Set-Cookie"])
    c = morsel["fixture_session"]
    assert not c["expires"] and not c["max-age"], (
        "the fixture now sets an expiry; this test's premise is gone -- "
        "re-derive it rather than deleting it")

    # Playwright reports a session cookie as expires == -1; do_login stores
    # ctx.cookies() through pw_to_json, which drops the key entirely.
    stored = pw_to_json([{"name": c.key, "value": c.value,
                          "domain": "127.0.0.1", "path": "/", "expires": -1,
                          "httpOnly": False, "secure": False,
                          "sameSite": "Lax"}])
    assert "expirationDate" not in stored[0] and "expires" not in stored[0]

    got = _state(stored)
    assert got == "ok", (
        f"BD classified its own post-login cookie jar as {got!r}. The login "
        f"succeeded (302 + session cookie issued), so /api/sites/v2 must not "
        f"report a state that is indistinguishable from never having logged in."
    )


# --------------------------------------------------------------------------
# 4. The -1 session sentinel on the producers that are NOT pw_to_json.
#
# The test above proves the pw_to_json path is clean: it drops a non-positive
# `expires` entirely, so the classifier never sees the sentinel. That is also
# why this defect survived -- the ONE producer with coverage was the one that
# normalises. The other two do not.
#
#   cookies.load_cookies_from_file:17   `if c.get("expirationDate"):`
#   app_sites_auth.api_load_cookies:551 the same line, duplicated
#
# `-1` is truthy, so both write `expires = -1` into the stored jar, and
# cookies_expiry_info's `if not exp:` is likewise truthiness-based, so the
# sentinel falls through to `elif exp < now` and is counted EXPIRED.
#
# -1 is not a BD invention: it is Playwright's own session marker
# (login_impl/replay.py:476 documents it) and the form browser-extension
# cookie exporters emit for a session cookie. A jar of live session cookies
# therefore reports expired>0 and session==0, which drives
# runner_auth._check_cookies_or_relogin (:1012) past both of its guards --
# forcing a re-login, or with no stored credentials routing the URL to
# _handle_failure("Cookies expired -- re-login needed") on a WORKING login.
# --------------------------------------------------------------------------

def test_a_non_positive_expiry_is_a_session_cookie_not_an_expired_one():
    """The classifier, stated directly. An expiry is meaningful only when it is
    a positive timestamp; 0 and -1 both mean 'no expiry was set'."""
    for sentinel in (-1, 0):
        ei = cookies_expiry_info([{"name": "sessionid", "value": "v",
                                   "domain": "example.test", "path": "/",
                                   "expires": sentinel}])
        assert ei["session"] == 1 and ei["expired"] == 0, (
            f"a cookie with expires={sentinel!r} was graded {ei!r}. A "
            f"non-positive expiry is Playwright's session marker, not a "
            f"timestamp in 1969 -- grading it EXPIRED tells the runner to "
            f"re-login a session that is live."
        )


def test_the_file_loader_does_not_manufacture_a_negative_expiry():
    """load_cookies_from_file is the disk path into the jar. An extension export
    carrying `expirationDate: -1` must not become `expires: -1`; pw_to_json's
    convention is that the key is absent unless the expiry is real."""
    import json as _json
    import tempfile
    from bulk_downloader.cookies import load_cookies_from_file

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cookies.json"
        p.write_text(_json.dumps([{"name": "sessionid", "value": "v",
                                   "domain": "example.test", "path": "/",
                                   "expirationDate": -1}]), encoding="utf-8")
        loaded = load_cookies_from_file(str(p))

    assert "expires" not in loaded[0], (
        f"loaded {loaded[0]!r}. `if c.get('expirationDate'):` is a truthiness "
        f"test and -1 is truthy, so the sentinel is copied through as a real "
        f"expiry. pw_to_json guards the same field with `> 0`; these two must "
        f"agree or a jar changes meaning by round-tripping through disk."
    )
    assert cookies_expiry_info(loaded)["session"] == 1


def test_the_upload_endpoint_shares_the_loaders_normalisation():
    """`/api/sites/<sid>/load_cookies` had its own byte-for-byte copy of the
    loader's normalisation loop, including the same truthiness bug. Two copies
    of one rule is the denominator that drifts -- and here the copy was already
    wrong in the same way. Assert they are ONE function, not two that happen to
    agree today."""
    from bulk_downloader import cookies as ck
    from bulk_downloader import app_sites_auth as asa

    shared = getattr(ck, "normalize_stored_cookie", None)
    assert shared is not None, (
        "bulk_downloader.cookies.normalize_stored_cookie does not exist; the "
        "upload endpoint and load_cookies_from_file still carry separate "
        "copies of the same normalisation."
    )
    assert getattr(asa, "normalize_stored_cookie", None) is shared, (
        "app_sites_auth does not use the shared normaliser, so the endpoint "
        "can drift from the file loader again."
    )
    assert "expires" not in shared({"name": "s", "expirationDate": -1})


def test_the_runner_and_the_panel_agree_on_a_sentinel_jar():
    """The end of the chain. Both consumers must read a -1 jar as live."""
    jar = [{"name": "sessionid", "value": "v", "domain": "example.test",
            "path": "/", "expires": -1}]
    ei = cookies_expiry_info(jar)
    runner_treats_as_live = (ei["expired"] <= 0 or ei["session"] != 0)
    assert runner_treats_as_live, (
        f"runner_auth._check_cookies_or_relogin would re-login this jar "
        f"(ei={ei}) even though every cookie in it is a live session cookie.")
    assert _state(jar) == "ok"
