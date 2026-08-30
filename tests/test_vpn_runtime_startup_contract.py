"""Regression coverage for VPN startup and deployed site config shapes."""

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


def _flat_vpn_site(site_id, tunnel_id):
    return {
        site_id: {
            "vpn_enabled": True,
            "vpn_tunnel_id": tunnel_id,
            "vpn_required": True,
        },
    }


def _isolate_vpn_runtime(monkeypatch, tmp_path):
    monkeypatch.delenv("BD_DISABLE_VPN_RUNTIME", raising=False)
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    monkeypatch.setenv("BD_VPN_CONFIG_PATH", str(tmp_path / "tunnels.json"))


def _clear_vpn_test_state():
    from bulk_downloader import (
        vpn,
        vpn_config,
        vpn_kill_switch,
        vpn_leak_tests,
        vpn_runtime,
    )

    vpn_runtime._reset_for_tests()
    vpn_config._reset_for_tests()
    vpn._reset_for_tests()
    vpn_leak_tests._reset_for_tests()
    vpn_kill_switch._reset_for_tests()


def test_vpn_runtime_reads_canonical_flat_site_mapping(monkeypatch):
    from bulk_downloader import vpn_runtime

    monkeypatch.delenv("BD_DISABLE_VPN_RUNTIME", raising=False)
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    vpn_runtime._reset_for_tests()
    try:
        result = vpn_runtime.init({
            "site-a": {
                "vpn_enabled": True,
                "vpn_tunnel_id": "tun-a",
                "vpn_required": True,
            },
            "site-b": {"vpn_enabled": False, "vpn_tunnel_id": "tun-b"},
        }, start_monitors=False)

        assert result["site_to_tunnel"] == {"site-a": "tun-a"}
        assert vpn_runtime.is_vpn_required_for_site("site-a") is True
    finally:
        vpn_runtime._reset_for_tests()


def test_app_startup_calls_vpn_runtime_init():
    source = (Path(__file__).resolve().parents[1]
              / "bulk_downloader" / "app.py").read_text(encoding="utf-8")
    assert "vpn_runtime.init(" in source


def test_vpn_runtime_applies_persisted_auto_recover(monkeypatch):
    from bulk_downloader import vpn_config, vpn_kill_switch, vpn_runtime

    applied = []
    monkeypatch.delenv("BD_DISABLE_VPN_RUNTIME", raising=False)
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    monkeypatch.setattr(vpn_config, "load", lambda: {})
    monkeypatch.setattr(vpn_config, "register_loaded_tunnels", lambda: (0, []))
    monkeypatch.setattr(
        vpn_config,
        "get_global_settings",
        lambda: {"kill_switch_auto_recover": False,
                 "leak_test_interval_s": 1800},
    )
    monkeypatch.setattr(
        vpn_kill_switch, "set_auto_recover", lambda value: applied.append(value))
    vpn_runtime._reset_for_tests()
    try:
        vpn_runtime.init({}, start_monitors=False)
        assert applied == [False]
    finally:
        vpn_runtime._reset_for_tests()


def test_shutdown_rebinds_runtime_to_new_site_mapping(monkeypatch, tmp_path):
    from bulk_downloader import vpn_runtime

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    _clear_vpn_test_state()
    try:
        vpn_runtime.init(
            _flat_vpn_site("old-site", "tun-old"), start_monitors=False)

        vpn_runtime.shutdown()
        result = vpn_runtime.init(
            _flat_vpn_site("new-site", "tun-new"), start_monitors=False)

        assert result["site_to_tunnel"] == {"new-site": "tun-new"}
        assert vpn_runtime.get_tunnel_for_site("old-site") is None
        assert vpn_runtime.get_tunnel_for_site("new-site") == "tun-new"
    finally:
        _clear_vpn_test_state()


def test_shutdown_unregisters_callback_before_reinit(monkeypatch, tmp_path):
    from bulk_downloader import vpn_kill_switch, vpn_runtime

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    _clear_vpn_test_state()
    paused = []
    try:
        config = _flat_vpn_site("site-a", "tun-a")
        vpn_runtime.init(
            config, siterunner_pauser=paused.append, start_monitors=False)

        vpn_runtime.shutdown()
        result = vpn_runtime.init(
            config, siterunner_pauser=paused.append, start_monitors=False)
        vpn_kill_switch.kill_tunnel("tun-a", reason="test")

        assert result["site_to_tunnel"] == {"site-a": "tun-a"}
        assert paused == ["site-a"]
    finally:
        _clear_vpn_test_state()


