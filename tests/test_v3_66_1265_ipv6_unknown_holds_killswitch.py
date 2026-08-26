"""IPv6 leak measurements have a fail-closed third state.

F35's four formerly passing branches are driven through an in-process HTTP
substitute.  UNKNOWN is neither a confirmed leak nor evidence that may clear an
armed kill switch; a positive IPv4 fallback remains the v4-only negative
control and may clear it.
"""

from types import SimpleNamespace

BD_GATE_SCOPE = "repo-wide"


def _measured_pass(probe_id, severity):
    from bulk_downloader.vpn_leak_tests import ProbeResult

    return ProbeResult(probe_id=probe_id, passed=True, severity=severity)


def _aggregate_with(ipv6_result, *, ipv4_result=None):
    from bulk_downloader.vpn_leak_tests import (
        ALL_PROBES,
        AggregateResult,
        ProbeId,
        Severity,
        _aggregate_probe_results,
    )

    probes = [
        _measured_pass(ProbeId.DNS.value, Severity.CRITICAL.value),
        ipv4_result or _measured_pass(ProbeId.IPV4.value, Severity.CRITICAL.value),
        ipv6_result,
        _measured_pass(ProbeId.WEBRTC.value, Severity.CRITICAL.value),
        _measured_pass(ProbeId.GEO.value, Severity.WARNING.value),
        _measured_pass(ProbeId.TIMEZONE.value, Severity.WARNING.value),
    ]
    aggregate = _aggregate_probe_results("probe", probes, timestamp=123.0)
    assert isinstance(aggregate, AggregateResult)
    assert len(aggregate.probes) == len(ALL_PROBES) == 6
    assert {probe.probe_id for probe in aggregate.probes} == set(ALL_PROBES)
    return aggregate


def test_all_four_ipv6_branches_have_explicit_states_and_no_network(monkeypatch):
    from bulk_downloader import vpn_leak_tests as leak

    def no_ip(*_args, **_kwargs):
        return None

    def v4_fallback(*_args, **_kwargs):
        return "198.51.100.41"

    def reachable_unverified_v6(*_args, **_kwargs):
        return "2001:db8::41"

    def endpoint_error(*_args, **_kwargs):
        raise OSError("synthetic endpoint outage")

    cases = (
        ("no_ip_measured", no_ip, leak.ProbeState.UNKNOWN.value, None,
         "could_not_measure"),
        ("v4_fallback", v4_fallback, leak.ProbeState.PASS.value, True,
         "proven_absent"),
        ("v6_reachable_unverified", reachable_unverified_v6,
         leak.ProbeState.UNKNOWN.value, None, "could_not_measure"),
        ("exception", endpoint_error, leak.ProbeState.UNKNOWN.value, None,
         "could_not_measure"),
    )
    assert len(cases) == 4

    observed = {}
    for name, http_substitute, state, passed, classification in cases:
        monkeypatch.setattr(leak, "_http_get_json_field", http_substitute)
        result = leak._probe_ipv6(19001)
        observed[name] = result.state
        assert result.state == state, (name, result)
        assert result.passed is passed, (name, result)
        assert result.details["classification"] == classification, (name, result)
        assert result.severity == leak.Severity.CRITICAL.value

    assert len(observed) == len(cases) == 4
    assert set(observed) == {name for name, *_rest in cases}


def test_aggregate_distinguishes_measured_zero_from_unknown_zero(monkeypatch):
    from bulk_downloader import vpn_leak_tests as leak

    monkeypatch.setattr(
        leak, "_http_get_json_field", lambda *_args, **_kwargs: "198.51.100.42"
    )
    measured = _aggregate_with(leak._probe_ipv6(19002))

    monkeypatch.setattr(leak, "_http_get_json_field", lambda *_args, **_kwargs: None)
    unmeasured = _aggregate_with(leak._probe_ipv6(19002))

    assert measured.critical_failures == unmeasured.critical_failures == 0
    assert measured.critical_unknowns == 0
    assert measured.all_critical_measured is True
    assert measured.passed is True
    assert unmeasured.critical_unknowns == 1
    assert unmeasured.all_critical_measured is False
    assert unmeasured.passed is False
    assert unmeasured.summary.startswith("UNKNOWN:")
    assert "failed" not in unmeasured.summary.lower()

    wire = unmeasured.to_dict()
    assert wire["critical_failures"] == 0
    assert wire["critical_unknowns"] == 1
    assert wire["all_critical_measured"] is False
    ipv6_wire = next(
        probe for probe in wire["probes"]
        if probe["probe_id"] == leak.ProbeId.IPV6.value
    )
    assert ipv6_wire["passed"] is None
    assert ipv6_wire["state"] == leak.ProbeState.UNKNOWN.value


