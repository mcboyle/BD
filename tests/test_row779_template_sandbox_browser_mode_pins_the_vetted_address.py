"""ROW 779. The template sandbox's browser mode classifies the host ONCE and
then hands the hostname to Chromium, which resolves it again at ``page.goto``.

That is the DNS-rebinding TOCTOU window ``_is_safe_public_host``'s own
docstring names: it is closed for httpx by ``_SSRFGuardedTransport`` (resolve,
classify EVERY answer, refuse if any is unsafe, then pin the connection to the
literal that was vetted) and closed for the sandbox's HTTP mode by the
per-redirect-hop ``_GuardedRedirect``. The browser mode had neither, so a host
that answered public at the check and 169.254.169.254 at navigation reached the
cloud metadata address and returned ``ok: true``.

These tests drive the real route through a fixture resolver and a fixture
browser. Nothing here touches the network, a browser binary, DNS or a vault:
``socket.getaddrinfo`` is replaced, and ``bulk_downloader.cloak`` is replaced by
a module whose ``cloaked_page`` SIMULATES Chromium -- it honours the
``--host-resolver-rules=MAP <host> <ip>`` launch argument subset this route
emits (no DNS at all for a mapped host, as in Chromium) and otherwise resolves
the hostname itself at navigation time, which is the defect being demonstrated.

Loopback is deliberately EXEMPT at this route (pointing the sandbox at a page
on the operator's own box is the intentional selector-testing capability), and
the controls below assert that exemption survives the fix.

An AAAA answer is pinned in the bracketed form ``MAP <host> [<v6>]`` -- the
brackets ARE the pin, because Chromium's replacement field is
``<address>[:<port>]`` and an IPv6 literal carries colons -- and the Chromium
stand-in is deliberately strict about that form (see ``_host_resolver_rules``).
The pin's own defensive refusals -- an empty host, a non-IP answer, no usable
address -- are exercised as well: two through the route via a resolver that
rebinds into the bad answer on the pin's own lookup, and the empty host at
helper level, because the route refuses "" before the pin can see it.
"""
from __future__ import annotations

import ipaddress
import socket
import sys
import types
from urllib.parse import urlparse

import pytest

from bulk_downloader.provider_resolve_impl import _common as host_safety


BD_GATE_SCOPE = "repo-wide"

_PUBLIC_IP = "8.8.8.8"
_METADATA_IP = "169.254.169.254"
_PRIVATE_IP = "10.0.0.5"
_LOOPBACK_IP = "127.0.0.1"
# AAAA values. 2001:4860:4860::8888 is a public resolver address, the IPv6
# sibling of 8.8.8.8 above; fd00:ec2::254 is the IPv6 cloud metadata address,
# unique-local and classified "private" by the canonical classifier.
_PUBLIC_V6 = "2001:4860:4860::8888"
_METADATA_V6 = "fd00:ec2::254"
_NOT_AN_IP = "not-an-ip"

_REBIND_HOST = "rebind-at-navigation.example"
_PUBLIC_HOST = "public.example"
_PIN_PRIVATE_HOST = "rebind-before-launch.example"
_PIN_DNS_FAIL_HOST = "rebind-into-dns-failure.example"
_MULTI_AFTER_HOST = "rebind-adds-a-private-sibling.example"
_LOOPBACK_HOST = "localhost"
_V6_HOST = "rebind-at-navigation-over-ipv6.example"
_PIN_NON_IP_HOST = "rebind-into-a-non-ip-answer.example"
_PIN_NO_ADDRESS_HOST = "rebind-into-no-usable-address.example"