def test_shutdown_waits_for_an_inflight_runtime_callback(monkeypatch, tmp_path):
    """No old-generation runner callback may land after shutdown returns."""
    from bulk_downloader import vpn_kill_switch, vpn_runtime

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    _clear_vpn_test_state()
    callback_entered = threading.Event()
    release_callback = threading.Event()
    shutdown_done = threading.Event()

    def pauser(_site_id):
        callback_entered.set()
        release_callback.wait(2)

    try:
        vpn_runtime.init(
            _flat_vpn_site("site-a", "tun-a"),
            siterunner_pauser=pauser,
            start_monitors=False,
        )
        callback_thread = threading.Thread(
            target=vpn_kill_switch.kill_tunnel,
            args=("tun-a",),
            kwargs={"reason": "test"},
        )
        callback_thread.start()
        assert callback_entered.wait(2)

        shutdown_thread = threading.Thread(
            target=lambda: (vpn_runtime.shutdown(), shutdown_done.set()))
        shutdown_thread.start()
        assert not shutdown_done.wait(0.2)

        release_callback.set()
        callback_thread.join(timeout=2)
        shutdown_thread.join(timeout=2)
        assert shutdown_done.is_set()
    finally:
        release_callback.set()
        _clear_vpn_test_state()


def test_shutdown_cleans_initialized_runtime_even_when_disabled(
    monkeypatch, tmp_path,
):
    from bulk_downloader import vpn_runtime

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    _clear_vpn_test_state()
    try:
        vpn_runtime.init(
            _flat_vpn_site("old-site", "tun-old"), start_monitors=False)

        monkeypatch.setenv("BD_DISABLE_VPN_RUNTIME", "1")
        vpn_runtime.shutdown()
        monkeypatch.delenv("BD_DISABLE_VPN_RUNTIME")
        result = vpn_runtime.init(
            _flat_vpn_site("new-site", "tun-new"), start_monitors=False)

        assert result["site_to_tunnel"] == {"new-site": "tun-new"}
        assert vpn_runtime.get_tunnel_for_site("old-site") is None
    finally:
        monkeypatch.delenv("BD_DISABLE_VPN_RUNTIME", raising=False)
        _clear_vpn_test_state()


def test_hard_reset_restores_all_vpn_leaf_module_state(monkeypatch, tmp_path):
    from bulk_downloader import (
        vpn,
        vpn_config,
        vpn_kill_switch,
        vpn_leak_tests,
        vpn_runtime,
    )

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    _clear_vpn_test_state()
    try:
        default_auto_recover = vpn_config.get_global_settings()[
            "kill_switch_auto_recover"
        ]
        vpn_config.update_global_settings(
            kill_switch_auto_recover=not default_auto_recover)
        vpn.register_tunnel(
            name="test tunnel",
            provider="generic",
            backend="wireguard",
            tunnel_id="tun-leaf",
        )
        vpn_kill_switch.kill_tunnel("tun-leaf", reason="test")
        vpn_leak_tests.record_external_probe_result(
            "tun-leaf",
            vpn_leak_tests.ProbeId.WEBRTC.value,
            {"passed": True, "details": {"public_ips": []}},
        )
        assert vpn_config.get_global_settings()[
            "kill_switch_auto_recover"
        ] is not default_auto_recover
        assert [t.tunnel_id for t in vpn.list_tunnels()] == ["tun-leaf"]
        assert vpn_kill_switch.get_kill_state("tun-leaf") is not None
        assert vpn_leak_tests.run_probe(
            vpn_leak_tests.ProbeId.WEBRTC.value,
            socks_port=0,
            tunnel_id="tun-leaf",
        ).passed is True

        vpn_runtime._reset_for_tests()

        assert vpn_config.get_global_settings()[
            "kill_switch_auto_recover"
        ] is default_auto_recover
        assert vpn.list_tunnels() == []
        assert vpn_kill_switch.get_kill_state("tun-leaf") is None
        webrtc = vpn_leak_tests.run_probe(
            vpn_leak_tests.ProbeId.WEBRTC.value,
            socks_port=0,
            tunnel_id="tun-leaf",
        )
        assert webrtc.passed is False
    finally:
        _clear_vpn_test_state()


def test_hard_reset_waits_for_inflight_vpn_health_monitor(monkeypatch, tmp_path):
    """Reset cannot drop a live monitor handle and reuse its stop event."""
    from bulk_downloader import vpn, vpn_runtime

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    _clear_vpn_test_state()
    entered = threading.Event()
    release = threading.Event()
    reset_done = threading.Event()
    reset_errors = []

    def blocking_health_pass():
        entered.set()
        release.wait(2)

    monkeypatch.setattr(vpn, "_run_health_pass", blocking_health_pass)
    reset_thread = None
    old_monitor = None
    try:
        vpn.start_health_thread()
        old_monitor = vpn._health_thread
        assert old_monitor is not None
        assert entered.wait(2)

        def reset_runtime():
            try:
                vpn_runtime._reset_for_tests()
            except Exception as exc:  # preserve the owner-thread traceback
                reset_errors.append(exc)
            finally:
                reset_done.set()

        reset_thread = threading.Thread(target=reset_runtime)
        reset_thread.start()
        assert not reset_done.wait(0.2), (
            "reset returned while its old VPN health pass was still in flight")

        release.set()
        reset_thread.join(timeout=2)
        assert reset_done.is_set()
        assert reset_errors == []
        assert not old_monitor.is_alive()
        assert vpn._health_thread is None
    finally:
        release.set()
        vpn._health_stop.set()
        if reset_thread is not None:
            reset_thread.join(timeout=2)
        if old_monitor is not None:
            old_monitor.join(timeout=2)
        _clear_vpn_test_state()


