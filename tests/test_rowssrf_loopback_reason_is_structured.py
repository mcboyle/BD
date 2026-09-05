"""Loopback exemptions use classifier facts, never attacker-controlled prose."""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
import ipaddress
from pathlib import Path
import socket
import subprocess
import sys
import types
import urllib.request
from urllib.parse import urlparse

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_PUBLIC_IP = "8.8.8.8"
_METADATA_IP = "169.254.169.254"
_ATTACKER_HOST = "loopback.x.example"
_MULTI_HOST = "multi-answer.example"
_DNS_FAILURE_HOST = "dns-failure.example"
_NO_ADDRESSES_HOST = "no-addresses.example"
_NO_USABLE_HOST = "no-usable-address.example"
_NON_IP_HOST = "non-ip-address.example"
_HOST_IPS = {
    _ATTACKER_HOST: _METADATA_IP,
    _MULTI_HOST: ("127.0.0.1", _METADATA_IP),
    _NO_USABLE_HOST: None,
    "notloopback.example": _PUBLIC_IP,
    "public.example": _PUBLIC_IP,
}

# Hand-audited from the canonical imports and calls, not derived from a result
# under test. A new runtime consumer must be classified here rather than
# silently broadening or escaping the census.
_EXPECTED_RUNTIME_CONSUMERS = {
    "bulk_downloader/app.py": {"_is_safe_public_host": 1},
    "bulk_downloader/app_flaresolverr.py": {"_is_safe_public_host": 1},
    "bulk_downloader/app_template.py": {"_is_safe_public_host": 2},
    "bulk_downloader/candidate_filter.py": {"_classify_ip": 1},
    "bulk_downloader/deep_detect/orchestrate.py": {"_is_safe_public_host": 1},
    "bulk_downloader/dev_suite/capture_diag.py": {"_is_safe_public_host": 1},
    "bulk_downloader/multi_conn.py": {
        "_is_safe_public_host": 2,
        "_via:_guard_url": 2,
    },
    "bulk_downloader/provider_resolve_impl/_common.py": {
        "_is_safe_public_host": 2,
        "_classify_ip": 3,
    },
    "bulk_downloader/runner.py": {"_is_safe_public_host": 1},
    "bulk_downloader/runner_extractors.py": {"_is_safe_public_host": 1},
    "bulk_downloader/runner_telemetry.py": {"_is_safe_public_host": 1},
    "bulk_downloader/selector_playground.py": {
        "_is_safe_public_host": 1,
        "_via:_host_public": 2,
    },
    "bulk_downloader/site_weather.py": {
        "_is_safe_public_host": 1,
        "_via:_host_is_public": 1,
    },
    "bulk_downloader/tier_probe.py": {"_is_safe_public_host": 1},
    # FOUND BY THE gen-c CROSS-MODULE PASS (E8), hand-verified at
    # template_selector_verifier.py:271/274 and :452/454, which import
    # selector_playground._host_public -- a wrapper naming neither canonical
    # symbol. The file was invisible to a population filtered on the canonical
    # names alone, so these two sites were never judged at all. Its only use of
    # the reason text is an f-string in a MESSAGE (:457), not a decision.
    "bulk_downloader/template_selector_verifier.py": {"_via:_host_public": 2},
}

# This tool has an unrelated local classifier with the same private name. Its
# three calls are measured so they cannot hide a new canonical-looking site,
# then excluded for this host-safety census for the stated semantic reason.
_NONCANONICAL_SAME_NAME_CALLS = {
    "toolchain/bin/bdtools_sec.py": {"_classify_ip": 3},
}


class _FixtureResponse:
    def __init__(self, url: str):
        self.headers = {"Content-Type": "text/html"}
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, _limit: int) -> bytes:
        return b"<html><body>fixture</body></html>"

    def geturl(self) -> str:
        return self._url


@dataclass
class _SandboxHarness:
    client: object
    counts: Counter
    redirect: dict[str, str | None]

    def post(
            self, url: str, *, redirect: str | None = None,
            mode: str = "http"):
        self.counts.clear()
        self.redirect["url"] = redirect
        return self.client.post(
            "/api/template/sandbox",
            json={"url": url, "template": {}, "mode": mode},
        )


@pytest.fixture
def sandbox_harness(fresh_app, monkeypatch) -> _SandboxHarness:
    """Patch DNS and urllib's opener so no test can contact a network."""
    from bulk_downloader.provider_resolve_impl import _common as common

    counts: Counter = Counter()
    real_classify = common._classify_ip

    def fixture_getaddrinfo(host, _port, *, type):
        counts["resolver"] += 1
        assert type == socket.SOCK_STREAM
        if host == _DNS_FAILURE_HOST:
            raise socket.gaierror("fixture DNS failure")
        if host == _NO_ADDRESSES_HOST:
            counts[("resolver_empty", host)] += 1
            return []
        if host == _NON_IP_HOST:
            counts[("resolver_answers", host)] += 1
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("fixture", 0))]
        assert host in _HOST_IPS, f"fixture has no address for {host!r}"
        configured = _HOST_IPS[host]
        if configured is None:
            counts[("resolver_answers", host)] += 1
            return [
                (socket.AF_UNSPEC, socket.SOCK_STREAM, 6, "", ("fixture", 0))]
        addresses = configured if isinstance(configured, tuple) else (configured,)
        if len(addresses) > 1:
            counts[("resolver_answers", host)] += len(addresses)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
            for address in addresses
        ]

    def counted_classify(addr, host_repr):
        counts["classifier"] += 1
        counts[(host_repr, str(addr))] += 1
        return real_classify(addr, host_repr)

    redirect: dict[str, str | None] = {"url": None}

    def fixture_build_opener(handler_factory):
        counts["build_opener"] += 1
        handler = handler_factory()

        class _FixtureOpener:
            def open(self, req, *, timeout):
                counts["open"] += 1
                assert timeout == 20
                target = redirect["url"]
                if target is not None:
                    counts["redirect_site"] += 1
                    redirected = handler.redirect_request(
                        req, None, 302, "Found", {"Location": target}, target)
                    assert redirected.full_url == target
                return _FixtureResponse(target or req.full_url)

        return _FixtureOpener()

    monkeypatch.setattr(socket, "getaddrinfo", fixture_getaddrinfo)
    monkeypatch.setattr(common, "_classify_ip", counted_classify)
    monkeypatch.setattr(urllib.request, "build_opener", fixture_build_opener)

    def fixture_cloaked_page(**_kwargs):
        counts["playwright"] += 1
        raise AssertionError("fixture browser boundary reached")

    fake_cloak = types.ModuleType("bulk_downloader.cloak")
    fake_cloak.cloaked_page = fixture_cloaked_page
    monkeypatch.setitem(sys.modules, "bulk_downloader.cloak", fake_cloak)
    import bulk_downloader
    monkeypatch.setattr(bulk_downloader, "cloak", fake_cloak, raising=False)
    return _SandboxHarness(fresh_app, counts, redirect)


