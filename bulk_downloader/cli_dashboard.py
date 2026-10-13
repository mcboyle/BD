"""Terminal dashboard for BulkDownloader (Phase 110b, Block O).

A rich-based live TUI for operators who want full status without a
browser. Useful over SSH, in tmux panes, or as a second-screen
companion to the web UI.

Layout (single screen, no scrolling):

  ┌─ BulkDownloader Dashboard ────────────────────────────────────────┐
  │ Site         │ Running │ Queued │ Done   │ Failed │ Review │ Rate │
  │ vixen        │   3     │   42   │ 1,247  │   8    │   2    │ 4/h  │
  │ blacked      │   1     │    7   │   523  │   3    │   0    │ 2/h  │
  ├─ Recent events ───────────────────────────────────────────────────┤
  │ 18:42:33 vixen  done    riley.reid.beach.mp4 (2.1 GB)             │
  │ 18:42:08 blacked failed Cloudflare challenge — turnstile           │
  ├─ Capacity ────────────────────────────────────────────────────────┤
  │ Disk: 487 GB free (12 days runway) │ Queue ETA: ~6h               │
  └───────────────────────────────────────────────────────────────────┘

  Last refresh: 0.5s ago    [q] quit  [p] pause-all  [r] resume-all

Implementation:
  • Uses `rich` if available (most BD installs have it via tqdm deps).
  • Falls back to plain stdout on import error.
  • Refreshes every 2 seconds.
  • Optional --once flag for one-shot status (useful in scripts).
"""
from __future__ import annotations

import sys
import time
from typing import Optional

from bulk_downloader.terminal_dashboard import (
    TerminalDashboardController, _finite_or_none, terminal_key_reader)


_HAS_RICH = False

try:
    from rich.console import Console  # type: ignore
    from rich.table import Table
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    _HAS_RICH = True
except ImportError:
    pass


def is_available() -> bool:
    return _HAS_RICH


def _is_2xx(response) -> bool:
    """True only for a 2xx answer. requests' Response.ok is "below 400", so a
    300/302 that was not followed would count as a successful read.

    A reply object that carries no status code at all (a response stand-in
    exposing only `.ok`) is judged by `.ok`; requests always sets the code."""
    code = getattr(response, "status_code", None)
    if code is None:
        return bool(getattr(response, "ok", False))
    try:
        return 200 <= int(code) < 300
    except (TypeError, ValueError):
        return False


# ─── Data fetch ───────────────────────────────────────────────────────

def _fetch_status(api_base: str) -> dict:
    """One HTTP call to /api/status?light=1 (per-site counts, no job lists).

    BD answers {sid: status} (there is no /api/status_all route); this returns
    {"sites": {sid: status}}, the shape the renderers read."""
    try:
        import requests
        r = requests.get(api_base.rstrip("/") + "/api/status",
                         params={"light": "1"}, timeout=5)
        if _is_2xx(r):
            sites = r.json()
            if not isinstance(sites, dict):
                # A list / string / null is not a status map: not "no sites".
                return {"error": "unexpected /api/status payload "
                                 f"({type(sites).__name__})"}
            return {"sites": sites}
        # A refusal or an unfollowed redirect (300, 302, 401, 500, ...) is
        # not "no sites": say what BD answered.
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:200] or type(e).__name__}


def _fetch_capacity(api_base: str) -> dict:
    try:
        import requests
        r = requests.get(api_base.rstrip("/") + "/api/capacity", timeout=5)
        if _is_2xx(r):
            return r.json() or {}
    except Exception:
        pass
    return {}


def _fetch_events(api_base: str, *, limit: int = 8) -> list:
    """The `limit` newest events across all sites, newest first.

    BD serves them at /api/events_all ({"events": [{ts, kind, message,
    site_id, ...}]}, oldest first); there is no /api/events route. Per site
    that route returns the OLDEST `limit` events of a 500-event buffer, so ask
    for 500 (its cap) and keep the newest `limit`."""
    try:
        import requests
        r = requests.get(api_base.rstrip("/") + "/api/events_all",
                        params={"limit": 500}, timeout=5)
        if _is_2xx(r):
            events = (r.json() or {}).get("events")
            return events[::-1][:limit] if isinstance(events, list) else []
    except Exception:
        pass
    return []


# ─── Summarize ────────────────────────────────────────────────────────

def _summarize_site(site_state: dict) -> dict:
    """Roll up per-site counts from the jobs dict, or from the per-status
    `counts` that /api/status?light=1 sends in place of jobs."""
    jobs = (site_state or {}).get("jobs") or {}
    counts = {"running": 0, "queued": 0, "done": 0, "failed": 0,
              "needs_review": 0}
    if not jobs and isinstance((site_state or {}).get("counts"), dict):
        for s, n in site_state["counts"].items():
            key = "queued" if s == "pending" else s
            if key in counts and isinstance(n, int) and not isinstance(n, bool):
                counts[key] += n
        return counts
    for j in (jobs.values() if isinstance(jobs, dict) else []):
        s = (j or {}).get("status", "")
        if s in counts:
            counts[s] += 1
        elif s == "pending":
            counts["queued"] += 1
    return counts


