"""Row 286: unavailable safety measurements must hold permission closed.

F35 is exercised through the real IPv6 probe, aggregate, and process kill
switch.  A dual-stack endpoint returning IPv4 has not measured IPv6 leak
exposure, so it is UNKNOWN just like an empty response, unverified IPv6, or an
endpoint exception.  The paired measured aggregate proves the switch can
still clear when every critical result is actually known.
"""
from __future__ import annotations

import builtins
import logging
import threading


BD_GATE_SCOPE = "repo-wide"


def _aggregate_with_ipv6(ipv6_result):
    from bulk_downloader.vpn_leak_tests import (
        ALL_PROBES,
        ProbeId,
        ProbeResult,
        Severity,
        _aggregate_probe_results,
    )

    probes = [
        ProbeResult(ProbeId.DNS.value, True, Severity.CRITICAL.value),
        ProbeResult(ProbeId.IPV4.value, True, Severity.CRITICAL.value),
        ipv6_result,
        ProbeResult(ProbeId.WEBRTC.value, True, Severity.CRITICAL.value),
        ProbeResult(ProbeId.GEO.value, True, Severity.WARNING.value),
        ProbeResult(ProbeId.TIMEZONE.value, True, Severity.WARNING.value),
    ]
    aggregate = _aggregate_probe_results("row-286", probes, timestamp=286.0)
    assert len(probes) == len(ALL_PROBES) == 6
    assert {probe.probe_id for probe in probes} == set(ALL_PROBES)
    return aggregate


def test_f35_every_indeterminate_ipv6_outcome_holds_an_armed_switch(
    monkeypatch, request
):
    from bulk_downloader import vpn_kill_switch as kill_switch
    from bulk_downloader import vpn_leak_tests as leak

    def endpoint_error(*_args, **_kwargs):
        raise OSError("row-286 synthetic endpoint outage")

    cases = (
        ("no_address", lambda *_args, **_kwargs: None),
        ("ipv4_fallback", lambda *_args, **_kwargs: "198.51.100.86"),
        ("unverified_ipv6", lambda *_args, **_kwargs: "2001:db8::286"),
        ("endpoint_error", endpoint_error),
    )
    assert len(cases) == 4
    assert {name for name, _response in cases} == {
        "no_address", "ipv4_fallback", "unverified_ipv6", "endpoint_error"
    }

    kill_switch._reset_for_tests()
    request.addfinalizer(kill_switch._reset_for_tests)
    kill_switch.set_auto_recover(False)
    events = []
    kill_switch.register_kill_callback(
        lambda tunnel_id, state: events.append((tunnel_id, state))
    )

    calls = []
    observed = {}
    for index, (name, response) in enumerate(cases):
        def http_substitute(url, proxy_url, field_names, *, _response=response):
            calls.append((url, proxy_url, field_names))
            return _response(url, proxy_url, field_names)

        monkeypatch.setattr(leak, "_http_get_json_field", http_substitute)
        result = leak._probe_ipv6(19286 + index)
        assert calls[-1] == (
            leak.IPV6_ENDPOINT,
            f"socks5://127.0.0.1:{19286 + index}",
            ("ip",),
        )
        assert result.probe_id == leak.ProbeId.IPV6.value
        assert result.severity == leak.Severity.CRITICAL.value

        aggregate = _aggregate_with_ipv6(result)
        assert aggregate.critical_failures == 0
        tunnel_id = f"row-286-{name}"
        kill_switch.kill_tunnel(tunnel_id, reason="pre-existing confirmed leak")
        assert events.count((tunnel_id, "killed")) == 1
        for _attempt in range(kill_switch.AUTO_CLEAR_THRESHOLD):
            kill_switch.notify_leak_test_result(tunnel_id, aggregate)
        state = kill_switch.get_kill_state(tunnel_id)
        assert state is not None
        observed[name] = (
            result.state,
            result.passed,
            aggregate.critical_unknowns,
            aggregate.all_critical_measured,
            state["state"],
            state["auto_cleared_streak"],
        )

    assert len(calls) == len(cases) == 4
    assert len(observed) == len(cases) == 4
    expected_hold = (leak.ProbeState.UNKNOWN.value, None, 1, False, "killed", 0)
    assert observed["ipv4_fallback"] == expected_hold, (
        "an IPv4 answer from a dual-stack endpoint did not measure IPv6 leak "
        f"exposure and must HOLD, observed={observed['ipv4_fallback']!r}"
    )
    assert set(observed.values()) == {expected_hold}
    assert events == [
        (f"row-286-{name}", "killed") for name, _response in cases
    ]