def test_every_multi_answer_address_is_classified_before_loopback_exemption(
        sandbox_harness):
    response = sandbox_harness.post(f"http://{_MULTI_HOST}/metadata")
    body = response.get_json()
    assert sandbox_harness.counts[("resolver_answers", _MULTI_HOST)] == 2
    assert body.get("error") == (
        "url host not allowed: refusing link-local address "
        f"({_MULTI_HOST} → {_METADATA_IP})"
    ), f"multi-answer fixture accepted an unsafe sibling: {body!r}"
    assert body.get("ok") is False
    assert sandbox_harness.counts == Counter({
        "resolver": 1,
        "classifier": 2,
        ("resolver_answers", _MULTI_HOST): 2,
        (_MULTI_HOST, "127.0.0.1"): 1,
        (_MULTI_HOST, _METADATA_IP): 1,
    })


@pytest.mark.parametrize("mode", ["http", "browser"])
@pytest.mark.parametrize(
    ("case", "url", "reason_name", "diagnostic", "expected_counts"),
    [
        pytest.param(
            "no-host", "http:///resource", "NO_HOST", "no host", {},
            id="no-host"),
        pytest.param(
            "dns-failure", f"http://{_DNS_FAILURE_HOST}/resource",
            "DNS_FAILURE", "DNS resolution failed: gaierror: fixture DNS failure",
            {"resolver": 1}, id="dns-failure"),
        pytest.param(
            "no-addresses", f"http://{_NO_ADDRESSES_HOST}/resource",
            "NO_ADDRESSES", "DNS resolution returned no addresses",
            {"resolver": 1, ("resolver_empty", _NO_ADDRESSES_HOST): 1},
            id="no-addresses"),
        pytest.param(
            "no-usable-addresses", f"http://{_NO_USABLE_HOST}/resource",
            "NO_ADDRESSES", "DNS resolution returned no usable addresses",
            {"resolver": 1, ("resolver_answers", _NO_USABLE_HOST): 1},
            id="no-usable-addresses"),
        pytest.param(
            "non-ip-address", f"http://{_NON_IP_HOST}/resource",
            "NON_IP_ADDRESS",
            "got non-IP from getaddrinfo: 'fixture'",
            {"resolver": 1, ("resolver_answers", _NON_IP_HOST): 1},
            id="non-ip-address"),
    ],
)
def test_unknown_host_safety_producer_refuses_before_either_fetch_mode(
        sandbox_harness, monkeypatch, mode, case, url, reason_name,
        diagnostic, expected_counts):
    from bulk_downloader.provider_resolve_impl import _common as common

    observed = []
    real_check = common._is_safe_public_host

    def observing_check(host):
        sandbox_harness.counts["host_check"] += 1
        verdict = real_check(host)
        observed.append(verdict)
        return verdict

    monkeypatch.setattr(common, "_is_safe_public_host", observing_check)
    response = sandbox_harness.post(url, mode=mode)
    body = response.get_json()

    assert len(observed) == 1
    refused, reason = observed[0]
    assert refused is False
    assert reason.code is getattr(common.HostSafetyReason, reason_name)
    assert response.status_code == 400
    assert body == {
        "error": f"url host not allowed: {diagnostic}",
        "ok": False,
    }
    assert sandbox_harness.counts["build_opener"] == 0
    assert sandbox_harness.counts["playwright"] == 0
    assert sandbox_harness.counts == Counter(
        {"host_check": 1, **expected_counts}), case


@pytest.mark.parametrize(
    ("site", "url", "redirect", "expected_error", "expected_counts"),
    [
        pytest.param(
            "pre-fetch",
            f"http://{_ATTACKER_HOST}/metadata",
            None,
            "url host not allowed: refusing link-local address "
            f"({_ATTACKER_HOST} → {_METADATA_IP})",
            {"resolver": 1, "classifier": 1,
             (_ATTACKER_HOST, _METADATA_IP): 1},
            id="pre-fetch",
        ),
        pytest.param(
            "redirect",
            "http://public.example/start",
            f"http://{_ATTACKER_HOST}/metadata",
            "fetch failed: <urlopen error SSRF redirect blocked: "
            f"refusing link-local address ({_ATTACKER_HOST} → {_METADATA_IP})>",
            {"resolver": 2, "classifier": 2, "build_opener": 1, "open": 1,
             "redirect_site": 1, ("public.example", _PUBLIC_IP): 1,
             (_ATTACKER_HOST, _METADATA_IP): 1},
            id="redirect",
        ),
    ],
)
def test_attacker_hostname_cannot_claim_the_loopback_exemption(
        sandbox_harness, site, url, redirect, expected_error, expected_counts):
    response = sandbox_harness.post(url, redirect=redirect)
    body = response.get_json()
    assert body.get("error") == expected_error, (
        f"{site} exemption accepted a link-local target: {body!r}")
    assert body.get("ok") is False
    assert sandbox_harness.counts == Counter(expected_counts)


@pytest.mark.parametrize(
    ("site", "url", "redirect", "expected_counts"),
    [
        pytest.param(
            "pre-fetch", "http://127.0.0.1/operator-page", None,
            {"classifier": 1, ("127.0.0.1", "127.0.0.1"): 1,
             "build_opener": 1, "open": 1}, id="pre-fetch",
        ),
        pytest.param(
            "redirect", "http://public.example/start",
            "http://127.0.0.1/operator-page",
            {"resolver": 1, "classifier": 2,
             ("public.example", _PUBLIC_IP): 1,
             ("127.0.0.1", "127.0.0.1"): 1,
             "build_opener": 1, "open": 1, "redirect_site": 1}, id="redirect",
        ),
    ],
)
def test_genuine_loopback_remains_admitted_at_both_exemption_sites(
        sandbox_harness, site, url, redirect, expected_counts):
    response = sandbox_harness.post(url, redirect=redirect)
    body = response.get_json()
    assert response.status_code == 200
    assert body.get("ok") is True, f"{site} rejected genuine loopback: {body!r}"
    assert sandbox_harness.counts == Counter(expected_counts)


@pytest.mark.parametrize(
    "url",
    [
        "http://notloopback.example/public",
        "http://public.example/loopback?next=loopback",
    ],
)
def test_loopback_word_near_misses_on_public_urls_remain_admitted(
        sandbox_harness, url):
    response = sandbox_harness.post(url)
    body = response.get_json()
    assert body.get("ok") is True, body
    host = urlparse(url).hostname
    assert sandbox_harness.counts == Counter({
        "resolver": 1,
        "classifier": 1,
        (host, _PUBLIC_IP): 1,
        "build_opener": 1,
        "open": 1,
    })


