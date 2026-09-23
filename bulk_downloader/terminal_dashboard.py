"""Interactive Terminal UI Live Operational Dashboard for BulkDownloader.

Provides a robust, rich-accelerated interactive terminal dashboard for operators.
Supports live operational telemetry, keyboard navigation (j/k, tabs, pause toggle),
site operational rollups, capacity monitoring, and anomaly alerts.
"""
from __future__ import annotations

import math
import os
import select
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from types import ModuleType
from typing import Any
from urllib.parse import quote

try:
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False

requests: ModuleType | None
try:
    import requests
except ImportError:
    requests = None

try:
    import termios
    import tty
except ImportError:  # Windows: the live dashboard runs without key input
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

# The only site actions the dashboard sends, keyed by the key that sends them.
# Each verb is the last segment of a real POST route /api/sites/<sid>/<verb>
# (tests/route_map_baseline.txt: api_pause, api_resume, api_retry), so no
# other verb can reach a URL.
SITE_ACTION_KEYS: dict[str, str] = {"x": "pause", "u": "resume", "t": "retry"}

# Arrow keys arrive as escape sequences once the terminal is in cbreak mode.
_ARROW_KEYS = {"[A": "up", "[B": "down"}


def _as_dict(value: Any) -> dict:
    """`value` if it is a JSON object, else {} (a null/list/str payload is empty)."""
    return value if isinstance(value, dict) else {}


