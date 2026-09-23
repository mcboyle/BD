"""Unit tests for Row 985: Interactive Terminal UI Live Operational Dashboard.

Guards:
- Localized interactive operational dashboard under bulk_downloader.terminal_dashboard
- Runtime capability introspection and TUI engine configuration
- Typed operational status models (SiteOperationalStatus, EventOperationalEntry, CapacityOperationalMetrics)
- State machine for interactive navigation, view tab switching, and site selection
- Keybinding handler (j/k navigation, p pause toggle, r refresh, s sort, 1/2/3 tab views, q quit)
- Site action execution (pause/resume/retry) dispatch
- Frame rendering and status bar generation across rich and fallback text modes
- Seamless compatibility with bulk_downloader.cli_dashboard
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

# H622 anti-orphan convention: scoped module test, collected by bd-test-shard
BD_GATE_SCOPE = "module"


def test_metadata_and_capabilities():
    """Verify operational dashboard metadata and feature introspection."""
    from bulk_downloader.terminal_dashboard import (
        ViewMode,
        get_terminal_dashboard_info,
        is_interactive_supported,
    )

    info = get_terminal_dashboard_info()
    assert isinstance(info, dict)
    assert info["version"] >= 1
    assert "rich_available" in info
    assert "supported_views" in info
    assert "SITES" in info["supported_views"]
    assert "EVENTS" in info["supported_views"]
    assert "CAPACITY" in info["supported_views"]
    for key in ("x", "u", "t"):
        assert key in info["keybindings"], f"site action key {key!r} undocumented"
    assert isinstance(is_interactive_supported(), bool)
    assert ViewMode.SITES.value == "sites"
    assert ViewMode.EVENTS.value == "events"
    assert ViewMode.CAPACITY.value == "capacity"


def test_operational_models():
    """Verify typed data containers for sites, events, and capacity."""
    from bulk_downloader.terminal_dashboard import (
        CapacityOperationalMetrics,
        EventOperationalEntry,
        SiteOperationalStatus,
    )

    site = SiteOperationalStatus(
        site_id="site_alpha",
        name="Alpha CDN",
        running=3,
        queued=15,
        done=420,
        failed=2,
        needs_review=1,
        throughput_per_hour=14.5,
        health_status="healthy",
    )
    assert site.total_jobs == 441
    assert site.failure_rate == pytest.approx(2 / 441, rel=1e-3)
    d = site.to_dict()
    assert d["site_id"] == "site_alpha"
    assert d["running"] == 3

    event = EventOperationalEntry(
        timestamp=time.time(),
        site_id="site_alpha",
        kind="done",
        message="batch completed",
        severity="info",
    )
    assert event.formatted_time != ""
    assert event.is_alert is False

    err_event = EventOperationalEntry(
        timestamp=time.time(),
        site_id="site_alpha",
        kind="failed",
        message="rate limit",
        severity="error",
    )
    assert err_event.is_alert is True

    cap = CapacityOperationalMetrics(
        disk_free_gb=128.5,
        disk_total_gb=500.0,
        runway_days=9.2,
        queue_eta_minutes=45.0,
        bottleneck="disk_io",
    )
    assert cap.formatted_eta == "45m"
    cap_long = CapacityOperationalMetrics(
        disk_free_gb=128.5,
        disk_total_gb=500.0,
        runway_days=9.2,
        queue_eta_minutes=150.0,
        bottleneck="network",
    )
    assert cap_long.formatted_eta == "2.5h"


def test_dashboard_state_navigation():
    """Verify state transitions, view changes, and selection navigation."""
    from bulk_downloader.terminal_dashboard import (
        DashboardState,
        ViewMode,
    )

    state = DashboardState()
    assert state.view_mode == ViewMode.SITES
    assert state.selected_site_index == 0
    assert state.paused is False
    assert state.filter_query == ""

    # Navigation bounds clamping
    state.move_selection(delta=1, max_items=5)
    assert state.selected_site_index == 1
    state.move_selection(delta=10, max_items=5)
    assert state.selected_site_index == 4  # clamped
    state.move_selection(delta=-10, max_items=5)
    assert state.selected_site_index == 0  # clamped at 0

    # View switching
    state.set_view(ViewMode.EVENTS)
    assert state.view_mode == ViewMode.EVENTS

    # Sort cycling
    initial_sort = state.sort_field
    state.cycle_sort()
    assert state.sort_field != initial_sort

    # Filter management
    state.set_filter("alpha")
    assert state.filter_query == "alpha"
    state.clear_filter()
    assert state.filter_query == ""


def test_controller_snapshot_and_alerts():
    """Verify snapshot assembly, site rollup, and anomaly alert detection."""
    from bulk_downloader.terminal_dashboard import (
        TerminalDashboardController,
    )

    raw_status = {
        "sites": {
            "s1": {
                "name": "Site One",
                "jobs": {
                    "j1": {"status": "running"},
                    "j2": {"status": "failed"},
                    "j3": {"status": "failed"},
                    "j4": {"status": "done"},
                },
            },
            "s2": {
                "name": "Site Two",
                "jobs": {
                    "j5": {"status": "running"},
                    "j6": {"status": "done"},
                },
            },
        }
    }
    raw_cap = {  # capacity.capacity_report's keys (/api/capacity)
        "disk": {"free_gb": 4.5, "runway_days": 0.5},
        "queue": {"eta_hours": 0.5},
        "bottleneck": {"bottleneck": "disk", "detail": "4.5GB free; runway short", "severity": 3},
    }
    raw_events = [
        {"ts": time.time(), "site_id": "s1", "kind": "failed", "message": "Disk write timeout"},
    ]

    controller = TerminalDashboardController(api_base="http://mock-bd")
    snapshot = controller.assemble_snapshot(raw_status, raw_cap, raw_events)

    assert len(snapshot.sites) == 2
    s1_status = next(s for s in snapshot.sites if s.site_id == "s1")
    assert s1_status.running == 1
    assert s1_status.failed == 2
    assert s1_status.done == 1
    assert snapshot.capacity.queue_eta_minutes == 30.0
    assert snapshot.capacity.formatted_eta == "30m"
    assert snapshot.capacity.bottleneck == "disk"

    # Anomaly alerts: low disk (< 10GB or < 1d runway) + high error site
    alerts = controller.evaluate_alerts(snapshot)
    assert len(alerts) >= 1
    assert any("disk" in a.lower() or "runway" in a.lower() for a in alerts)


def test_keybinding_handler():
    """Verify interactive keyboard dispatch and operational actions."""
    from bulk_downloader.terminal_dashboard import (
        TerminalDashboardController,
        ViewMode,
    )

    controller = TerminalDashboardController(api_base="http://mock-bd")
    # Add dummy sites for navigation
    controller.current_site_ids = ["s1", "s2", "s3"]

    # 'j' moves selection down
    assert controller.state.selected_site_index == 0
    controller.handle_key("j")
    assert controller.state.selected_site_index == 1

    # 'k' moves selection up
    controller.handle_key("k")
    assert controller.state.selected_site_index == 0

    # 'p' toggles pause state
    assert controller.state.paused is False
    controller.handle_key("p")
    assert controller.state.paused is True
    controller.handle_key("p")
    assert controller.state.paused is False

    # Tabs 1, 2, 3
    controller.handle_key("2")
    assert controller.state.view_mode == ViewMode.EVENTS
    controller.handle_key("3")
    assert controller.state.view_mode == ViewMode.CAPACITY
    controller.handle_key("1")
    assert controller.state.view_mode == ViewMode.SITES

    # 'q' signals quit
    assert controller.should_quit is False
    controller.handle_key("q")
    assert controller.should_quit is True


def test_site_action_dispatch():
    """Verify action commands to pause/resume or retry site jobs."""
    from bulk_downloader.terminal_dashboard import TerminalDashboardController

    controller = TerminalDashboardController(api_base="http://mock-bd")
    controller.current_site_ids = ["site_alpha"]

    with patch("requests.post") as mock_post:
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {"status": "ok"}

        assert controller.execute_site_action("pause", "site_alpha") is True
        assert controller.execute_site_action("resume", "site_alpha") is True
        assert controller.execute_site_action("retry", "site_alpha") is True
        # retry_failed has no route (the backend's is /api/sites/<sid>/retry)
        assert controller.execute_site_action("retry_failed", "site_alpha") is False

    assert [c.args[0] for c in mock_post.call_args_list] == [
        "http://mock-bd/api/sites/site_alpha/pause",
        "http://mock-bd/api/sites/site_alpha/resume",
        "http://mock-bd/api/sites/site_alpha/retry",
    ]


def test_frame_rendering_and_status_bar():
    """Verify frame rendering in text and rich mode."""
    from bulk_downloader.terminal_dashboard import (
        CapacityOperationalMetrics,
        SiteOperationalStatus,
        TerminalDashboardController,
    )

    controller = TerminalDashboardController(api_base="http://mock-bd")
    controller.latest_snapshot.sites = [
        SiteOperationalStatus(
            site_id="s1",
            name="Alpha",
            running=1,
            queued=2,
            done=10,
            failed=0,
            needs_review=0,
            throughput_per_hour=5.0,
            health_status="healthy",
        )
    ]
    controller.latest_snapshot.capacity = CapacityOperationalMetrics(
        disk_free_gb=200.0,
        disk_total_gb=500.0,
        runway_days=20.0,
        queue_eta_minutes=15.0,
        bottleneck="",
    )

    # Status bar formatting
    status_bar = controller.format_status_bar()
    assert "[1] Sites" in status_bar
    assert "[p] Pause" in status_bar or "[p] Resume" in status_bar
    assert "[q] Quit" in status_bar

    # Plain frame render (string fallback / export)
    text_frame = controller.render_plain_frame()
    assert "Alpha" in text_frame
    assert "Capacity" in text_frame or "Disk" in text_frame

    # Rich renderable
    renderable = controller.render_rich_frame()
    assert renderable is not None


def test_cli_dashboard_integration():
    """Verify backward-compatible bridge with existing cli_dashboard."""
    from bulk_downloader import cli_dashboard

    # Existing exports must be preserved intact (the run_live wiring is pinned
    # by test_run_live_loop_drives_controller_poll_render_keys_and_actions)
    assert hasattr(cli_dashboard, "run_once")
    assert hasattr(cli_dashboard, "run_live")
    assert hasattr(cli_dashboard, "_summarize_site")
    assert hasattr(cli_dashboard, "_build_site_table")


def test_independent_alert_low_disk():
    """Verify low disk alert branch fires independently and handles 0.0 GB."""
    from bulk_downloader.terminal_dashboard import (
        CapacityOperationalMetrics,
        OperationalSnapshot,
        TerminalDashboardController,
    )

    controller = TerminalDashboardController()
    snap = OperationalSnapshot(
        capacity=CapacityOperationalMetrics(disk_free_gb=5.0, runway_days=10.0),
    )
    alerts = controller.evaluate_alerts(snap)
    assert alerts == ["CRITICAL: Low disk free space (5.0 GB)"]

    # 0.0 GB full disk must trigger alert
    snap_zero = OperationalSnapshot(
        capacity=CapacityOperationalMetrics(disk_free_gb=0.0, runway_days=10.0),
    )
    alerts_zero = controller.evaluate_alerts(snap_zero)
    assert alerts_zero == ["CRITICAL: Low disk free space (0.0 GB)"]


def test_independent_alert_short_runway():
    """Verify short runway alert branch fires independently and handles 0.0 days."""
    from bulk_downloader.terminal_dashboard import (
        CapacityOperationalMetrics,
        OperationalSnapshot,
        TerminalDashboardController,
    )

    controller = TerminalDashboardController()
    snap = OperationalSnapshot(
        capacity=CapacityOperationalMetrics(disk_free_gb=50.0, runway_days=0.5),
    )
    alerts = controller.evaluate_alerts(snap)
    assert alerts == ["WARNING: Disk runway under 24 hours (0.5 days)"]

    # 0.0 days runway must trigger alert
    snap_zero = OperationalSnapshot(
        capacity=CapacityOperationalMetrics(disk_free_gb=50.0, runway_days=0.0),
    )
    alerts_zero = controller.evaluate_alerts(snap_zero)
    assert alerts_zero == ["WARNING: Disk runway under 24 hours (0.0 days)"]


def test_independent_alert_site_critical():
    """Verify site critical failure rate alert fires independently."""
    from bulk_downloader.terminal_dashboard import (
        CapacityOperationalMetrics,
        OperationalSnapshot,
        SiteOperationalStatus,
        TerminalDashboardController,
    )

    controller = TerminalDashboardController()
    snap = OperationalSnapshot(
        sites=[
            SiteOperationalStatus(
                site_id="s_crit",
                name="Beta",
                running=1,
                done=2,
                failed=12,
                health_status="critical",
            ),
            SiteOperationalStatus(
                site_id="s_ok",
                name="Gamma",
                running=1,
                done=20,
                failed=0,
                health_status="healthy",
            ),
        ],
        capacity=CapacityOperationalMetrics(disk_free_gb=50.0, runway_days=10.0),
    )
    alerts = controller.evaluate_alerts(snap)
    assert alerts == ["CRITICAL: Site Beta elevated failure rate (12 failed)"]


def test_zero_runway_rendering_accuracy():
    """Verify 0.0 runway renders accurately as 0.0 days rather than N/A or Unknown."""
    from bulk_downloader.terminal_dashboard import (
        CapacityOperationalMetrics,
        TerminalDashboardController,
    )

    controller = TerminalDashboardController()
    from bulk_downloader.terminal_dashboard import ViewMode
    controller.state.view_mode = ViewMode.CAPACITY
    controller.latest_snapshot.capacity = CapacityOperationalMetrics(
        disk_free_gb=12.0,
        runway_days=0.0,
        queue_eta_minutes=0.0,
    )

    plain = controller.render_plain_frame()
    assert "runway: 0.0 days" in plain
    assert "runway: N/A" not in plain

    rich_layout = controller.render_rich_frame()
    assert rich_layout is not None


def test_keybinding_refresh_and_clear_filter():
    """Verify 'r' refresh and 'c' clear filter keybindings."""
    from bulk_downloader.terminal_dashboard import TerminalDashboardController

    controller = TerminalDashboardController()
    controller.state.filter_query = "active_site"
    controller.handle_key("c")
    assert controller.state.filter_query == ""

    # 'r' refresh re-evaluates alerts
    controller.handle_key("r")
    assert isinstance(controller.latest_snapshot.alerts, list)


# --- Refute fixes (N6-A, P4-B, N1-A): the run loop drives the controller, and
# --- a site action is a whitelisted verb on a real route for a fetched, quoted id.

class _Reply:
    def __init__(self, ok, payload=None):
        self.ok = ok
        self._payload = payload

    def json(self):
        return self._payload


class _FakeRequests:
    """Stands in for the requests module inside terminal_dashboard."""

    class RequestException(Exception):
        pass

    def __init__(self, ok=True, raises=False):
        self.ok = ok
        self.raises = raises
        self.posts = []

    def post(self, url, **_kwargs):
        self.posts.append(url)
        if self.raises:
            raise self.RequestException("connection refused")
        return _Reply(self.ok)


# /api/status?light=1 as BD sends it: {sid: status}, per-status counts, no jobs.
_STATUS = {
    "vixen": {"name": "Vixen", "state": "running", "jobs": {},
              "counts": {"pending": 42, "running": 3, "done": 1247, "failed": 8,
                         "stopped": 0, "needs_review": 2}},
    "blacked": {"name": "Blacked", "state": "idle", "jobs": {},
                "counts": {"pending": 7, "running": 1, "done": 523, "failed": 3,
                           "stopped": 0, "needs_review": 0}},
}


def _drive_run_live(monkeypatch, *, status, capacity, keys, max_frames=20,
                    refresh_seconds=0, events_all=None, waits=None):
    """Run cli_dashboard.run_live against a fake BD and a scripted keyboard.

    Returns (gets, posts, frames): each GET as (url, params), each POST url, and
    each frame the loop handed to rich Live, rendered to text. A loop that never
    reads the keyboard is stopped with ^C after `max_frames` frames. `status` is
    one /api/status?light=1 payload or a list served one per poll (the last
    repeats; None is a failed poll); `events_all(params)` answers
    GET /api/events_all. With a `waits` list the loop's clock is simulated -- a
    key arrives 1 s into the wait, a wait with no key runs out -- and each
    timeout the loop hands the keyboard reader is appended to `waits`.
    """
    import contextlib
    import io
    import time
    import types

    import requests
    from rich.console import Console

    from bulk_downloader import cli_dashboard

    gets, posts, frames = [], [], []
    statuses = list(status) if isinstance(status, list) else [status]

    def fake_get(url, params=None, timeout=None):
        gets.append((url, dict(params or {})))
        if url.endswith("/api/status") and (params or {}).get("light") == "1":
            payload = statuses.pop(0) if len(statuses) > 1 else statuses[0]
            return _Reply(payload is not None, payload)
        if url.endswith("/api/capacity"):
            return _Reply(True, capacity)
        if url.endswith("/api/events_all") and events_all is not None:
            return events_all(dict(params or {}))
        return _Reply(False)  # any other path is a 404, as on a real BD

    def fake_post(url, **_kwargs):
        posts.append(url)
        return _Reply(True, {"ok": True})

    class FakeLive:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def update(self, renderable):
            buf = io.StringIO()
            Console(file=buf, width=160, height=40, color_system=None).print(renderable)
            frames.append(buf.getvalue())
            if len(frames) >= max_frames:
                raise KeyboardInterrupt

    script = iter(keys)
    now = [1000.0]

    def read_key(timeout):
        key = next(script, None)
        if waits is not None:
            waits.append(timeout)
            now[0] += timeout if key is None else min(timeout, 1.0)
        return key

    @contextlib.contextmanager
    def scripted_keys():
        yield read_key

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(cli_dashboard, "Live", FakeLive)
    monkeypatch.setattr(cli_dashboard, "terminal_key_reader", scripted_keys, raising=False)
    if waits is not None:
        monkeypatch.setattr(cli_dashboard, "time", types.SimpleNamespace(
            monotonic=lambda: now[0], strftime=time.strftime, localtime=time.localtime))
    cli_dashboard.run_live(api_base="http://127.0.0.1:5000", refresh_seconds=refresh_seconds)
    return gets, posts, frames


def test_run_live_loop_drives_controller_poll_render_keys_and_actions(monkeypatch):
    """N6-A E1 / P4-B E1 / N1-A R1: the product entry (python -m
    bulk_downloader.cli_dashboard -> run_live) polls BD into the controller,
    renders its frame and routes keys to it: j selects a site, x/u/t POST that
    site's action, q ends the loop."""
    gets, posts, frames = _drive_run_live(
        monkeypatch, status=_STATUS,
        capacity={"disk": {"free_gb": 487.0, "runway_days": 12.0}},
        keys=["j", "x", "u", "t", "q"])

    assert posts == [
        "http://127.0.0.1:5000/api/sites/vixen/pause",
        "http://127.0.0.1:5000/api/sites/vixen/resume",
        "http://127.0.0.1:5000/api/sites/vixen/retry",
    ], f"run_live sent no site action for the keys j,x,u,t (posts={posts})"
    assert len(frames) == 5, "one frame per tick; q ends the loop"
    assert ("http://127.0.0.1:5000/api/status", {"light": "1"}) in gets
    last = frames[-1]
    assert "Vixen" in last and "Blacked" in last
    assert "1247" in last  # the light payload's counts reach the frame
    assert "retry vixen: ok" in last  # the outcome of the last key
    assert "[x] Pause site" in last and "[q] Quit" in last  # key hints survive rich


