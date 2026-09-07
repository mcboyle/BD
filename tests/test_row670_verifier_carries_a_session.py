"""Row 670: ``verify_template_source`` carries an operator session.

Row 455 left four of the five reviewed selector kinds UNKNOWN for a single
reason: the verifier rendered every URL subject through an ephemeral
``cloak.cloaked_page`` with no cookies and no storage state, so no template
could be resolved against an authenticated page. This gate drives the shipped
verifier against a FAKE browser -- no network, no Playwright launch -- whose
rendered DOM depends on whether the context it was created with carries a
session a browser would offer to the page: the members-only selectors exist
only when authenticated, the login wall only when not. Cookie applicability is
judged by BD's own ``session_scope.applicable_cookies`` so the fake's notion of
"the browser would send this cookie" is the shipped one, not a stub's.

Every value here is a documented zero-entropy fixture; nothing is a secret.
"""
from __future__ import annotations

import importlib
import json
import runpy
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_HOST = "members.bd-row670-fixture.invalid"
_URL = f"https://{_HOST}/scene/1"
_SESSION_COOKIE = "bd_session"
# zero-entropy fixture value, not a secret
_SESSION_VALUE = "0" * 32
_STORAGE_TOKEN_KEY = "auth_token"
# zero-entropy fixture value, not a secret
_STORAGE_TOKEN = "test-token-zero-entropy"

_LOGIN = {
    "user_field": "input[name='email']",
    "pass_field": "input[name='password']",
    "submit_btn": "button[type='submit']",
}
_PLAYER = "video#member-player"
_ROWS = "a.member-download[href*='/download/']"
_TEMPLATE = {
    "id": "row670-members-fixture",
    "login": dict(_LOGIN),
    "learned": {
        "player": {"selectors": [_PLAYER]},
        "download": {"row_selectors": [_ROWS]},
    },
}
# What the fixture site renders. The login wall carries exactly the three
# login controls; the members page carries the player and six download rows
# (the 2026-08-29 number of tiers) and no login control at all.
_LOGIN_WALL_COUNTS = {sel: 1 for sel in _LOGIN.values()} | {_PLAYER: 0, _ROWS: 0}
_MEMBER_COUNTS = {sel: 0 for sel in _LOGIN.values()} | {_PLAYER: 1, _ROWS: 6}
_NO_SESSION = {
    "supplied": False,
    "source": "",
    "cookies": 0,
    "origins": 0,
    "applicable_cookies": 0,
    "applicable_origins": 0,
}
_UNSET = object()


def _api():
    return importlib.import_module("bulk_downloader.template_selector_verifier")


def _cookie(**overrides) -> dict:
    cookie = {
        "name": _SESSION_COOKIE,
        "value": _SESSION_VALUE,
        "domain": _HOST,
        "path": "/",
    }
    cookie.update(overrides)
    return cookie


class _Locator:
    def __init__(self, count: int) -> None:
        self._count = count

    def count(self) -> int:
        return self._count

    @property
    def first(self):
        return self

    def wait_for(self, **_kwargs) -> None:
        return None

    def click(self, **_kwargs) -> None:
        return None

    def or_(self, other):
        return _Locator(self._count + other._count)


class _Route:
    def __init__(self, url: str) -> None:
        self.request = SimpleNamespace(url=url, resource_type="document")
        self.outcome = ""
        self.status = 200

    def continue_(self) -> None:
        self.outcome = "continue"

    def abort(self) -> None:
        self.outcome = "abort"

    def fulfill(self, status=200, **_kwargs) -> None:
        self.outcome = "fulfill"
        self.status = status


class _Context:
    """The jar a real context would hold: storage state at creation plus
    anything added later through ``add_cookies``."""

    def __init__(self, options) -> None:
        self.options = dict(options or {})
        state = self.options.get("storage_state") or {}
        self.cookies = list(state.get("cookies") or [])
        self.origins = list(state.get("origins") or [])
        self.offline = False

    def add_cookies(self, cookies) -> None:
        self.cookies.extend(cookies)

    def set_offline(self, flag: bool) -> None:
        self.offline = flag


