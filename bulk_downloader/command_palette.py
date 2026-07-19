"""Command palette (Phase 145, Block Q).

A Ctrl+K-style command palette needs a server-side catalog of actions.
This module exposes one: every common operator action is registered
here with a name, description, and minimal invocation hint. The
front-end fuzzy-matches user input against the catalog and renders
results.

Each command:
  {
    id: 'queue.pause_all',
    label: 'Pause all workers',
    description: 'Stop all running jobs across all sites',
    keywords: ['pause', 'stop', 'halt', 'workers'],
    category: 'queue',
    endpoint: {method, path, body_template},
    requires_confirm: bool
  }

This module is pure declarative — it doesn't execute anything. The
front-end calls the appropriate /api/* endpoint when the operator
picks a command.
"""
from __future__ import annotations


CATALOG = [
    # ─── Queue control ────────────────────────────────────────────
    {
        "id": "queue.pause_all",
        "label": "Pause all workers",
        "description": "Stop all running jobs across all sites",
        "keywords": ["pause", "stop", "halt", "workers", "all"],
        "category": "queue",
        "endpoint": {"method": "POST", "path": "/api/pause_all"},
        "requires_confirm": False,
    },
    {
        "id": "queue.resume_all",
        "label": "Resume all workers",
        "description": "Restart all paused workers",
        "keywords": ["resume", "start", "restart", "workers"],
        "category": "queue",
        "endpoint": {"method": "POST", "path": "/api/resume_all"},
        "requires_confirm": False,
    },
    {
        "id": "queue.retry_failed",
        "label": "Retry all failed jobs",
        "description": "Re-queue every job in 'failed' status",
        "keywords": ["retry", "failed", "redo", "requeue"],
        "category": "queue",
        "endpoint": {"method": "POST", "path": "/api/batch/retry",
                     "body_template": {"filter": {"status": "failed"},
                                       "dry_run": False}},
        "requires_confirm": True,
    },
    # ─── Health & diagnostics ─────────────────────────────────────
    {
        "id": "diagnostics.health",
        "label": "Run health checklist",
        "description": "Run all health checks and show results",
        "keywords": ["health", "check", "diagnose", "audit"],
        "category": "diagnostics",
        "endpoint": {"method": "GET", "path": "/api/health/checklist"},
        "requires_confirm": False,
    },
    {
        "id": "diagnostics.bundle_download",
        "label": "Download diagnostics bundle",
        "description": "Generate redacted state snapshot for support",
        "keywords": ["diagnostics", "support", "bundle", "download"],
        "category": "diagnostics",
        "endpoint": {"method": "GET", "path": "/api/diagnostics/download"},
        "requires_confirm": False,
    },
    {
        "id": "diagnostics.cleanup_summary",
        "label": "Find cleanup candidates",
        "description": "Scan for tiny files, orphans, stale partials",
        "keywords": ["cleanup", "tinies", "orphans", "stale", "junk"],
        "category": "diagnostics",
        "endpoint": {"method": "GET", "path": "/api/cleanup/summary"},
        "requires_confirm": False,
    },
    # ─── Cookies & accounts ───────────────────────────────────────
    {
        "id": "cookies.quality_all",
        "label": "Score all cookie jars",
        "description": "Rate every site's cookies; lowest first",
        "keywords": ["cookies", "auth", "login", "score", "quality"],
        "category": "auth",
        "endpoint": {"method": "GET", "path": "/api/cookie_quality"},
        "requires_confirm": False,
    },
    {
        "id": "accounts.health",
        "label": "Account health report",
        "description": "Per-account success rate and risk",
        "keywords": ["accounts", "health", "vpn"],
        "category": "auth",
        "endpoint": {"method": "GET", "path": "/api/accounts/health"},
        "requires_confirm": False,
    },
    # ─── Library ──────────────────────────────────────────────────
    {
        "id": "library.export_csv",
        "label": "Export library as CSV",
        "description": "Download history as a CSV file",
        "keywords": ["export", "csv", "download", "spreadsheet"],
        "category": "library",
        "endpoint": {"method": "GET", "path": "/api/export/csv"},
        "requires_confirm": False,
    },
    {
        "id": "library.export_m3u",
        "label": "Export library as M3U playlist",
        "description": "Generate an M3U for VLC / mpv",
        "keywords": ["export", "m3u", "playlist", "vlc"],
        "category": "library",
        "endpoint": {"method": "GET", "path": "/api/export/m3u"},
        "requires_confirm": False,
    },
    {
        "id": "library.dedup_scan",
        "label": "Find duplicate files",
        "description": "Two-pass scan: size collisions, then hash",
        "keywords": ["dedup", "duplicates", "scan"],
        "category": "library",
        "endpoint": {"method": "POST", "path": "/api/batch/dedup_scan"},
        "requires_confirm": False,
    },
    {
        "id": "library.full_text_search",
        "label": "Search library",
        "description": "Full-text search across history",
        "keywords": ["search", "find", "fts"],
        "category": "library",
        "endpoint": {"method": "GET", "path": "/api/search",
                     "prompts": [{"name": "q", "label": "Query"}]},
        "requires_confirm": False,
    },
    # ─── Storage ──────────────────────────────────────────────────
    {
        "id": "storage.capacity",
        "label": "Storage capacity",
        "description": "Free space + runway days per disk",
        "keywords": ["disk", "storage", "free", "capacity"],
        "category": "storage",
        "endpoint": {"method": "GET", "path": "/api/capacity"},
        "requires_confirm": False,
    },
    {
        "id": "storage.rebalance_inventory",
        "label": "Storage rebalance — inventory",
        "description": "Show current disk usage across mount points",
        "keywords": ["rebalance", "inventory", "disks"],
        "category": "storage",
        "endpoint": {"method": "GET", "path": "/api/rebalance/inventory"},
        "requires_confirm": False,
    },
    # ─── Updates & maintenance ────────────────────────────────────
    {
        "id": "ytdlp.check_version",
        "label": "Check yt-dlp version",
        "description": "Show current yt-dlp version + age",
        "keywords": ["ytdlp", "yt-dlp", "update", "version"],
        "category": "maintenance",
        "endpoint": {"method": "GET", "path": "/api/ytdlp/version"},
        "requires_confirm": False,
    },
    {
        "id": "bitrot.scan",
        "label": "Bit-rot integrity scan",
        "description": "Verify checksums on a sample of files",
        "keywords": ["bitrot", "integrity", "checksum", "verify"],
        "category": "maintenance",
        "endpoint": {"method": "POST", "path": "/api/bitrot/scan"},
        "requires_confirm": True,
    },
    # ─── Templates & sharing ──────────────────────────────────────
    {
        "id": "marketplace.export",
        "label": "Export site template",
        "description": "Generate a portable .bdtpl bundle",
        "keywords": ["template", "marketplace", "export", "share"],
        "category": "templates",
        "endpoint": {"method": "POST",
                     "path": "/api/marketplace/export/<sid>",
                     "prompts": [{"name": "sid", "label": "Site ID"}]},
        "requires_confirm": False,
    },
    {
        "id": "gamification.report",
        "label": "Show achievements",
        "description": "Lifetime stats + unlocked badges",
        "keywords": ["achievements", "stats", "badges", "streak"],
        "category": "stats",
        "endpoint": {"method": "GET", "path": "/api/gamification/report"},
        "requires_confirm": False,
    },
]


def list_commands(*, category: str = "", q: str = "") -> list:
    """Return the catalog, optionally filtered by category or
    keyword. The frontend does its own fuzzy match; this is the
    coarse pre-filter."""
    out = list(CATALOG)
    if category:
        out = [c for c in out if c.get("category") == category]
    if q:
        q_low = q.lower().strip()
        out = [c for c in out
               if q_low in c.get("label", "").lower()
                  or q_low in c.get("description", "").lower()
                  or any(q_low in kw.lower()
                         for kw in c.get("keywords", []))]
    return out


def categories() -> list:
    """Distinct category list, sorted."""
    return sorted({c.get("category", "other") for c in CATALOG})