def _finite_or_none(value: Any) -> float | None:
    """A JSON number as a finite float; None for null, text, bool, NaN or inf."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


class ViewMode(str, Enum):
    """Operational dashboard view modes."""
    SITES = "sites"
    EVENTS = "events"
    CAPACITY = "capacity"


class SortField(str, Enum):
    """Sorting fields for the operational sites table."""
    NAME = "name"
    RUNNING = "running"
    QUEUED = "queued"
    DONE = "done"
    FAILED = "failed"


@dataclass
class SiteOperationalStatus:
    """Operational status metrics for an individual site runner."""
    site_id: str
    name: str
    running: int = 0
    queued: int = 0
    done: int = 0
    failed: int = 0
    needs_review: int = 0
    throughput_per_hour: float = 0.0
    health_status: str = "healthy"

    @property
    def total_jobs(self) -> int:
        return self.running + self.queued + self.done + self.failed + self.needs_review

    @property
    def failure_rate(self) -> float:
        tot = self.total_jobs
        return (self.failed / tot) if tot > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "name": self.name,
            "running": self.running,
            "queued": self.queued,
            "done": self.done,
            "failed": self.failed,
            "needs_review": self.needs_review,
            "throughput_per_hour": self.throughput_per_hour,
            "health_status": self.health_status,
            "total_jobs": self.total_jobs,
            "failure_rate": self.failure_rate,
        }


@dataclass
class EventOperationalEntry:
    """Telemetry event logged during live operation."""
    timestamp: float
    site_id: str
    kind: str
    message: str
    severity: str = "info"

    @property
    def formatted_time(self) -> str:
        try:
            return time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        except (ValueError, OSError, OverflowError):
            return str(self.timestamp)

    @property
    def is_alert(self) -> bool:
        return self.severity in ("error", "warning") or self.kind in ("failed", "alert", "error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "formatted_time": self.formatted_time,
            "site_id": self.site_id,
            "kind": self.kind,
            "message": self.message,
            "severity": self.severity,
            "is_alert": self.is_alert,
        }


@dataclass
class CapacityOperationalMetrics:
    """Resource capacity and queue runway metrics."""
    disk_free_gb: float | None = None
    disk_total_gb: float = 0.0
    runway_days: float | None = None
    queue_eta_minutes: float | None = None
    bottleneck: str = ""

    @property
    def formatted_eta(self) -> str:
        if self.queue_eta_minutes is None:
            return "N/A"
        if self.queue_eta_minutes < 60:
            return f"{int(self.queue_eta_minutes)}m"
        return f"{self.queue_eta_minutes / 60:.1f}h"

    def to_dict(self) -> dict[str, Any]:
        return {
            "disk_free_gb": self.disk_free_gb,
            "disk_total_gb": self.disk_total_gb,
            "runway_days": self.runway_days,
            "queue_eta_minutes": self.queue_eta_minutes,
            "formatted_eta": self.formatted_eta,
            "bottleneck": self.bottleneck,
        }


@dataclass
class OperationalSnapshot:
    """Aggregated operational state across the cluster."""
    sites: list[SiteOperationalStatus] = field(default_factory=list)
    events: list[EventOperationalEntry] = field(default_factory=list)
    capacity: CapacityOperationalMetrics = field(default_factory=CapacityOperationalMetrics)
    timestamp: float = field(default_factory=time.time)
    alerts: list[str] = field(default_factory=list)


@dataclass
class DashboardState:
    """UI state machine for the interactive dashboard session."""
    view_mode: ViewMode = ViewMode.SITES
    selected_site_index: int = 0
    paused: bool = False
    filter_query: str = ""
    sort_field: SortField = SortField.NAME
    sort_reverse: bool = False
    refresh_interval: float = 2.0

    def move_selection(self, delta: int, max_items: int) -> None:
        if max_items <= 0:
            self.selected_site_index = 0
            return
        new_idx = self.selected_site_index + delta
        self.selected_site_index = max(0, min(new_idx, max_items - 1))

    def set_view(self, view: ViewMode) -> None:
        self.view_mode = view

    def cycle_sort(self) -> None:
        sort_order = [
            SortField.NAME,
            SortField.RUNNING,
            SortField.QUEUED,
            SortField.DONE,
            SortField.FAILED,
        ]
        idx = sort_order.index(self.sort_field)
        self.sort_field = sort_order[(idx + 1) % len(sort_order)]

    def set_filter(self, query: str) -> None:
        self.filter_query = query.strip()

    def clear_filter(self) -> None:
        self.filter_query = ""


def get_terminal_dashboard_info() -> dict[str, Any]:
    """Capability introspection for operational terminal UI."""
    return {
        "version": 1,
        "rich_available": _HAS_RICH,
        "supported_views": [v.name for v in ViewMode],
        "supported_sorts": [s.name for s in SortField],
        "keybindings": {
            "j / down": "Select next site",
            "k / up": "Select previous site",
            "x": "Pause the selected site (POST /api/sites/<sid>/pause)",
            "u": "Resume the selected site (POST /api/sites/<sid>/resume)",
            "t": "Retry failed jobs on the selected site (POST /api/sites/<sid>/retry)",
            "p": "Pause / resume the live refresh",
            "r": "Refresh telemetry snapshot",
            "s": "Cycle site sorting order",
            "1": "Switch to Sites view",
            "2": "Switch to Events view",
            "3": "Switch to Capacity view",
            "c": "Clear active search filter",
            "q": "Quit dashboard",
        },
    }


def is_interactive_supported() -> bool:
    """True when the live dashboard (cli_dashboard.run_live) takes keys: rich
    draws the frame and termios puts a POSIX TTY in cbreak mode. Without
    termios (Windows) terminal_key_reader only waits out each refresh, and
    without rich run_live prints one snapshot: neither is interactive."""
    return _HAS_RICH and termios is not None


class TerminalDashboardController:
    """Interactive controller managing live operational updates and key commands."""

    def __init__(
        self,
        api_base: str = "http://127.0.0.1:5000",
        state: DashboardState | None = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.state = state or DashboardState()
        self.should_quit = False
        self.refresh_requested = False  # [r]: the run loop polls BD at once
        self.selected_site_id: str | None = None  # the cursor's site, kept across polls
        self.current_site_ids: list[str] = []
        self.latest_snapshot = OperationalSnapshot()
        self.last_action = ""

    def assemble_snapshot(
        self,
        status_dict: dict[str, Any],
        cap_dict: dict[str, Any],
        events_list: list[dict[str, Any]],
    ) -> OperationalSnapshot:
        """Parse raw JSON payloads from APIs into strongly typed operational snapshot."""
        sites: list[SiteOperationalStatus] = []
        raw_sites = _as_dict(_as_dict(status_dict).get("sites"))
        for sid, sdata in sorted(raw_sites.items()):
            sdata = _as_dict(sdata)
            name = str(sdata.get("name") or sid)
            jobs = _as_dict(sdata.get("jobs"))
            # /api/status?light=1 omits jobs and sends per-status counts instead.
            tally = ([(_as_dict(j).get("status", ""), 1) for j in jobs.values()] if jobs else
                     [(st, n) for st, n in _as_dict(sdata.get("counts")).items()
                      if isinstance(n, int) and not isinstance(n, bool)])
            running = 0
            queued = 0
            done = 0
            failed = 0
            needs_review = 0
            for st, n in tally:
                if st == "running":
                    running += n
                elif st in ("pending", "queued"):
                    queued += n
                elif st == "done":
                    done += n
                elif st == "failed":
                    failed += n
                elif st in ("needs_review", "review"):
                    needs_review += n

            health = "healthy"
            if failed > 5 or (failed > 0 and failed >= done):
                health = "critical" if failed > 10 else "degraded"

            sites.append(
                SiteOperationalStatus(
                    site_id=sid,
                    name=name,
                    running=running,
                    queued=queued,
                    done=done,
                    failed=failed,
                    needs_review=needs_review,
                    health_status=health,
                )
            )

        events: list[EventOperationalEntry] = []
        for evt in events_list if isinstance(events_list, list) else []:
            evt = _as_dict(evt)
            ts = _finite_or_none(evt.get("ts"))
            ts = time.time() if ts is None else ts
            kind = str(evt.get("kind") or "info")
            msg = str(evt.get("message") or "")
            sid = str(evt.get("site_id") or "")
            severity = "error" if kind in ("failed", "error") else ("warning" if kind == "needs_review" else "info")
            events.append(
                EventOperationalEntry(
                    timestamp=ts,
                    site_id=sid,
                    kind=kind,
                    message=msg,
                    severity=severity,
                )
            )

        # /api/capacity is capacity.capacity_report(): disk {free_gb, runway_days}
        # ("free_gb": null when no download dir is known), queue {eta_hours}
        # (null when the rate is unknown) and bottleneck {bottleneck, detail}.
        # An absent or null number is "unknown", never 0.0 GB (a false alert);
        # so is an ETA whose minutes overflow a float (formatted_eta's int()).
        disk = _as_dict(_as_dict(cap_dict).get("disk"))
        queue = _as_dict(_as_dict(cap_dict).get("queue"))
        bot = _as_dict(_as_dict(cap_dict).get("bottleneck"))
        eta_hours = _finite_or_none(queue.get("eta_hours"))
        capacity = CapacityOperationalMetrics(
            disk_free_gb=_finite_or_none(disk.get("free_gb")),
            disk_total_gb=_finite_or_none(disk.get("total_gb")) or 0.0,
            runway_days=_finite_or_none(disk.get("runway_days")),
            queue_eta_minutes=None if eta_hours is None else _finite_or_none(eta_hours * 60.0),
            bottleneck=str(bot.get("bottleneck") or ""),
        )

        snapshot = OperationalSnapshot(
            sites=sites,
            events=events,
            capacity=capacity,
            timestamp=time.time(),
        )
        snapshot.alerts = self.evaluate_alerts(snapshot)
        # The cursor follows its site by id, not row number, across a refresh --
        # a failed poll that lists no sites included -- so a key typed while a
        # poll was in flight acts on the site the operator saw selected. While
        # that site is absent nothing is selected (x/u/t send nothing) until it
        # is back or j/k picks another.
        idx = self.state.selected_site_index
        if 0 <= idx < len(self.current_site_ids):
            self.selected_site_id = self.current_site_ids[idx]
        self.latest_snapshot = snapshot
        self.current_site_ids = [s.site_id for s in sites]
        if self.selected_site_id in self.current_site_ids:
            self.state.selected_site_index = self.current_site_ids.index(self.selected_site_id)
        elif self.selected_site_id is None:
            self.state.move_selection(0, len(sites))  # nothing chosen yet: the first site
        else:
            self.state.selected_site_index = -1
        self.refresh_requested = False
        return snapshot

    def evaluate_alerts(self, snapshot: OperationalSnapshot) -> list[str]:
        """Detect operational alerts and anomalies from cluster telemetry."""
        alerts: list[str] = []
        if snapshot.capacity.disk_free_gb is not None and snapshot.capacity.disk_free_gb < 10.0:
            alerts.append(f"CRITICAL: Low disk free space ({snapshot.capacity.disk_free_gb:.1f} GB)")
        if snapshot.capacity.runway_days is not None and snapshot.capacity.runway_days < 1.0:
            alerts.append(f"WARNING: Disk runway under 24 hours ({snapshot.capacity.runway_days:.1f} days)")

        for site in snapshot.sites:
            if site.health_status == "critical":
                alerts.append(f"CRITICAL: Site {site.name} elevated failure rate ({site.failed} failed)")

        return alerts

    def handle_key(self, key: str) -> None:
        """Process keyboard input command."""
        k = key.lower().strip()
        if k in ("q", "quit"):
            self.should_quit = True
        elif k in ("j", "down"):
            self.state.move_selection(1, len(self.current_site_ids))
        elif k in ("k", "up"):
            self.state.move_selection(-1, len(self.current_site_ids))
        elif k == "p":
            self.state.paused = not self.state.paused
        elif k == "r":
            self.refresh_requested = True
            self.latest_snapshot.alerts = self.evaluate_alerts(self.latest_snapshot)
        elif k == "s":
            self.state.cycle_sort()
        elif k == "1":
            self.state.set_view(ViewMode.SITES)
        elif k == "2":
            self.state.set_view(ViewMode.EVENTS)
        elif k == "3":
            self.state.set_view(ViewMode.CAPACITY)
        elif k == "c":
            self.state.clear_filter()
        elif k in SITE_ACTION_KEYS:
            self.run_site_action(SITE_ACTION_KEYS[k])

    def run_site_action(self, action: str) -> bool:
        """Send `action` for the selected site; the outcome shows in the action bar."""
        idx = self.state.selected_site_index
        if not 0 <= idx < len(self.current_site_ids):
            self.last_action = f"{action}: no site selected"
            return False
        site_id = self.current_site_ids[idx]
        ok = self.execute_site_action(action, site_id)
        self.last_action = f"{action} {site_id}: {'ok' if ok else 'FAILED'}"
        return ok

    def execute_site_action(self, action: str, site_id: str) -> bool:
        """POST one whitelisted action for a site this dashboard fetched.

        Only a SITE_ACTION_KEYS verb and a site id from the last snapshot of the
        operator's own instance reach the URL, and the id is quoted as a single
        path segment, so neither argument can steer the POST to another route.
        """
        if requests is None or action not in SITE_ACTION_KEYS.values():
            return False
        if site_id not in self.current_site_ids or site_id in ("", ".", ".."):
            return False
        endpoint = f"{self.api_base}/api/sites/{quote(site_id, safe='')}/{action}"
        try:
            r = requests.post(endpoint, json={"action": action}, timeout=5)
            return bool(r.ok)
        except (requests.RequestException, OSError):
            return False

    def format_status_bar(self) -> str:
        """Format bottom operational status bar."""
        pause_label = "[p] Resume" if self.state.paused else "[p] Pause"
        sort_label = f"Sort: {self.state.sort_field.value}"
        view_label = f"View: {self.state.view_mode.value.upper()}"
        alerts_count = len(self.latest_snapshot.alerts)
        alert_str = f" | Alerts: {alerts_count}" if alerts_count > 0 else ""
        return (
            f"[1] Sites  [2] Events  [3] Capacity  |  "
            f"{pause_label}  |  {sort_label}  |  {view_label}{alert_str}  |  [q] Quit"
        )

    def format_action_bar(self) -> str:
        """Site action keys for the selected site, then the last action's outcome."""
        bar = "[x] Pause site  [u] Resume site  [t] Retry failed"
        return f"{bar}  |  {self.last_action}" if self.last_action else bar

    def render_plain_frame(self) -> str:
        """Render plain text operational frame for headless or non-rich sessions."""
        lines = [
            f"=== BulkDownloader Live Operational Dashboard ({time.strftime('%H:%M:%S')}) ===",
            f"Mode: {self.state.view_mode.value.upper()} | Paused: {self.state.paused}",
            "-" * 60,
        ]
        if self.state.view_mode == ViewMode.SITES:
            lines.append(f"{'Site':20} {'Run':4} {'Queue':6} {'Done':6} {'Fail':5} {'Health':8}")
            for idx, s in enumerate(self.latest_snapshot.sites):
                prefix = "> " if idx == self.state.selected_site_index else "  "
                lines.append(
                    f"{prefix}{s.name[:18]:18} {s.running:4} {s.queued:6} {s.done:6} {s.failed:5} {s.health_status:8}"
                )
        elif self.state.view_mode == ViewMode.EVENTS:
            lines.append(f"{'Time':8} {'Site':12} {'Kind':10} {'Message'}")
            for e in self.latest_snapshot.events[:12]:
                lines.append(f"{e.formatted_time:8} {e.site_id[:12]:12} {e.kind[:10]:10} {e.message[:40]}")
        elif self.state.view_mode == ViewMode.CAPACITY:
            cap = self.latest_snapshot.capacity
            disk_str = f"{cap.disk_free_gb:.1f} GB" if cap.disk_free_gb is not None else "Unknown"
            runway_str = f"{cap.runway_days:.1f} days" if cap.runway_days is not None else "N/A"
            lines.append(f"Disk: {disk_str} free (runway: {runway_str})")
            lines.append(f"Queue ETA: {cap.formatted_eta} | Bottleneck: {cap.bottleneck or 'Unknown'}")

        if self.latest_snapshot.alerts:
            lines.append("-" * 60)
            for alert in self.latest_snapshot.alerts:
                lines.append(f"! {alert}")

        lines.append("-" * 60)
        lines.append(self.format_status_bar())
        lines.append(self.format_action_bar())
        return "\n".join(lines)

    def render_rich_frame(self) -> Any:
        """Render rich renderable layout for interactive terminal display."""
        if not _HAS_RICH:
            return self.render_plain_frame()

        layout = Layout()
        title_text = f"BulkDownloader Live Operational Dashboard — [bold cyan]{self.state.view_mode.value.upper()}[/]"
        if self.state.paused:
            title_text += " [bold red](PAUSED)[/]"

        # The key bars and every payload string go in as plain Text: "[p]" or a
        # site named "[/]" must print as typed, not parse as rich markup.
        header = Text.from_markup(title_text)
        header.append(f"\n{self.format_status_bar()}\n{self.format_action_bar()}")
        header_panel = Panel(header, title="BD Ops", border_style="cyan")

        content_table = Table(expand=True, show_lines=False)
        if self.state.view_mode == ViewMode.SITES:
            content_table.title = "Operational Sites"
            content_table.add_column("Sel", justify="center", width=3)
            content_table.add_column("Site", style="cyan")
            content_table.add_column("Running", justify="right", style="green")
            content_table.add_column("Queued", justify="right", style="yellow")
            content_table.add_column("Done", justify="right", style="white")
            content_table.add_column("Failed", justify="right", style="red")
            content_table.add_column("Health", justify="center")

            if not self.latest_snapshot.sites:
                content_table.add_row("", "(no sites active)", "-", "-", "-", "-", "-")
            else:
                for idx, s in enumerate(self.latest_snapshot.sites):
                    sel = "▶" if idx == self.state.selected_site_index else " "
                    health_style = "green" if s.health_status == "healthy" else "bold red"
                    content_table.add_row(
                        sel,
                        Text(s.name[:24]),
                        str(s.running) if s.running else "·",
                        str(s.queued) if s.queued else "·",
                        str(s.done) if s.done else "·",
                        str(s.failed) if s.failed else "·",
                        Text(s.health_status, style=health_style),
                    )

        elif self.state.view_mode == ViewMode.EVENTS:
            content_table.title = "Recent Events"
            content_table.add_column("Time", style="dim", no_wrap=True)
            content_table.add_column("Site", style="cyan", no_wrap=True)
            content_table.add_column("Kind", no_wrap=True)
            content_table.add_column("Message", style="white")

            if not self.latest_snapshot.events:
                content_table.add_row("·", "·", "·", "(no events)")
            else:
                for evt in self.latest_snapshot.events[:15]:
                    content_table.add_row(
                        Text(evt.formatted_time),
                        Text(evt.site_id[:12]),
                        Text(evt.kind, style="red" if evt.is_alert else "green"),
                        Text(evt.message[:60]),
                    )

        elif self.state.view_mode == ViewMode.CAPACITY:
            content_table.title = "Cluster Capacity & Bottlenecks"
            cap = self.latest_snapshot.capacity
            content_table.add_column("Resource", style="bold yellow")
            content_table.add_column("Metric Value", style="white")
            disk_str = f"{cap.disk_free_gb:.1f} GB" if cap.disk_free_gb is not None else "Unknown"
            runway_str = f"{cap.runway_days:.1f} days" if cap.runway_days is not None else "Unknown"
            content_table.add_row("Disk Free", disk_str)
            content_table.add_row("Runway", runway_str)
            content_table.add_row("Queue ETA", cap.formatted_eta)
            content_table.add_row("Active Bottleneck", Text(cap.bottleneck or "Unknown"))

        layout.split_column(
            Layout(header_panel, name="header", size=6),
            Layout(content_table, name="body"),
        )
        return layout


