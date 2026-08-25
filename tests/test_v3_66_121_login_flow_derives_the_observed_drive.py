"""Row 121: capture-derived login plans must reproduce the observed drives.

The two fixtures below are deliberately separate capture slices.  They retain
every action-timeline row plus the network rows that define the successful
login transition and the later document-navigation noise which caused the old
deriver to emit post-login CDN waits.  The source WACZ digest and its complete
timeline/network denominators are pinned independently of the derived plan.

The defect has two coupled mechanisms:

* Cloak labels every WowGirls login pick ``login/submit``.  That is a broad
  classifier label, not an element type; role-first dispatch turned the email
  and password INPUTS into clicks.
* origin inference ran before deciding whether a pick was a login step.  Later
  player/download picks consequently manufactured waits for post-login media
  origins.  Reptyle has no login picks at all, so the old result was only one
  such CDN wait; its exact auth-host selector template is the independently
  curated fallback for the successful credential POST recorded by the capture.

No live site is contacted by this module.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest

BD_GATE_SCOPE = "module"


def _network_row(seq, timestamp, method, kind, status, url, location=None):
    headers = []
    if location is not None:
        headers.append({"name": "location", "value": location})
    return {
        "seq": seq,
        "timestamp": timestamp,
        "method": method,
        "type": kind,
        "response_status": status,
        "url": url,
        "response_headers": headers,
    }


_REPTYLE_TIMELINE_ROWS = [
    (1782744694043, "img.showpacity", "element", "img"),
    (1782744696654, "path:nth-child(1)", "element", "path"),
    (1782744698838, "path:nth-child(2)", "element", "path"),
    (1782744699279, "a:nth-child(30)", "download link", "a"),
    (1782744702238, "span:nth-child(2)", "element", "span"),
    (1782744717559, "div:nth-child(19)", "element", "div"),
    (1782744724716, "svg:nth-child(1)", "element", "svg"),
    (1782744727769,
     "button.vjs-icon-cog.theo-settings-control-button."
     "theo-controlbar-button.vjs-menu-button.vjs-menu-button-popup."
     "vjs-control.vjs-button", "element", "button"),
    (1782744729238, "div:nth-child(19)", "element", "div"),
    (1782744735159, "div:nth-child(19)", "element", "div"),
    (1782744737763,
     "button.vjs-icon-cog.theo-settings-control-button."
     "theo-controlbar-button.vjs-menu-button.vjs-menu-button-popup."
     "vjs-control.vjs-button", "element", "button"),
    (1782744739812, "span:nth-child(2)", "quality select", "span"),
    (1782744741687, "li.theo-menu-item.vjs-menu-item", "quality select", "li"),
    (1782744787090, "div.touch-grid-item.pause-area", "element", "div"),
    (1782744792459, "svg:nth-child(1)", "element", "svg"),
    (1782744797111, "div:nth-child(19)", "element", "div"),
    (1782744799137, "div:nth-child(19)", "element", "div"),
    (1782744800667, "body.notHomePage", "element", "body"),
    (1782744855964, "div.theoplayer-chapterbar", "play button", "div"),
    (1782744873913, "img.showpacity", "element", "img"),
    (1782744889840, "img.showpacity", "element", "img"),
    (1782744892043, "svg:nth-child(1)", "element", "svg"),
    (1782744893542, "div.touch-grid-item.pause-area", "element", "div"),
    (1782745021697, "div:nth-child(19)", "element", "div"),
    (1782745039731, "div:nth-child(19)", "element", "div"),
    (1782745062623, "div.transparent-dark-bg", "element", "div"),
    (1782745064236, "div:nth-child(19)", "element", "div"),
    (1782745094735, "circle:nth-child(1)", "element", "circle"),
    (1782745146427, "div:nth-child(19)", "element", "div"),
    (1782745171139, "div:nth-child(19)", "element", "div"),
    (1782745171871, "div.search-bar", "element", "div"),
    (1782745172927, "div:nth-child(19)", "element", "div"),
    (1782745174238, "div:nth-child(19)", "element", "div"),
    (1782745175250, "div:nth-child(19)", "element", "div"),
    (1782745176134, "div:nth-child(19)", "element", "div"),
    (1782745177207, "div:nth-child(19)", "element", "div"),
]

_REPTYLE_CAPTURE = {
    "source": "auth.reptyle.com_0b60f1ec_20260629_145050_52e5.wacz",
    "source_sha256":
        "809d34c22aa8196f1fa72e6538c7508ab1de7991ab3bab43c48f8404a241754d",
    "captured_at": "2026-06-29T14:50:51.001368+00:00",
    "source_action_timeline_count": 36,
    "source_network_log_count": 844,
    "action_timeline": [
        {"ts": ts, "selector": selector, "role": role, "tag": tag}
        for ts, selector, role, tag in _REPTYLE_TIMELINE_ROWS
    ],
    "network_log": [
        _network_row(0, 1782744652200, "GET", "document", 200,
                     "https://auth.reptyle.com/oauth/login"),
        _network_row(84, 1782744673210, "POST", "redirect", 302,
                     "https://auth.reptyle.com/oauth/login-user",
                     "https://auth.reptyle.com"),
        _network_row(85, 1782744674095, "GET", "redirect", 302,
                     "https://auth.reptyle.com/",
                     "https://app.reptyle.com/"),
        _network_row(91, 1782744674259, "GET", "document", 200,
                     "https://app.reptyle.com/"),
        _network_row(135, 1782744676824, "POST", "fetch", 200,
                     "https://identitytoolkit.googleapis.com/v1/"
                     "accounts:signInWithCustomToken"),
        _network_row(143, 1782744677207, "POST", "fetch", 200,
                     "https://identitytoolkit.googleapis.com/v1/"
                     "accounts:lookup"),
        # A later operator download opened as a document.  It is post-login
        # evidence, not a login-flow origin, and must never become await_url.
        _network_row(341, 1782744699393, "GET", "document", 200,
                     "https://vod1.cachefly.net/vod/reptyle/full.mp4"),
    ],
    "observed_authorization_requests": {
        "api2.reptyle.com": 51,
        "ma-store.reptyle.com": 43,
    },
    "observed_storage_keys": [
        "firebase:authUser:<redacted>:[DEFAULT]",
        "firebase:host:tms-fuel-1-ma-a1420.firebaseio.com",
    ],
}


_WOWGIRLS_CAPTURE = {
    "source": "auth.wowgirls.com_d9f19e92_20260728_011245_0917.wacz",
    "source_sha256":
        "7e468d6c47aab518f7efc736c40b36eeff836803559485736f65ee2882339b53",
    "captured_at": "2026-07-28T01:12:45.444583+00:00",
    "source_action_timeline_count": 8,
    "source_network_log_count": 234,
    "action_timeline": [
        {"ts": 1785201280237, "selector": "#user-email",
         "role": "login/submit", "tag": "input",
         "excerpt": '<input type="text" name="username" id="user-email">'},
        {"ts": 1785201285961, "selector": "#user-email",
         "role": "login/submit", "tag": "input",
         "excerpt": '<input type="text" name="username" id="user-email">'},
        {"ts": 1785201309409, "selector": "#user-password",
         "role": "login/submit", "tag": "input",
         "excerpt": '<input type="password" name="password" id="user-password">'},
        {"ts": 1785201311604,
         "selector": "div.loginform-submit-button",
         "role": "login/submit", "tag": "div",
         "excerpt": '<div class="loginform-submit-button">Get Inside</div>'},
        {"ts": 1785201322312, "selector": "video.jw-video.jw-reset",
         "role": "media element", "tag": "video"},
        {"ts": 1785201325936, "selector": "span.title",
         "role": "element", "tag": "span"},
        {"ts": 1785201328668,
         "selector": "div.jw-icon.jw-icon-settings",
         "role": "element", "tag": "div"},
        {"ts": 1785201330141,
         "selector": "button.jw-settings-content-item",
         "role": "quality select", "tag": "button"},
    ],
    "network_log": [
        _network_row(0, 1785201166419, "GET", "document", 200,
                     "https://auth.wowgirls.com/login"),
        _network_row(20, 1785201311620, "POST", "redirect", 302,
                     "https://auth.wowgirls.com/login", "/user/return"),
        _network_row(21, 1785201311761, "GET", "redirect", 302,
                     "https://auth.wowgirls.com/user/return",
                     "https://venus.wowgirls.com?token=<scrubbed>"),
        _network_row(22, 1785201311828, "GET", "redirect", 302,
                     "https://venus.wowgirls.com/",
                     "https://venus.wowgirls.com/"),
        _network_row(23, 1785201312008, "GET", "document", 200,
                     "https://venus.wowgirls.com/"),
        # Later post-login page/media documents are negative noise controls.
        _network_row(163, 1785201320123, "GET", "document", 200,
                     "https://venus.wowgirls.com/film/example"),
        _network_row(226, 1785201325948, "GET", "document", 200,
                     "https://content-video2.wowgirls.com/download/example.mp4"),
    ],
    "observed_origin_count": 17,
    # State continuity is explicitly NOT OBSERVED in this capture.  These are
    # the complete header-name counts over all 17 recorded origins, not a claim
    # that continuity existed because the redirect happened to succeed.
    "observed_state_header_counts": {
        "Cookie": 0,
        "Set-Cookie": 0,
        "Authorization": 0,
    },
}


class _Page:
    def __init__(self):
        self.calls = []

    def wait_for_url(self, url, **_kwargs):
        self.calls.append(("await_url", url))

    class _Locator:
        def __init__(self, page, selector):
            self.page = page
            self.selector = selector

        @property
        def first(self):
            return self

        def fill(self, value, **_kwargs):
            self.page.calls.append(("fill", self.selector, value))

        def click(self, **_kwargs):
            self.page.calls.append(("click", self.selector))

    def locator(self, selector):
        return self._Locator(self, selector)


def _assert_executable_plan(monkeypatch, flow, expected_actions, expected_calls):
    from bulk_downloader import login_flow_recorder as lfr
    from bulk_downloader import macro_recorder as mr

    actions = flow["actions"]
    credential = [a for a in actions
                  if a.get("kind") == "type" and a.get("credential")]
    secrets = [a for a in actions
               if a.get("kind") == "type" and a.get("secret")]
    submits = [a for a in actions if a.get("kind") == "click"]
    assert len(credential) == 1, (
        "derived login plan does not type exactly one credential field: %r"
        % actions)
    assert len(secrets) == 1, (
        "derived login plan does not type exactly one vault-backed password: %r"
        % actions)
    assert len(submits) == 1, (
        "derived login plan does not click exactly one submit control: %r"
        % actions)
    assert actions == expected_actions, (
        "derived plan diverges from the successful capture sequence")

    plan = lfr.plan_login_flow(flow, username="capture-user")
    monkeypatch.setattr(mr, "_resolve_secret_for",
                        lambda _site_id: "capture-password")
    page = _Page()
    result = mr.replay_macro(page, {"actions": plan},
                             site_id="capture-fixture", strict=True)
    assert len(expected_calls) > 0, "precondition: expected drive is empty"
    assert result["ok"], result
    assert result["executed"] == len(expected_calls), result
    assert page.calls == expected_calls


def test_reptyle_capture_derives_its_own_successful_login_drive(monkeypatch):
    """Reptyle is one complete proof; no WowGirls evidence is borrowed."""
    from bulk_downloader import login_flow_recorder as lfr
    from bulk_downloader import login_templates_data as ltd
    from bulk_downloader.macro_recorder import VAULT_MARKER

    cap = _REPTYLE_CAPTURE
    assert cap["source_sha256"] == (
        "809d34c22aa8196f1fa72e6538c7508ab1de7991ab3bab43c48f8404a241754d")
    assert cap["captured_at"] == "2026-06-29T14:50:51.001368+00:00"
    assert cap["source_action_timeline_count"] == 36
    assert len(cap["action_timeline"]) == 36
    assert cap["source_network_log_count"] == 844
    assert len(cap["network_log"]) == 7
    assert not [e for e in cap["action_timeline"]
                if e["role"].startswith("login")], (
        "precondition: Reptyle's capture must retain its zero-login-pick shape")

    observed = [(e["seq"], e["method"], e["response_status"],
                 urlsplit(e["url"]).netloc)
                for e in cap["network_log"] if e["seq"] in {84, 85, 91, 135, 143}]
    assert observed == [
        (84, "POST", 302, "auth.reptyle.com"),
        (85, "GET", 302, "auth.reptyle.com"),
        (91, "GET", 200, "app.reptyle.com"),
        (135, "POST", 200, "identitytoolkit.googleapis.com"),
        (143, "POST", 200, "identitytoolkit.googleapis.com"),
    ], "precondition: the successful POST/redirect/IdP sequence changed"
    assert cap["observed_authorization_requests"] == {
        "api2.reptyle.com": 51, "ma-store.reptyle.com": 43}
    assert sum(cap["observed_authorization_requests"].values()) == 94
    assert all(n > 0 for n in cap["observed_authorization_requests"].values())
    assert sum(k.startswith("firebase:authUser:")
               for k in cap["observed_storage_keys"]) == 1

    suggestions = ltd.suggest_login_for_url(cap["network_log"][0]["url"])
    assert suggestions and suggestions[0] == "login_reptyle", suggestions
    selectors = ltd.get_login_template(suggestions[0])["login"]
    expected = [
        {"kind": "type", "selector": selectors["user_field"][0],
         "text": "", "credential": True},
        {"kind": "type", "selector": selectors["pass_field"][0],
         "text": VAULT_MARKER, "secret": True},
        {"kind": "click", "selector": selectors["submit_btn"][0]},
        {"kind": "await_url", "url": "https://app.reptyle.com/*"},
    ]
    flow = lfr.derive_login_flow(cap["action_timeline"],
                                 network_log=cap["network_log"],
                                 name="reptyle-capture")
    _assert_executable_plan(monkeypatch, flow, expected, [
        ("fill", selectors["user_field"][0], "capture-user"),
        ("fill", selectors["pass_field"][0], "capture-password"),
        ("click", selectors["submit_btn"][0]),
        ("await_url", "https://app.reptyle.com/*"),
    ])


def test_wowgirls_capture_derives_its_own_successful_login_drive(monkeypatch):
    """WowGirls is a second proof; state continuity is NOT OBSERVED here."""
    from bulk_downloader import login_flow_recorder as lfr
    from bulk_downloader.macro_recorder import VAULT_MARKER

    cap = _WOWGIRLS_CAPTURE
    assert cap["source_sha256"] == (
        "7e468d6c47aab518f7efc736c40b36eeff836803559485736f65ee2882339b53")
    assert cap["captured_at"] == "2026-07-28T01:12:45.444583+00:00"
    assert cap["source_action_timeline_count"] == 8
    assert len(cap["action_timeline"]) == 8
    assert cap["source_network_log_count"] == 234
    assert len(cap["network_log"]) == 7
    assert sum(e["selector"] == "#user-email"
               for e in cap["action_timeline"]) == 2
    assert sum('type="password"' in e.get("excerpt", "")
               for e in cap["action_timeline"]) == 1

    observed = [(e["seq"], e["method"], e["response_status"],
                 urlsplit(e["url"]).netloc)
                for e in cap["network_log"] if e["seq"] in {20, 21, 22, 23}]
    assert observed == [
        (20, "POST", 302, "auth.wowgirls.com"),
        (21, "GET", 302, "auth.wowgirls.com"),
        (22, "GET", 302, "venus.wowgirls.com"),
        (23, "GET", 200, "venus.wowgirls.com"),
    ], "precondition: the successful three-hop redirect sequence changed"

    # Do not infer continuity from that redirect.  The complete capture has 17
    # origins and zero Cookie/Set-Cookie/Authorization observations on all of
    # them, so the honest continuity verdict for this session is NOT OBSERVED.
    assert cap["observed_origin_count"] == 17
    assert cap["observed_state_header_counts"] == {
        "Cookie": 0, "Set-Cookie": 0, "Authorization": 0}
    assert sum(cap["observed_state_header_counts"].values()) == 0

    expected = [
        {"kind": "type", "selector": "#user-email",
         "text": "", "credential": True},
        {"kind": "type", "selector": "#user-password",
         "text": VAULT_MARKER, "secret": True},
        {"kind": "click", "selector": "div.loginform-submit-button"},
        {"kind": "await_url", "url": "https://venus.wowgirls.com/*"},
    ]
    flow = lfr.derive_login_flow(cap["action_timeline"],
                                 network_log=cap["network_log"],
                                 name="wowgirls-capture")
    _assert_executable_plan(monkeypatch, flow, expected, [
        ("fill", "#user-email", "capture-user"),
        ("fill", "#user-password", "capture-password"),
        ("click", "div.loginform-submit-button"),
        ("await_url", "https://venus.wowgirls.com/*"),
    ])


def test_capture_oracle_rejects_the_role_collapsed_empty_form_plan(monkeypatch):
    """Negative control: the exact old WowGirls plan fails for its real reason."""
    broken = {"actions": [
        {"kind": "click", "selector": "#user-email"},
        {"kind": "click", "selector": "#user-email"},
        {"kind": "click", "selector": "#user-password"},
        {"kind": "click", "selector": "div.loginform-submit-button"},
        {"kind": "await_url", "url": "https://venus.wowgirls.com/*"},
        {"kind": "await_url", "url": "https://content-video2.wowgirls.com/*"},
    ]}
    with pytest.raises(
            AssertionError,
            match="does not type exactly one credential field"):
        _assert_executable_plan(monkeypatch, broken, [], [])


def test_post_login_picks_are_outside_the_login_step_and_origin_denominator():
    """Exactly four later player/media picks must fire zero derived steps."""
    from bulk_downloader import login_flow_recorder as lfr

    cap = _WOWGIRLS_CAPTURE
    later_picks = cap["action_timeline"][4:]
    assert len(later_picks) == 4, (
        "precondition: the post-login pick denominator is not four")
    assert all(not e["role"].startswith("login") for e in later_picks)
    flow = lfr.derive_login_flow(cap["action_timeline"],
                                 network_log=cap["network_log"])
    selectors = [a.get("selector") for a in flow["actions"]
                 if a.get("selector")]
    assert len(flow["actions"]) == 4, flow
    assert sum(selector in selectors for selector in (
        "video.jw-video.jw-reset",
        "span.title",
        "div.jw-icon.jw-icon-settings",
        "button.jw-settings-content-item",
    )) == 0, "a post-login pick entered the derived login-step denominator"
    assert all("content-video2.wowgirls.com" not in a.get("url", "")
               for a in flow["actions"]), (
        "a later media document entered the login-origin denominator")


def test_template_fallback_requires_success_and_an_exact_auth_host():
    """Over-correction control: no success or only a sister host means no plan."""
    from bulk_downloader import login_flow_recorder as lfr

    failed_login = [
        _network_row(0, 100, "GET", "document", 200,
                     "https://auth.reptyle.com/oauth/login"),
        _network_row(1, 200, "POST", "xhr", 200,
                     "https://auth.reptyle.com/oauth/login-user"),
        _network_row(2, 300, "GET", "document", 200,
                     "https://app.reptyle.com/"),
    ]
    sister_host = [
        _network_row(0, 100, "GET", "document", 200,
                     "https://login.reptyle.com/login"),
        _network_row(1, 200, "POST", "redirect", 302,
                     "https://login.reptyle.com/login"),
        _network_row(2, 300, "GET", "document", 200,
                     "https://app.reptyle.com/"),
    ]
    refused = [
        lfr.derive_login_flow([], network_log=failed_login)["actions"],
        lfr.derive_login_flow([], network_log=sister_host)["actions"],
    ]
    assert len(refused) == 2, "precondition: both refusal arms must execute"
    assert refused == [[], []], (
        "template fallback invented a plan without a captured success or an "
        "exact-host selector template: %r" % refused)


def test_transform_control_imports_the_deriver_without_judging_its_behaviour():
    """Mutation transform control: import the subject, assert no row behavior."""
    from bulk_downloader import login_flow_recorder as lfr

    assert lfr.__name__ == "bulk_downloader.login_flow_recorder"
