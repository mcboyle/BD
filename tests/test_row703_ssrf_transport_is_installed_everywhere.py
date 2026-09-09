"""Row 703 -- every httpx client BD constructs is pinned through the guarded transport.

SUBJECT.  ``provider_resolve_impl._common._SSRFGuardedTransport`` closes the DNS
rebinding window (resolve in the transport, classify, connect to the vetted IP
literal) but was installed by ONE builder while every other
``httpx.Client(...)`` in the package was built bare and re-resolved at connect.
The fix is ``bulk_downloader/ssrf_transport.py``: ``guarded_transport(policy)``
is the one seam, and every construction passes ``transport=`` through it.

THIS GATE.  The population is derived from the TREE by
``tools/ssrf_client_census.py`` -- ``git ls-files`` for the file denominator and
``ast`` for the constructions -- never from the mutant spec it reconciles
against and never from a line grep (no construction carries ``transport=`` on
its own line).  It is asserted nonzero, both policies and the shared builder
must be present, and an unmeasurable population is UNKNOWN, which FAILS here.
RED on the base tree: ``FAIL: 30 of 31 httpx client constructions across 22
files are NOT pinned`` naming each ``file:line`` (the register's "31 across 21"
was a line grep that counted a docstring and missed ``_hx.Client``).

POLICIES, so the SUPERSET rule holds (this cut only ADDS refusals):
``PUBLIC_ONLY`` is the unchanged guard, installed where the site already
vets public-only on its executed path; ``PINNED`` keeps a site's base
admissibility (loopback/private/ULA/CGNAT stay admitted) and refuses only
addresses that can never be an HTTP peer, judged on every embedded IPv4 too.

Offline: literal-IP classification needs no DNS; hostname pinning is driven
through a fake ``getaddrinfo`` and a fake base transport, so the exact request
the socket layer would see is asserted without a network.  Fixture trees are
zero-entropy synthetic packages in a temporary git checkout.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import subprocess
import sys
from collections import Counter
from unittest.mock import MagicMock
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import ssrf_client_census as census_tool  # noqa: E402

SPEC = ROOT / "tests" / "mutants" / "row703_ssrf_transport_installation.json"


# --------------------------------------------------------------------------
# The tree gate
# --------------------------------------------------------------------------

def test_every_tracked_httpx_client_construction_is_pinned_through_the_seam():
    state, detail, result = census_tool.verdict(ROOT)
    assert result is not None, f"population unmeasured -> {state}: {detail}"
    # Preconditions: nonzero population; the shared builder is recognised
    # exactly once, so the rule can tell a pin from a bare construction.
    assert len(result.files) > 0 and len(result.constructions) > 0, (
        len(result.files), len(result.constructions))
    builders = [c for c in result.constructions if c.policy == "shared-builder"]
    assert [c.file for c in builders] == [census_tool.SHARED_BUILDER_FILE], builders
    assert state == census_tool.OK, f"{state}: {detail}"
    # Both policies are in use on the tree, so the seam's two arms are both live.
    assert {"pinned", "public-only"} <= {c.policy for c in result.constructions}


def test_the_unpin_spec_reconciles_to_the_tree_derived_population():
    """spec-count == call-site-count, with the call-site count derived from the
    tree and reconciled PER FILE, not read back from the spec."""
    doc = json.loads(SPEC.read_text(encoding="utf-8"))
    unpin = [m for m in doc["mutants"] if m["label"].startswith("unpin ")]
    result = census_tool.census(ROOT)
    assert len(result.constructions) > 0
    assert Counter(m["file"] for m in unpin) == Counter(result.per_file()), (
        "spec files != tree files: "
        f"{Counter(m['file'] for m in unpin) - Counter(result.per_file())} "
        f"| {Counter(result.per_file()) - Counter(m['file'] for m in unpin)}")
    assert len(unpin) == len(result.constructions)


# --------------------------------------------------------------------------
# Fixture trees: the RED shape, negative controls, and the UNKNOWN arms
# --------------------------------------------------------------------------

_PINNED_CORPUS = {
    # every recognised pinned form, one construction each
    "bulk_downloader/plain.py": (
        "import httpx\n"
        "from bulk_downloader.ssrf_transport import guarded_transport, PINNED\n"
        "def f():\n"
        "    with httpx.Client(timeout=1, transport=guarded_transport(PINNED)) as c:\n"
        "        return c\n"),
    "bulk_downloader/aliased.py": (
        "import httpx as _hx\n"
        "from bulk_downloader.ssrf_transport import guarded_transport as _gt, PUBLIC_ONLY as _po\n"
        "def f():\n"
        "    return _hx.AsyncClient(transport=_gt(_po))\n"),
    "bulk_downloader/module_alias.py": (
        "import httpx\n"
        "from bulk_downloader import ssrf_transport as st\n"
        "def f():\n"
        "    return httpx.Client(transport=st.guarded_transport(st.PINNED))\n"),
    "bulk_downloader/string_policy.py": (
        "import httpx\n"
        "from bulk_downloader.ssrf_transport import guarded_transport\n"
        "def f():\n"
        "    return httpx.Client(transport=guarded_transport(\"public-only\"))\n"),
    "bulk_downloader/provider_resolve_impl/_common.py": (
        "import httpx\n"
        "def _SSRFGuardedTransport_factory():\n"
        "    return httpx.HTTPTransport\n"
        "def _make_default_http_get():\n"
        "    def _http_get(url):\n"
        "        transport_cls = _SSRFGuardedTransport_factory()\n"
        "        transport = transport_cls()\n"
        "        with httpx.Client(transport=transport) as client:\n"
        "            return client\n"
        "    return _http_get\n"),
    "bulk_downloader/client_method.py": (
        # the request METHODS of a pinned client are not constructions: only the
        # Client and the module-level helpers (which build their own) count
        "import httpx\n"
        "from bulk_downloader.ssrf_transport import guarded_transport, owning_stream, PINNED\n"
        "def f(url):\n"
        "    c = httpx.Client(transport=guarded_transport(PINNED))\n"
        "    c.get(url); c.post(url); c.head(url); c.request('GET', url)\n"
        "    with c.stream('GET', url) as r:\n"
        "        return r\n"
        "def g(url):\n"
        "    with owning_stream(httpx.Client(transport=guarded_transport(PINNED)), 'GET', url) as r:\n"
        "        return r\n"),
}

# constructions in the pinned corpus: one per file, except client_method.py
# which constructs twice (a client whose methods are called, and one handed to
# owning_stream)
_PINNED_COUNT = len(_PINNED_CORPUS) + 1

_BARE_CORPUS = {
    # every bare counterpart; the line of each construction is asserted by name
    "bulk_downloader/bare_plain.py": "import httpx\ndef f():\n    return httpx.Client(timeout=1)\n",
    "bulk_downloader/bare_from.py": "from httpx import Client\ndef f():\n    return Client()\n",
    "bulk_downloader/bare_alias.py": "import httpx as _hx\ndef f():\n    return _hx.AsyncClient()\n",
    "bulk_downloader/bare_splat.py": "import httpx\ndef f(kw):\n    return httpx.Client(**kw)\n",
    "bulk_downloader/bare_other_transport.py": (
        "import httpx\ndef f():\n    return httpx.Client(transport=httpx.HTTPTransport())\n"),
    "bulk_downloader/bare_no_policy.py": (
        "import httpx\nfrom bulk_downloader.ssrf_transport import guarded_transport\n"
        "def f():\n    return httpx.Client(transport=guarded_transport())\n"),
    "bulk_downloader/bare_builder_shape_elsewhere.py": (
        "import httpx\ndef _make_default_http_get():\n    transport_cls = object\n"
        "    transport = transport_cls()\n    return httpx.Client(transport=transport)\n"),
    "bulk_downloader/bare_rebound.py": "import httpx\nC = httpx.Client\ndef f():\n    return C()\n",
    # module-level helpers build a throwaway client with the DEFAULT transport
    # and accept no transport= at all: every one is an unpinned construction
    "bulk_downloader/bare_helper_get.py": "import httpx\ndef f(url):\n    return httpx.get(url, timeout=1)\n",
    "bulk_downloader/bare_helper_stream_alias.py": (
        "import httpx as _hx\ndef f(url):\n    with _hx.stream('GET', url) as r:\n        return r\n"),
    "bulk_downloader/bare_helper_from.py": "from httpx import post as _post\ndef f(url):\n    return _post(url)\n",
    "bulk_downloader/bare_helper_rebound.py": "import httpx\n_head = httpx.head\ndef f(url):\n    return _head(url)\n",
    "bulk_downloader/bare_helper_request.py": "import httpx\ndef f(url):\n    return httpx.request('GET', url)\n",
}
_BARE_LINES = {
    "bulk_downloader/bare_plain.py": 3, "bulk_downloader/bare_from.py": 3,
    "bulk_downloader/bare_alias.py": 3, "bulk_downloader/bare_splat.py": 3,
    "bulk_downloader/bare_other_transport.py": 3, "bulk_downloader/bare_no_policy.py": 4,
    "bulk_downloader/bare_builder_shape_elsewhere.py": 5, "bulk_downloader/bare_rebound.py": 4,
    "bulk_downloader/bare_helper_get.py": 3, "bulk_downloader/bare_helper_stream_alias.py": 3,
    "bulk_downloader/bare_helper_from.py": 3, "bulk_downloader/bare_helper_rebound.py": 4,
    "bulk_downloader/bare_helper_request.py": 3,
}
_HELPER_KINDS = {
    "bulk_downloader/bare_helper_get.py": "httpx.get",
    "bulk_downloader/bare_helper_stream_alias.py": "httpx.stream",
    "bulk_downloader/bare_helper_from.py": "httpx.post",
    "bulk_downloader/bare_helper_rebound.py": "httpx.head",
    "bulk_downloader/bare_helper_request.py": "httpx.request",
}


def _fixture_tree(tmp_path: Path, files: dict, *, git: bool = True) -> Path:
    root = tmp_path / "tree"
    root.mkdir(parents=True)
    for rel, src in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src, encoding="utf-8")
    if git:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def _unpinned_lines(detail: str) -> list:
    return [line.strip() for line in detail.splitlines()[1:] if line.strip()]


def test_the_gate_names_an_unpinned_construction_added_to_a_clean_tree(tmp_path):
    """RED shape from the row: add one bare construction, the gate fails naming it."""
    clean = _fixture_tree(tmp_path / "clean", _PINNED_CORPUS)
    state, detail, result = census_tool.verdict(clean)
    assert state == census_tool.OK, f"{state}: {detail}"
    assert len(result.constructions) == _PINNED_COUNT == 7

    drifted = dict(_PINNED_CORPUS)
    drifted["bulk_downloader/new_site.py"] = "import httpx\n\ndef fetch():\n    with httpx.Client(timeout=3) as c:\n        return c\n"
    tree = _fixture_tree(tmp_path / "drifted", drifted)
    state, detail, result = census_tool.verdict(tree)
    assert state == census_tool.FAIL, f"{state}: {detail}"
    named = _unpinned_lines(detail)
    assert named == [
        "bulk_downloader/new_site.py:4 Client: no transport= keyword on the construction"], named
    assert len(result.constructions) == _PINNED_COUNT + 1 and len(result.unpinned) == 1


def test_every_bare_form_is_named_and_no_pinned_form_is(tmp_path):
    tree = _fixture_tree(tmp_path, {**_PINNED_CORPUS, **_BARE_CORPUS})
    state, detail, result = census_tool.verdict(tree)
    assert state == census_tool.FAIL
    named = {line.split(" ")[0] for line in _unpinned_lines(detail)}
    expected = {f"{rel}:{line}" for rel, line in _BARE_LINES.items()}
    assert named == expected, (named ^ expected)
    assert len(result.unpinned) == len(_BARE_CORPUS) == 13
    assert len(result.pinned) == _PINNED_COUNT == 7
    # the helper arm reports the helper it saw, under every alias form, with
    # the evidence that says why no transport= could ever pin it
    kinds = {c.file: c.kind for c in result.unpinned if c.kind.startswith("httpx.")}
    assert kinds == _HELPER_KINDS, kinds
    assert {c.evidence for c in result.unpinned if c.kind.startswith("httpx.")} == {
        census_tool.HELPER_EVIDENCE}
    assert all(not c.pinned and c.policy == "" for c in result.unpinned if c.kind.startswith("httpx."))


def test_a_module_level_helper_added_to_a_clean_tree_fails_the_gate_naming_it(tmp_path):
    """R1 of the review: ``httpx.stream(...)`` in a tracked file is an egress the
    old census did not count. It is a construction now, and the gate names it."""
    drifted = dict(_PINNED_CORPUS)
    drifted["bulk_downloader/new_helper.py"] = (
        "import httpx\n\ndef fetch(url, headers):\n"
        "    with httpx.stream('GET', url, headers=headers, follow_redirects=True) as r:\n"
        "        return r\n")
    tree = _fixture_tree(tmp_path, drifted)
    state, detail, result = census_tool.verdict(tree)
    assert state == census_tool.FAIL, f"{state}: {detail}"
    assert _unpinned_lines(detail) == [
        "bulk_downloader/new_helper.py:4 httpx.stream: " + census_tool.HELPER_EVIDENCE]
    assert len(result.constructions) == _PINNED_COUNT + 1 and len(result.unpinned) == 1


def test_the_tree_has_no_module_level_helper_egress_left():
    """Note 12(c) of the first DONE.md claimed this without measuring it. This
    is the measurement: every helper call the census can see, on the real tree,
    under every alias form the census resolves."""
    result = census_tool.census(Path(__file__).resolve().parents[1])
    helpers = [(c.file, c.line, c.kind) for c in result.constructions if c.kind.startswith("httpx.")]
    assert helpers == [], helpers
    assert len(result.constructions) >= 53, len(result.constructions)


def test_owning_stream_streams_through_the_client_and_closes_it(monkeypatch):
    """``owning_stream(client, ...)`` is the ``with httpx.stream(...) as r:``
    replacement: the request goes through ``client.stream`` (so the client's
    transport is the one that connects) and the client is closed on exit."""
    import contextlib
    import httpx
    st = _seam()
    seen = {}

    @contextlib.contextmanager
    def fake_stream(self, method, url, **kw):
        seen["call"] = (self, method, url, kw)
        yield "response"

    monkeypatch.setattr(httpx.Client, "stream", fake_stream)
    client = httpx.Client(transport=st.guarded_transport(st.PINNED))
    with st.owning_stream(client, "GET", "https://example.invalid/x", headers={"Range": "bytes=0-1"}) as r:
        assert r == "response"
        assert not client.is_closed
    assert client.is_closed
    assert seen["call"] == (client, "GET", "https://example.invalid/x", {"headers": {"Range": "bytes=0-1"}})


def test_the_builder_rule_needs_the_factory_binding_not_just_the_name(tmp_path):
    """A `transport=transport` in _make_default_http_get is a pin only when that
    transport is bound from _SSRFGuardedTransport_factory(); the same shape
    bound from anything else, or outside the builder, is named."""
    impostor = (
        "import httpx\n"
        "def _make_default_http_get():\n"
        "    def _http_get(url):\n"
        "        transport = httpx.HTTPTransport()\n"
        "        with httpx.Client(transport=transport) as client:\n"
        "            return client\n"
        "    return _http_get\n"
        "def other():\n"
        "    transport_cls = _SSRFGuardedTransport_factory()\n"
        "    transport = transport_cls()\n"
        "    return httpx.Client(transport=transport)\n")
    tree = _fixture_tree(tmp_path, {"bulk_downloader/provider_resolve_impl/_common.py": impostor})
    state, detail, result = census_tool.verdict(tree)
    assert state == census_tool.FAIL, f"{state}: {detail}"
    assert [c.line for c in result.unpinned] == [5, 11], detail
    assert len(result.constructions) == 2


@pytest.mark.parametrize("shape", ["no-git", "no-tracked-package", "unparseable", "empty-population"])
def test_an_unmeasurable_population_is_UNKNOWN_and_never_OK(tmp_path, shape):
    if shape == "no-git":
        tree = _fixture_tree(tmp_path, _PINNED_CORPUS, git=False)
        needle = "git"
    elif shape == "no-tracked-package":
        tree = _fixture_tree(tmp_path, {"README.md": "no package here\n"})
        needle = "no tracked"
    elif shape == "unparseable":
        broken = dict(_PINNED_CORPUS)
        broken["bulk_downloader/broken.py"] = "def (:\n"
        tree = _fixture_tree(tmp_path, broken)
        needle = "does not parse"
    else:
        tree = _fixture_tree(tmp_path, {"bulk_downloader/quiet.py": "import httpx\nX = httpx.Limits\n"})
        needle = "zero httpx client constructions"
    state, detail, _ = census_tool.verdict(tree)
    assert state == census_tool.UNKNOWN, f"{shape}: {state}: {detail}"
    assert needle in detail, detail


# --------------------------------------------------------------------------
# The seam itself, offline
# --------------------------------------------------------------------------

def _seam():
    from bulk_downloader import ssrf_transport
    return ssrf_transport


def _url(literal: str, port: int = 8) -> str:
    host = f"[{literal}]" if ":" in literal else literal
    return f"http://{host}:{port}/"


# Never a legitimate HTTP peer at a PINNED site: unspecified, multicast,
# IPv4 reserved/broadcast, link-local (169.254/16 = cloud metadata, fe80::/10),
# and the same ranges reached through mapped / 6to4 / Teredo / NAT64 embeddings.
_NEVER = [
    "0.0.0.0", "::", "224.0.0.1", "ff02::1", "255.255.255.255", "240.0.0.1",
    "169.254.169.254", "fe80::1", "::ffff:169.254.169.254", "2002:a9fe:a9fe::1",
    "2001:0:a9fe:a9fe::1", "64:ff9b::a9fe:a9fe",
]
# Admitted at a PINNED site because the base admitted them there (SUPERSET):
# loopback, RFC1918, ULA, CGNAT, documentation space, and public.
_ADMITTED = [
    "127.0.0.1", "::1", "10.0.0.1", "192.168.1.1", "172.16.5.5", "fc00::1",
    "100.64.0.1", "203.0.113.5", "93.184.216.34",
]


def test_pinned_transport_refuses_only_what_can_never_be_a_peer():
    import httpx
    st = _seam()
    transport = st.guarded_transport(st.PINNED)
    refused, admitted = [], []
    for literal in _NEVER:
        request = httpx.Request("GET", _url(literal))
        with pytest.raises(st.GuardedTransportRefused) as caught:
            transport.pin(request)
        assert isinstance(caught.value, httpx.ConnectError)
        refused.append((literal, caught.value.reason))
    for literal in _ADMITTED:
        request = httpx.Request("GET", _url(literal))
        assert transport.pin(request) is None, literal
        assert ipaddress.ip_address(request.url.host) == ipaddress.ip_address(literal)
        assert "sni_hostname" not in request.extensions
        admitted.append(literal)
    assert len(refused) == len(_NEVER) == 12, refused
    assert len(admitted) == len(_ADMITTED) == 9, admitted
    assert {reason for _, reason in refused} == {"unspecified", "multicast", "reserved", "link-local"}
    # the base case the family regressed before stays refused on BOTH arms
    from bulk_downloader.provider_resolve_impl import _common
    assert _common._classify_ip(ipaddress.ip_address("0.0.0.0"), "0.0.0.0")[0] is False
    assert st.never_admissible(ipaddress.ip_address("0.0.0.0")) == "unspecified"


def test_public_only_policy_is_the_unchanged_guard():
    st = _seam()
    from bulk_downloader.provider_resolve_impl import _common
    transport = st.guarded_transport(st.PUBLIC_ONLY)
    assert isinstance(transport, _common._SSRFGuardedTransport_factory())
    assert transport._allow_private_hosts is False
    assert not isinstance(transport, st.PinnedTransport)
    with pytest.raises(ValueError):
        st.guarded_transport("bogus")


def _stand_in(kind):
    if kind == "magicmock":
        return MagicMock(name="httpx-stand-in")
    if kind == "bare-holder":
        return type("Httpx", (), {"Client": object})
    if kind == "none":
        return None
    return _ABSENT


_ABSENT = object()


@pytest.mark.parametrize("kind", ["magicmock", "bare-holder", "none", "absent"])
def test_the_established_guard_is_built_against_the_real_httpx_under_a_stand_in(monkeypatch, kind):
    """A fixture standing in for httpx in sys.modules (a MagicMock, a bare
    holder class, None, or nothing at all) must not reach the established
    guard's factory: it imports httpx by name and memoizes what it builds."""
    import httpx
    st = _seam()
    from bulk_downloader.provider_resolve_impl import _common
    monkeypatch.setattr(_common, "_SSRF_GUARDED_TRANSPORT_CLS", None)   # the factory has not run yet
    monkeypatch.setattr(st, "_ESTABLISHED_GUARD_CLS", None)
    stand_in = _stand_in(kind)
    saved_modules = {"httpx": sys.modules["httpx"]}
    try:
        if stand_in is _ABSENT:
            del sys.modules["httpx"]
        else:
            sys.modules["httpx"] = stand_in
        transport = st.guarded_transport(st.PUBLIC_ONLY)
        # the stand-in is back in place once the factory has run
        if stand_in is _ABSENT:
            assert "httpx" not in sys.modules
        else:
            assert sys.modules["httpx"] is stand_in
    finally:
        sys.modules.update(saved_modules)
    assert sys.modules["httpx"] is httpx
    assert isinstance(transport, httpx.HTTPTransport)
    assert type(transport).__name__ == "_SSRFGuardedTransport"
    assert transport._allow_private_hosts is False
    assert _common._SSRFGuardedTransport_factory() is type(transport)   # memoized the real class


