"""Row 293: a provider-verified IPv6 result is reachable in production.

The regression in v3.66.1282 left every real ``_probe_ipv6`` outcome UNKNOWN.
These tests drive the endpoint seam itself, the public aggregate runner, the
process kill switch, and the API input that supplies the expected exit address.
"""
from __future__ import annotations

from types import SimpleNamespace


BD_GATE_SCOPE = "repo-wide"

_EXPECTED_V6 = "2001:db8::293"
_EXPANDED_EXPECTED_V6 = "2001:0db8:0000:0000:0000:0000:0000:0293"
_SOCKS_PORT = 19293


def _measured_pass(probe_id, severity):
    from bulk_downloader.vpn_leak_tests import ProbeResult

    return ProbeResult(probe_id=probe_id, passed=True, severity=severity)


def _aggregate_with_real_ipv6(ipv6_result):
    from bulk_downloader.vpn_leak_tests import (
        ALL_PROBES,
        ProbeId,
        Severity,
        _aggregate_probe_results,
    )

    probes = [
        _measured_pass(ProbeId.DNS.value, Severity.CRITICAL.value),
        _measured_pass(ProbeId.IPV4.value, Severity.CRITICAL.value),
        ipv6_result,
        _measured_pass(ProbeId.WEBRTC.value, Severity.CRITICAL.value),
        _measured_pass(ProbeId.GEO.value, Severity.WARNING.value),
        _measured_pass(ProbeId.TIMEZONE.value, Severity.WARNING.value),
    ]
    assert len(probes) == len(ALL_PROBES) == 6
    assert {probe.probe_id for probe in probes} == set(ALL_PROBES)
    return _aggregate_probe_results("row-293", probes, timestamp=293.0)


def test_real_ipv6_probe_passes_only_after_matching_provider_address(monkeypatch):
    from bulk_downloader import vpn_leak_tests as leak

    endpoint_calls = []

    def matching_endpoint(url, proxy_url, field_names):
        endpoint_calls.append((url, proxy_url, field_names))
        return _EXPANDED_EXPECTED_V6

    monkeypatch.setattr(leak, "_http_get_json_field", matching_endpoint)
    result = leak._probe_ipv6(_SOCKS_PORT, expected_exit_ip=_EXPECTED_V6)

    assert endpoint_calls == [
        (leak.IPV6_ENDPOINT, f"socks5://127.0.0.1:{_SOCKS_PORT}", ("ip",))
    ]
    assert result.probe_id == leak.ProbeId.IPV6.value
    assert result.severity == leak.Severity.CRITICAL.value
    assert result.passed is True
    assert result.state == leak.ProbeState.PASS.value
    assert result.error is None
    assert result.details == {
        "classification": "provider_verified",
        "observed_v6": _EXPANDED_EXPECTED_V6,
        "expected_v6": _EXPECTED_V6,
    }