class _Page:
    def __init__(self, context: _Context) -> None:
        self.context = context
        self.url = ""
        self.goto_calls: list[str] = []
        self.locator_calls: list[str] = []
        self._handlers: list = []

    def set_default_timeout(self, _ms) -> None:
        return None

    def route_web_socket(self, _pattern, _handler) -> None:
        return None

    def route(self, _pattern, handler) -> None:
        self._handlers.append(handler)

    def goto(self, url: str, **_kwargs):
        self.goto_calls.append(url)
        self.url = url
        response = SimpleNamespace(status=200)
        for handler in self._handlers:
            route = _Route(url)
            handler(route)
            if route.outcome == "abort":
                raise RuntimeError(f"net::ERR_FAILED at {url}")
            if route.outcome == "fulfill":
                response = SimpleNamespace(status=route.status)
        return response

    def wait_for_load_state(self, _state, **_kwargs) -> None:
        return None

    def _authenticated(self) -> bool:
        from bulk_downloader.session_scope import applicable_cookies, host_of

        offered = applicable_cookies(self.context.cookies, self.url)
        if any(c.get("name") == _SESSION_COOKIE and c.get("value") == _SESSION_VALUE
               for c in offered):
            return True
        host = host_of(self.url)
        for origin in self.context.origins:
            if host_of(str(origin.get("origin", ""))) != host:
                continue
            if any(item.get("name") == _STORAGE_TOKEN_KEY
                   and item.get("value") == _STORAGE_TOKEN
                   for item in origin.get("localStorage", [])):
                return True
        return False

    def locator(self, selector: str) -> _Locator:
        self.locator_calls.append(selector)
        counts = _MEMBER_COUNTS if self._authenticated() else _LOGIN_WALL_COUNTS
        assert selector in counts, f"fixture has no count for {selector!r}"
        return _Locator(counts[selector])


@pytest.fixture
def browser(monkeypatch):
    """Fake the canonical browser and the DNS-resolving public-host guard.

    Returns the list of launches; each records the ``cloaked_page`` kwargs,
    the context built from them and the page that rendered."""
    launches: list[dict] = []

    @contextmanager
    def fake_cloaked_page(**kwargs):
        context = _Context(kwargs.get("context_options"))
        page = _Page(context)
        launches.append({"kwargs": kwargs, "context": context, "page": page})
        yield page

    cloak = importlib.import_module("bulk_downloader.cloak")
    monkeypatch.setattr(cloak, "cloaked_page", fake_cloaked_page)
    playground = importlib.import_module("bulk_downloader.selector_playground")

    def fake_host_public(url: str):
        host = urlparse(url).hostname or ""
        if host == _HOST:
            return True, ""
        return False, f"row670 fixture guard refuses {host!r}"

    monkeypatch.setattr(playground, "_host_public", fake_host_public)
    return launches


def _verify(subject, session=_UNSET, **kwargs) -> dict:
    """Call the shipped verifier with ``session`` when the API accepts one.

    On the unmodified base the API has no session option at all (the row's
    defect), so the only render it can offer is the sessionless one; the
    behaviour assertions then fail on what that render resolved."""
    api = _api()
    if session is _UNSET:
        return api.verify_template_source(_TEMPLATE, subject, **kwargs)
    try:
        return api.verify_template_source(
            _TEMPLATE, subject, session=session, **kwargs
        )
    except TypeError as exc:
        if "session" not in str(exc):
            raise
        return api.verify_template_source(_TEMPLATE, subject, **kwargs)


def _resolved(report: dict) -> dict[str, tuple[str, object]]:
    return {row["selector"]: (row["status"], row["count"]) for row in report["selectors"]}


def _assert_members_page(report: dict) -> None:
    resolved = _resolved(report)
    members = {sel: resolved[sel] for sel in (_PLAYER, _ROWS)}
    assert members == {_PLAYER: ("HIT", 1), _ROWS: ("HIT", 6)}, (
        "member-only selectors did not resolve: the verifier rendered the page "
        f"without the operator's session; resolved={resolved}"
    )
    assert {resolved[sel] for sel in _LOGIN.values()} == {("MISS", 0)}
    assert report["verdict"] == "HIT" and report["ok"] is True
    assert report["match_count"] == 7
    assert report["selector_count"] == 5


def test_members_only_selectors_resolve_when_the_session_is_carried(browser):
    """RED on the base: the verifier cannot carry a session, so the members
    page never renders and the members-only selectors read MISS."""
    report = _verify(_URL, [_cookie()])

    assert len(browser) == 1, "exactly one browser launch per resolution"
    assert browser[0]["page"].goto_calls == [_URL]
    _assert_members_page(report)
    assert report["subject"]["status"] == "OK"
    assert report["subject"]["session"] == {
        "supplied": True,
        "source": "cookies",
        "cookies": 1,
        "origins": 0,
        "applicable_cookies": 1,
        "applicable_origins": 0,
    }
    injected = browser[0]["kwargs"]["context_options"]["storage_state"]
    assert [c["name"] for c in injected["cookies"]] == [_SESSION_COOKIE]
    assert injected["origins"] == []
    assert browser[0]["kwargs"]["context_options"]["service_workers"] == "block"