def test_classifier_carries_machine_reason_without_changing_human_message(
        monkeypatch):
    from bulk_downloader.provider_resolve_impl import _common as common

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (_METADATA_IP, 0))],
    )
    ok, metadata_reason = common._is_safe_public_host(_ATTACKER_HOST)
    loopback_ok, loopback_reason = common._classify_ip(
        ipaddress.ip_address("127.0.0.1"), "127.0.0.1")
    assert ok is False and loopback_ok is False
    assert str(metadata_reason) == (
        f"refusing link-local address ({_ATTACKER_HOST} → {_METADATA_IP})")
    assert str(loopback_reason) == (
        "refusing loopback address (127.0.0.1 → 127.0.0.1)")
    assert getattr(metadata_reason, "code", None) is common.HostSafetyReason.LINK_LOCAL
    assert getattr(loopback_reason, "code", None) is common.HostSafetyReason.LOOPBACK

    flags = ("is_loopback", "is_link_local", "is_private", "is_reserved",
             "is_multicast", "is_unspecified")
    refusal_cases = (
        (common.HostSafetyReason.LOOPBACK, "is_loopback", None),
        (common.HostSafetyReason.PRIVATE, "is_private", None),
        (common.HostSafetyReason.LINK_LOCAL, "is_link_local", None),
        (common.HostSafetyReason.CGNAT, None, "100.64.0.1"),
        (common.HostSafetyReason.MULTICAST, "is_multicast", None),
        (common.HostSafetyReason.RESERVED, "is_reserved", None),
        (common.HostSafetyReason.UNSPECIFIED, "is_unspecified", None),
    )
    for expected, active_flag, literal in refusal_cases:
        probe_type = type("_ProbeAddress", (), {
            flag: flag == active_flag for flag in flags})
        addr = ipaddress.ip_address(literal) if literal else probe_type()
        refused, reason = common._classify_ip(addr, expected.value)
        assert refused is False
        assert reason.code is expected, (
            f"{expected.value} producer returned {reason.code.value}")
    assert len(refusal_cases) == 7

    source_tree = ast.parse((_REPO / common.__file__).read_text(encoding="utf-8"))
    producer_functions = {
        node.name: node for node in ast.walk(source_tree)
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_is_safe_public_host", "_classify_ip"}
    }
    assert set(producer_functions) == {"_is_safe_public_host", "_classify_ip"}
    producers = [
        getattr(common.HostSafetyReason, call.args[0].attr)
        for function in producer_functions.values()
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_host_safety_message"
        and call.args
        and isinstance(call.args[0], ast.Attribute)
    ]
    expected_counts = Counter({reason: 1 for reason in common.HostSafetyReason})
    expected_counts[common.HostSafetyReason.NO_ADDRESSES] = 2
    assert len(producers) == 13
    assert Counter(producers) == expected_counts
    unknown_counts = {
        reason: sum(item is reason for item in producers)
        for reason in (
            common.HostSafetyReason.NO_HOST,
            common.HostSafetyReason.DNS_FAILURE,
            common.HostSafetyReason.NO_ADDRESSES,
            common.HostSafetyReason.NON_IP_ADDRESS,
        )
    }
    assert len(unknown_counts) == 4
    assert all(count > 0 for count in unknown_counts.values())
    assert unknown_counts == {
        common.HostSafetyReason.NO_HOST: 1,
        common.HostSafetyReason.DNS_FAILURE: 1,
        common.HostSafetyReason.NO_ADDRESSES: 2,
        common.HostSafetyReason.NON_IP_ADDRESS: 1,
    }
    precedence_checks = [
        node for node in ast.walk(producer_functions["_is_safe_public_host"])
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Attribute)
        and node.left.attr == "code"
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.IsNot)
        and len(node.comparators) == 1
        and isinstance(node.comparators[0], ast.Attribute)
        and node.comparators[0].attr == "LOOPBACK"
    ]
    assert len(precedence_checks) == 1