def test_run_live_survives_real_capacity_payload_and_markup_in_payload(monkeypatch):
    """The wired loop outlives what BD really sends: /api/capacity reports
    "free_gb": null when no download dir is known (capacity.capacity_report),
    and site names are free text ("[/]" is not rich markup)."""
    from bulk_downloader.terminal_dashboard import TerminalDashboardController

    status = {"odd": {"name": "Odd [/] site", "jobs": {}, "counts": {"running": 1}}}
    _gets, posts, frames = _drive_run_live(
        monkeypatch, status=status,
        capacity={"disk": {"free_gb": None, "runway_days": None},
                  "queue": {"eta_hours": None}, "bottleneck": {"bottleneck": "idle"}},
        keys=["3", "1", "q"])
    assert len(frames) == 3 and posts == []
    assert "Odd [/] site" in frames[0]
    assert "Unknown" in frames[1]  # capacity view: free space unknown
    assert "Alerts:" not in frames[0]  # null free space is not a full disk

    # capacity unavailable ({} from a failed poll) raises no low-disk alarm either
    assert TerminalDashboardController().assemble_snapshot({}, {}, []).alerts == []


def test_cli_dashboard_import_does_not_swallow_terminal_dashboard_failure(monkeypatch):
    """P4-B E1: cli_dashboard imports the controller plainly; a broken
    terminal_dashboard fails loudly instead of vanishing behind
    'except ImportError: pass'."""
    import importlib
    import sys

    from bulk_downloader import cli_dashboard

    monkeypatch.setitem(sys.modules, "bulk_downloader.terminal_dashboard", None)
    try:
        with pytest.raises(ImportError):
            importlib.reload(cli_dashboard)
    finally:
        monkeypatch.undo()
        importlib.reload(cli_dashboard)
    assert callable(cli_dashboard.run_live)