def test_hard_reset_waits_for_inflight_vpn_leak_monitor(monkeypatch, tmp_path):
    """The leak-monitor owner must prove its old generation has exited too."""
    from bulk_downloader import vpn_leak_tests, vpn_runtime

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    _clear_vpn_test_state()
    entered = threading.Event()
    release = threading.Event()
    reset_done = threading.Event()
    reset_errors = []

    def blocking_monitor(_interval_s):
        entered.set()
        release.wait(2)

    monkeypatch.setattr(vpn_leak_tests, "_monitor_loop", blocking_monitor)
    reset_thread = None
    old_monitor = None
    try:
        vpn_leak_tests.start_periodic_monitor(interval_s=1)
        old_monitor = vpn_leak_tests._monitor_thread
        assert old_monitor is not None
        assert entered.wait(2)

        def reset_runtime():
            try:
                vpn_runtime._reset_for_tests()
            except Exception as exc:
                reset_errors.append(exc)
            finally:
                reset_done.set()

        reset_thread = threading.Thread(target=reset_runtime)
        reset_thread.start()
        assert not reset_done.wait(0.2), (
            "reset returned while its old VPN leak monitor was still in flight")

        release.set()
        reset_thread.join(timeout=2)
        assert reset_done.is_set()
        assert reset_errors == []
        assert not old_monitor.is_alive()
        assert vpn_leak_tests._monitor_thread is None
    finally:
        release.set()
        vpn_leak_tests._monitor_stop.set()
        if reset_thread is not None:
            reset_thread.join(timeout=2)
        if old_monitor is not None:
            old_monitor.join(timeout=2)
        _clear_vpn_test_state()


def test_shutdown_joins_leak_monitor_outside_runtime_callback_lock(
    monkeypatch, tmp_path,
):
    """A monitor waiting on the runtime callback lock must not time out join."""
    from bulk_downloader import vpn_leak_tests, vpn_runtime

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    _clear_vpn_test_state()
    monitor_started = threading.Event()
    allow_callback = threading.Event()
    callback_attempted = threading.Event()
    monitor_done = threading.Event()
    stop_results = []
    real_stop = vpn_leak_tests.stop_periodic_monitor

    def callback_monitor(_interval_s):
        monitor_started.set()
        allow_callback.wait(2)
        callback_attempted.set()
        vpn_runtime._on_kill_switch_event("tun-lock-order", "killed")
        monitor_done.set()

    def observed_stop():
        allow_callback.set()
        assert callback_attempted.wait(2)
        result = real_stop(timeout=0.5)
        stop_results.append(result)
        return result

    monkeypatch.setattr(vpn_leak_tests, "_monitor_loop", callback_monitor)
    monkeypatch.setattr(vpn_leak_tests, "stop_periodic_monitor", observed_stop)
    try:
        vpn_runtime.init({}, start_monitors=False)
        vpn_leak_tests.start_periodic_monitor(interval_s=1)
        assert monitor_started.wait(2)

        vpn_runtime.shutdown()

        assert stop_results == [True], (
            "shutdown joined the leak monitor while holding _init_lock")
        assert monitor_done.wait(2)
    finally:
        monkeypatch.setattr(
            vpn_leak_tests, "stop_periodic_monitor", real_stop)
        allow_callback.set()
        real_stop(timeout=2)
        _clear_vpn_test_state()


def test_reinit_refuses_while_old_leak_monitor_teardown_is_pending(
    monkeypatch, tmp_path,
):
    """A timed-out stop cannot publish a new initialized runtime without a monitor."""
    from bulk_downloader import vpn_leak_tests, vpn_runtime

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    _clear_vpn_test_state()
    entered = threading.Event()
    release = threading.Event()
    real_stop = vpn_leak_tests.stop_periodic_monitor

    def blocked_monitor(_interval_s):
        entered.set()
        release.wait(2)

    monkeypatch.setattr(vpn_leak_tests, "_monitor_loop", blocked_monitor)
    monkeypatch.setattr(
        vpn_leak_tests,
        "stop_periodic_monitor",
        lambda: real_stop(timeout=0.05),
    )
    try:
        vpn_runtime.init({}, start_monitors=False)
        vpn_leak_tests.start_periodic_monitor(interval_s=1)
        assert entered.wait(2)

        vpn_runtime.shutdown()
        result = vpn_runtime.init({}, start_monitors=False)

        assert result["ok"] is False, result
        assert result["reason"] == "monitor teardown pending", result
        assert vpn_runtime._initialized is False
    finally:
        release.set()
        monkeypatch.setattr(
            vpn_leak_tests, "stop_periodic_monitor", real_stop)
        real_stop(timeout=2)
        _clear_vpn_test_state()