def _consumer_census():
    measured: dict[str, Counter] = {}
    noncanonical: dict[str, Counter] = {}
    judged = 0
    escapes: list[str] = []
    call_names = {"_is_safe_public_host", "_classify_ip"}

    def is_reachable(node) -> bool:
        current = node
        while hasattr(current, "_parent"):
            parent = current._parent
            # A CONSTANT TEST IS JUDGED BY TRUTHINESS, NEVER BY IDENTITY.
            # `parent.test.value is False` is True only for the literal False,
            # so `if 0:` read as reachable and an unreachable decoy was REPORTED
            # as a live escape -- a false positive in the gate itself, which is
            # worse than a miss because it teaches the reader to discount it.
            if isinstance(parent, (ast.If, ast.While)):
                if isinstance(parent.test, ast.Constant):
                    truthy = bool(parent.test.value)
                    if not truthy and current in parent.body:
                        return False
                    if (truthy and isinstance(parent, ast.If)
                            and current in parent.orelse):
                        return False
            elif isinstance(parent, ast.IfExp) and isinstance(
                    parent.test, ast.Constant):
                if bool(parent.test.value) and current is parent.orelse:
                    return False
                if not bool(parent.test.value) and current is parent.body:
                    return False
            current = parent
        return True

    def has_reason_text(node, reason_names, pair_names=frozenset()) -> bool:
        """True when the node reads the TEXT of any of these names.

        A read of ``<name>.code`` is the STRUCTURED field and is excluded, which
        is what keeps a legitimate structured decision -- stored in a variable or
        not -- out of the escape list.
        """
        names = ({reason_names} if isinstance(reason_names, str)
                 else set(reason_names))

        def structured(item) -> bool:
            parent = getattr(item, "_parent", None)
            return (isinstance(parent, ast.Attribute)
                    and parent.value is item and parent.attr == "code")

        for item in ast.walk(node):
            if isinstance(item, ast.Name) and item.id in names:
                if not structured(item):
                    return True
            # E4: `result = guard(host)` binds the PAIR to one name, so the
            # reason never gets a name of its own; its slot is result[1].
            if (isinstance(item, ast.Subscript)
                    and isinstance(item.value, ast.Name)
                    and item.value.id in pair_names
                    and isinstance(item.slice, ast.Constant)
                    and item.slice.value in (1, -1)
                    and not structured(item)):
                return True
        return False

    def text_derived_names(scope, reason_name, pair_names=frozenset()):
        """The reason name plus every name bound from a read of its TEXT.

        THE GATE'S BLIND SPOT: it only inspected the TEST of a branch, and only
        when that test named the reason directly. `admitted = "loopback" in
        str(reason)` followed by `if admitted:` names the reason nowhere in the
        branch, so seven real sites scored "1 passed" while deciding on English.
        The decision is followed to wherever it is STORED: any name bound from an
        expression that reads the text (str(), an f-string, .lower(), in, ==,
        startswith, not) joins the set, transitively. Reads of `.code` never
        taint, so a structured decision stored in a variable stays clean.
        """
        names = {reason_name} if reason_name else set()
        derived: set = set()
        changed = True
        while changed:
            changed = False
            for stmt in ast.walk(scope):
                target = value = None
                if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                        and isinstance(stmt.targets[0], ast.Name)):
                    target, value = stmt.targets[0], stmt.value
                elif (isinstance(stmt, (ast.AnnAssign, ast.NamedExpr))
                        and isinstance(stmt.target, ast.Name)):
                    target, value = stmt.target, stmt.value
                if target is None or value is None or target.id in names:
                    continue
                if is_reachable(stmt) and has_reason_text(value, names, pair_names):
                    names.add(target.id)
                    derived.add(target.id)
                    changed = True
        return names, derived

    def build_aliases(tree, wrapper_names):
        """Map every local name that reaches the guard to its canonical symbol.

        Handles three bindings: a plain assignment (`g = _is_safe_public_host`),
        an IMPORT ALIAS (`from ... import _is_safe_public_host as guard` -- E9,
        which the Assign-only fixpoint could not see, so an aliased consumer
        scored "1 passed"), and a local function that RETURNS the guard result,
        which becomes a `_via:` wrapper. Wrapper names discovered tree-wide are
        passed back in so an IMPORTED wrapper resolves too (E8).
        """
        aliases = {name: name for name in call_names}
        # E9: ImportFrom binds under `asname` when present.
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for entry in node.names:
                bound = entry.asname or entry.name
                if entry.name in call_names:
                    aliases[bound] = entry.name
                elif entry.name in wrapper_names:
                    aliases[bound] = f"_via:{entry.name}"
        changed = True
        while changed:
            changed = False
            for binding in ast.walk(tree):
                if not is_reachable(binding):
                    continue
                target = value = None
                if (isinstance(binding, ast.Assign)
                        and len(binding.targets) == 1
                        and isinstance(binding.targets[0], ast.Name)):
                    target, value = binding.targets[0], binding.value
                elif (isinstance(binding, (ast.AnnAssign, ast.NamedExpr))
                        and isinstance(binding.target, ast.Name)):
                    target, value = binding.target, binding.value
                if target is None:
                    continue
                if isinstance(value, ast.Name):
                    canonical = aliases.get(value.id)
                elif (isinstance(value, ast.Attribute)
                      and value.attr in call_names):
                    canonical = value.attr
                else:
                    canonical = None
                if canonical is not None and aliases.get(target.id) != canonical:
                    aliases[target.id] = canonical
                    changed = True
            for function in (
                    node for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name not in call_names):
                marker = f"_via:{function.name}"
                returns_guard_result = False
                # E6: a wrapper need not `return guard(...)` in one expression.
                # `result = guard(host)` then `return result` is the same
                # wrapper, and matching only the direct call form meant such a
                # wrapper was never named -- so its consumers were never derived
                # and decided on the reason text unjudged.
                stored_results = set()
                for stmt in ast.walk(function):
                    if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                            and isinstance(stmt.targets[0], ast.Name)
                            and isinstance(stmt.value, ast.Call)
                            and is_reachable(stmt)):
                        continue
                    func = stmt.value.func
                    if isinstance(func, ast.Name):
                        stored_name = aliases.get(func.id)
                    elif isinstance(func, ast.Attribute) and func.attr in call_names:
                        stored_name = func.attr
                    else:
                        stored_name = None
                    if stored_name in call_names or (
                            isinstance(stored_name, str)
                            and stored_name.startswith("_via:")):
                        stored_results.add(stmt.targets[0].id)
                for returned in ast.walk(function):
                    if not isinstance(returned, ast.Return) or not is_reachable(returned):
                        continue
                    scope = returned._parent
                    while not isinstance(
                            scope,
                            (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                        scope = scope._parent
                    if scope is not function:
                        continue
                    if (isinstance(returned.value, ast.Name)
                            and returned.value.id in stored_results):
                        returns_guard_result = True
                        break
                    if not isinstance(returned.value, ast.Call):
                        continue
                    result_call = returned.value
                    if isinstance(result_call.func, ast.Name):
                        result_name = aliases.get(result_call.func.id)
                    elif (isinstance(result_call.func, ast.Attribute)
                          and result_call.func.attr in call_names):
                        result_name = result_call.func.attr
                    else:
                        result_name = None
                    if result_name in call_names or (
                            isinstance(result_name, str)
                            and result_name.startswith("_via:")):
                        returns_guard_result = True
                        break
                if returns_guard_result and aliases.get(function.name) != marker:
                    aliases[function.name] = marker
                    changed = True
        return aliases

    candidate_result = subprocess.run(
        [
            "git", "ls-files", "-z", "--",
            "bulk_downloader", "tools", "toolchain", "scripts",
        ],
        cwd=_REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert candidate_result.returncode == 0, (
        f"UNKNOWN: runtime consumer search unavailable: "
        f"{candidate_result.stderr!r}")
    tracked = [rel for rel in candidate_result.stdout.split("\0") if rel]
    assert tracked, "UNKNOWN: runtime source population is empty"

    # Each tracked file is read EXACTLY ONCE. The wrapper pass walks the same
    # population as the counting pass, and re-reading would both double the I/O
    # and make the read set itself wrong for any caller measuring it.
    source_cache: dict = {}

    def collect_sources(names):
        collected = []
        for rel in tracked:
            path = _REPO / rel
            if path.suffix not in {"", ".py"}:
                continue
            if rel in source_cache:
                source = source_cache[rel]
            else:
                try:
                    source = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as ex:
                    raise AssertionError(
                        f"UNKNOWN: runtime consumer source unavailable: {rel}: {ex}")
                source_cache[rel] = source
            if path.suffix != ".py":
                first_line = source.partition("\n")[0]
                if not (first_line.startswith("#!") and "python" in first_line):
                    continue
            if not any(name in source for name in names):
                continue
            collected.append((rel, source))
        return collected

    def parsed(rel, source):
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError as ex:
            raise AssertionError(
                f"UNKNOWN: runtime consumer source is not parseable: {rel}: {ex}")
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                child._parent = parent
        return tree

    # PASS 1 -- name the wrappers. A consumer that imports a wrapper mentions no
    # canonical symbol at all, so a population filtered on the canonical names
    # never even reads its file: E8 scored "1 passed" by being invisible. The
    # wrapper names are derived here and the population is then RE-FILTERED with
    # them, which is what makes the census transitive through the import graph.
    wrapper_names: set = set()
    for rel, source in collect_sources(call_names):
        for name, canonical in build_aliases(parsed(rel, source), set()).items():
            if isinstance(canonical, str) and canonical.startswith("_via:"):
                wrapper_names.add(name)

    python_sources = collect_sources(call_names | wrapper_names)
    assert python_sources, "UNKNOWN: classifier binding population is empty"
    for rel, source in python_sources:
        tree = parsed(rel, source)
        aliases = build_aliases(tree, wrapper_names)
        counts: Counter = Counter()
        local_counts: Counter = Counter()
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            if not is_reachable(call):
                continue
            if isinstance(call.func, ast.Name):
                name = aliases.get(call.func.id)
            elif isinstance(call.func, ast.Attribute):
                name = (call.func.attr if call.func.attr in call_names else None)
            else:
                name = None
            if name not in call_names and not (
                    isinstance(name, str) and name.startswith("_via:")):
                continue
            if name in _NONCANONICAL_SAME_NAME_CALLS.get(rel, {}):
                local_counts[name] += 1
                continue
            parent = call._parent
            reason_target = None
            pair_target = None
            if isinstance(parent, ast.Assign) and parent.targets:
                target = parent.targets[0]
                if (isinstance(target, (ast.Tuple, ast.List))
                        and len(target.elts) == 2
                        and isinstance(target.elts[1], ast.Name)
                        and target.elts[1].id != "_"):
                    reason_target = target.elts[1]
                elif isinstance(target, ast.Name):
                    # E4: the whole (ok, reason) pair under one name.
                    pair_target = target
            if (isinstance(name, str) and name.startswith("_via:")
                    and reason_target is None
                    and not isinstance(parent, ast.Return)):
                continue
            counts[name] += 1
            judged += 1
            if reason_target is None and pair_target is None:
                continue
            scope = parent
            while not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                scope = scope._parent
            pair_names = {pair_target.id} if pair_target is not None else frozenset()
            reason_label = (reason_target.id if reason_target is not None
                            else pair_target.id)
            flow_names, derived_names = text_derived_names(
                scope, reason_target.id if reason_target is not None else None,
                pair_names)
            for statement in ast.walk(scope):
                if isinstance(statement, (ast.If, ast.While, ast.IfExp)):
                    decision = statement.test
                elif isinstance(statement, ast.Assert):
                    decision = statement.test
                elif isinstance(statement, ast.Match):
                    # E5: `match str(reason):` is the test of a branch under
                    # another name -- the subject decides which case runs.
                    decision = statement.subject
                elif isinstance(statement, ast.Return):
                    # E3: a decision does not stop being one for having no `if`.
                    # `return ok or "loopback" in str(reason)` decides. Bare
                    # forwarding -- `return reason`, `return ok, reason` -- is
                    # NOT a decision, so a value that only NAMES the reason
                    # without reading its text is skipped below by the same
                    # comparison/call/fstring test every other form uses.
                    if statement.value is None:
                        continue
                    decision = statement.value
                else:
                    continue
                if not is_reachable(statement) or not has_reason_text(
                        decision, flow_names, pair_names):
                    continue
                strings = {
                    node.value for node in ast.walk(decision)
                    if isinstance(node, ast.Constant)
                    and isinstance(node.value, str)}
                comparisons = [
                    node for node in ast.walk(decision)
                    if isinstance(node, ast.Compare)
                    and has_reason_text(node, flow_names, pair_names)]
                calls = [
                    node for node in ast.walk(decision)
                    if isinstance(node, ast.Call)
                    and has_reason_text(node, flow_names, pair_names)]
                fstrings = [
                    node for node in ast.walk(decision)
                    if isinstance(node, ast.JoinedStr)
                    and has_reason_text(node, flow_names, pair_names)]
                if isinstance(statement, ast.Return):
                    # A RETURNED VALUE IS A DECISION ONLY WHEN IT IS A PREDICATE
                    # OVER THE TEXT. `return reason` forwards it; `return {"error":
                    # f"host not allowed: {reason}"}` REPORTS it -- measured, that
                    # message shape occurs at seven real sites and flagging it made
                    # the gate cry wolf on every one. A comparison, a startswith /
                    # endswith test, or a name already derived from the text is a
                    # decision; an f-string or a bare str() is not.
                    predicate_calls = [
                        item for item in calls
                        if isinstance(item.func, ast.Attribute)
                        and item.func.attr in {"startswith", "endswith"}]
                    if not comparisons and not predicate_calls and not any(
                            isinstance(node, ast.Name)
                            and node.id in derived_names
                            for node in ast.walk(decision)):
                        continue
                if comparisons:
                    kind = ("in" if any(
                        isinstance(op, (ast.In, ast.NotIn))
                        for comparison in comparisons for op in comparison.ops)
                            else "comparison")
                elif fstrings:
                    kind = "fstring"
                elif calls:
                    kind = ("startswith" if any(
                        isinstance(item.func, ast.Attribute)
                        and item.func.attr == "startswith" for item in calls)
                            else "call")
                else:
                    kind = "text"
                escapes.append(
                    f"{rel}:{statement.lineno}:{reason_label}:"
                    f"{kind}:{sorted(strings)!r}")
        if counts:
            measured[rel] = counts
        if local_counts:
            noncanonical[rel] = local_counts
    return measured, noncanonical, judged, escapes


def _assert_consumer_verdict(judged: int, escapes: list[str]) -> None:
    assert judged != 0, "UNKNOWN: structured-reason consumer population is empty"
    assert escapes == [], (
        f"census {judged} sites, {judged} judged, escapes: {escapes}")


def test_runtime_consumer_census_judges_every_site_without_english_decisions():
    measured, noncanonical, judged, escapes = _consumer_census()
    expected = {path: Counter(counts)
                for path, counts in _EXPECTED_RUNTIME_CONSUMERS.items()}
    expected_noncanonical = {
        path: Counter(counts)
        for path, counts in _NONCANONICAL_SAME_NAME_CALLS.items()
    }
    assert measured == expected
    assert noncanonical == expected_noncanonical
    assert sum(sum(counts.values()) for counts in measured.values()) == 27
    assert judged == 27
    _assert_consumer_verdict(judged, escapes)


def test_consumer_census_rejects_reason_text_startswith_decision(monkeypatch):
    target = _REPO / "bulk_downloader/app_template.py"
    old = (
        "if not _host_ok and _host_why.code is not "
        "_HostSafetyReason.LOOPBACK:")
    new = 'if not _host_ok and str(_host_why).startswith("refusing"):'
    real_read_text = Path.read_text
    observed = []

    def fixture_read_text(path, *args, **kwargs):
        source = real_read_text(path, *args, **kwargs)
        if path != target:
            return source
        assert source.count(old) == 1
        observed.append(path)
        return source.replace(old, new)

    monkeypatch.setattr(Path, "read_text", fixture_read_text)
    _measured, _noncanonical, judged, escapes = _consumer_census()
    source = real_read_text(target, encoding="utf-8")
    mutant_line = source[:source.index(old)].count("\n") + 1
    expected = (
        f"bulk_downloader/app_template.py:{mutant_line}:"
        "_host_why:startswith:['refusing']")
    assert observed == [target]
    assert judged == 27
    assert escapes == [expected]
    with pytest.raises(AssertionError, match=r"census 27 sites, 27 judged"):
        _assert_consumer_verdict(judged, escapes)


@pytest.mark.parametrize(
    ("decision", "kind", "expected_strings"),
    [
        pytest.param(
            'not str(_host_why).startswith("refusing")', "startswith", ["refusing"],
            id="negated-startswith"),
        pytest.param(
            "not str(_host_why).lower()", "call", [], id="negated-lower"),
        pytest.param(
            'not f"{_host_why}"', "fstring", [], id="negated-fstring"),
        pytest.param(
            'str(_host_why) == "refusing"', "comparison", ["refusing"],
            id="comparison"),
        pytest.param(
            '"refusing" in str(_host_why)', "in", ["refusing"],
            id="membership"),
    ],
)
def test_consumer_census_rejects_every_reason_text_decision_form(
        monkeypatch, decision, kind, expected_strings):
    target = _REPO / "bulk_downloader/app_template.py"
    old = (
        "if not _host_ok and _host_why.code is not "
        "_HostSafetyReason.LOOPBACK:")
    new = f"if not _host_ok and {decision}:"
    real_read_text = Path.read_text

    def fixture_read_text(path, *args, **kwargs):
        source = real_read_text(path, *args, **kwargs)
        if path != target:
            return source
        assert source.count(old) == 1
        return source.replace(old, new)

    monkeypatch.setattr(Path, "read_text", fixture_read_text)
    _measured, _noncanonical, judged, escapes = _consumer_census()
    source = real_read_text(target, encoding="utf-8")
    mutant_line = source[:source.index(old)].count("\n") + 1
    expected = (
        f"bulk_downloader/app_template.py:{mutant_line}:"
        f"_host_why:{kind}:{expected_strings!r}")
    assert judged == 27
    assert escapes == [expected]


def test_consumer_census_resolves_alias_and_ignores_unreachable_decoy(
        monkeypatch):
    target = _REPO / "bulk_downloader/app_template.py"
    old_call = (
        "    _host_ok, _host_why = _is_safe_public_host("
        "_urlparse(url).hostname or \"\")")
    alias_call = "\n".join((
        "    _host_ok, _host_why = (False, \"\")",
        "    _resolve_safe_public_host = _is_safe_public_host",
        "    _host_ok, _host_why = _resolve_safe_public_host(",
        "        _urlparse(url).hostname or \"\")",
        "    if False:",
        "        _is_safe_public_host(_urlparse(url).hostname or \"\")",
    ))
    old_decision = (
        "if not _host_ok and _host_why.code is not "
        "_HostSafetyReason.LOOPBACK:")
    new_decision = 'if not _host_ok and "refusing" not in str(_host_why):'
    real_read_text = Path.read_text
    mutated_source = real_read_text(target, encoding="utf-8")
    assert mutated_source.count(old_call) == 1
    assert mutated_source.count(old_decision) == 1
    mutated_source = mutated_source.replace(old_call, alias_call)
    mutated_source = mutated_source.replace(old_decision, new_decision)

    def fixture_read_text(path, *args, **kwargs):
        if path == target:
            return mutated_source
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fixture_read_text)
    measured, _noncanonical, judged, escapes = _consumer_census()
    expected_line = mutated_source[:mutated_source.index(new_decision)].count("\n") + 1
    expected_escape = (
        f"bulk_downloader/app_template.py:{expected_line}:"
        "_host_why:in:['refusing']")
    assert measured["bulk_downloader/app_template.py"] == Counter(
        {"_is_safe_public_host": 2})
    assert judged == 27
    assert escapes == [expected_escape]


def test_consumer_census_finds_alias_only_consumer_in_new_file(monkeypatch):
    synthetic_rel = "bulk_downloader/fixture_alias_consumer.py"
    synthetic_path = _REPO / synthetic_rel
    synthetic_source = "\n".join((
        "from bulk_downloader.provider_resolve_impl import (",
        "    HostSafetyReason, _is_safe_public_host)",
        "guard = _is_safe_public_host",
        "def inspect(host):",
        "    ok, reason = guard(host)",
        "    return ok or reason.code is HostSafetyReason.LOOPBACK",
        "",
    ))
    real_run = subprocess.run
    real_read_text = Path.read_text

    def fixture_run(*args, **kwargs):
        result = real_run(*args, **kwargs)
        separator = "\0" if "\0" in result.stdout else "\n"
        return subprocess.CompletedProcess(
            result.args, result.returncode,
            result.stdout + synthetic_rel + separator, result.stderr)

    def fixture_read_text(path, *args, **kwargs):
        if path == synthetic_path:
            return synthetic_source
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fixture_run)
    monkeypatch.setattr(Path, "read_text", fixture_read_text)
    measured, _noncanonical, judged, escapes = _consumer_census()
    assert measured[synthetic_rel] == Counter({"_is_safe_public_host": 1})
    assert judged == 28
    assert escapes == []


def test_consumer_census_rejects_text_decisions_in_indirect_consumers(
        monkeypatch):
    real_read_text = Path.read_text
    replacements = {
        "bulk_downloader/site_weather.py": [(
            "            if not ok:\n                raise SSRFBlocked(reason)",
            "            if not ok and \"loopback\" not in str(reason):\n"
            "                raise SSRFBlocked(reason)"),
        ],
        "bulk_downloader/selector_playground.py": [
            (
                "    if not _ok:\n        return {\"ok\": False, \"error\": "
                "f\"blocked: {_why}\", \"html\": \"\"}",
                "    if not _ok and \"loopback\" not in str(_why):\n"
                "        return {\"ok\": False, \"error\": "
                "f\"blocked: {_why}\", \"html\": \"\"}"),
            (
                "            if not _hop_ok:\n                raise SSRFBlocked(_hop_why)",
                "            if not _hop_ok and \"loopback\" not in str(_hop_why):\n"
                "                raise SSRFBlocked(_hop_why)"),
        ],
        "bulk_downloader/multi_conn.py": [
            (
                "    if not ok:\n        return ProbeResult(ok=False, error=",
                "    if not ok and \"loopback\" not in str(reason):\n"
                "        return ProbeResult(ok=False, error="),
            (
                "    if not ok:\n        return DownloadResult(\n"
                "            ok=False, error=",
                "    if not ok and \"loopback\" not in str(reason):\n"
                "        return DownloadResult(\n            ok=False, error="),
        ],
    }
    mutated = {}
    for rel, changes in replacements.items():
        source = real_read_text(_REPO / rel, encoding="utf-8")
        for old, new in changes:
            assert source.count(old) == 1
            source = source.replace(old, new)
        mutated[rel] = source

    def fixture_read_text(path, *args, **kwargs):
        rel = str(path.relative_to(_REPO))
        if rel in mutated:
            return mutated[rel]
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fixture_read_text)
    _measured, _noncanonical, judged, escapes = _consumer_census()
    assert judged == 27
    assert len(escapes) == 5
    assert Counter(item.split(":", 1)[0] for item in escapes) == Counter({
        "bulk_downloader/multi_conn.py": 2,
        "bulk_downloader/selector_playground.py": 2,
        "bulk_downloader/site_weather.py": 1,
    })
    assert all(":in:['loopback']" in item for item in escapes)