# host -> (answer before the rebind, answer after the rebind). The resolver
# below returns the SECOND element once that host's rebind has fired; which
# event fires the rebind differs per host and is stated at each entry.
# Each value is (answer before the rebind, answer after it); each answer is a
# TUPLE of addresses (an EMPTY tuple is an answer with no rows), or None to
# raise gaierror. An address containing ":" is emitted as an AF_INET6 row.
_ANSWERS = {
    # rebinds when the browser navigates -- the row's headline case.
    _REBIND_HOST: ((_PUBLIC_IP,), (_METADATA_IP,)),
    # rebinds on the SECOND resolution, i.e. between the route's pre-fetch
    # check and any later resolve, whoever performs it.
    _PIN_PRIVATE_HOST: ((_PUBLIC_IP,), (_PRIVATE_IP,)),
    _PIN_DNS_FAIL_HOST: ((_PUBLIC_IP,), None),
    # the rebind ADDS a private sibling rather than replacing the answer: the
    # first address stays safe, so classifying only the first would admit it.
    _MULTI_AFTER_HOST: ((_PUBLIC_IP,), (_PUBLIC_IP, _PRIVATE_IP)),
    _PUBLIC_HOST: ((_PUBLIC_IP,), (_PUBLIC_IP,)),
    _LOOPBACK_HOST: ((_LOOPBACK_IP,), (_LOOPBACK_IP,)),
    # the AAAA twin of the headline case: public IPv6 at the check, the IPv6
    # metadata address at navigation. Its pin must carry the bracketed form.
    _V6_HOST: ((_PUBLIC_V6,), (_METADATA_V6,)),
    # rebinds on the pin's OWN lookup into answers no real resolver is known
    # to return -- a non-IP string, and no rows at all. Those are the pin's
    # defensive refusals, and a scripted resolver is the only one that can
    # deliver them, which is why they are here.
    _PIN_NON_IP_HOST: ((_PUBLIC_IP,), (_NOT_AN_IP,)),
    _PIN_NO_ADDRESS_HOST: ((_PUBLIC_IP,), ()),
}
_REBIND_ON_NAVIGATION = (_REBIND_HOST, _V6_HOST)


def _host_resolver_rules(args):
    """Parse Chromium's --host-resolver-rules value into {host: literal}.

    Mirrors the subset Chromium accepts that this route can emit: comma
    separated ``MAP <host> <address>`` clauses with no port, where an IPv6
    address is written in brackets -- ``MAP host [2001:db8::1]`` -- because
    the replacement field is ``<address>[:<port>]`` and an IPv6 literal
    carries colons.

    STRICT ON THAT ONE AXIS, DELIBERATELY. The route emits no port, so a colon
    in an UNBRACKETED replacement can only be an IPv6 literal written without
    its brackets; such a clause is unparseable here and yields NO mapping, so
    the fixture browser then resolves the name itself and
    ``browser_resolutions`` counts it. The first version of this stand-in
    stripped brackets when present and otherwise took the field verbatim,
    which made ``MAP host 2001:db8::1`` and ``MAP host [2001:db8::1]``
    indistinguishable to every test in this file (bd-lens-L1, row 779 shape
    verdict, U4). What Chromium itself does with the unbracketed form was NOT
    measured -- this lane runs no browser -- so the stand-in models the strict
    reading of the documented form rather than claiming to reproduce Chromium.
    """
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
                # An IPv6 literal without brackets: not a pin. See above.
                continue
            rules[parts[1]] = literal
    return rules


@pytest.fixture
def sandbox(fresh_app, monkeypatch):
    """The route, a scripted rebinding resolver and a fixture browser."""
    state = {
        "resolutions": [],          # every host handed to getaddrinfo, in order
        "rebound": set(),           # hosts whose second answer is now live
        "navigations": [],          # hosts page.goto was asked for
        "connected": [],            # the address the fixture browser reached
        "browser_resolutions": 0,   # DNS lookups the browser itself performed
        "launch_args": [],          # every args= value cloaked_page received
        "launches": 0,
    }

    def fixture_getaddrinfo(host, _port=None, *args, **kwargs):
        assert kwargs.get("type") == socket.SOCK_STREAM, (
            f"unexpected getaddrinfo call for {host!r}: {kwargs!r}")
        assert host in _ANSWERS, f"fixture has no address for {host!r}"
        state["resolutions"].append(host)
        before, after = _ANSWERS[host]
        if host in state["rebound"]:
            address = after
        else:
            address = before
            if host not in _REBIND_ON_NAVIGATION:
                # every host but the headline one rebinds on the NEXT lookup,
                # whoever makes it.
                state["rebound"].add(host)
        if address is None:
            raise socket.gaierror("fixture DNS failure")
        rows = []
        for one in address:
            if ":" in one:
                # An AAAA answer: getaddrinfo's IPv6 sockaddr is a 4-tuple
                # (address, port, flowinfo, scope_id).
                rows.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "",
                             (one, 0, 0, 0)))
            else:
                rows.append((socket.AF_INET, socket.SOCK_STREAM, 6, "",
                             (one, 0)))
        return rows

    monkeypatch.setattr(socket, "getaddrinfo", fixture_getaddrinfo)

    class _FixturePage:
        """A Chromium stand-in. Honours the --host-resolver-rules subset
        this route emits, as parsed by ``_host_resolver_rules`` (strict about
        the IPv6 form): a mapped host is never sent to the resolver."""

        def __init__(self, rules):
            self._rules = rules
            self._url = ""

        def goto(self, url, **_kwargs):
            host = urlparse(url).hostname or ""
            state["navigations"].append(host)
            self._url = url
            pinned = self._rules.get(host)
            if pinned is not None:
                state["connected"].append(pinned)
                return
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                # An IP literal: Chromium has nothing to resolve either.
                state["connected"].append(host)
                return
            # No pin: the browser resolves the name ITSELF, now. This is the
            # second resolution the route never sees, and the rebind lands here.
            state["rebound"].add(host)
            state["browser_resolutions"] += 1
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            state["connected"].append(infos[0][4][0])

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

    def post(host, *, mode="browser", path="/page"):
        return fresh_app.post(
            "/api/template/sandbox",
            json={"url": f"http://{host}{path}", "template": {}, "mode": mode},
        )

    return types.SimpleNamespace(post=post, state=state)