def test_reset_cancels_old_kill_cycle_before_reused_tunnel_reinit(
        monkeypatch, tmp_path,
):
    """An old auto-cycle must never publish into a new runtime generation."""
    from bulk_downloader import vpn, vpn_kill_switch, vpn_runtime

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    _clear_vpn_test_state()
    cycle_called = threading.Event()
    old_paused = []
    new_paused = []
    monkeypatch.setattr(vpn_kill_switch, "CYCLE_BACKOFF_S", 0.25)

    def failed_cycle(_tunnel_id):
        cycle_called.set()
        return False

    monkeypatch.setattr(vpn, "cycle_tunnel", failed_cycle)
    old_threads = []
    try:
        vpn_runtime.init(
            _flat_vpn_site("old-site", "tun-reused"),
            siterunner_pauser=old_paused.append,
            start_monitors=False,
        )
        vpn_kill_switch.kill_tunnel("tun-reused", reason="old generation")
        old_threads = [
            thread for thread in threading.enumerate()
            if thread.name == "bd-killswitch-cycle-tun-reused"
        ]
        assert len(old_threads) == 1

        vpn_runtime._reset_for_tests()
        result = vpn_runtime.init(
            _flat_vpn_site("new-site", "tun-reused"),
            siterunner_pauser=new_paused.append,
            start_monitors=False,
        )
        assert result["ok"] is True

        old_threads[0].join(timeout=1)
        assert not old_threads[0].is_alive()
        assert not cycle_called.is_set()
        assert new_paused == []
    finally:
        monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
        for thread in old_threads:
            thread.join(timeout=1)
        _clear_vpn_test_state()


def test_copied_old_kill_callback_cannot_target_reinitialized_runtime(
        monkeypatch, tmp_path,
):
    """Unregister must revoke a callback already copied by the dispatcher."""
    from bulk_downloader import vpn_kill_switch, vpn_runtime

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    _clear_vpn_test_state()
    blocker_entered = threading.Event()
    release_blocker = threading.Event()
    new_paused = []

    def blocking_callback(_tunnel_id, _state):
        blocker_entered.set()
        release_blocker.wait(2)

    vpn_kill_switch.register_kill_callback(blocking_callback)
    callback_thread = None
    try:
        vpn_runtime.init(
            _flat_vpn_site("old-site", "tun-reused"),
            siterunner_pauser=lambda _sid: None,
            start_monitors=False,
        )
        callback_thread = threading.Thread(
            target=vpn_kill_switch.kill_tunnel,
            args=("tun-reused",),
            kwargs={"reason": "queued old callback"},
        )
        callback_thread.start()
        assert blocker_entered.wait(2)

        vpn_runtime.shutdown()
        result = vpn_runtime.init(
            _flat_vpn_site("new-site", "tun-reused"),
            siterunner_pauser=new_paused.append,
            start_monitors=False,
        )
        assert result["ok"] is True

        release_blocker.set()
        callback_thread.join(timeout=2)
        assert not callback_thread.is_alive()
        assert new_paused == []
    finally:
        release_blocker.set()
        if callback_thread is not None:
            callback_thread.join(timeout=2)
        _clear_vpn_test_state()


def test_stale_clear_transition_cannot_resume_reinitialized_runtime(
        monkeypatch, tmp_path,
):
    """Delivery delayed before its snapshot must retain exact version."""
    from bulk_downloader import vpn_kill_switch, vpn_runtime

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    _clear_vpn_test_state()
    clear_entered = threading.Event()
    release_clear = threading.Event()
    new_paused = []
    new_resumed = []
    real_fire = vpn_kill_switch._fire_transition_callbacks
    blocked_once = False

    def gated_fire(tunnel_id, new_state, version, **kwargs):
        nonlocal blocked_once
        if new_state == "cleared" and not blocked_once:
            blocked_once = True
            clear_entered.set()
            release_clear.wait(2)
        return real_fire(tunnel_id, new_state, version, **kwargs)

    clear_thread = None
    monkeypatch.setattr(
        vpn_kill_switch, "_fire_transition_callbacks", gated_fire)
    try:
        vpn_runtime.init(
            _flat_vpn_site("old-site", "tun-reused"),
            siterunner_pauser=lambda _sid: None,
            siterunner_resumer=lambda _sid: None,
            start_monitors=False,
        )
        vpn_kill_switch.kill_tunnel("tun-reused", reason="old kill")
        clear_thread = threading.Thread(
            target=vpn_kill_switch.clear_kill,
            args=("tun-reused",),
        )
        clear_thread.start()
        assert clear_entered.wait(2)

        assert vpn_runtime.shutdown() is True
        result = vpn_runtime.init(
            _flat_vpn_site("new-site", "tun-reused"),
            siterunner_pauser=new_paused.append,
            siterunner_resumer=new_resumed.append,
            start_monitors=False,
        )
        assert result["ok"] is True
        vpn_kill_switch.kill_tunnel("tun-reused", reason="new kill")
        assert new_paused == ["new-site"]

        release_clear.set()
        clear_thread.join(timeout=2)
        assert not clear_thread.is_alive()
        assert new_resumed == []
        assert vpn_kill_switch.get_kill_state(
            "tun-reused")["state"] == "killed"
    finally:
        release_clear.set()
        if clear_thread is not None:
            clear_thread.join(timeout=2)
        _clear_vpn_test_state()