def test_runtime_consumer_census_returns_unknown_when_search_is_unavailable(
        monkeypatch):
    unavailable = subprocess.CompletedProcess(
        args=["git", "grep"], returncode=2, stdout="", stderr="fixture unavailable")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: unavailable)
    with pytest.raises(
            AssertionError,
            match=r"^UNKNOWN: runtime consumer search unavailable"):
        _consumer_census()


def test_consumer_census_negative_control_rejects_an_english_decision():
    escape = "fixture_consumer.py:7:reason:['loopback']"
    with pytest.raises(
            AssertionError,
            match=r"census 1 sites, 1 judged, escapes"):
        _assert_consumer_verdict(1, [escape])


def test_provider_facade_still_exports_both_classifier_seams():
    import bulk_downloader.provider_resolve_impl as facade
    from bulk_downloader.provider_resolve_impl import _common as common

    assert getattr(facade, "_is_safe_public_host", None) is common._is_safe_public_host
    assert getattr(facade, "_classify_ip", None) is common._classify_ip


def test_transform_control_imports_subject_without_judging_the_exemption():
    from bulk_downloader.provider_resolve_impl import _common as common

    assert callable(common._is_safe_public_host)


# ── gen c: the census is defeated by INDIRECTION ─────────────────────────────
#
# MEASURED on the b-layer: the census only inspects the TEST of an if/while/
# ifexp/assert and only when that test names the reason directly. Three ways
# past it, each scoring "1 passed" today:
#   E1-E7  the decision is computed first and STORED   -- `admitted = "loopback"
#          in str(reason)` then `if admitted:` -- the branch never names reason.
#   E8     the consumer imports a WRAPPER in another module that contains
#          neither canonical name, so its file is filtered out before parsing.
#   E9     the guard is bound by an IMPORT ALIAS (`... as guard`); aliases are
#          only grown from Assign, never from ImportFrom asname.