def test_f35_fully_measured_critical_passes_still_clear_the_switch(request):
    from bulk_downloader import vpn_kill_switch as kill_switch
    from bulk_downloader.vpn_leak_tests import ProbeId, ProbeResult, Severity

    kill_switch._reset_for_tests()
    request.addfinalizer(kill_switch._reset_for_tests)
    kill_switch.set_auto_recover(False)
    measured_ipv6 = ProbeResult(
        ProbeId.IPV6.value,
        True,
        Severity.CRITICAL.value,
        details={"measurement": "provider-verified tunnel address"},
    )
    aggregate = _aggregate_with_ipv6(measured_ipv6)
    assert aggregate.critical_failures == 0
    assert aggregate.critical_unknowns == 0
    assert aggregate.all_critical_measured is True

    events = []
    kill_switch.register_kill_callback(
        lambda tunnel_id, state: events.append((tunnel_id, state))
    )
    kill_switch.kill_tunnel("row-286-measured", reason="old confirmed leak")
    for _attempt in range(kill_switch.AUTO_CLEAR_THRESHOLD):
        kill_switch.notify_leak_test_result("row-286-measured", aggregate)

    state = kill_switch.get_kill_state("row-286-measured")
    assert state is not None and state["state"] == "cleared"
    assert events == [
        ("row-286-measured", "killed"),
        ("row-286-measured", "cleared"),
    ]


def test_f35_transform_control_only_imports_the_mutated_module():
    from bulk_downloader import vpn_leak_tests as leak

    assert leak.__name__ == "bulk_downloader.vpn_leak_tests"


class _UnreadableConnection:
    def __init__(self, calls, diagnostic):
        self._calls = calls
        self._diagnostic = diagnostic

    def __enter__(self):
        self._calls.append(self._diagnostic)
        raise OSError(self._diagnostic)

    def __exit__(self, *_args):
        return False


def test_f36_unreadable_url_policy_fires_twice_and_refuses_enqueue(monkeypatch):
    from bulk_downloader import content_rights as rights
    from bulk_downloader import runner_queue

    reads = []
    ensures = []
    audits = []
    monkeypatch.setattr(rights, "_ensure_tables", lambda: ensures.append("ensure"))
    monkeypatch.setattr(
        rights._db,
        "db_conn",
        lambda: _UnreadableConnection(reads, "row-286 blocklist unreadable"),
    )
    monkeypatch.setattr(
        rights,
        "record_refusal",
        lambda target, reason: audits.append((target, reason)),
    )
    monkeypatch.setattr(runner_queue, "queue_bulk_upsert", lambda *_a, **_k: None)

    target = "https://blocked-if-policy-is-unknown.example/video"
    direct = rights.url_is_blocked(target)
    assert direct == {
        "blocked": None,
        "unknown": True,
        "error": "content-rights blocklist unreadable: row-286 blocklist unreadable",
    }

    class QueueProbe(runner_queue.QueueMixin):
        def __init__(self):
            self.config = {"download_dir": ""}
            self.jobs = {}
            self.urls = []
            self.site_id = "row-286-rights"
            self._lock = threading.RLock()
            self.log = logging.getLogger("row-286-rights")
            self.events = []

        def log_event(self, kind, message=None, **kwargs):
            self.events.append((kind, message, kwargs))

    probe = QueueProbe()
    added, refused, skipped = probe.load_urls([target])
    assert (added, refused, skipped) == (0, 1, 0)
    assert probe.jobs == {}
    assert len(ensures) == len(reads) == 2
    assert reads == ["row-286 blocklist unreadable"] * 2
    event_kinds = [event[0] for event in probe.events]
    assert event_kinds.count("content_rights_unknown") == 1
    assert event_kinds.count("import") == 1
    assert len(event_kinds) == 2
    assert len(audits) == 1 and audits[0][0] == target
    assert "unreadable" in audits[0][1]