def test_run_all_real_ipv6_measurement_advances_streak_and_clears(
    monkeypatch, request
):
    from bulk_downloader import vpn, vpn_kill_switch
    from bulk_downloader import vpn_leak_tests as leak

    leak._reset_for_tests()
    vpn_kill_switch._reset_for_tests()
    request.addfinalizer(leak._reset_for_tests)
    request.addfinalizer(vpn_kill_switch._reset_for_tests)
    vpn_kill_switch.set_auto_recover(False)

    tunnel = SimpleNamespace(
        socks_port=_SOCKS_PORT,
        public_ip=None,
        last_health_check=None,
    )
    monkeypatch.setattr(vpn, "get_tunnel", lambda tunnel_id: tunnel)

    endpoint_calls = []

    def matching_endpoint(url, proxy_url, field_names):
        endpoint_calls.append((url, proxy_url, field_names))
        return _EXPECTED_V6

    monkeypatch.setattr(leak, "_http_get_json_field", matching_endpoint)
    real_run_probe = leak.run_probe
    probe_calls = []

    def probe_substitute(probe_id, socks_port, **kwargs):
        probe_calls.append((probe_id, socks_port, dict(kwargs)))
        if probe_id == leak.ProbeId.IPV6.value:
            return real_run_probe(probe_id, socks_port, **kwargs)
        return _measured_pass(probe_id, leak._severity_for(probe_id))

    monkeypatch.setattr(leak, "run_probe", probe_substitute)
    events = []
    vpn_kill_switch.register_kill_callback(
        lambda tunnel_id, state: events.append((tunnel_id, state))
    )
    vpn_kill_switch.kill_tunnel("row-293", reason="pre-existing confirmed leak")

    threshold = vpn_kill_switch.AUTO_CLEAR_THRESHOLD
    assert threshold == 2
    aggregates = []
    for attempt in range(threshold):
        aggregates.append(
            leak.run_all_probes("row-293", expected_exit_ip=_EXPECTED_V6)
        )
        state = vpn_kill_switch.get_kill_state("row-293")
        assert state is not None
        if attempt + 1 < threshold:
            assert state["state"] == "killed"
            assert state["auto_cleared_streak"] == attempt + 1

    assert len(aggregates) == threshold == 2
    assert all(aggregate.critical_failures == 0 for aggregate in aggregates)
    assert all(aggregate.critical_unknowns == 0 for aggregate in aggregates)
    assert all(aggregate.all_critical_measured is True for aggregate in aggregates)
    assert all(aggregate.passed is True for aggregate in aggregates)
    assert len(probe_calls) == len(leak.ALL_PROBES) * threshold == 12
    expected_probe_calls = []
    for _attempt in range(threshold):
        for probe_id in leak.ALL_PROBES:
            kwargs = {"tunnel_id": "row-293"}
            if probe_id == leak.ProbeId.IPV6.value:
                kwargs["expected_exit_ip"] = _EXPECTED_V6
            expected_probe_calls.append((probe_id, _SOCKS_PORT, kwargs))
    assert len(expected_probe_calls) == len(probe_calls) == 12
    assert probe_calls == expected_probe_calls
    ipv6_calls = [call for call in probe_calls if call[0] == leak.ProbeId.IPV6.value]
    assert ipv6_calls == [
        (
            leak.ProbeId.IPV6.value,
            _SOCKS_PORT,
            {"tunnel_id": "row-293", "expected_exit_ip": _EXPECTED_V6},
        )
    ] * threshold
    assert len(endpoint_calls) == threshold == 2
    assert endpoint_calls == [
        (leak.IPV6_ENDPOINT, f"socks5://127.0.0.1:{_SOCKS_PORT}", ("ip",))
    ] * threshold
    state = vpn_kill_switch.get_kill_state("row-293")
    assert state is not None
    assert state["state"] == "cleared"
    assert state["auto_cleared_streak"] == 0
    assert events == [("row-293", "killed"), ("row-293", "cleared")]


def test_every_unverified_ipv6_outcome_remains_unknown(monkeypatch):
    from bulk_downloader import vpn_leak_tests as leak

    def endpoint_error(*_args, **_kwargs):
        raise OSError("row-293 synthetic endpoint outage")

    cases = (
        (
            "empty_response",
            lambda *_args, **_kwargs: None,
            _EXPECTED_V6,
            "IPv6 endpoint returned no address; reachability was not measured",
        ),
        (
            "exception",
            endpoint_error,
            _EXPECTED_V6,
            "IPv6 endpoint raised before reachability could be measured",
        ),
        (
            "ipv4_fallback",
            lambda *_args, **_kwargs: "198.51.100.293",
            _EXPECTED_V6,
            "dual-stack endpoint used IPv4; IPv6 exposure was not measured",
        ),
        (
            "v6_without_expected",
            lambda *_args, **_kwargs: _EXPECTED_V6,
            None,
            "IPv6 is reachable but no provider-supplied expected address is available",
        ),
        (
            "v6_mismatch",
            lambda *_args, **_kwargs: "2001:db8::294",
            _EXPECTED_V6,
            "observed IPv6 address did not match the provider-supplied expected address",
        ),
    )
    assert len(cases) == 5
    assert {name for name, *_rest in cases} == {
        "empty_response",
        "exception",
        "ipv4_fallback",
        "v6_without_expected",
        "v6_mismatch",
    }

    endpoint_calls = []
    observed = {}
    for index, (name, response, expected, note) in enumerate(cases):
        def endpoint(url, proxy_url, field_names, *, _response=response):
            endpoint_calls.append((name, url, proxy_url, field_names))
            return _response(url, proxy_url, field_names)

        monkeypatch.setattr(leak, "_http_get_json_field", endpoint)
        kwargs = {} if expected is None else {"expected_exit_ip": expected}
        result = leak._probe_ipv6(_SOCKS_PORT + index, **kwargs)
        assert result.probe_id == leak.ProbeId.IPV6.value
        assert result.severity == leak.Severity.CRITICAL.value
        assert result.passed is None
        assert result.state == leak.ProbeState.UNKNOWN.value
        assert result.details["classification"] == "could_not_measure"
        assert result.details["note"] == note
        if name == "exception":
            assert result.error == "OSError: row-293 synthetic endpoint outage"
        else:
            assert result.error is None
        observed[name] = result.state

    assert len(endpoint_calls) == len(cases) == 5
    assert endpoint_calls == [
        (
            name,
            leak.IPV6_ENDPOINT,
            f"socks5://127.0.0.1:{_SOCKS_PORT + index}",
            ("ip",),
        )
        for index, (name, *_rest) in enumerate(cases)
    ]
    assert len(observed) == len(cases) == 5
    assert set(observed.values()) == {leak.ProbeState.UNKNOWN.value}