def test_site_action_refuses_unlisted_verbs_and_unfetched_or_hostile_site_ids(monkeypatch):
    """N6-A E2 / P4-B E3: only a whitelisted verb for a site id the dashboard
    fetched reaches a URL, and the id travels as one quoted path segment."""
    from bulk_downloader import terminal_dashboard as td

    fake = _FakeRequests(ok=True)
    monkeypatch.setattr(td, "requests", fake)
    c = td.TerminalDashboardController(api_base="http://127.0.0.1:5000")
    c.assemble_snapshot({"sites": {"vixen": {}, "a b": {}, "..": {}, "x/../y?z": {}}}, {}, [])

    # the lenses' probes (P4-B E3, N6-A P2/P3): nothing may be sent
    assert c.execute_site_action("pause", "../../admin/shutdown?") is False
    assert c.execute_site_action("pause", "a/../../tokens") is False
    assert c.execute_site_action("../../admin/shutdown", "vixen") is False
    assert c.execute_site_action("pause#x", "vixen") is False
    assert c.execute_site_action("retry_failed", "vixen") is False
    assert c.execute_site_action("pause", "..") is False  # fetched, but a dot segment
    assert fake.posts == []

    # a fetched id with URL-special characters stays a single segment
    assert c.execute_site_action("pause", "a b") is True
    assert c.execute_site_action("resume", "x/../y?z") is True
    assert fake.posts == [
        "http://127.0.0.1:5000/api/sites/a%20b/pause",
        "http://127.0.0.1:5000/api/sites/x%2F..%2Fy%3Fz/resume",
    ]