@contextmanager
def terminal_key_reader(stream: Any = None) -> Iterator[Callable[[float], str | None]]:
    """Yield ``read_key(timeout)`` for the dashboard run loop (cli_dashboard.run_live).

    On a POSIX TTY the terminal is in cbreak mode (one key at a time, no Enter,
    no echo; ^C still interrupts) for the life of the context and is restored
    on exit; arrow keys come back as "up"/"down". Without a TTY (piped stdin,
    Windows, no termios) ``read_key`` waits out the timeout and returns None,
    which is the passive refresh loop the dashboard ran before.
    """
    stream = sys.stdin if stream is None else stream
    saved = None
    fd = -1
    if termios is not None:
        try:
            fd = stream.fileno()
            saved = termios.tcgetattr(fd) if os.isatty(fd) else None
        except (AttributeError, OSError, ValueError, termios.error):
            saved = None
    if saved is None:
        def wait_only(timeout: float) -> str | None:
            time.sleep(max(0.0, timeout))
            return None

        yield wait_only
        return

    def read_key(timeout: float) -> str | None:
        ready, _, _ = select.select([fd], [], [], max(0.0, timeout))
        if not ready:
            return None
        key = os.read(fd, 1).decode("utf-8", "replace")
        if key != "\x1b":
            return key
        more, _, _ = select.select([fd], [], [], 0.05)
        return _ARROW_KEYS.get(os.read(fd, 2).decode("utf-8", "replace") if more else "")

    tty.setcbreak(fd)
    try:
        yield read_key
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


# Backward-compatible alias
InteractiveOperationalDashboard = TerminalDashboardController
