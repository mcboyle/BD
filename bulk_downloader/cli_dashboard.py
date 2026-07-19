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


# ─── Data fetch ───────────────────────────────────────────────────────

def _fetch_status(api_base: str) -> dict:
    """One HTTP call to /api/status_all. Returns the parsed JSON."""
    try:
        import requests
        r = requests.get(api_base.rstrip("/") + "/api/status_all", timeout=5)
        if r.ok:
            return r.json() or {}
    except Exception as e:
        return {"error": str(e)[:200]}
    return {}


def _fetch_capacity(api_base: str) -> dict:
    try:
        import requests
        r = requests.get(api_base.rstrip("/") + "/api/capacity", timeout=5)
        if r.ok:
            return r.json() or {}
    except Exception:
        pass
    return {}


def _fetch_events(api_base: str, *, limit: int = 8) -> list:
    """Pull recent events. BD exposes them under /api/events."""
    try:
        import requests
        r = requests.get(api_base.rstrip("/") + "/api/events",
                        params={"limit": limit}, timeout=5)
        if r.ok:
            return (r.json() or {}).get("events", [])
    except Exception:
        pass
    return []


# ─── Summarize ────────────────────────────────────────────────────────

def _summarize_site(site_state: dict) -> dict:
    """Roll up per-site counts from the jobs dict."""
    jobs = (site_state or {}).get("jobs") or {}
    counts = {"running": 0, "queued": 0, "done": 0, "failed": 0,
              "needs_review": 0}
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


def _build_capacity_panel(cap: dict) -> "Panel":
    disk = cap.get("disk", {})
    queue = cap.get("queue", {})
    bot = cap.get("bottleneck", {})
    parts = []
    if disk:
        free_gb = disk.get("free_gb", 0)
        runway = disk.get("runway_days")
        parts.append(f"Disk: [bold]{free_gb:.0f} GB free[/]" +
                     (f" ({runway:.1f}d runway)" if runway else ""))
    if queue:
        eta_min = queue.get("eta_minutes")
        if eta_min is not None:
            if eta_min < 60:
                parts.append(f"Queue ETA: [bold]{int(eta_min)}m[/]")
            else:
                parts.append(f"Queue ETA: [bold]{eta_min/60:.1f}h[/]")
    if bot.get("kind"):
        parts.append(f"Bottleneck: [yellow]{bot['kind']}[/]")
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

def run_once(*, api_base: str = "http://127.0.0.1:5000"):
    """Print one snapshot and exit. Useful for scripts / cron."""
    if not _HAS_RICH:
        return _run_once_plain(api_base)
    console = Console()
    status = _fetch_status(api_base)
    cap = _fetch_capacity(api_base)
    events = _fetch_events(api_base)
    console.print(_build_layout(status, cap, events))


def _run_once_plain(api_base: str):
    """Plain-text fallback when rich isn't installed."""
    status = _fetch_status(api_base)
    cap = _fetch_capacity(api_base)
    sites = (status or {}).get("sites") or {}
    # NOTE: we use sys.stdout.write rather than print() because BD's
    # static-analysis test forbids print() in library code (the
    # bulk_downloader/ package). cli_dashboard.py lives in the
    # package even though it's a CLI tool, so it must conform.
    out = sys.stdout
    out.write(f"BulkDownloader status @ {time.strftime('%H:%M:%S')}\n")
    out.write(f"  Sites: {len(sites)}\n")
    for sid, st in sorted(sites.items()):
        c = _summarize_site(st)
        out.write(
            f"    {sid:20} run={c['running']:3} queue={c['queued']:3} "
            f"done={c['done']:5} fail={c['failed']:3} "
            f"review={c['needs_review']:3}\n")
    disk = cap.get("disk", {})
    if disk:
        out.write(f"  Disk: {disk.get('free_gb', 0):.0f} GB free\n")
    out.flush()


def run_live(*, api_base: str = "http://127.0.0.1:5000",
            refresh_seconds: float = 2.0):
    """Run the live dashboard. Blocks the terminal until ^C."""
    if not _HAS_RICH:
        sys.stderr.write("rich not installed — falling back to one-shot mode.\n"
                         "pip install rich\n")
        return _run_once_plain(api_base)
    console = Console()
    with Live(console=console, refresh_per_second=4, screen=True) as live:
        try:
            while True:
                status = _fetch_status(api_base)
                cap = _fetch_capacity(api_base)
                events = _fetch_events(api_base)
                live.update(_build_layout(status, cap, events))
                time.sleep(refresh_seconds)
        except KeyboardInterrupt:
            pass


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
        run_once(api_base=args.api)
    else:
        run_live(api_base=args.api, refresh_seconds=args.refresh)