def test_site_action_verbs_are_real_post_routes(monkeypatch):
    """P4-B E2 / N1-A R2: every POST a site-action key sends names a real route
    in tests/route_map_baseline.txt (retry_failed does not exist; retry does)."""
    from pathlib import Path

    from bulk_downloader import terminal_dashboard as td

    post_routes = set()
    for line in (Path(__file__).parent / "route_map_baseline.txt").read_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and "POST" in parts[1].split(","):
            post_routes.add(parts[0])

    fake = _FakeRequests(ok=True)
    monkeypatch.setattr(td, "requests", fake)
    c = td.TerminalDashboardController(api_base="http://bd")
    c.assemble_snapshot({"sites": {"s1": {}}}, {}, [])
    for key in ("x", "u", "t"):
        c.handle_key(key)

    sent = [u.replace("http://bd/api/sites/s1/", "/api/sites/<sid>/") for u in fake.posts]
    assert sent == ["/api/sites/<sid>/pause", "/api/sites/<sid>/resume",
                    "/api/sites/<sid>/retry"], sent
    assert set(sent) <= post_routes, sorted(set(sent) - post_routes)
    assert "/api/sites/<sid>/retry_failed" not in post_routes
    assert c.execute_site_action("retry_failed", "s1") is False


def test_site_action_reports_false_without_requests_or_on_http_error(monkeypatch):
    """P4-B M3/M4, N1-A M3/M4: no requests, a non-ok reply or a transport error
    is False; only an ok reply is True, and the endpoint is exact."""
    from bulk_downloader import terminal_dashboard as td

    c = td.TerminalDashboardController(api_base="http://bd")
    c.assemble_snapshot({"sites": {"s1": {}}}, {}, [])

    monkeypatch.setattr(td, "requests", None)
    assert c.execute_site_action("pause", "s1") is False

    refused = _FakeRequests(ok=False)
    monkeypatch.setattr(td, "requests", refused)
    assert c.execute_site_action("pause", "s1") is False
    assert refused.posts == ["http://bd/api/sites/s1/pause"]

    down = _FakeRequests(raises=True)
    monkeypatch.setattr(td, "requests", down)
    assert c.execute_site_action("resume", "s1") is False

    accepted = _FakeRequests(ok=True)
    monkeypatch.setattr(td, "requests", accepted)
    assert c.execute_site_action("retry", "s1") is True
    assert accepted.posts == ["http://bd/api/sites/s1/retry"]