def test_a_guard_memoized_under_a_stand_in_is_refused_not_handed_out(monkeypatch):
    st = _seam()
    from bulk_downloader.provider_resolve_impl import _common
    monkeypatch.setattr(_common, "_SSRF_GUARDED_TRANSPORT_CLS", MagicMock(name="mock-class"))
    monkeypatch.setattr(st, "_ESTABLISHED_GUARD_CLS", None)
    with pytest.raises(RuntimeError, match="httpx.HTTPTransport"):
        st.guarded_transport(st.PUBLIC_ONLY)


def _fake_answer(*addresses):
    def getaddrinfo(host, port, *args, **kwargs):
        getaddrinfo.calls.append((host, port))
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port))
            for a in addresses]
    getaddrinfo.calls = []
    return getaddrinfo


def test_pinned_transport_resolves_once_and_the_socket_layer_sees_the_vetted_literal(monkeypatch):
    import httpx
    st = _seam()
    answer = _fake_answer("203.0.113.5", "203.0.113.6")
    monkeypatch.setattr(socket, "getaddrinfo", answer)
    seen = []

    def fake_base(self, request):
        seen.append((str(request.url), request.extensions.get("sni_hostname"),
                     request.headers.get("host")))
        return httpx.Response(204)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fake_base)
    with httpx.Client(transport=st.guarded_transport(st.PINNED)) as client:
        response = client.get("https://site.test/path?q=1")
    assert response.status_code == 204
    # the connect target is the literal the check saw; TLS and Host keep the name
    assert seen == [("https://203.0.113.5/path?q=1", "site.test", "site.test")]
    assert answer.calls == [("site.test", 443)]


