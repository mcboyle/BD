"""Loopback exemptions use classifier facts, never attacker-controlled prose."""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
import ipaddress
from pathlib import Path
import socket
import subprocess
import urllib.request
from urllib.parse import urlparse

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_PUBLIC_IP = "8.8.8.8"
_METADATA_IP = "169.254.169.254"
_ATTACKER_HOST = "loopback.x.example"
_HOST_IPS = {
    _ATTACKER_HOST: _METADATA_IP,
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
    "bulk_downloader/multi_conn.py": {"_is_safe_public_host": 2},
    "bulk_downloader/provider_resolve_impl/_common.py": {
        "_is_safe_public_host": 2,
        "_classify_ip": 3,
    },
    "bulk_downloader/runner.py": {"_is_safe_public_host": 1},
    "bulk_downloader/runner_extractors.py": {"_is_safe_public_host": 1},
    "bulk_downloader/runner_telemetry.py": {"_is_safe_public_host": 1},
    "bulk_downloader/selector_playground.py": {"_is_safe_public_host": 1},
    "bulk_downloader/site_weather.py": {"_is_safe_public_host": 1},
    "bulk_downloader/tier_probe.py": {"_is_safe_public_host": 1},
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

    def post(self, url: str, *, redirect: str | None = None):
        self.counts.clear()
        self.redirect["url"] = redirect
        return self.client.post(
            "/api/template/sandbox",
            json={"url": url, "template": {}, "mode": "http"},
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
        assert host in _HOST_IPS, f"fixture has no address for {host!r}"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_HOST_IPS[host], 0))]

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
    return _SandboxHarness(fresh_app, counts, redirect)


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
    classifier = next(node for node in ast.walk(source_tree)
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "_classify_ip")
    producers = [
        call.args[0].attr
        for call in ast.walk(classifier)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_host_safety_message"
        and call.args
        and isinstance(call.args[0], ast.Attribute)
    ]
    assert len(producers) == 8
    assert Counter(producers) == Counter(
        {reason.name: 1 for reason in common.HostSafetyReason
         if reason not in {common.HostSafetyReason.NO_HOST,
                           common.HostSafetyReason.DNS_FAILURE,
                           common.HostSafetyReason.NO_ADDRESSES,
                           common.HostSafetyReason.NON_IP_ADDRESS}})


def _consumer_census():
    measured: dict[str, Counter] = {}
    noncanonical: dict[str, Counter] = {}
    judged = 0
    escapes: list[str] = []
    call_names = {"_is_safe_public_host", "_classify_ip"}
    candidate_result = subprocess.run(
        [
            "git", "grep", "-l", "-E",
            r"(_is_safe_public_host|_classify_ip)\(", "--",
            "bulk_downloader", "tools", "toolchain", "scripts",
        ],
        cwd=_REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert candidate_result.returncode == 0, (
        f"runtime consumer search unavailable: {candidate_result.stderr!r}")
    candidates = candidate_result.stdout.splitlines()
    assert len(candidates) == 15, (
        f"runtime consumer candidate population changed: {candidates!r}")
    for rel in candidates:
        tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"), filename=rel)
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                child._parent = parent
        counts: Counter = Counter()
        local_counts: Counter = Counter()
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            if isinstance(call.func, ast.Name):
                name = call.func.id
            elif isinstance(call.func, ast.Attribute):
                name = call.func.attr
            else:
                name = None
            if name not in call_names:
                continue
            if name in _NONCANONICAL_SAME_NAME_CALLS.get(rel, {}):
                local_counts[name] += 1
                continue
            counts[name] += 1
            judged += 1
            parent = call._parent
            if not isinstance(parent, ast.Assign) or not parent.targets:
                continue
            target = parent.targets[0]
            if not isinstance(target, (ast.Tuple, ast.List)) or len(target.elts) != 2:
                continue
            reason_target = target.elts[1]
            if not isinstance(reason_target, ast.Name) or reason_target.id == "_":
                continue
            scope = parent
            while not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                scope = scope._parent
            for comparison in (
                    node for node in ast.walk(scope) if isinstance(node, ast.Compare)):
                names = {node.id for node in ast.walk(comparison)
                         if isinstance(node, ast.Name)}
                strings = {node.value for node in ast.walk(comparison)
                           if isinstance(node, ast.Constant)
                           and isinstance(node.value, str)}
                if reason_target.id in names and strings:
                    escapes.append(
                        f"{rel}:{comparison.lineno}:{reason_target.id}:{sorted(strings)!r}")
        if counts:
            measured[rel] = counts
        if local_counts:
            noncanonical[rel] = local_counts
    return measured, noncanonical, judged, escapes


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
    assert sum(sum(counts.values()) for counts in measured.values()) == 20
    assert judged == 20
    assert escapes == [], f"census 20 sites, 20 judged, escapes: {escapes}"


def test_provider_facade_still_exports_both_classifier_seams():
    import bulk_downloader.provider_resolve_impl as facade
    from bulk_downloader.provider_resolve_impl import _common as common

    assert getattr(facade, "_is_safe_public_host", None) is common._is_safe_public_host
    assert getattr(facade, "_classify_ip", None) is common._classify_ip


def test_transform_control_imports_subject_without_judging_the_exemption():
    from bulk_downloader.provider_resolve_impl import _common as common

    assert callable(common._is_safe_public_host)