def test_ipv4_fallback_still_never_clears_the_switch(monkeypatch, request):
    from bulk_downloader import vpn_kill_switch
    from bulk_downloader import vpn_leak_tests as leak

    vpn_kill_switch._reset_for_tests()
    request.addfinalizer(vpn_kill_switch._reset_for_tests)
    vpn_kill_switch.set_auto_recover(False)
    endpoint_calls = []

    def ipv4_fallback(url, proxy_url, field_names):
        endpoint_calls.append((url, proxy_url, field_names))
        return "198.51.100.293"

    monkeypatch.setattr(leak, "_http_get_json_field", ipv4_fallback)
    ipv6_result = leak._probe_ipv6(
        _SOCKS_PORT, expected_exit_ip=_EXPECTED_V6
    )
    assert endpoint_calls == [
        (leak.IPV6_ENDPOINT, f"socks5://127.0.0.1:{_SOCKS_PORT}", ("ip",))
    ]
    assert ipv6_result.passed is None
    aggregate = _aggregate_with_real_ipv6(ipv6_result)
    assert aggregate.critical_failures == 0
    assert aggregate.critical_unknowns == 1
    assert aggregate.all_critical_measured is False

    events = []
    vpn_kill_switch.register_kill_callback(
        lambda tunnel_id, state: events.append((tunnel_id, state))
    )
    vpn_kill_switch.kill_tunnel("row-293-v4", reason="pre-existing confirmed leak")
    notifications = 0
    for _attempt in range(vpn_kill_switch.AUTO_CLEAR_THRESHOLD):
        vpn_kill_switch.notify_leak_test_result("row-293-v4", aggregate)
        notifications += 1

    assert notifications == vpn_kill_switch.AUTO_CLEAR_THRESHOLD == 2
    state = vpn_kill_switch.get_kill_state("row-293-v4")
    assert state is not None
    assert state["state"] == "killed"
    assert state["auto_cleared_streak"] == 0
    assert events == [("row-293-v4", "killed")]


def test_leak_test_api_forwards_provider_expected_exit_address(monkeypatch):
    from flask import Flask

    from bulk_downloader import app_vpn_api, vpn
    from bulk_downloader import vpn_leak_tests as leak

    monkeypatch.setattr(vpn, "get_tunnel", lambda tunnel_id: object())
    calls = []

    def run_all_substitute(
        tunnel_id, expected_country=None, expected_exit_ip=None
    ):
        calls.append((tunnel_id, expected_country, expected_exit_ip))
        return SimpleNamespace(to_dict=lambda: {"measured": True})

    monkeypatch.setattr(leak, "run_all_probes", run_all_substitute)
    app = Flask("row-293-api")
    app_vpn_api.register_routes(app)
    client = app.test_client()

    supplied = client.post(
        "/api/vpn/tunnels/row-293-api/leak_test/run",
        json={
            "expected_country": "GB",
            "expected_exit_ip": _EXPECTED_V6,
        },
    )
    omitted = client.post(
        "/api/vpn/tunnels/row-293-api/leak_test/run",
        json={"expected_country": "GB"},
    )

    assert supplied.status_code == omitted.status_code == 200
    assert supplied.get_json()["result"] == {"measured": True}
    assert omitted.get_json()["result"] == {"measured": True}
    assert calls == [
        ("row-293-api", "GB", _EXPECTED_V6),
        ("row-293-api", "GB", None),
    ]


def test_transform_control_imports_probe_without_asserting_ipv6_behavior():
    from bulk_downloader import vpn_leak_tests as leak

    assert leak.__name__ == "bulk_downloader.vpn_leak_tests"