def test_stale_success_probe_cannot_clear_a_new_kill_generation(
        monkeypatch, tmp_path,
):
    """Auto-clear is conditional on the exact state identity it measured."""
    from bulk_downloader import vpn_kill_switch

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    _clear_vpn_test_state()
    vpn_kill_switch.set_auto_recover(False)
    probe_entered = threading.Event()
    release_probe = threading.Event()
    real_clear = vpn_kill_switch.clear_kill
    aggregate = SimpleNamespace(
        critical_failures=0,
        critical_unknowns=0,
        all_critical_measured=True,
        summary="all measured",
    )

    def gated_clear(tunnel_id, **conditions):
        if conditions:
            probe_entered.set()
            release_probe.wait(2)
        return real_clear(tunnel_id, **conditions)

    notify_thread = None
    monkeypatch.setattr(vpn_kill_switch, "clear_kill", gated_clear)
    try:
        vpn_kill_switch.kill_tunnel("tun-probe", reason="old kill")
        vpn_kill_switch.notify_leak_test_result("tun-probe", aggregate)
        notify_thread = threading.Thread(
            target=vpn_kill_switch.notify_leak_test_result,
            args=("tun-probe", aggregate),
        )
        notify_thread.start()
        assert probe_entered.wait(2)

        assert real_clear("tun-probe") is True
        vpn_kill_switch.kill_tunnel("tun-probe", reason="new kill")
        release_probe.set()
        notify_thread.join(timeout=2)
        assert not notify_thread.is_alive()
        state = vpn_kill_switch.get_kill_state("tun-probe")
        assert state["state"] == "killed"
        assert state["reason"] == "new kill"
        assert state["auto_cleared_streak"] == 0
    finally:
        release_probe.set()
        if notify_thread is not None:
            notify_thread.join(timeout=2)
        vpn_kill_switch._reset_for_tests()


def test_probe_measurement_token_rejects_results_from_before_shutdown(
        monkeypatch, tmp_path,
):
    """A probe delayed before notification cannot adopt the next runtime."""
    from bulk_downloader import vpn_kill_switch

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    _clear_vpn_test_state()
    vpn_kill_switch.set_auto_recover(False)
    aggregate = SimpleNamespace(
        critical_failures=0,
        critical_unknowns=0,
        all_critical_measured=True,
        summary="old measured success",
    )
    try:
        vpn_kill_switch.kill_tunnel("tun-measure", reason="old kill")
        old_measurement = (
            vpn_kill_switch.capture_leak_measurement_token("tun-measure"))
        vpn_kill_switch.clear_kill("tun-measure")
        assert vpn_kill_switch.shutdown() is True
        vpn_kill_switch.kill_tunnel("tun-measure", reason="new kill")

        vpn_kill_switch.notify_leak_test_result(
            "tun-measure", aggregate, old_measurement)
        vpn_kill_switch.notify_leak_test_result(
            "tun-measure", aggregate, old_measurement)

        state = vpn_kill_switch.get_kill_state("tun-measure")
        assert state["state"] == "killed"
        assert state["reason"] == "new kill"
        assert state["auto_cleared_streak"] == 0
    finally:
        vpn_kill_switch._reset_for_tests()


def test_health_monitor_start_failure_rolls_back_published_generation(
        monkeypatch, tmp_path,
):
    """A Thread.start exception must leave reset and a later start usable."""
    from bulk_downloader import vpn

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    vpn._reset_for_tests()
    real_start = threading.Thread.start
    failed = False

    def fail_health_once(thread):
        nonlocal failed
        if thread.name == "bd-vpn-health" and not failed:
            failed = True
            raise RuntimeError("planted health start failure")
        return real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_health_once)
    try:
        with pytest.raises(RuntimeError, match="planted health start failure"):
            vpn.start_health_thread()
        assert vpn._health_thread is None
        vpn._reset_for_tests()

        vpn.start_health_thread()
        retry = vpn._health_thread
        assert retry is not None and retry.is_alive()
    finally:
        vpn.shutdown()
        vpn._reset_for_tests()