def test_terminal_key_reader_reads_single_keys_in_cbreak_and_restores_the_tty():
    """The keyboard half of the loop: on a TTY keys arrive one at a time (no
    Enter), arrows map to up/down, and the terminal mode is restored; a
    non-TTY stdin gets the passive wait instead."""
    import io
    import os

    termios = pytest.importorskip("termios")
    from bulk_downloader.terminal_dashboard import terminal_key_reader

    with terminal_key_reader(io.StringIO()) as read_key:  # no fileno: passive
        assert read_key(0) is None

    try:
        master, slave = os.openpty()
    except OSError as exc:
        pytest.skip(f"no pty on this host: {exc}")

    class _Tty:
        def fileno(self):
            return slave

    try:
        before = termios.tcgetattr(slave)
        with terminal_key_reader(_Tty()) as read_key:
            assert read_key(0.01) is None
            os.write(master, b"j")
            assert read_key(2.0) == "j"  # no newline needed: cbreak
            os.write(master, b"\x1b[B")
            assert read_key(2.0) == "down"
        assert termios.tcgetattr(slave) == before
    finally:
        os.close(master)
        os.close(slave)


def test_once_mode_reads_the_light_status_counts(monkeypatch, capsys):
    """--once (run_once, rich and plain) polls the same real route as the live
    loop, /api/status?light=1, whose per-status counts stand in for the job
    list light mode leaves out; the old /api/status_all is a 404 on BD."""
    import io

    import requests
    from rich.console import Console

    from bulk_downloader import cli_dashboard

    gets = []

    def fake_get(url, params=None, timeout=None):
        gets.append((url, dict(params or {})))
        if url.endswith("/api/status") and (params or {}).get("light") == "1":
            return _Reply(True, _STATUS)
        return _Reply(False)  # any other path is a 404, as on a real BD

    monkeypatch.setattr(requests, "get", fake_get)

    buf = io.StringIO()
    monkeypatch.setattr(cli_dashboard, "Console",
                        lambda: Console(file=buf, width=120, height=40, color_system=None))
    cli_dashboard.run_once(api_base="http://127.0.0.1:5000/")
    rich_out = buf.getvalue()
    assert ("http://127.0.0.1:5000/api/status", {"light": "1"}) in gets
    assert "Vixen" in rich_out and "1247" in rich_out and "523" in rich_out

    monkeypatch.setattr(cli_dashboard, "_HAS_RICH", False)
    cli_dashboard.run_once(api_base="http://127.0.0.1:5000")
    out = capsys.readouterr().out
    assert "Sites: 2" in out
    assert "run=  3 queue= 42 done= 1247 fail=  8 review=  2" in out
    assert "run=  1 queue=  7 done=  523 fail=  3 review=  0" in out