def test_unmeasurable_does_not_advance_or_report_a_confirmed_leak(monkeypatch):
    from bulk_downloader import vpn_kill_switch as kill_switch
    from bulk_downloader import vpn_leak_tests as leak

    kill_switch._reset_for_tests()
    kill_switch.set_auto_recover(False)
    monkeypatch.setattr(leak, "_http_get_json_field", lambda *_args, **_kwargs: None)
    aggregate = _aggregate_with(leak._probe_ipv6(19003))
    assert aggregate.critical_failures == 0
    assert aggregate.critical_unknowns == 1

    kill_switch.notify_leak_test_result("clear-unknown", aggregate)
    assert kill_switch.get_kill_state("clear-unknown") is None

    events = []
    kill_switch.register_kill_callback(lambda tunnel_id, state: events.append((tunnel_id, state)))
    kill_switch.kill_tunnel("unknown-v6", reason="pre-existing confirmed leak")

    monkeypatch.setattr(
        leak, "_http_get_json_field", lambda *_args, **_kwargs: "198.51.100.45"
    )
    measured = _aggregate_with(leak._probe_ipv6(19003))
    kill_switch.notify_leak_test_result("unknown-v6", measured)
    assert kill_switch.get_kill_state("unknown-v6")["auto_cleared_streak"] == 1

    for _ in range(kill_switch.AUTO_CLEAR_THRESHOLD + 1):
        kill_switch.notify_leak_test_result("unknown-v6", aggregate)

    state = kill_switch.get_kill_state("unknown-v6")
    assert state is not None
    assert state["state"] == "killed"
    assert state["auto_cleared_streak"] == 0
    assert state["reason"] == "pre-existing confirmed leak"
    assert events == [("unknown-v6", "killed")]
    kill_switch._reset_for_tests()


def test_proven_absent_v6_still_allows_v4_only_auto_clear(monkeypatch):
    from bulk_downloader import vpn_kill_switch as kill_switch
    from bulk_downloader import vpn_leak_tests as leak

    kill_switch._reset_for_tests()
    kill_switch.set_auto_recover(False)
    monkeypatch.setattr(
        leak, "_http_get_json_field", lambda *_args, **_kwargs: "198.51.100.43"
    )
    ipv6 = leak._probe_ipv6(19004)
    assert ipv6.state == leak.ProbeState.PASS.value
    assert ipv6.details["classification"] == "proven_absent"
    aggregate = _aggregate_with(ipv6)
    assert aggregate.critical_unknowns == aggregate.critical_failures == 0

    kill_switch.kill_tunnel("v4-only", reason="old confirmed leak")
    for _ in range(kill_switch.AUTO_CLEAR_THRESHOLD):
        kill_switch.notify_leak_test_result("v4-only", aggregate)

    assert kill_switch.is_killed("v4-only") is False
    assert kill_switch.get_kill_state("v4-only")["state"] == "cleared"
    kill_switch._reset_for_tests()


def test_real_critical_leak_still_fails_and_kills(monkeypatch):
    from bulk_downloader import vpn_kill_switch as kill_switch
    from bulk_downloader import vpn_leak_tests as leak

    kill_switch._reset_for_tests()
    kill_switch.set_auto_recover(False)
    calls = []

    def leaked_ip(*_args, **_kwargs):
        calls.append(1)
        return "203.0.113.99"

    monkeypatch.setattr(leak, "_http_get_json_field", leaked_ip)
    real_leak = leak._probe_ipv4(19005, expected_exit_ip="198.51.100.44")
    assert len(calls) == len(leak.IPV4_ENDPOINTS) == 3
    assert real_leak.passed is False
    assert real_leak.state == leak.ProbeState.FAIL.value

    proven_absent = leak._probe_ipv6(19005)
    assert proven_absent.details["classification"] == "proven_absent"
    aggregate = _aggregate_with(proven_absent, ipv4_result=real_leak)
    assert aggregate.critical_failures == 1
    assert aggregate.critical_unknowns == 0
    assert aggregate.summary.startswith("CRITICAL:")
    kill_switch.notify_leak_test_result("real-leak", aggregate)
    assert kill_switch.is_killed("real-leak") is True
    kill_switch._reset_for_tests()


def test_run_all_probes_surfaces_unknown_without_external_io(monkeypatch):
    """The public aggregate path, not only its helper, carries UNKNOWN."""
    from bulk_downloader import vpn, vpn_kill_switch
    from bulk_downloader import vpn_leak_tests as leak

    tunnel = SimpleNamespace(socks_port=19006, public_ip=None, last_health_check=None)
    monkeypatch.setattr(vpn, "get_tunnel", lambda tunnel_id: tunnel)
    called = []

    def fake_run_probe(probe_id, socks_port, **_kwargs):
        called.append((probe_id, socks_port))
        if probe_id == leak.ProbeId.IPV6.value:
            return leak.ProbeResult(
                probe_id=probe_id,
                passed=None,
                state=leak.ProbeState.UNKNOWN.value,
                severity=leak.Severity.CRITICAL.value,
                details={"classification": "could_not_measure"},
            )
        return _measured_pass(probe_id, leak._severity_for(probe_id))

    monkeypatch.setattr(leak, "run_probe", fake_run_probe)
    monkeypatch.setattr(vpn_kill_switch, "notify_leak_test_result", lambda *_args: None)
    aggregate = leak.run_all_probes("public-path")

    assert called == [(probe_id, 19006) for probe_id in leak.ALL_PROBES]
    assert len(called) == len(leak.ALL_PROBES) == 6
    assert aggregate.critical_failures == 0
    assert aggregate.critical_unknowns == 1
    assert aggregate.all_critical_measured is False
    assert aggregate.passed is False


def test_unknown_to_pass_transform_control_does_not_exercise_unknown_path():
    """The mutation is valid and importable when its branch is not driven."""
    from bulk_downloader.vpn_leak_tests import ProbeState, Severity

    result = _measured_pass("control", Severity.CRITICAL.value)
    assert result.state == ProbeState.PASS.value
    assert result.passed is True