# ─── Render ───────────────────────────────────────────────────────────

def _build_site_table(status: dict) -> "Table":
    table = Table(title="Sites", expand=True, show_lines=False)
    table.add_column("Site", style="cyan", no_wrap=True)
    table.add_column("Running", justify="right", style="green")
    table.add_column("Queued", justify="right", style="yellow")
    table.add_column("Done", justify="right", style="white")
    table.add_column("Failed", justify="right", style="red")
    table.add_column("Review", justify="right", style="magenta")
    sites = (status or {}).get("sites") or {}
    err = _status_error(status)
    if err:
        # BD down or refusing must not read as an empty install.
        table.add_row(Text(f"(BD unreachable: {err})"[:120], style="red"),
                      "-", "-", "-", "-", "-")
        return table
    if not sites:
        table.add_row("(no sites configured)", "-", "-", "-", "-", "-")
        return table
    for sid, st in sorted(sites.items()):
        c = _summarize_site(st)
        name = (st or {}).get("name") or sid
        table.add_row(
            name[:24],
            str(c["running"]) if c["running"] else "·",
            str(c["queued"]) if c["queued"] else "·",
            str(c["done"]) if c["done"] else "·",
            str(c["failed"]) if c["failed"] else "·",
            str(c["needs_review"]) if c["needs_review"] else "·",
        )
    return table


def _build_events_table(events: list) -> "Table":
    table = Table(title="Recent events", expand=True, show_lines=False)
    table.add_column("Time", style="dim", no_wrap=True)
    table.add_column("Site", style="cyan", no_wrap=True)
    table.add_column("Kind", no_wrap=True)
    table.add_column("Message", style="white")
    if not events:
        table.add_row("·", "·", "·", "(no events yet)")
        return table
    for evt in events[:8]:
        ts = evt.get("ts", "")
        if isinstance(ts, (int, float)):
            ts = time.strftime("%H:%M:%S", time.localtime(ts))
        kind = evt.get("kind", "")
        style = {"done": "green", "failed": "red",
                 "needs_review": "magenta"}.get(kind, "white")
        table.add_row(
            str(ts)[-8:],
            (evt.get("site_id") or "")[:12],
            Text(kind, style=style),
            (evt.get("message") or "")[:60],
        )
    return table


def _status_error(status: dict) -> str:
    """The reason /api/status could not be read, or "" when it was."""
    err = (status or {}).get("error") if isinstance(status, dict) else None
    return str(err) if err else ""


def _capacity_facts(cap: dict) -> dict:
    """The figures both renderers show, read from capacity.capacity_report():
    disk {free_gb, runway_days} (free_gb is null when no download dir is known
    or disk_usage failed), queue {eta_hours} (null = rate unknown) and
    bottleneck {bottleneck, detail, severity}. A figure BD did not measure is
    None here, never 0."""
    cap = cap if isinstance(cap, dict) else {}

    def _d(key):
        v = cap.get(key)
        return v if isinstance(v, dict) else {}

    disk, queue, bot = _d("disk"), _d("queue"), _d("bottleneck")
    eta_hours = _finite_or_none(queue.get("eta_hours"))
    eta_min = (eta_hours * 60.0 if eta_hours is not None
               else _finite_or_none(queue.get("eta_minutes")))
    kind = bot.get("bottleneck") or bot.get("kind") or ""
    return {
        "has_disk": bool(disk),
        "free_gb": _finite_or_none(disk.get("free_gb")),
        "runway_days": _finite_or_none(disk.get("runway_days")),
        "eta_minutes": eta_min,
        # "idle" is capacity.bottleneck_hint's "no active load": not a bottleneck.
        "bottleneck": "" if kind == "idle" else str(kind),
    }


def _build_capacity_panel(cap: dict) -> "Panel":
    facts = _capacity_facts(cap)
    parts = []
    if facts["has_disk"]:
        free_gb, runway = facts["free_gb"], facts["runway_days"]
        if free_gb is None:
            parts.append("Disk: free space unknown")
        else:
            parts.append(f"Disk: [bold]{free_gb:.0f} GB free[/]" +
                         (f" ({runway:.1f}d runway)" if runway else ""))
    eta_min = facts["eta_minutes"]
    if eta_min is not None:
        if eta_min < 60:
            parts.append(f"Queue ETA: [bold]{int(eta_min)}m[/]")
        else:
            parts.append(f"Queue ETA: [bold]{eta_min/60:.1f}h[/]")
    if facts["bottleneck"]:
        parts.append(f"Bottleneck: [yellow]{facts['bottleneck']}[/]")
    if not parts:
        return Panel("(capacity data unavailable)", title="Capacity")
    return Panel("  ·  ".join(parts), title="Capacity")


def _build_layout(status: dict, cap: dict, events: list) -> "Layout":
    layout = Layout()
    layout.split_column(
        Layout(_build_site_table(status), name="sites"),
        Layout(_build_events_table(events), name="events", size=12),
        Layout(_build_capacity_panel(cap), name="capacity", size=3),
    )
    return layout