def test_without_a_session_the_login_wall_renders(browser):
    """Negative control: the fixture DIVERGES on the session. Sessionless, the
    same URL resolves the three login controls and nothing members-only, and
    the report says no session was supplied."""
    report = _verify(_URL)

    assert len(browser) == 1
    resolved = _resolved(report)
    assert {resolved[sel] for sel in _LOGIN.values()} == {("HIT", 1)}
    assert {sel: resolved[sel] for sel in (_PLAYER, _ROWS)} == {
        _PLAYER: ("MISS", 0), _ROWS: ("MISS", 0),
    }
    assert report["match_count"] == 3
    assert report["subject"]["session"] == _NO_SESSION
    assert "storage_state" not in browser[0]["kwargs"]["context_options"]


def test_a_storage_state_file_carries_origins_to_the_context(browser, tmp_path):
    """A Playwright ``storage_state()`` export authenticates through
    localStorage alone: origins reach the context, cookies may be empty."""
    state = {
        "cookies": [],
        "origins": [
            {
                "origin": f"https://{_HOST}",
                "localStorage": [{"name": _STORAGE_TOKEN_KEY, "value": _STORAGE_TOKEN}],
                "indexedDB": [{"name": "bd-fixture-db", "version": 1, "stores": []}],
            },
            {
                # Same host, different scheme: a different origin, so a browser
                # would not offer its storage to the https subject.
                "origin": f"http://{_HOST}",
                "localStorage": [{"name": _STORAGE_TOKEN_KEY, "value": _STORAGE_TOKEN}],
            },
        ],
    }
    path = tmp_path / "storage_state.json"
    path.write_text(json.dumps(state), encoding="utf-8")

    report = _verify(_URL, path)

    _assert_members_page(report)
    assert report["subject"]["session"] == {
        "supplied": True,
        "source": str(path),
        "cookies": 0,
        "origins": 2,
        "applicable_cookies": 0,
        "applicable_origins": 1,
    }
    injected = browser[0]["kwargs"]["context_options"]["storage_state"]
    assert injected == {"cookies": [], "origins": state["origins"]}, (
        "origins, IndexedDB included, must reach the context untouched"
    )

    # Only the http origin: same host, wrong scheme -- covers nothing, UNKNOWN.
    http_only = tmp_path / "http_only.json"
    http_only.write_text(json.dumps({"origins": state["origins"][1:]}), encoding="utf-8")
    report = _verify(_URL, http_only)
    assert report["verdict"] == "UNKNOWN"
    assert report["subject"]["error"].endswith(
        f"0 cookie(s) and 1 origin(s), none applicable to {_HOST}"
    )
    assert len(browser) == 1


def test_cookies_are_normalised_to_the_storage_state_shape(browser, tmp_path):
    """A url-scoped jar cookie gains its host and default path; a BD cookie
    file's ``expirationDate`` becomes ``expires`` only when positive, and the
    -1 session-cookie sentinel is dropped rather than injected as a timestamp."""
    url_scoped = {
        "name": _SESSION_COOKIE,
        "value": _SESSION_VALUE,
        "url": f"https://{_HOST}/",
        "expires": -1,
        "httpOnly": True,
        "sameSite": "Lax",
    }
    report = _verify(_URL, [url_scoped])
    _assert_members_page(report)
    assert browser[0]["kwargs"]["context_options"]["storage_state"]["cookies"] == [{
        "name": _SESSION_COOKIE,
        "value": _SESSION_VALUE,
        "domain": _HOST,
        "path": "/",
        "httpOnly": True,
        "sameSite": "Lax",
    }], "url-scoped cookie must gain its host, keep its flags, drop expires=-1"
    # A cookie scoped under /members is not offered to /scene/1 by a browser
    # (RFC 6265 default-path), and the coverage seam applies BD's own path
    # rule: the narrowed jar covers nothing on the subject and reads UNKNOWN.
    narrowed = _verify(
        _URL, [dict(url_scoped, url=f"https://{_HOST}/members/only/here")]
    )
    assert narrowed["verdict"] == "UNKNOWN"
    assert "none applicable to" in narrowed["subject"]["error"]
    assert len(browser) == 1, "the uncovered jar must not launch"

    jar_file = tmp_path / "cookies.json"
    jar_file.write_text(json.dumps({
        "exported": [
            _cookie(expirationDate=4102444800, secure=True, sameSite="bogus"),
            _cookie(name="theme", value="dark", expirationDate=-1),
        ],
    }), encoding="utf-8")
    report = _verify(_URL, jar_file)
    _assert_members_page(report)
    assert report["subject"]["session"]["cookies"] == 2
    assert report["subject"]["session"]["applicable_cookies"] == 2
    injected = browser[-1]["kwargs"]["context_options"]["storage_state"]["cookies"]
    assert injected == [
        _cookie(expires=4102444800.0, secure=True),
        _cookie(name="theme", value="dark"),
    ]