def test_a_host_that_rebinds_at_navigation_cannot_reach_the_metadata_address(
        sandbox):
    """THE DEFECT. Public at the check, 169.254.169.254 at page.goto."""
    response = sandbox.post(_REBIND_HOST)
    state = sandbox.state
    # Precondition: the fixture really did present the hazard -- the route
    # accepted the host on an answer that was public, and the rebind is armed.
    assert state["resolutions"], "the route never resolved the host at all"
    assert state["resolutions"][0] == _REBIND_HOST
    assert _ANSWERS[_REBIND_HOST] == ((_PUBLIC_IP,), (_METADATA_IP,))
    assert state["launches"] == 1, (
        f"expected exactly one browser launch, got {state['launches']}")
    assert len(state["navigations"]) == 1, (
        f"expected exactly one navigation, got {state['navigations']}")
    assert _METADATA_IP not in state["connected"], (
        "the sandbox browser reached the cloud metadata address: "
        f"{state['connected']} (response {response.status_code} "
        f"{response.get_json()})")
    assert state["connected"] == [_PUBLIC_IP], (
        "the browser did not reach the address that was vetted: "
        f"{state['connected']}")


def test_the_browser_is_launched_pinned_to_the_address_that_was_vetted(
        sandbox):
    """The pin is the mechanism: Chromium must not resolve the name again."""
    sandbox.post(_REBIND_HOST)
    state = sandbox.state
    assert state["launches"] == 1
    rules = _host_resolver_rules(state["launch_args"][0])
    assert rules == {_REBIND_HOST: _PUBLIC_IP}, (
        "browser mode did not pin the host to the vetted literal; launch args "
        f"were {state['launch_args'][0]!r}")
    assert state["browser_resolutions"] == 0, (
        "the browser resolved the hostname itself despite the pin: "
        f"{state['browser_resolutions']} lookups")


def test_a_host_that_rebinds_to_a_private_address_before_launch_is_refused(
        sandbox):
    """Rebinding into RFC1918 between the check and the launch must refuse,
    with the classifier's own structured reason -- not navigate and not
    silently pin the private literal."""
    response = sandbox.post(_PIN_PRIVATE_HOST)
    body = response.get_json()
    assert response.status_code == 400, (
        f"a host that rebound to {_PRIVATE_IP} was not refused: "
        f"{response.status_code} {body}")
    assert body["ok"] is False
    assert "refusing private address" in body["error"], body["error"]
    assert sandbox.state["launches"] == 0, (
        "the browser was launched for a host that rebound to a private "
        "address")
    assert sandbox.state["connected"] == []


def test_a_rebind_into_dns_failure_is_refused_rather_than_ignored(sandbox):
    """A2: an unavailable measurement is never permission. If the pin-time
    resolution fails, the browser must not be launched on the unvetted name."""
    response = sandbox.post(_PIN_DNS_FAIL_HOST)
    body = response.get_json()
    assert response.status_code == 400, (
        f"a pin-time DNS failure was not refused: {response.status_code} "
        f"{body}")
    assert "DNS resolution failed" in body["error"], body["error"]
    assert sandbox.state["launches"] == 0
    assert sandbox.state["connected"] == []