def test_leak_monitor_start_failure_rolls_back_published_generation(
        monkeypatch, tmp_path,
):
    """Leak-monitor Thread.start failure cannot poison hard reset/retry."""
    from bulk_downloader import vpn_leak_tests

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    vpn_leak_tests._reset_for_tests()
    real_start = threading.Thread.start
    failed = False

    def fail_leak_once(thread):
        nonlocal failed
        if thread.name == "bd-vpn-leak-monitor" and not failed:
            failed = True
            raise RuntimeError("planted leak start failure")
        return real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_leak_once)
    try:
        with pytest.raises(RuntimeError, match="planted leak start failure"):
            vpn_leak_tests.start_periodic_monitor(interval_s=1)
        assert vpn_leak_tests._monitor_thread is None
        vpn_leak_tests._reset_for_tests()

        vpn_leak_tests.start_periodic_monitor(interval_s=1)
        retry = vpn_leak_tests._monitor_thread
        assert retry is not None and retry.is_alive()
    finally:
        vpn_leak_tests.stop_periodic_monitor(timeout=2)
        vpn_leak_tests._reset_for_tests()


def test_runtime_init_fails_and_rolls_back_when_monitor_start_fails(
        monkeypatch, tmp_path,
):
    """Monitor start errors cannot publish a false initialized runtime."""
    from bulk_downloader import vpn, vpn_runtime

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    _clear_vpn_test_state()
    real_start = vpn.start_health_thread
    monkeypatch.setattr(
        vpn,
        "start_health_thread",
        lambda: (_ for _ in ()).throw(
            RuntimeError("planted runtime monitor start failure")),
    )
    try:
        result = vpn_runtime.init({}, start_monitors=True)
        assert result["ok"] is False, result
        assert result["reason"] == "monitor start failed", result
        assert vpn_runtime._initialized is False

        monkeypatch.setattr(vpn, "start_health_thread", real_start)
        retry = vpn_runtime.init({}, start_monitors=False)
        assert retry["ok"] is True and not retry.get("skipped", False)
    finally:
        monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
        _clear_vpn_test_state()


def test_kill_cycle_start_failure_restores_retry_budget_and_registry(
        monkeypatch, tmp_path,
):
    """Failed Thread.start must not consume an auto-recovery attempt."""
    from bulk_downloader import vpn_kill_switch

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    vpn_kill_switch._reset_for_tests()
    real_start = threading.Thread.start
    failed = False
    ran = threading.Event()

    def fail_cycle_once(thread):
        nonlocal failed
        if thread.name == "bd-killswitch-cycle-tun-start" and not failed:
            failed = True
            raise RuntimeError("planted cycle start failure")
        return real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_cycle_once)
    monkeypatch.setattr(
        vpn_kill_switch,
        "_auto_cycle_worker",
        lambda _tunnel_id: ran.set(),
    )
    try:
        with pytest.raises(RuntimeError, match="planted cycle start failure"):
            vpn_kill_switch.kill_tunnel(
                "tun-start", reason="start rollback")
        state = vpn_kill_switch.get_kill_state("tun-start")
        assert state["state"] == "killed"
        assert state["cycle_attempts"] == 0
        assert state["last_cycle_at"] is None
        assert vpn_kill_switch._cycle_records == {}
        assert "tun-start" not in vpn_kill_switch._cycle_current

        vpn_kill_switch.clear_kill("tun-start")
        vpn_kill_switch.kill_tunnel("tun-start", reason="retry")
        threads = [
            thread for thread in threading.enumerate()
            if thread.name == "bd-killswitch-cycle-tun-start"
        ]
        for thread in threads:
            thread.join(timeout=2)
        assert ran.wait(2)
        assert vpn_kill_switch.get_kill_state(
            "tun-start")["cycle_attempts"] == 1
    finally:
        vpn_kill_switch._reset_for_tests()


def test_shutdown_serializes_kill_admission_through_cycle_publication(
        monkeypatch, tmp_path,
):
    """A pre-shutdown kill cannot schedule its cycle after shutdown returns."""
    from bulk_downloader import vpn, vpn_kill_switch, vpn_runtime

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    _clear_vpn_test_state()
    admission_entered = threading.Event()
    release_admission = threading.Event()
    shutdown_done = threading.Event()
    new_paused = []
    real_get_auto_recover = vpn_kill_switch.get_auto_recover

    def blocked_admission():
        admission_entered.set()
        release_admission.wait(2)
        return real_get_auto_recover()

    monkeypatch.setattr(vpn_kill_switch, "get_auto_recover", blocked_admission)
    monkeypatch.setattr(vpn_kill_switch, "CYCLE_BACKOFF_S", 0.01)
    monkeypatch.setattr(vpn, "cycle_tunnel", lambda _tid: False)
    kill_thread = None
    shutdown_thread = None
    try:
        vpn_runtime.init(
            _flat_vpn_site("old-site", "tun-admission"),
            siterunner_pauser=lambda _sid: None,
            start_monitors=False,
        )
        kill_thread = threading.Thread(
            target=vpn_kill_switch.kill_tunnel,
            args=("tun-admission",),
            kwargs={"reason": "admission race"},
        )
        kill_thread.start()
        assert admission_entered.wait(2)

        shutdown_thread = threading.Thread(
            target=lambda: (vpn_runtime.shutdown(), shutdown_done.set()))
        shutdown_thread.start()
        shutdown_returned_early = shutdown_done.wait(0.2)
        if shutdown_returned_early:
            # Epoch-based implementations may finish shutdown before the old
            # caller resumes; reinitialize now to maximize the stale target.
            result = vpn_runtime.init(
                _flat_vpn_site("new-site", "tun-admission"),
                siterunner_pauser=new_paused.append,
                start_monitors=False,
            )
            release_admission.set()
        else:
            # Lock-serialized implementations hold shutdown until admission
            # and publication finish, then cancel that published generation.
            release_admission.set()
            shutdown_thread.join(timeout=2)
            result = vpn_runtime.init(
                _flat_vpn_site("new-site", "tun-admission"),
                siterunner_pauser=new_paused.append,
                start_monitors=False,
            )
        kill_thread.join(timeout=2)
        shutdown_thread.join(timeout=2)
        assert shutdown_done.is_set()
        assert result["ok"] is True
        assert new_paused == []
        assert vpn_kill_switch._cycle_generation_quiesced()
    finally:
        release_admission.set()
        if kill_thread is not None:
            kill_thread.join(timeout=2)
        if shutdown_thread is not None:
            shutdown_thread.join(timeout=2)
        monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
        _clear_vpn_test_state()