# ─── Public entry ─────────────────────────────────────────────────────

def run_once(*, api_base: str = "http://127.0.0.1:5000") -> int:
    """Print one snapshot and exit. Useful for scripts / cron.

    Returns 0 when /api/status was read, 1 when BD was down or refused -- the
    exit code of `--once`, so a script can tell "no sites" from "no BD"."""
    if not _HAS_RICH:
        return _run_once_plain(api_base)
    console = Console()
    status = _fetch_status(api_base)
    cap = _fetch_capacity(api_base)
    events = _fetch_events(api_base)
    console.print(_build_layout(status, cap, events))
    return 1 if _status_error(status) else 0


def _run_once_plain(api_base: str) -> int:
    """Plain-text fallback when rich isn't installed. Returns run_once's code."""
    status = _fetch_status(api_base)
    cap = _fetch_capacity(api_base)
    sites = (status or {}).get("sites") or {}
    # NOTE: we use sys.stdout.write rather than print() because BD's
    # static-analysis test forbids print() in library code (the
    # bulk_downloader/ package). cli_dashboard.py lives in the
    # package even though it's a CLI tool, so it must conform.
    out = sys.stdout
    frame: list = []

    def emit(line: str) -> None:
        # Row 988: each line is written as soon as it is built (as before)
        # and kept, so the finished frame can be inspected once it is out.
        frame.append(line)
        out.write(line + "\n")

    emit(f"BulkDownloader status @ {time.strftime('%H:%M:%S')}")
    err = _status_error(status)
    if err:
        emit(f"  BD unreachable: {err}")
    else:
        emit(f"  Sites: {len(sites)}")
    for sid, st in sorted(sites.items()):
        c = _summarize_site(st)
        emit(
            f"    {sid:20} run={c['running']:3} queue={c['queued']:3} "
            f"done={c['done']:5} fail={c['failed']:3} "
            f"review={c['needs_review']:3}")
    facts = _capacity_facts(cap)
    if facts["has_disk"]:
        if facts["free_gb"] is None:
            emit("  Disk: free space unknown")
        else:
            emit(f"  Disk: {facts['free_gb']:.0f} GB free")
    out.flush()
    # Row 988: stdout keeps the frame byte-for-byte (scripts parse it); what
    # would corrupt the operator's terminal -- e.g. a site id carrying an
    # unclosed ANSI style or a raw control char -- is named on stderr.
    for anomaly in inspect_dashboard_frame("\n".join(frame)).anomalies:
        sys.stderr.write(f"dashboard render anomaly: {anomaly.anomaly_type}: "
                         f"{anomaly.description}\n")
    return 1 if err else 0


def run_live(*, api_base: str = "http://127.0.0.1:5000",
            refresh_seconds: float = 2.0):
    """Run the live dashboard. Blocks the terminal until [q] or ^C.

    Row 985: every `refresh_seconds` tick polls BD into a
    TerminalDashboardController snapshot; each key goes to the controller and
    is drawn at once (j/k select a site, x/u/t pause/resume/retry it, 1/2/3
    switch view, p holds the refresh, r polls now, q quits). A key costs no
    poll, so a burst of keys is not a burst of GETs."""
    if not _HAS_RICH:
        sys.stderr.write("rich not installed — falling back to one-shot mode.\n"
                         "pip install rich\n")
        return _run_once_plain(api_base)
    console = Console()
    dashboard = TerminalDashboardController(api_base=api_base)
    with Live(console=console, refresh_per_second=4, screen=True) as live, \
            terminal_key_reader() as read_key:
        try:
            next_poll = 0.0
            while not dashboard.should_quit:
                if dashboard.refresh_requested or time.monotonic() >= next_poll:
                    if not dashboard.state.paused:
                        dashboard.assemble_snapshot(_fetch_status(api_base),
                                                    _fetch_capacity(api_base),
                                                    _fetch_events(api_base))
                    next_poll = time.monotonic() + refresh_seconds
                live.update(dashboard.render_rich_frame())
                key = read_key(max(0.0, next_poll - time.monotonic()))
                if key:
                    dashboard.handle_key(key)
        except KeyboardInterrupt:
            pass


def inspect_dashboard_frame(frame_text: str, max_width: Optional[int] = None):
    """Inspect a rendered dashboard frame for visual anomalies and ANSI integrity."""
    from .terminal_drift import inspect_visual_artifact
    return inspect_visual_artifact(frame_text, max_allowed_width=max_width)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="BulkDownloader CLI dashboard")
    p.add_argument("--api", default="http://127.0.0.1:5000",
                  help="BD dashboard URL (default: http://127.0.0.1:5000)")
    p.add_argument("--once", action="store_true",
                  help="Print one snapshot and exit (default: live mode)")
    p.add_argument("--refresh", type=float, default=2.0,
                  help="Refresh interval seconds (default: 2.0)")
    args = p.parse_args()
    if args.once:
        sys.exit(run_once(api_base=args.api))
    else:
        run_live(api_base=args.api, refresh_seconds=args.refresh)
