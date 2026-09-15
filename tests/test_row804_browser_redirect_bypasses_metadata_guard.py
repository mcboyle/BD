"""ROW 804. FOLLOW-UP to row 779.

Row 779 pinned the template sandbox's browser mode to the address vetted at
the pre-fetch check, closing the DNS-rebind-at-``page.goto`` window for the
FIRST navigation. It covers nothing after that: a redirect, a JS
``window.location`` hop, or an XHR/fetch inside the SAME page is a fresh
request Chromium resolves and reaches on its own, because neither
``bulk_downloader/cloak.py`` nor the browser-mode branch of
``api_template_sandbox`` registered a ``page.route``/``.abort`` interception.
A public host at the pre-fetch check that then redirects (or navigates) to
``http://169.254.169.254/latest/meta-data/`` therefore reaches the cloud
metadata address, and the route returns ``ok: true`` with the metadata body.

These tests drive the real route through a fixture resolver and a fixture
browser. Nothing here touches the network, a browser binary, DNS or a vault.
The fixture browser SIMULATES a same-page redirect: after the pinned initial
``page.goto`` lands, it visits one more URL on the SAME page object, through
whatever ``page.route`` handler the route registered (if any) -- exactly how
Chromium treats a redirect or a script-driven navigation on an existing page.
If the route never called ``page.route``, the hop is never checked at all,
which is the defect this file demonstrates.
"""
from __future__ import annotations

import socket
import sys
import types
from urllib.parse import urlparse

import pytest

from bulk_downloader.provider_resolve_impl import _common as host_safety


BD_GATE_SCOPE = "repo-wide"

_PUBLIC_IP = "8.8.8.8"
_METADATA_IP = "169.254.169.254"
_SAFE_SECOND_IP = "93.184.216.35"
_INITIAL_HOST = "public-initial.example"

_ADDRS = {_INITIAL_HOST: (_PUBLIC_IP,)}


def _host_resolver_rules(args):
    """Parse Chromium's --host-resolver-rules value into {host: literal}."""
    rules = {}
    for arg in args or []:
        if not str(arg).startswith("--host-resolver-rules="):
            continue
        for clause in str(arg).split("=", 1)[1].split(","):
            parts = clause.strip().split()
            if len(parts) != 3 or parts[0].upper() != "MAP":
                continue
            literal = parts[2]
            if literal.startswith("[") and literal.endswith("]"):
                literal = literal[1:-1]
            elif ":" in literal:
                continue
            rules[parts[1]] = literal
    return rules


@pytest.fixture
def sandbox(fresh_app, monkeypatch):
    """The route, a fixture resolver, and a fixture browser that simulates a
    same-page redirect after the pinned initial navigation lands."""
    state = {
        "resolutions": [],       # every host handed to getaddrinfo
        "navigations": [],       # hosts page.goto was asked for
        "connected": [],         # every address the fixture reached (post-route)
        "routed_urls": [],       # every URL that reached the route() handler
        "route_registrations": 0,
        "browser_resolutions": 0,
        "launch_args": [],
        "launches": 0,
        "redirect_target": None,  # set per-test before posting
    }

    def fixture_getaddrinfo(host, _port=None, *_args, **kwargs):
        assert kwargs.get("type") == socket.SOCK_STREAM, (
            f"unexpected getaddrinfo call for {host!r}: {kwargs!r}")
        assert host in _ADDRS, f"fixture has no address for {host!r}"
        state["resolutions"].append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (one, 0))
                for one in _ADDRS[host]]

    monkeypatch.setattr(socket, "getaddrinfo", fixture_getaddrinfo)

    class _FixturePage:
        def __init__(self, rules):
            self._rules = rules
            self._url = ""
            self._route_handler = None

        def route(self, _pattern, handler):
            state["route_registrations"] += 1
            self._route_handler = handler

        def _resolve(self, host):
            pinned = self._rules.get(host)
            if pinned is not None:
                return pinned
            try:
                socket.inet_aton(host)
                return host
            except OSError:
                pass
            state["browser_resolutions"] += 1
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            return infos[0][4][0]

        def _visit(self, url):
            host = urlparse(url).hostname or ""
            ip = self._resolve(host)
            if self._route_handler is not None:
                state["routed_urls"].append(url)
                outcome = {}
                route = types.SimpleNamespace(
                    request=types.SimpleNamespace(url=url),
                    continue_=lambda: outcome.setdefault("action", "continue"),
                    abort=lambda: outcome.setdefault("action", "abort"),
                )
                self._route_handler(route)
                if outcome.get("action") == "abort":
                    raise RuntimeError(f"net::ERR_ABORTED at {url}")
            state["connected"].append(ip)
            self._url = url

        def goto(self, url, **_kwargs):
            state["navigations"].append(urlparse(url).hostname or "")
            self._visit(url)
            target = state["redirect_target"]
            if target is not None:
                # Same-page redirect / script navigation to a fresh host --
                # Chromium's launch-time pin never covers this hop.
                self._visit(f"http://{target}/latest/meta-data/")

        def wait_for_timeout(self, _ms):
            return None

        def content(self):
            return "<html><body>fixture</body></html>"

        @property
        def url(self):
            return self._url

    class _FixtureBrowser:
        def __init__(self, args):
            self._page = _FixturePage(_host_resolver_rules(args))

        def __enter__(self):
            return self._page

        def __exit__(self, *_exc):
            return False

    def fixture_cloaked_page(*, args=None, **_kwargs):
        state["launches"] += 1
        state["launch_args"].append(args)
        return _FixtureBrowser(args)

    fake_cloak = types.ModuleType("bulk_downloader.cloak")
    fake_cloak.cloaked_page = fixture_cloaked_page
    monkeypatch.setitem(sys.modules, "bulk_downloader.cloak", fake_cloak)
    import bulk_downloader
    monkeypatch.setattr(bulk_downloader, "cloak", fake_cloak, raising=False)

    def post(*, redirect_target, host=_INITIAL_HOST):
        state["redirect_target"] = redirect_target
        return fresh_app.post(
            "/api/template/sandbox",
            json={"url": f"http://{host}/page", "template": {},
                  "mode": "browser"},
        )

    return types.SimpleNamespace(post=post, state=state)