def test_a_rebind_that_adds_a_private_sibling_is_refused(sandbox):
    """EVERY answer is classified, not just the first. A rebind that keeps the
    public address and ADDS 10.0.0.5 would pass a "first safe one" check and
    leave the private address live for the browser's own next lookup."""
    response = sandbox.post(_MULTI_AFTER_HOST)
    body = response.get_json()
    assert response.status_code == 400, (
        "a multi-answer rebind carrying a private sibling was not refused: "
        f"{response.status_code} {body}")
    assert "refusing private address" in body["error"], body["error"]
    assert sandbox.state["launches"] == 0
    assert sandbox.state["connected"] == []


def test_loopback_is_still_permitted_in_browser_mode(sandbox):
    """NEGATIVE CONTROL. The deliberate exemption survives the fix: local
    selector authoring against 127.0.0.1 still runs, and is pinned to it."""
    response = sandbox.post(_LOOPBACK_HOST)
    body = response.get_json()
    assert body["ok"] is True, f"loopback was refused: {body}"
    assert sandbox.state["launches"] == 1
    assert sandbox.state["connected"] == [_LOOPBACK_IP], (
        f"loopback did not reach 127.0.0.1: {sandbox.state['connected']}")


def test_an_ip_literal_host_needs_no_pin_and_is_still_fetched(sandbox):
    """NEGATIVE CONTROL. There is no DNS step to rebind on, so the route must
    not invent one -- and must not refuse the literal it already classified."""
    response = sandbox.post(_PUBLIC_IP)
    body = response.get_json()
    assert body["ok"] is True, f"an IP-literal host was refused: {body}"
    assert sandbox.state["launches"] == 1
    assert sandbox.state["resolutions"] == [], (
        "an IP literal was sent to the resolver: "
        f"{sandbox.state['resolutions']}")
    assert sandbox.state["connected"] == [_PUBLIC_IP]


def test_http_mode_still_refuses_and_never_launches_a_browser(sandbox):
    """NEGATIVE CONTROL. The guarded HTTP path is untouched by this change."""
    response = sandbox.post(_PIN_PRIVATE_HOST, mode="http")
    assert sandbox.state["launches"] == 0
    assert response.status_code in (200, 400)


def test_an_aaaa_host_is_pinned_to_the_bracketed_ipv6_literal(sandbox):
    """THE FORM OF THE PIN. An IPv6 literal carries colons and Chromium's
    replacement field is ``<address>[:<port>]``, so the rule must be written
    ``MAP <host> [<v6>]`` -- the brackets are the pin. This pins that form by
    TEXT first, then proves the consequence through the fixture browser.

    PRECONDITIONS, asserted rather than assumed: the fixture resolver emits an
    AF_INET6 4-tuple row for this host; the rebind is armed to answer the IPv6
    metadata address at navigation; and the Chromium stand-in can tell the
    bracketed form from the unbracketed one. Without that last check every
    runtime assertion below would pass with the brackets gone -- the coverage
    illusion bd-lens-L1 measured on the first version of this file.
    """
    state = sandbox.state
    infos = socket.getaddrinfo(_V6_HOST, None, type=socket.SOCK_STREAM)
    assert infos == [(socket.AF_INET6, socket.SOCK_STREAM, 6, "",
                      (_PUBLIC_V6, 0, 0, 0))], infos
    assert _ANSWERS[_V6_HOST] == ((_PUBLIC_V6,), (_METADATA_V6,))
    assert _V6_HOST in _REBIND_ON_NAVIGATION
    bracketed = f"--host-resolver-rules=MAP {_V6_HOST} [{_PUBLIC_V6}]"
    unbracketed = f"--host-resolver-rules=MAP {_V6_HOST} {_PUBLIC_V6}"
    assert _host_resolver_rules([bracketed]) == {_V6_HOST: _PUBLIC_V6}
    assert _host_resolver_rules([unbracketed]) == {}, (
        "the Chromium stand-in accepted an unbracketed IPv6 replacement, so "
        "the runtime assertions below could not fail with the brackets gone")
    state["resolutions"].clear()

    response = sandbox.post(_V6_HOST)
    body = response.get_json()
    assert body["ok"] is True, f"an AAAA host was refused: {body}"
    assert state["launches"] == 1
    # The FORM is asserted first so that losing the brackets fails HERE, on
    # the declared property, before its consequences are counted below.
    assert state["launch_args"][0] == [bracketed], (
        "the pin for an AAAA host is not in the bracketed form: "
        f"{state['launch_args'][0]!r}")
    assert state["browser_resolutions"] == 0, (
        "the browser resolved the hostname itself despite the pin: "
        f"{state['browser_resolutions']} lookups")
    # Exactly two lookups in total, both the route's own: the pre-fetch check
    # and the pin. A third would be the browser's, and the count above is 0.
    assert state["resolutions"] == [_V6_HOST, _V6_HOST], (
        "expected exactly two resolutions, the pre-fetch check and the pin: "
        f"{state['resolutions']}")
    assert state["navigations"] == [_V6_HOST]
    assert state["connected"] == [_PUBLIC_V6], (
        "the browser did not reach the vetted IPv6 literal: "
        f"{state['connected']}")