# The anchor sits at twelve spaces inside site_weather's nested block, so the
# stored decision must be written at that indent or the fixture is a syntax
# error rather than the hazard it is meant to be.
_I = " " * 12
_STORED_DECISION_FORMS = (
    ("membership", f'{_I}_admitted = "loopback" in str(reason)\n'),
    ("fstring", f'{_I}_admitted = "loopback" in f"{{reason}}"\n'),
    ("lower", f'{_I}_admitted = "loopback" in str(reason).lower()\n'),
    ("equality", f'{_I}_admitted = str(reason) == "loopback"\n'),
    ("startswith", f'{_I}_admitted = str(reason).startswith("loopback")\n'),
    ("negation", f'{_I}_admitted = not ("loopback" not in str(reason))\n'),
)

_WEATHER = "bulk_downloader/site_weather.py"
_WEATHER_OLD = "            if not ok:\n                raise SSRFBlocked(reason)"


def _census_with_sources(monkeypatch, overrides, extra_rel=None):
    """Run the census with some tracked files replaced, optionally adding one."""
    real_read_text = Path.read_text
    real_run = subprocess.run

    def fixture_read_text(path, *args, **kwargs):
        for rel, source in overrides.items():
            if path == _REPO / rel:
                return source
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fixture_read_text)
    if extra_rel is not None:
        def fixture_run(*args, **kwargs):
            result = real_run(*args, **kwargs)
            separator = "\0" if "\0" in result.stdout else "\n"
            return subprocess.CompletedProcess(
                result.args, result.returncode,
                result.stdout + extra_rel + separator, result.stderr)
        monkeypatch.setattr(subprocess, "run", fixture_run)
    return _consumer_census()


