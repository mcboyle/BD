"""Incremental URL discovery (Phase 135, Block Q).

Most adult sites expose either an RSS feed (newest scenes) or a
sitemap.xml (full URL index). This module polls those sources on a
schedule and feeds new URLs into the queue automatically.

Two discovery mechanisms:

  • RSS — poll `<channel><item><link>` from a feed URL. Track the
    most recent seen `guid`/`link` so we don't re-enqueue old
    entries.

  • Sitemap — fetch sitemap.xml, parse `<url><loc>` entries. Filter
    by URL pattern (e.g. only paths matching `/v/.+`). Track all
    seen URLs (more memory; sitemaps can be 100K+ entries).

Per-site config:
  discovery:
    rss_url: "https://site.com/feed.xml"     # or
    sitemap_url: "https://site.com/sitemap.xml"
    url_pattern: "https://site\\.com/v/.+"   # only match these
    poll_interval_minutes: 60
    enabled: true

This module is fail-open: any network error or parse failure is
logged and the next poll tries again.
"""
from __future__ import annotations

import re
import sys
import time
from typing import Optional
from xml.etree import ElementTree as ET


def _ensure_table():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS discovery_seen(
                site_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                first_seen REAL NOT NULL,
                PRIMARY KEY (site_id, source_url)
            )""")
            cx.execute("""CREATE TABLE IF NOT EXISTS discovery_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                site_id TEXT NOT NULL,
                source TEXT NOT NULL,
                discovered INTEGER DEFAULT 0,
                enqueued INTEGER DEFAULT 0,
                error TEXT DEFAULT ''
            )""")
    except Exception as e:
        sys.stderr.write(f"[discovery] schema init: {e}\n")


# ─── Source fetchers ──────────────────────────────────────────────────

def _fetch(url: str, *, timeout: int = 30) -> Optional[bytes]:
    try:
        import requests
        r = requests.get(url, timeout=timeout, headers={
            "User-Agent": "BulkDownloader-Discovery/1.0"})
        if r.ok:
            return r.content
    except Exception:
        pass
    return None


def parse_rss(body: bytes) -> list:
    """Extract URLs from an RSS feed's <item><link> entries."""
    out = []
    try:
        root = ET.fromstring(body)
        # RSS 2.0 has channel/item/link; Atom has entry/link@href
        # Try RSS first
        for item in root.iter():
            tag = item.tag.lower().split("}")[-1]
            if tag == "link":
                # RSS: text content. Atom: href attribute.
                href = item.get("href") or item.text
                if href and isinstance(href, str) and href.startswith("http"):
                    out.append(href.strip())
    except ET.ParseError:
        pass
    return out


def parse_sitemap(body: bytes) -> list:
    """Extract URLs from <url><loc>...</loc></url> entries."""
    out = []
    try:
        root = ET.fromstring(body)
        for loc in root.iter():
            tag = loc.tag.lower().split("}")[-1]
            if tag == "loc" and loc.text:
                href = loc.text.strip()
                if href.startswith("http"):
                    out.append(href)
    except ET.ParseError:
        pass
    return out


# ─── Seen-set management ──────────────────────────────────────────────

def _already_seen(site_id: str, urls: list) -> set:
    """Return the subset of `urls` we've already recorded."""
    _ensure_table()
    if not urls:
        return set()
    try:
        from . import db as _db
        placeholders = ",".join("?" * len(urls))
        with _db.db_conn() as cx:
            rows = cx.execute(
                f"""SELECT source_url FROM discovery_seen
                    WHERE site_id = ? AND source_url IN ({placeholders})""",
                (site_id, *urls)).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def _record_seen(site_id: str, urls: list):
    if not urls:
        return
    _ensure_table()
    now = time.time()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.executemany(
                """INSERT OR IGNORE INTO discovery_seen(
                    site_id, source_url, first_seen)
                   VALUES (?,?,?)""",
                [(site_id, u, now) for u in urls])
    except Exception:
        pass


# ─── Discovery passes ────────────────────────────────────────────────

def discover_one(site_id: str, discovery_cfg: dict,
                *, enqueue_fn=None) -> dict:
    """Run one discovery cycle for one site.

    `discovery_cfg` is the per-site `discovery` dict.
    `enqueue_fn(site_id, urls)` is invoked with the new-URL list;
    pass None for dry-run mode."""
    out = {"site_id": site_id, "source": "", "discovered": 0,
           "new_urls": 0, "enqueued": 0, "error": ""}
    src_url = (discovery_cfg.get("rss_url") or
               discovery_cfg.get("sitemap_url") or "")
    if not src_url:
        out["error"] = "no rss_url or sitemap_url configured"
        return out
    out["source"] = "rss" if discovery_cfg.get("rss_url") else "sitemap"
    body = _fetch(src_url)
    if not body:
        out["error"] = f"fetch failed: {src_url}"
        _log_run(site_id, out)
        return out
    if out["source"] == "rss":
        urls = parse_rss(body)
    else:
        urls = parse_sitemap(body)
    out["discovered"] = len(urls)
    # Pattern filter
    pattern = discovery_cfg.get("url_pattern")
    if pattern:
        try:
            rx = re.compile(pattern)
            urls = [u for u in urls if rx.search(u)]
        except re.error:
            pass
    # Dedup against seen-set
    seen = _already_seen(site_id, urls)
    new_urls = [u for u in urls if u not in seen]
    out["new_urls"] = len(new_urls)
    if not new_urls:
        _log_run(site_id, out)
        return out
    # Enqueue + record
    if enqueue_fn:
        try:
            enq_count = enqueue_fn(site_id, new_urls) or 0
            out["enqueued"] = int(enq_count)
        except Exception as e:
            out["error"] = f"enqueue failed: {e}"
    _record_seen(site_id, new_urls)
    _log_run(site_id, out)
    return out


def _log_run(site_id: str, out: dict):
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""INSERT INTO discovery_log(
                ts, site_id, source, discovered, enqueued, error
            ) VALUES (?,?,?,?,?,?)""",
                (time.time(), site_id, out.get("source", ""),
                 out.get("discovered", 0), out.get("enqueued", 0),
                 out.get("error", "")[:300]))
    except Exception:
        pass


def discover_all(s_cfg: dict, *, enqueue_fn=None) -> list:
    """Run discovery for every site with `discovery.enabled=True`."""
    results = []
    for sid, cfg in (s_cfg or {}).items():
        dcfg = (cfg or {}).get("discovery") or {}
        if not dcfg.get("enabled"):
            continue
        try:
            results.append(discover_one(sid, dcfg, enqueue_fn=enqueue_fn))
        except Exception as e:
            results.append({"site_id": sid, "error": str(e)[:200]})
    return results


def recent_runs(limit: int = 50) -> list:
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT * FROM discovery_log
                                  ORDER BY id DESC LIMIT ?""",
                              (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