def test_f37_hash_file_io_error_is_quarantined_not_verified(monkeypatch, tmp_path):
    from bulk_downloader import runner_integrity

    class HashProbe:
        _verify_hash_or_quarantine = (
            runner_integrity.IntegrityMixin._verify_hash_or_quarantine
        )

        def __init__(self):
            self.site_id = "row-286-hash"
            self.config = {"name": "Row 286 Hash"}
            self.updates = []
            self.events = []

        def _update_job(self, *args, **kwargs):
            self.updates.append((args, kwargs))

        def log_event(self, *args, **kwargs):
            self.events.append((args, kwargs))

    final = tmp_path / "unreadable.mp4"
    final.write_bytes(b"bytes whose advertised digest must be checked")
    open_calls = []

    def unreadable_open(path, mode="r", *_args, **_kwargs):
        open_calls.append((path, mode))
        raise OSError("row-286 digest read unavailable")

    db_rows = []
    monkeypatch.setattr(builtins, "open", unreadable_open)
    monkeypatch.setattr(
        runner_integrity, "db_log", lambda *args: db_rows.append(args)
    )
    probe = HashProbe()
    result = probe._verify_hash_or_quarantine(
        "https://example.test/hash-io",
        "sha256",
        "0" * 64,
        final,
        final.name,
        final.stat().st_size,
    )

    assert result is False
    assert open_calls == [(final, "rb")]
    assert not final.exists()
    assert (tmp_path / "_failed" / final.name).read_bytes() == (
        b"bytes whose advertised digest must be checked"
    )
    assert len(probe.updates) == 1
    assert probe.updates[0][0][1] == "failed"
    assert "OSError: row-286 digest read unavailable" in probe.updates[0][0][2]
    assert probe.events == []
    assert len(db_rows) == 1
    assert "hash verification unavailable" in db_rows[0][-1]


def test_f38_both_unreadable_counters_hold_configured_caps(monkeypatch):
    from bulk_downloader import daily_budget
    from bulk_downloader.runner import SiteRunner

    raw_reads = []
    monkeypatch.setattr(daily_budget, "_ensure_table", lambda: None)
    monkeypatch.setattr(
        daily_budget._db,
        "db_conn",
        lambda: _UnreadableConnection(raw_reads, "row-286 counter unreadable"),
    )
    assert daily_budget.bytes_today("site") is None
    assert daily_budget.bytes_today_all() is None
    assert raw_reads == ["row-286 counter unreadable"] * 2

    calls = {"site": 0, "global": 0}

    def unavailable_site(_site_id):
        calls["site"] += 1
        return None

    def unavailable_global():
        calls["global"] += 1
        return None

    monkeypatch.setattr(daily_budget, "bytes_today", unavailable_site)
    monkeypatch.setattr(daily_budget, "bytes_today_all", unavailable_global)
    monkeypatch.setattr(daily_budget, "_GLOBAL_BUDGET", 286)

    site_report = daily_budget.is_over_budget(
        "site", site_cfg={"daily_byte_budget": 286}
    )
    global_report = daily_budget.is_over_global_budget()
    for report in (site_report, global_report):
        assert report["over"] is None
        assert report["unknown"] is True
        assert report["available"] is False
        assert report["used_bytes"] is None

    site_probe = SiteRunner.__new__(SiteRunner)
    site_probe.site_id = "row-286-site-cap"
    site_probe.config = {"daily_byte_budget": 286}
    site_probe._state = "running"
    site_hold = site_probe._resource_admission_hold()
    assert site_hold["state"] == site_probe._state == "daily_budget_unknown"

    global_probe = SiteRunner.__new__(SiteRunner)
    global_probe.site_id = "row-286-global-cap"
    global_probe.config = {}
    global_probe._state = "running"
    global_hold = global_probe._resource_admission_hold()
    assert (
        global_hold["state"]
        == global_probe._state
        == "global_daily_budget_unknown"
    )
    assert len(calls) == 2
    assert calls["site"] == 3
    assert calls["global"] == 2