def test_reinit_refuses_while_old_kill_cycle_action_is_still_live(
        monkeypatch, tmp_path,
):
    """A bounded cycle stop may time out, but its generation cannot cross."""
    from bulk_downloader import vpn, vpn_kill_switch, vpn_runtime

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    _clear_vpn_test_state()
    cycle_entered = threading.Event()
    release_cycle = threading.Event()
    real_shutdown = vpn_kill_switch.shutdown
    real_vpn_shutdown = vpn.shutdown
    vpn_shutdown_calls = []

    def blocked_cycle(_tunnel_id):
        cycle_entered.set()
        release_cycle.wait(2)
        return False

    monkeypatch.setattr(vpn_kill_switch, "CYCLE_BACKOFF_S", 0)
    monkeypatch.setattr(vpn, "cycle_tunnel", blocked_cycle)
    monkeypatch.setattr(
        vpn_kill_switch,
        "shutdown",
        lambda timeout=vpn_kill_switch.CYCLE_STOP_TIMEOUT_S: real_shutdown(
            timeout=0.05),
    )
    monkeypatch.setattr(
        vpn,
        "shutdown",
        lambda: (vpn_shutdown_calls.append(True), real_vpn_shutdown())[1],
    )
    try:
        vpn_runtime.init(
            _flat_vpn_site("old-site", "tun-blocked"),
            start_monitors=False,
        )
        vpn_kill_switch.kill_tunnel("tun-blocked", reason="blocked action")
        assert cycle_entered.wait(2)

        assert vpn_runtime.shutdown() is False
        assert vpn_shutdown_calls == []
        pending = vpn_runtime.init(
            _flat_vpn_site("new-site", "tun-blocked"),
            start_monitors=False,
        )
        assert pending["ok"] is False, pending
        assert "vpn kill cycle" in pending["pending_monitors"], pending
        assert vpn_runtime._initialized is False

        release_cycle.set()
        assert real_shutdown(timeout=2) is True
        monkeypatch.setattr(vpn, "shutdown", real_vpn_shutdown)
        retry = vpn_runtime.init(
            _flat_vpn_site("new-site", "tun-blocked"),
            start_monitors=False,
        )
        assert retry["ok"] is True and not retry.get("skipped", False)
    finally:
        release_cycle.set()
        monkeypatch.setattr(vpn_kill_switch, "shutdown", real_shutdown)
        monkeypatch.setattr(vpn, "shutdown", real_vpn_shutdown)
        real_shutdown(timeout=2)
        monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
        _clear_vpn_test_state()


def test_backend_start_cannot_resurrect_after_bounded_shutdown(
        monkeypatch, tmp_path,
):
    """Ordinary start_tunnel shares shutdown's tracked action boundary."""
    from bulk_downloader import vpn

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    vpn._reset_for_tests()
    start_entered = threading.Event()
    release_start = threading.Event()
    backend_stops = []
    start_result = []

    class BlockingBackend:
        @staticmethod
        def start(_tunnel):
            start_entered.set()
            release_start.wait(2)
            return True

        @staticmethod
        def stop(tunnel):
            backend_stops.append(tunnel.tunnel_id)
            return True

    monkeypatch.setattr(vpn, "_get_backend", lambda _tunnel: BlockingBackend)
    vpn.register_tunnel(
        name="blocked backend",
        provider="generic",
        backend="wireguard",
        tunnel_id="tun-backend-race",
    )
    start_thread = threading.Thread(
        target=lambda: start_result.append(
            vpn.start_tunnel("tun-backend-race")))
    try:
        start_thread.start()
        assert start_entered.wait(2)
        assert vpn.shutdown(backend_timeout=0.05) is False
        assert vpn._open_backend_actions() is False

        release_start.set()
        start_thread.join(timeout=2)
        assert not start_thread.is_alive()
        assert start_result == [False]
        assert backend_stops == ["tun-backend-race"]
        tunnel = vpn.get_tunnel("tun-backend-race")
        assert tunnel.state == "down"
        assert tunnel.socks_port == 0

        assert vpn.shutdown(backend_timeout=2) is True
        assert vpn._open_backend_actions() is True
    finally:
        release_start.set()
        start_thread.join(timeout=2)
        vpn._reset_for_tests()


