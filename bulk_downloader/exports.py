"""Export helpers (Phase 129, Block Q).

Render BD's history + library to common formats for external tools:

  • CSV      — Excel / Google Sheets / pandas
  • JSON     — generic structured consumer (jq pipelines, scripts)
  • NDJSON   — line-delimited, streamable for big datasets
  • M3U      — playlist for VLC / mpv / Plex auto-import
  • OPML     — RSS-reader-compatible site-as-feed export

All export functions take an optional `filter` dict (same shape as
batch_ops.bulk_*) and return bytes ready to write to disk or POST.
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
from typing import Optional


def _rows_from_filter(filter_dict: Optional[dict] = None) -> list:
    from . import batch_ops as _bo
    return _bo._matching_rows(filter_dict or {"limit": 10000})


# ─── CSV ──────────────────────────────────────────────────────────────

_CSV_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def _csv_neutralize(cell: str) -> str:
    """Prefix a leading spreadsheet formula/command trigger with a single quote
    so a site-controlled cell cannot execute when the CSV export is opened in
    Excel/Google Sheets (CSV formula injection, F-SWEEP-N4). The quote is
    stripped by the spreadsheet on display, so legitimate values read unchanged.
    """
    if cell and cell[0] in _CSV_FORMULA_LEADS:
        return "'" + cell
    return cell


def to_csv(filter_dict: Optional[dict] = None,
          *, columns: Optional[list] = None,
          rows: Optional[list] = None) -> bytes:
    """Render filtered rows as CSV.

    Default columns: id, site_id, url, filename, file_size, ts, status,
    message. Override with `columns` for slimmer output.

    CAP-3 (v3.66.667): ``rows`` pre-resolves the export set (e.g. a saved
    search's FTS matches); when given it bypasses ``filter_dict``."""
    rows = rows if rows is not None else _rows_from_filter(filter_dict)
    default_cols = ["id", "site_id", "site_name", "url", "filename",
                    "file_size", "ts", "status", "message"]
    cols = columns or default_cols
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow(cols)
    for r in rows:
        w.writerow([_csv_neutralize(str(r.get(c, "") or "")) for c in cols])
    return buf.getvalue().encode("utf-8")


# ─── JSON / NDJSON ────────────────────────────────────────────────────

def to_json(filter_dict: Optional[dict] = None,
           *, pretty: bool = True, rows: Optional[list] = None) -> bytes:
    """Render filtered rows as a JSON array. CAP-3: ``rows`` override."""
    rows = rows if rows is not None else _rows_from_filter(filter_dict)
    if pretty:
        return json.dumps(rows, indent=2, default=str,
                         ensure_ascii=False).encode("utf-8")
    return json.dumps(rows, default=str,
                     ensure_ascii=False).encode("utf-8")


def to_ndjson(filter_dict: Optional[dict] = None,
             *, rows: Optional[list] = None) -> bytes:
    """Render filtered rows as newline-delimited JSON. Streamable for
    very large exports; jq-friendly. CAP-3: ``rows`` override."""
    rows = rows if rows is not None else _rows_from_filter(filter_dict)
    out = []
    for r in rows:
        out.append(json.dumps(r, default=str, ensure_ascii=False))
    return "\n".join(out).encode("utf-8") + b"\n"


# ─── M3U playlist ─────────────────────────────────────────────────────

def to_m3u(filter_dict: Optional[dict] = None,
          *, base_url: Optional[str] = None,
          rows: Optional[list] = None) -> bytes:
    """Render filtered rows as a M3U/M3U8 extended playlist.

    Filters to rows with an actual local file (`filename` set + exists).
    If `base_url` is provided, paths are rewritten as URLs under that
    base (useful when the playlist is served over HTTP)."""
    rows = rows if rows is not None else _rows_from_filter(filter_dict)
    out = ["#EXTM3U"]
    for r in rows:
        fn = r.get("filename") or ""
        if not fn:
            continue
        if not os.path.isfile(fn):
            continue
        # Title preference: basename without extension
        title = (r.get("site_name", "") + " — " +
                 os.path.splitext(os.path.basename(fn))[0])[:200]
        out.append(f"#EXTINF:-1,{title}")
        if base_url:
            # Strip leading slashes, join with base
            rel = fn.lstrip("/")
            out.append(base_url.rstrip("/") + "/" + rel)
        else:
            out.append(fn)
    return ("\n".join(out) + "\n").encode("utf-8")


# ─── OPML ─────────────────────────────────────────────────────────────

def to_opml(s_cfg: dict) -> bytes:
    """Render configured sites as an OPML 2.0 outline. RSS readers
    can import this to subscribe to each site's "newest" feed (if BD
    exposes RSS — which is a separate phase). At minimum it's a
    durable site-list backup format."""
    if not s_cfg:
        s_cfg = {}
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        '  <head>',
        '    <title>BulkDownloader configured sites</title>',
        f'    <dateCreated>{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}</dateCreated>',
        '  </head>',
        '  <body>',
    ]
    for sid, cfg in (s_cfg or {}).items():
        name = (cfg or {}).get("name", sid).replace("&", "&amp;").replace('"', "&quot;")
        sid_safe = sid.replace("&", "&amp;").replace('"', "&quot;")
        feed_url = f"/api/recent/{sid_safe}.rss"  # speculative — fine
        lines.append(
            f'    <outline type="rss" text="{name}" '
            f'title="{name}" xmlUrl="{feed_url}"/>'
        )
    lines.extend(['  </body>', '</opml>'])
    return "\n".join(lines).encode("utf-8")