def test_load_session_normalises_shapes_without_rendering():
    """Pure normaliser contract; drives no verifier and launches nothing.
    This is the transform-control band: a mutant of the injection seam MUST
    escape a test that never renders."""
    api = _api()
    state, source = api.load_session([_cookie()])
    assert source == "cookies"
    assert state == {"cookies": [_cookie()], "origins": []}
    state, source = api.load_session({"cookies": [_cookie(path="")], "origins": []})
    assert (source, state["cookies"][0]["path"]) == ("mapping", "/")
    with pytest.raises(ValueError, match="has no name"):
        api.load_session([{"value": _SESSION_VALUE, "domain": _HOST}])
    # RFC 6265 5.1.4 default-path for a url-scoped cookie without a path.
    paths = {
        url_path: api.load_session(
            [_cookie(domain="", path="", url=f"https://{_HOST}{url_path}")]
        )[0]["cookies"][0]["path"]
        for url_path in ("/", "/scene", "/members/home", "/members/only/")
    }
    assert paths == {
        "/": "/", "/scene": "/", "/members/home": "/members",
        "/members/only/": "/members/only",
    }


def test_an_unreadable_session_is_unknown_and_never_launches(browser, tmp_path):
    """A2: a session that cannot be loaded is UNKNOWN, never a logged-out
    render whose MISS looks like an answer. Eleven shapes, eleven UNKNOWNs,
    zero launches: seven malformed sessions, then four well-shaped sessions
    holding one malformed ELEMENT each (shape lens E1-E4), because a dropped
    or mistyped element renders logged-out with the report claiming a
    session."""
    broken_json = tmp_path / "broken.json"
    broken_json.write_text("{not json", encoding="utf-8")
    cases = [
        ([{"value": _SESSION_VALUE, "domain": _HOST}], "has no name"),
        ([_cookie(domain="")], "has neither domain nor url"),
        ({"cookies": [], "origins": []}, "no cookies and no storage origins"),
        ({"cookies": [], "origins": [{"origin": "nohost", "localStorage": []}]}, "has no host"),
        (tmp_path / "absent.json", "is not a file"),
        (broken_json, "is not readable JSON"),
        (42, "must be a cookie list"),
        # E1: a cookie file that is a list of ints/strings, not of mappings.
        ([42], "cookie [0] is not a mapping"),
        # E2: an exporter that writes the cookie value as a number.
        ([_cookie(value=12345)], "has no string value"),
        # E3: a storage-state origin that is not a mapping.
        ({"cookies": [], "origins": [42]}, "origin [0] is not a mapping"),
        # E4: a localStorage item whose name/value are not strings.
        ({"cookies": [], "origins": [{"origin": f"https://{_HOST}",
                                      "localStorage": [{"name": 1, "value": 2}]}]},
         "is not a name/value pair"),
    ]
    unknown = 0
    for session, needle in cases:
        report = _verify(_URL, session)
        assert report["verdict"] == "UNKNOWN" and report["ok"] is False, (needle, report)
        error = report["subject"]["error"]
        assert error.startswith("session unavailable: ") and needle in error, error
        assert report["selector_count"] == 5
        assert {row["status"] for row in report["selectors"]} == {"UNKNOWN"}
        assert report["subject"]["session"]["supplied"] is True
        unknown += 1
    assert unknown == len(cases) == 11
    assert browser == [], "an unreadable session must never reach a browser"


