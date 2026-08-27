"""Row 296: the runner cannot download when VPN admission is unmeasurable.

The probe-side kill-switch states are already tri-state.  This gate drives the
caller that runs before each queue claim so an exception or missing runtime
cannot be laundered into permission to reach ``_process_worker_url``.
"""
from __future__ import annotations

import contextlib
import queue
import threading

import pytest


BD_GATE_SCOPE = "repo-wide"

_URL = "https://example.test/row-296.mp4"
_RAISES = object()
_NO_VPN_CONFIGURED = object()
_GATE_CASES = (
    "no_vpn_configured",
    "tunnel_measured_down",
    "measurement_raised",
    "runtime_module_absent",
)


class _RecordingStop:
    """An Event whose waits end the single worker iteration immediately."""

    def __init__(self):
        self._event = threading.Event()
        self.waits = []

    def is_set(self):
        return self._event.is_set()

    def set(self):
        self._event.set()

    def wait(self, timeout=None):
        self.waits.append(timeout)
        self._event.set()
        return True


def _runner_iteration(monkeypatch, *, runtime_available, vpn_result):
    """Run one real worker-loop iteration with only external seams replaced."""
    from bulk_downloader import global_config, maintenance, netns_isolation
    from bulk_downloader import runner as runner_mod
    from bulk_downloader import smart_wakeup

    runner = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    runner.site_id = "row-296"
    runner.config = {"max_concurrent": 1}
    runner.jobs = {_URL: {"status": "pending"}}
    runner.cookies = []
    runner._cookies_updated_at = 0.0
    runner._worker_heartbeats_lock = threading.Lock()
    runner._worker_heartbeats = {}
    runner._worker_run_generation = 1
    runner._worker_context = threading.local()
    runner._pause = threading.Event()
    runner._pause.set()
    runner._session_ok = threading.Event()
    runner._session_ok.set()
    runner._stop = _RecordingStop()
    runner._url_queue = queue.Queue()
    runner._url_queue.put((1, _URL))

    launches = []
    downloads = []
    vpn_calls = []

    real_maybe_wait_for_vpn = runner_mod.vpn_runtime.maybe_wait_for_vpn
    configured_tunnel = "not-measured"
    if vpn_result is _NO_VPN_CONFIGURED:
        monkeypatch.delenv("BD_DISABLE_VPN_RUNTIME", raising=False)
        monkeypatch.setattr(runner_mod.vpn_runtime, "_site_to_tunnel", {})
        monkeypatch.setattr(runner_mod.vpn_runtime, "_global_tunnel_id", None)
        configured_tunnel = runner_mod.vpn_runtime.get_tunnel_for_site(
            runner.site_id
        )
        assert configured_tunnel is None, (
            "negative-control fixture accidentally configured a VPN tunnel"
        )

    def launch_browser(*, worker_idx, netns):
        launches.append((worker_idx, netns))
        return None, None, None, "row-296-test"

    def process_worker_url(
        worker_idx, browser, url, *, persistent_ctx, run_generation
    ):
        downloads.append(
            (worker_idx, browser, url, persistent_ctx, run_generation)
        )
        runner._stop.set()
        return runner_mod.SiteRunner._WORKER_CLAIM_PROCESSED

    def maybe_wait_for_vpn(site_id, *, timeout):
        vpn_calls.append((site_id, timeout))
        if vpn_result is _RAISES:
            raise RuntimeError("row-296 tunnel measurement unavailable")
        if vpn_result is _NO_VPN_CONFIGURED:
            return real_maybe_wait_for_vpn(site_id, timeout=timeout)
        return vpn_result

    runner._launch_browser = launch_browser
    runner._effective_concurrency = lambda: 1
    runner._generation_item_is_processable = lambda generation, url: True
    runner._resource_admission_hold = lambda: None
    runner._process_worker_url = process_worker_url
    runner._maybe_drift_recover = lambda: None

    monkeypatch.setattr(runner_mod, "_VPN_RUNTIME_AVAILABLE", runtime_available)
    monkeypatch.setattr(
        runner_mod.vpn_runtime, "maybe_wait_for_vpn", maybe_wait_for_vpn
    )
    monkeypatch.setattr(runner_mod, "_global_sem", None)
    monkeypatch.setattr(
        netns_isolation,
        "capture_netns",
        lambda *_args, **_kwargs: contextlib.nullcontext(None),
    )
    monkeypatch.setattr(maintenance, "is_action_paused", lambda action: False)
    monkeypatch.setattr(
        smart_wakeup, "should_wake_now", lambda **_kwargs: {"wake": True}
    )
    monkeypatch.setattr(global_config, "get_config", lambda: {})

    runner._worker_loop(worker_idx=0, run_generation=1)

    return {
        "downloads": downloads,
        "configured_tunnel": configured_tunnel,
        "launches": launches,
        "queue_size": runner._url_queue.qsize(),
        "unfinished": runner._url_queue.unfinished_tasks,
        "vpn_calls": vpn_calls,
        "waits": runner._stop.waits,
    }