# --- Round 2 (VERIFY-r1 F1-F6): the wired views read what BD's routes really
# --- send, and the loop's pause, key handling and terminal restore are pinned.

def test_run_live_capacity_view_shows_what_capacity_report_sends(monkeypatch, tmp_path):
    """VERIFY-r1 F1: /api/capacity answers capacity.capacity_report(), whose queue
    ETA is queue.eta_hours and whose bottleneck is bottleneck.bottleneck. The
    Capacity view (key 3) is fed that function's own output, as JSON."""
    import contextlib
    import json
    import threading

    from bulk_downloader import capacity
    from bulk_downloader.terminal_dashboard import TerminalDashboardController, ViewMode

    class _Runner:  # what queue_forecast and bottleneck_hint read off a SiteRunner
        def __init__(self, statuses, rate_limited=False):
            self._lock = threading.Lock()
            self.jobs = {f"j{i}": {"status": s} for i, s in enumerate(statuses)}
            self.config = {"max_concurrent": 2}
            self._hung_workers = []
            self._rate_limited = rate_limited

        def is_rate_limited(self):
            return self._rate_limited

    class _History:  # history: 168 GiB in 60 rows over 7 days; 48 done in the last 24 h
        def execute(self, sql, _params=()):
            row = (168 * 1024 ** 3, 60) if "SUM(file_size)" in sql else (48,)
            return type("_Cursor", (), {"fetchone": lambda _self: row})()

    @contextlib.contextmanager
    def history_conn(path=None):
        yield _History()

    monkeypatch.setattr("bulk_downloader.db.db_conn", history_conn)
    monkeypatch.setattr(capacity, "_safe_disk_free_bytes", lambda _path: 200 * 1024 ** 3)
    runners = {"vixen": _Runner(["pending"] * 5 + ["running"]),
               "blacked": _Runner([], rate_limited=True)}
    report = json.loads(json.dumps(capacity.capacity_report(runners, str(tmp_path))))
    assert report["queue"]["eta_hours"] == 2.5  # 5 pending at 48 done / 24 h
    assert report["bottleneck"]["bottleneck"] == "rate_limit"
    assert "eta_minutes" not in report["queue"] and "kind" not in report["bottleneck"]

    _gets, _posts, frames = _drive_run_live(
        monkeypatch, status=_STATUS, capacity=report, keys=["3", "q"])
    view = frames[-1]
    assert "Cluster Capacity" in view
    assert "200.0 GB" in view and "8.3 days" in view  # disk: free_gb, runway_days
    assert "2.5h" in view  # queue.eta_hours
    assert "rate_limit" in view  # bottleneck.bottleneck

    # a failed /api/capacity poll ({}) is "Unknown", never a reassuring "None"
    _gets, _posts, frames = _drive_run_live(
        monkeypatch, status=_STATUS, capacity={}, keys=["3", "q"])
    lines = frames[-1].splitlines()
    assert "Unknown" in next(ln for ln in lines if "Active Bottleneck" in ln)
    assert "N/A" in next(ln for ln in lines if "Queue ETA" in ln)
    c = TerminalDashboardController()
    c.state.view_mode = ViewMode.CAPACITY
    c.assemble_snapshot({}, {}, [])
    assert "Queue ETA: N/A | Bottleneck: Unknown" in c.render_plain_frame()