@pytest.mark.parametrize("answer, reason", [
    (("0.0.0.0",), "unspecified"),
    (("203.0.113.5", "169.254.169.254"), "link-local"),   # one bad sibling poisons the set
    (("::ffff:224.0.0.1",), "multicast"),
    ((), "no-address"),
])
def test_pinned_transport_refuses_a_never_admissible_resolution_before_any_socket(monkeypatch, answer, reason):
    import httpx
    st = _seam()
    fake = _fake_answer(*answer)
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    seen = []
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request",
                        lambda self, request: seen.append(request) or httpx.Response(204))
    with httpx.Client(transport=st.guarded_transport(st.PINNED)) as client:
        with pytest.raises(st.GuardedTransportRefused) as caught:
            client.get("http://site.test/")
    assert caught.value.reason == reason
    assert caught.value.host == "site.test"
    assert isinstance(caught.value, httpx.ConnectError)
    assert seen == [] and len(fake.calls) == 1


def test_pinned_transport_fails_closed_on_resolution_failure_with_the_class_httpcore_uses(monkeypatch):
    import httpx
    st = _seam()

    def failing(host, port, *args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", failing)
    transport = st.guarded_transport(st.PINNED)
    with pytest.raises(httpx.ConnectError) as caught:
        transport.pin(httpx.Request("GET", "http://nowhere.test/"))
    assert isinstance(caught.value, st.GuardedTransportRefused)
    assert caught.value.reason == "resolution-failed"
    assert "nowhere.test" in str(caught.value)


def test_transport_kwargs_reach_the_transport_because_httpx_stops_applying_them():
    """With transport= installed httpx ignores the client's verify=; the PIA
    gateway site passes verify=False through the seam instead."""
    st = _seam()
    assert st.guarded_transport(st.PINNED, verify=False)._pool._ssl_context.verify_mode == ssl.CERT_NONE
    assert st.guarded_transport(st.PINNED)._pool._ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert st.guarded_transport(st.PUBLIC_ONLY, verify=False)._pool._ssl_context.verify_mode == ssl.CERT_NONE


def test_embedded_ipv4_views_are_all_classified():
    st = _seam()
    assert st.embedded_ipv4(ipaddress.ip_address("10.0.0.1")) == ()
    assert st.embedded_ipv4(ipaddress.ip_address("::ffff:10.0.0.1")) == (ipaddress.ip_address("10.0.0.1"),)
    teredo = st.embedded_ipv4(ipaddress.ip_address("2001:0:a9fe:a9fe::1"))
    assert ipaddress.ip_address("169.254.169.254") in teredo and len(teredo) == 2
    assert st.embedded_ipv4(ipaddress.ip_address("64:ff9b::a9fe:a9fe")) == (ipaddress.ip_address("169.254.169.254"),)
    assert st.never_admissible(ipaddress.ip_address("::ffff:10.0.0.1")) is None