def test_a_redirect_to_the_metadata_address_after_the_pinned_navigation_is_refused(
        sandbox):
    """THE DEFECT. The initial host is public and pinned; a same-page hop
    (redirect / script navigation) then reaches 169.254.169.254 -- a fresh
    connection the launch pin does not cover -- and must be refused."""
    response = sandbox.post(redirect_target=_METADATA_IP)
    state = sandbox.state
    # Precondition: the fixture really did present the hazard -- the browser
    # was launched, pinned to the initial host, and a second hop toward the
    # metadata address actually reached the route handler.
    assert state["launches"] == 1, (
        f"expected exactly one browser launch, got {state['launches']}")
    assert state["routed_urls"], "the fixture never simulated any routed hop"
    assert any(_METADATA_IP in u for u in state["routed_urls"]), (
        "the fixture never presented the metadata hop to the route handler: "
        f"{state['routed_urls']}")
    assert state["route_registrations"] == 1, (
        "the route never registered a page.route handler at all -- the "
        f"fixture could not have exercised the guard: {state}")
    # The verdict: the metadata address must never appear as REACHED.
    assert _METADATA_IP not in state["connected"], (
        "the sandbox browser reached the cloud metadata address via a "
        f"same-page redirect: {state['connected']} (response "
        f"{response.status_code} {response.get_json()})")
    body = response.get_json()
    assert body["ok"] is False, (
        f"a same-page redirect to the metadata address was not refused: {body}")


def test_a_redirect_to_a_second_safe_host_still_succeeds(sandbox):
    """NEGATIVE CONTROL. A redirect that lands on another PUBLIC host must
    still succeed -- the guard classifies per-hop, it does not refuse every
    hop after the first."""
    response = sandbox.post(redirect_target=_SAFE_SECOND_IP)
    state = sandbox.state
    # Precondition, same as above: the fixture built the redirect shape.
    assert state["routed_urls"], "the fixture never simulated any routed hop"
    assert len(state["routed_urls"]) == 2, (
        "expected exactly two routed hops (initial + redirect), got "
        f"{len(state['routed_urls'])}: {state['routed_urls']}")
    body = response.get_json()
    assert body["ok"] is True, f"a redirect to a second public host was refused: {body}"
    assert state["connected"] == [_PUBLIC_IP, _SAFE_SECOND_IP], (
        f"expected both hops to be reached in order: {state['connected']}")


def test_app_template_import_transform_control():
    """TRANSFORM CONTROL. Imports the subject without driving it; a real
    regression mutation banded on this test must ESCAPE."""
    import bulk_downloader.app_template as subject

    assert subject.__name__ == "bulk_downloader.app_template"