@pytest.mark.parametrize("eta_hours", (1e308, -1e308),
                         ids=("positive_overflow", "negative_overflow"))
def test_run_live_capacity_eta_overflow_is_unknown(monkeypatch, eta_hours):
    """Finite hours that overflow when converted to minutes stay unknown."""
    _gets, _posts, frames = _drive_run_live(
        monkeypatch, status=_STATUS,
        capacity={"queue": {"eta_hours": eta_hours}}, keys=["3", "q"])
    view = frames[-1]
    assert "Cluster Capacity" in view
    eta_row = next(line for line in view.splitlines() if "Queue ETA" in line)
    assert "N/A" in eta_row, eta_row


def test_run_live_events_view_shows_the_newest_events_from_events_all(monkeypatch):
    """VERIFY-r1 F2: the Events view (key 2) is fed by GET /api/events_all -- BD
    has no /api/events -- answered here by the real route (app_events_all) over
    buffers read with the real TelemetryMixin.get_events. Per site that route
    returns the OLDEST `limit` buffered events, so the dashboard asks for the
    whole buffer and lists the newest first. Every GET the loop sends is a GET
    route in tests/route_map_baseline.txt."""
    import collections
    from pathlib import Path

    from flask import Flask

    from bulk_downloader import app_events_all
    from bulk_downloader.runner_telemetry import TelemetryMixin

    class _Runner:  # a SiteRunner's event buffer, read by the real get_events
        get_events = TelemetryMixin.get_events

        def __init__(self, sid, t0, n=40):
            self._event_log = collections.deque(
                ({"seq": i, "ts": t0 + 10.0 * i, "kind": "done", "message": f"{sid} file {i:03d}",
                  "url": None, "extra": {}} for i in range(1, n + 1)), maxlen=500)
            self._event_seq = n

    runners = {"vixen": _Runner("vixen", 1_758_600_000.0),
               "blacked": _Runner("blacked", 1_758_600_005.0)}
    monkeypatch.setattr(app_events_all, "_app_runners", lambda: runners)
    monkeypatch.setattr(app_events_all, "_app_s_meta", dict)
    monkeypatch.setattr(app_events_all, "_runners_generation", lambda mapping: list(mapping.items()))
    app = Flask(__name__)
    app.register_blueprint(app_events_all.events_all_bp)
    client = app.test_client()

    def events_all(params):
        resp = client.get("/api/events_all", query_string=params)
        return _Reply(resp.status_code == 200, resp.get_json())

    gets, _posts, frames = _drive_run_live(
        monkeypatch, status=_STATUS, capacity={}, events_all=events_all, keys=["2", "q"])
    rows = [ln for ln in frames[-1].splitlines() if " file " in ln]
    assert [r.split(" file ")[1][:3] for r in rows] == [
        "040", "040", "039", "039", "038", "038", "037", "037"], rows  # newest 8, newest first
    assert "blacked file 040" in rows[0] and "vixen file 040" in rows[1]

    get_routes = set()
    for line in (Path(__file__).parent / "route_map_baseline.txt").read_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and "GET" in parts[1].split(","):
            get_routes.add(parts[0])
    polled = {url.replace("http://127.0.0.1:5000", "") for url, _params in gets}
    assert polled == {"/api/status", "/api/capacity", "/api/events_all"}, sorted(polled)
    assert polled <= get_routes, sorted(polled - get_routes)