def _stored_decision_source(form_body):
    source = Path.read_text(_REPO / _WEATHER, encoding="utf-8")
    assert source.count(_WEATHER_OLD) == 1, "site_weather anchor moved"
    return source.replace(
        _WEATHER_OLD,
        f"{form_body}{_I}if _admitted:\n"
        f"{_I}    return None\n"
        "            if not ok:\n                raise SSRFBlocked(reason)")


@pytest.mark.parametrize(("form", "body"), _STORED_DECISION_FORMS)
def test_census_catches_a_stored_text_decision(monkeypatch, form, body):
    """E1-E7: storing the decision must not launder it past the census."""
    mutated = _stored_decision_source(body)
    _measured, _nc, judged, escapes = _census_with_sources(
        monkeypatch, {_WEATHER: mutated})
    assert judged > 0, "UNKNOWN: census judged nothing"
    assert any(escape.startswith(f"{_WEATHER}:") for escape in escapes), (
        f"a stored {form} decision on the reason text escaped the census: "
        f"census {judged} sites, {judged} judged, escapes: {escapes}")


def test_census_catches_a_stored_membership_decision(monkeypatch):
    """Unparameterized catcher for the stored-decision mutants."""
    mutated = _stored_decision_source(_STORED_DECISION_FORMS[0][1])
    _measured, _nc, judged, escapes = _census_with_sources(
        monkeypatch, {_WEATHER: mutated})
    assert judged > 0
    assert any(escape.startswith(f"{_WEATHER}:") for escape in escapes), (
        f"census {judged} sites, {judged} judged, escapes: {escapes}")


def test_census_catches_a_cross_module_wrapper_consumer(monkeypatch):
    """E8: a consumer importing a WRAPPER names no canonical symbol at all."""
    rel = "bulk_downloader/fixture_wrapper_consumer.py"
    source = "\n".join((
        "from bulk_downloader.site_weather import _host_is_public",
        "def inspect(host):",
        "    ok, reason = _host_is_public(host)",
        '    if "loopback" in str(reason):',
        "        return True",
        "    return ok",
        "",
    ))
    _measured, _nc, judged, escapes = _census_with_sources(
        monkeypatch, {rel: source}, extra_rel=rel)
    assert any(escape.startswith(f"{rel}:") for escape in escapes), (
        f"a cross-module wrapper consumer escaped: census {judged} sites, "
        f"{judged} judged, escapes: {escapes}")


def test_census_catches_an_import_alias_consumer(monkeypatch):
    """E9: `from ... import _is_safe_public_host as guard` then decide on text."""
    rel = "bulk_downloader/fixture_import_alias_consumer.py"
    source = "\n".join((
        "from bulk_downloader.provider_resolve_impl import (",
        "    _is_safe_public_host as guard)",
        "def inspect(host):",
        "    ok, reason = guard(host)",
        '    if "loopback" in str(reason):',
        "        return True",
        "    return ok",
        "",
    ))
    _measured, _nc, judged, escapes = _census_with_sources(
        monkeypatch, {rel: source}, extra_rel=rel)
    assert any(escape.startswith(f"{rel}:") for escape in escapes), (
        f"an import-alias consumer escaped: census {judged} sites, "
        f"{judged} judged, escapes: {escapes}")


def test_e1_attacker_host_is_refused_with_zero_fetch_and_the_census_is_clean(
        sandbox_harness):
    """E1 both halves: the real refusal, and the shipped tree judged clean."""
    response = sandbox_harness.post(f"http://{_ATTACKER_HOST}/metadata")
    body = response.get_json()
    assert body.get("ok") is False, body
    assert sandbox_harness.counts["open"] == 0, (
        f"a fetch was attempted at the attacker host: {sandbox_harness.counts!r}")
    assert sandbox_harness.counts["build_opener"] == 0, sandbox_harness.counts
    _measured, _nc, judged, escapes = _consumer_census()
    _assert_consumer_verdict(judged, escapes)