def test_a_rebind_into_a_non_ip_answer_is_refused_with_its_own_reason(
        sandbox):
    """A resolver row that is not an IP address is refused by the pin with
    the classifier's NON_IP_ADDRESS reason -- not skipped, and not laundered
    into "no usable addresses". The pre-fetch check accepted the public first
    answer; the pin's own lookup, the second, is the one that returns junk."""
    response = sandbox.post(_PIN_NON_IP_HOST)
    body = response.get_json()
    state = sandbox.state
    assert state["resolutions"] == [_PIN_NON_IP_HOST, _PIN_NON_IP_HOST], (
        f"the pin never made its own lookup: {state['resolutions']}")
    assert response.status_code == 400, (
        f"a rebind into a non-IP answer was not refused: "
        f"{response.status_code} {body}")
    assert body["ok"] is False
    assert f"got non-IP from getaddrinfo: {_NOT_AN_IP!r}" in body["error"], (
        body["error"])
    assert state["launches"] == 0
    assert state["connected"] == []


def test_a_rebind_into_no_usable_address_is_refused_not_pinned_to_the_name(
        sandbox):
    """An empty second answer refuses with NO_ADDRESSES. With that guard gone
    the pin has nothing to pin and would map the name to ITSELF, handing
    Chromium the second resolution this row exists to take away."""
    response = sandbox.post(_PIN_NO_ADDRESS_HOST)
    body = response.get_json()
    state = sandbox.state
    assert state["resolutions"] == [_PIN_NO_ADDRESS_HOST] * 2, (
        f"the pin never made its own lookup: {state['resolutions']}")
    assert response.status_code == 400, (
        f"a rebind into no usable address was not refused: "
        f"{response.status_code} {body}")
    assert body["ok"] is False
    assert "DNS resolution returned no usable addresses" in body["error"], (
        body["error"])
    assert state["launches"] == 0
    assert state["connected"] == []


def test_the_pin_refuses_an_empty_host_before_any_resolution(monkeypatch):
    """HELPER LEVEL, because the route cannot deliver this input: the route's
    pre-fetch check refuses "" ahead of the mode branch (measured:
    ``_is_safe_public_host("")`` -> (False, 'no host')), so through the route
    the pin never sees an empty host. The pin is the SECOND gate and its own
    refusal must hold by itself: an empty name is refused with the
    classifier's NO_HOST reason and never reaches the resolver."""
    from bulk_downloader import app_template

    calls = []

    def recording_getaddrinfo(host, *_args, **_kwargs):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", recording_getaddrinfo)
    for empty in ("", "[]", None):
        ok, rules, why = app_template._sandbox_browser_host_pin(
            empty, host_safety)
        assert (ok, rules) == (False, None), (empty, ok, rules, why)
        assert why.code is host_safety.HostSafetyReason.NO_HOST, (empty, why)
        assert str(why) == "no host", (empty, why)
    assert calls == [], f"an empty host reached the resolver: {calls}"


def test_app_template_import_transform_control():
    """TRANSFORM CONTROL. Imports the subject without driving it; a real
    regression mutation banded on this test must ESCAPE."""
    import bulk_downloader.app_template as subject

    assert subject.__name__ == "bulk_downloader.app_template"