def test_an_unknown_decided_before_the_render_still_records_the_session(
    browser, tmp_path, monkeypatch
):
    """Shape lens E5: every UNKNOWN the verifier decides before (or instead
    of) rendering threads the live session summary into its report, so
    ``subject.session.supplied`` never claims a sessionless run on a run where
    the operator supplied one. Zero launches on every arm."""
    api = _api()
    session = [_cookie()]
    cases = [
        # template arms, decided before the subject is read
        (lambda: api.verify_template_source(
            "row670-no-such-template", _URL, session=session),
         "unknown committed template id"),
        (lambda: api.verify_template_source(42, _URL, session=session),
         "template must be an id or mapping"),
        (lambda: api.verify_template_source(
            {"id": "row670-empty-denominator"}, _URL, session=session),
         "template has no selector denominator"),
        # subject arms, decided before the session is read
        (lambda: _verify(tmp_path / "absent.html", session),
         "FileNotFoundError: saved HTML subject is not a file"),
        (lambda: _verify(_URL, session, timeout=0),
         "timeout must be greater than zero"),
        (lambda: _verify(f"https://other.{_HOST}/scene/1", session),
         "blocked URL subject: row670 fixture guard refuses"),
    ]
    for call, needle in cases:
        report = call()
        assert report["verdict"] == "UNKNOWN" and report["ok"] is False, (needle, report)
        error = report["subject"]["error"]
        assert error.startswith(needle), (needle, error)
        assert report["subject"]["session"]["supplied"] is True, (needle, report["subject"])

    # The selector-parser arm is decided AFTER the session was loaded, so its
    # UNKNOWN report must carry the loaded summary, not merely the flag.
    def parser_missing():
        raise FileNotFoundError("row670 fixture: no selector parser on this host")

    monkeypatch.setattr(api, "_playwright_parser_paths", parser_missing)
    report = _verify(_URL, session)
    assert report["verdict"] == "UNKNOWN" and report["ok"] is False, report
    assert report["subject"]["error"].startswith(
        "selector parser unavailable: FileNotFoundError"), report["subject"]["error"]
    info = report["subject"]["session"]
    assert info["supplied"] is True, info
    assert (info["cookies"], info["applicable_cookies"]) == (1, 1), info
    assert browser == [], "an UNKNOWN decided before the render must never launch"

def test_a_session_covering_no_subject_host_is_unknown_not_miss(browser):
    """The brazzers shape: cookies scoped to the login host are not offered to
    the members host, so the page would render logged-out. That is UNKNOWN
    with the uncovered host named, decided before any launch; the same cookie
    on the parent domain covers the host and resolves."""
    login_scoped = _cookie(domain="auth.bd-row670-fixture.invalid")
    report = _verify(_URL, [login_scoped])
    assert report["verdict"] == "UNKNOWN" and report["ok"] is False
    assert report["subject"]["error"] == (
        "session covers no host the subject uses: 1 cookie(s) and 0 origin(s), "
        f"none applicable to {_HOST}"
    )
    assert report["subject"]["session"]["applicable_cookies"] == 0
    assert browser == []

    covering = _verify(_URL, [_cookie(domain=".bd-row670-fixture.invalid")])
    _assert_members_page(covering)
    assert covering["subject"]["session"]["applicable_cookies"] == 1
    assert len(browser) == 1


def test_a_saved_html_subject_refuses_a_session(browser, tmp_path):
    """Saved HTML is served offline at a fixture origin no cookie applies to;
    accepting a session there would silently discard it."""
    saved = tmp_path / "members.html"
    saved.write_text("<html><body><video id='member-player'></video></body></html>",
                     encoding="utf-8")
    report = _verify(saved, [_cookie()])
    assert report["verdict"] == "UNKNOWN" and report["ok"] is False
    assert report["subject"]["error"].startswith(
        "saved HTML subject cannot carry a session"
    )
    assert report["subject"]["session"]["cookies"] == 1
    assert browser == []

    # Precondition: the same saved subject without a session still renders.
    sessionless = _verify(saved)
    assert sessionless["subject"]["status"] == "OK"
    assert len(browser) == 1

    # An UNKNOWN decided before the session is even read still records that a
    # session was supplied: the report never implies a sessionless run.
    absent = _verify(tmp_path / "absent.html", [_cookie()])
    assert absent["verdict"] == "UNKNOWN"
    assert absent["subject"]["error"].startswith("FileNotFoundError")
    assert absent["subject"]["session"]["supplied"] is True
    assert absent["subject"]["session"]["cookies"] == 0
    assert len(browser) == 1


def test_cli_forwards_the_session_file_to_the_verifier(tmp_path):
    tool = runpy.run_path(str(_REPO / "toolchain/bin/bd-template-verify"))
    calls: list[tuple] = []

    def verify(*args, **kwargs):
        calls.append((args, kwargs))
        return {"template_id": "row670", "verdict": "HIT", "selector_count": 5,
                "match_count": 7, "selectors": []}

    tool["main"].__globals__["verify_template_source"] = verify
    state = tmp_path / "storage_state.json"
    state.write_text("{}", encoding="utf-8")
    assert tool["main"](["row670", _URL, "--session", str(state)]) == 0
    assert tool["main"](["row670", _URL]) == 0
    assert [call[1].get("session") for call in calls] == [str(state), None]
    assert [call[0] for call in calls] == [("row670", _URL)] * 2