@pytest.mark.parametrize(
    ("case", "runtime_available", "vpn_result"),
    (
        ("no_vpn_configured", True, _NO_VPN_CONFIGURED),
        ("tunnel_measured_down", True, False),
        ("measurement_raised", True, _RAISES),
        ("runtime_module_absent", False, None),
    ),
    ids=_GATE_CASES,
)
def test_worker_vpn_gate_has_four_distinct_outcomes(
    monkeypatch, capsys, case, runtime_available, vpn_result
):
    """Every complete caller-side state reaches its intended exact verdict."""
    assert len(_GATE_CASES) == 4
    assert set(_GATE_CASES) == {
        "no_vpn_configured",
        "tunnel_measured_down",
        "measurement_raised",
        "runtime_module_absent",
    }

    observed = _runner_iteration(
        monkeypatch,
        runtime_available=runtime_available,
        vpn_result=vpn_result,
    )
    stderr = capsys.readouterr().err

    if case == "no_vpn_configured":
        assert observed["configured_tunnel"] is None
        assert observed["vpn_calls"] == [("row-296", 30.0)]
        assert observed["downloads"] == [
            (0, None, _URL, None, 1)
        ], "an unconfigured site must proceed through the download seam promptly"
        assert observed["launches"] == [(0, None)]
        assert observed["waits"] == []
        assert observed["queue_size"] == 0
        assert observed["unfinished"] == 0
        assert stderr == ""
    elif case == "tunnel_measured_down":
        assert observed["vpn_calls"] == [("row-296", 30.0)]
        assert observed["downloads"] == []
        assert observed["launches"] == [(0, None)]
        assert observed["waits"] == [30]
        assert observed["queue_size"] == 1
        assert observed["unfinished"] == 1
        assert stderr == ""
    elif case == "measurement_raised":
        assert observed["vpn_calls"] == [("row-296", 30.0)]
        assert observed["downloads"] == [], (
            "a raised VPN measurement must not grant download permission"
        )
        assert observed["launches"] == [(0, None)]
        assert observed["waits"] == [30]
        assert observed["queue_size"] == 1
        assert observed["unfinished"] == 1
        assert stderr == (
            "[runner] vpn check raised: "
            "row-296 tunnel measurement unavailable\n"
        )
    else:
        assert case == "runtime_module_absent"
        assert observed["vpn_calls"] == []
        assert observed["downloads"] == [], (
            "an absent VPN runtime must refuse worker startup, not skip the gate"
        )
        assert observed["launches"] == []
        assert observed["waits"] == []
        assert observed["queue_size"] == 1
        assert observed["unfinished"] == 1
        assert "vpn runtime unavailable; refusing worker startup" in stderr


def test_transform_control_imports_runner_without_asserting_vpn_admission():
    from bulk_downloader import runner

    assert runner.__name__ == "bulk_downloader.runner"