def test_unknown_tunnel_actions_do_not_grow_lifecycle_lock_registry(
        monkeypatch, tmp_path,
):
    from bulk_downloader import vpn

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    vpn._reset_for_tests()
    locks = vpn._backend_tunnel_locks
    try:
        for index in range(512):
            tunnel_id = f"unknown-{index}"
            assert vpn.start_tunnel(tunnel_id) is False
            assert vpn.stop_tunnel(tunnel_id) is False
            assert vpn.unregister_tunnel(tunnel_id) is False
        assert vpn._backend_tunnel_locks is locks
        assert len(locks) == 64
    finally:
        vpn._reset_for_tests()


def test_backend_stop_failure_keeps_shutdown_retryable(
        monkeypatch, tmp_path,
):
    """External stop failure is not published as a completed down state."""
    from bulk_downloader import vpn

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    vpn._reset_for_tests()

    class RetryableBackend:
        running = False
        fail_stop = True

        @classmethod
        def start(cls, _tunnel):
            cls.running = True
            return True

        @classmethod
        def stop(cls, _tunnel):
            if cls.fail_stop:
                raise RuntimeError("planted backend stop failure")
            cls.running = False
            return True

    monkeypatch.setattr(vpn, "_get_backend", lambda _tunnel: RetryableBackend)
    vpn.register_tunnel(
        name="retryable stop",
        provider="generic",
        backend="wireguard",
        tunnel_id="tun-stop-retry",
    )
    try:
        assert vpn.start_tunnel("tun-stop-retry") is True
        assert RetryableBackend.running is True
        assert vpn.unregister_tunnel("tun-stop-retry") is False
        retained = vpn.get_tunnel("tun-stop-retry")
        assert retained is not None
        assert retained.state == "stopping"
        assert retained.socks_port != 0
        assert vpn.shutdown(backend_timeout=0.1) is False
        held = vpn.get_tunnel("tun-stop-retry")
        assert held.state == "stopping"
        assert held.socks_port != 0
        assert RetryableBackend.running is True

        RetryableBackend.fail_stop = False
        assert vpn.shutdown(backend_timeout=2) is True
        stopped = vpn.get_tunnel("tun-stop-retry")
        assert stopped.state == "down"
        assert stopped.socks_port == 0
        assert RetryableBackend.running is False
    finally:
        RetryableBackend.fail_stop = False
        vpn.shutdown(backend_timeout=2)
        vpn._reset_for_tests()


def test_backend_admission_cannot_reopen_inside_shutdown_transaction(
        monkeypatch, tmp_path,
):
    from bulk_downloader import vpn

    _isolate_vpn_runtime(monkeypatch, tmp_path)
    vpn._reset_for_tests()
    stop_entered = threading.Event()
    release_stop = threading.Event()
    shutdown_done = threading.Event()
    reopen_done = threading.Event()
    starts = []
    shutdown_results = []
    reopen_results = []

    class BlockingStopBackend:
        @staticmethod
        def start(_tunnel):
            starts.append(True)
            return True

        @staticmethod
        def stop(_tunnel):
            stop_entered.set()
            release_stop.wait(2)
            return True

    monkeypatch.setattr(
        vpn, "_get_backend", lambda _tunnel: BlockingStopBackend)
    vpn.register_tunnel(
        name="shutdown serialization",
        provider="generic",
        backend="wireguard",
        tunnel_id="tun-shutdown-serialization",
    )
    assert vpn.start_tunnel("tun-shutdown-serialization") is True
    shutdown_thread = threading.Thread(
        target=lambda: (
            shutdown_results.append(vpn.shutdown(backend_timeout=2)),
            shutdown_done.set(),
        ))
    reopen_thread = threading.Thread(
        target=lambda: (
            reopen_results.append(vpn._open_backend_actions()),
            reopen_done.set(),
        ))
    try:
        shutdown_thread.start()
        assert stop_entered.wait(2)
        reopen_thread.start()
        assert not reopen_done.wait(0.1)
        assert vpn.start_tunnel("tun-shutdown-serialization") is False
        assert len(starts) == 1

        release_stop.set()
        shutdown_thread.join(timeout=2)
        reopen_thread.join(timeout=2)
        assert shutdown_done.is_set()
        assert reopen_done.is_set()
        assert shutdown_results == [True]
        assert reopen_results == [True]
        assert vpn.get_tunnel(
            "tun-shutdown-serialization").state == "down"
    finally:
        release_stop.set()
        shutdown_thread.join(timeout=2)
        reopen_thread.join(timeout=2)
        vpn._reset_for_tests()