def test_run_live_holds_every_poll_while_paused(monkeypatch):
    """VERIFY-r1 F3 (V03): [p] holds the refresh -- no GET reaches BD until [p]
    releases it -- and the held frames say PAUSED."""
    gets, _posts, frames = _drive_run_live(
        monkeypatch, status=_STATUS, capacity={}, keys=["p", None, None, "p", "q"])
    assert len(frames) == 5
    assert [u for u, _p in gets].count("http://127.0.0.1:5000/api/status") == 2  # before and after the hold
    assert len(gets) == 6  # status, capacity and events, once on each side of the hold
    assert ["(PAUSED)" in f for f in frames] == [False, True, True, True, False]


def test_terminal_key_reader_restores_the_tty_when_the_loop_raises():
    """VERIFY-r1 F3 (V11): an error inside the live loop (a failed render, a bug)
    still hands the operator's terminal back in the mode it was in."""
    import os

    termios = pytest.importorskip("termios")
    from bulk_downloader.terminal_dashboard import terminal_key_reader

    try:
        master, slave = os.openpty()
    except OSError as exc:
        pytest.skip(f"no pty on this host: {exc}")

    class _Tty:
        def fileno(self):
            return slave

    try:
        before = termios.tcgetattr(slave)
        with pytest.raises(RuntimeError, match="render failed"), terminal_key_reader(_Tty()):
            assert termios.tcgetattr(slave) != before  # cbreak inside the loop
            raise RuntimeError("render failed")
        assert termios.tcgetattr(slave) == before
    finally:
        os.close(master)
        os.close(slave)


def test_run_live_polls_bd_once_per_refresh_not_once_per_key(monkeypatch):
    """VERIFY-r1 F4: a key is handled and drawn at once, but BD is polled once per
    refresh tick, not once per key (a 30-key burst cost 36 status GETs); [r]
    polls at once."""
    waits = []
    gets, _posts, frames = _drive_run_live(
        monkeypatch, status=_STATUS, capacity={}, keys=["j", "k", "j", "r", "k", "q"],
        refresh_seconds=3600, waits=waits)
    assert len(frames) == 6  # every key is drawn
    assert [u for u, _p in gets].count("http://127.0.0.1:5000/api/status") == 2  # first tick, then [r]
    selected = [next(ln for ln in f.splitlines() if "▶" in ln) for f in frames]
    assert ["Vixen" in ln for ln in selected] == [False, True, False, True, True, False]
    # between keys the loop waits in the reader for what is left of the tick --
    # never 0 (a busy loop), never a whole new tick per key; [r] starts a tick
    assert waits == [3600.0, 3599.0, 3598.0, 3597.0, 3600.0, 3599.0]


def test_run_live_key_acts_on_the_site_the_operator_saw_after_a_refresh(monkeypatch):
    """VERIFY-r1 F4: the cursor follows its site by id, not row number. While the
    selected site is not listed -- a failed poll, or a poll that lists only other
    sites -- nothing is selected, so an [x] typed then sends nothing; when the
    site is back -- a new row now above it -- the cursor is on it again and [x]
    pauses the site the operator had selected, not whatever is in its old row."""
    def site(name):
        return {"name": name, "jobs": {}, "counts": {"running": 1}}

    before = {"alpha": site("Alpha"), "gamma": site("Gamma")}
    others = {"alpha": site("Alpha"), "beta": site("Beta")}
    after = {"alpha": site("Alpha"), "beta": site("Beta"), "gamma": site("Gamma")}
    _gets, posts, frames = _drive_run_live(
        monkeypatch, status=[before, None, others, after], capacity={},
        keys=["j", "x", "x", None, "x", "q"])
    assert posts == ["http://127.0.0.1:5000/api/sites/gamma/pause"], posts
    assert "▶" not in frames[1]  # the failed poll: no site listed, none selected
    assert "Alpha" in frames[2] and "Beta" in frames[2] and "Gamma" not in frames[2]
    assert "▶" not in frames[2]  # Gamma unlisted: not Alpha or Beta in its stead
    assert "pause: no site selected" in frames[3]
    assert "▶" in next(ln for ln in frames[3].splitlines() if "Gamma" in ln)
    assert "pause gamma: ok" in frames[5]


def test_interactive_mode_needs_termios_and_rich(monkeypatch):
    """VERIFY-r1 F6: without termios (Windows) the loop only waits out each
    refresh -- keys never arrive -- so is_interactive_supported() is False, as it
    is without rich (run_live then prints one snapshot)."""
    import os

    pytest.importorskip("termios")
    from bulk_downloader import terminal_dashboard as td

    assert td.is_interactive_supported() is True  # POSIX + rich: this host
    monkeypatch.setattr(td, "_HAS_RICH", False)
    assert td.is_interactive_supported() is False
    monkeypatch.setattr(td, "_HAS_RICH", True)
    monkeypatch.setattr(td, "termios", None)
    assert td.is_interactive_supported() is False

    try:
        master, slave = os.openpty()
    except OSError as exc:
        pytest.skip(f"no pty on this host: {exc}")

    class _Tty:
        def fileno(self):
            return slave

    try:  # no termios: even a TTY gets the passive wait, never a key
        with td.terminal_key_reader(_Tty()) as read_key:
            os.write(master, b"j")
            assert read_key(0.05) is None
    finally:
        os.close(master)
        os.close(slave)