# ── gen d: four more decision shapes, and one FALSE POSITIVE of our own ──────
#
# MEASURED on the gen-c census. It reads decisions only out of an if/while/
# ifexp/assert TEST, taints only a name bound directly to the reason, and judges
# reachability by `is False` identity:
#   E3 a text decision in a RETURN value is not a branch, so it was never read.
#   E4 `result = guard(host)` binds the pair to ONE name; the reason slot
#      `result[1]` is not a name bound to the reason, so nothing was tainted.
#   E5 `match str(reason):` is a decision whose subject the scan never inspects.
#   E6 a wrapper that STORES the guard result and returns the name is not
#      `return guard(...)`, so it was never recognised as a wrapper at all.
#   E7 OUR OWN FALSE POSITIVE: `if 0:` is constant-false, but `0 is False` is
#      False, so an unreachable decoy was REPORTED as a live escape.

_GUARD_IMPORT = (
    "from bulk_downloader.provider_resolve_impl import _is_safe_public_host")


def test_census_reports_a_text_decision_in_a_return_value(monkeypatch):
    """E3: a decision does not stop being one for having no `if`."""
    rel = "bulk_downloader/fixture_return_decision.py"
    source = "\n".join((
        _GUARD_IMPORT,
        "def inspect(host):",
        "    ok, reason = _is_safe_public_host(host)",
        '    return ok or "loopback" in str(reason)',
        "",
    ))
    _m, _n, judged, escapes = _census_with_sources(
        monkeypatch, {rel: source}, extra_rel=rel)
    assert any(e.startswith(f"{rel}:") for e in escapes), (
        f"a text decision in a return escaped: census {judged} sites, "
        f"{judged} judged, escapes: {escapes}")


def test_census_reports_a_decision_on_a_tuple_bound_reason(monkeypatch):
    """E4: binding the pair to one name must not launder the reason."""
    rel = "bulk_downloader/fixture_tuple_bound_reason.py"
    source = "\n".join((
        _GUARD_IMPORT,
        "def inspect(host):",
        "    result = _is_safe_public_host(host)",
        '    if "loopback" in str(result[1]):',
        "        return True",
        "    return result[0]",
        "",
    ))
    _m, _n, judged, escapes = _census_with_sources(
        monkeypatch, {rel: source}, extra_rel=rel)
    assert any(e.startswith(f"{rel}:") for e in escapes), (
        f"a tuple-bound reason decision escaped: census {judged} sites, "
        f"{judged} judged, escapes: {escapes}")


def test_census_reports_a_match_statement_on_the_reason_text(monkeypatch):
    """E5: a match subject is the test of a branch by another name."""
    rel = "bulk_downloader/fixture_match_subject.py"
    source = "\n".join((
        _GUARD_IMPORT,
        "def inspect(host):",
        "    ok, reason = _is_safe_public_host(host)",
        "    match str(reason):",
        '        case "loopback":',
        "            return True",
        "    return ok",
        "",
    ))
    _m, _n, judged, escapes = _census_with_sources(
        monkeypatch, {rel: source}, extra_rel=rel)
    assert any(e.startswith(f"{rel}:") for e in escapes), (
        f"a match on the reason text escaped: census {judged} sites, "
        f"{judged} judged, escapes: {escapes}")


def test_census_follows_a_wrapper_that_stores_then_returns_the_result(
        monkeypatch):
    """E6: `r = guard(h); return r` is a wrapper too."""
    wrapper_rel = "bulk_downloader/fixture_storing_wrapper.py"
    wrapper_source = "\n".join((
        _GUARD_IMPORT,
        "def wrapped(host):",
        "    result = _is_safe_public_host(host)",
        "    return result",
        "",
    ))
    consumer_rel = "bulk_downloader/fixture_storing_wrapper_consumer.py"
    consumer_source = "\n".join((
        "from bulk_downloader.fixture_storing_wrapper import wrapped",
        "def inspect(host):",
        "    ok, reason = wrapped(host)",
        '    if "loopback" in str(reason):',
        "        return True",
        "    return ok",
        "",
    ))
    real_run = subprocess.run
    real_read_text = Path.read_text

    def fixture_run(*args, **kwargs):
        result = real_run(*args, **kwargs)
        sep = "\0" if "\0" in result.stdout else "\n"
        return subprocess.CompletedProcess(
            result.args, result.returncode,
            result.stdout + wrapper_rel + sep + consumer_rel + sep,
            result.stderr)

    def fixture_read_text(path, *args, **kwargs):
        if path == _REPO / wrapper_rel:
            return wrapper_source
        if path == _REPO / consumer_rel:
            return consumer_source
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fixture_run)
    monkeypatch.setattr(Path, "read_text", fixture_read_text)
    _m, _n, judged, escapes = _consumer_census()
    assert any(e.startswith(f"{consumer_rel}:") for e in escapes), (
        f"a consumer of a storing wrapper escaped: census {judged} sites, "
        f"{judged} judged, escapes: {escapes}")


def test_census_does_not_report_an_unreachable_integer_zero_decoy(monkeypatch):
    """E7, OUR OWN FALSE POSITIVE: `if 0:` is dead code, not a decision.

    The reachability test compared with `is False`, and `0 is False` is False,
    so a decoy under `if 0:` was reported as a live escape. An unreachable
    decision is not a decision; the same code under `if 1:` still is.
    """
    rel = "bulk_downloader/fixture_integer_zero_decoy.py"
    # The decision must be an IF TEST inside the dead branch. An earlier draft
    # put it in a `return`, which made this a second E3 fixture and proved
    # nothing about reachability -- measured: neither `if 0:` nor `if 1:` was
    # reported, for the wrong reason.
    body = (
        _GUARD_IMPORT,
        "def inspect(host):",
        "    ok, reason = _is_safe_public_host(host)",
        "    if {guard}:",
        '        if "loopback" in str(reason):',
        "            return True",
        "    return ok",
        "",
    )
    dead = "\n".join(body).format(guard="0")
    _m, _n, judged, escapes = _census_with_sources(
        monkeypatch, {rel: dead}, extra_rel=rel)
    assert [e for e in escapes if e.startswith(f"{rel}:")] == [], (
        f"an unreachable `if 0:` decoy was reported: census {judged} sites, "
        f"{judged} judged, escapes: {escapes}")

    monkeypatch.undo()
    live = "\n".join(body).format(guard="1")
    _m, _n, judged2, escapes2 = _census_with_sources(
        monkeypatch, {rel: live}, extra_rel=rel)
    assert any(e.startswith(f"{rel}:") for e in escapes2), (
        f"the same decision under `if 1:` was NOT reported, so the "
        f"reachability rule is now too broad: census {judged2} sites, "
        f"{judged2} judged, escapes: {escapes2}")
