"""Error fingerprinting (Phase 159, Block Q).

20 unique-looking error messages often reduce to 2-3 root causes once
you normalize away variable bits (timestamps, IDs, paths). This module
fingerprints error strings to cluster them for faster triage.

The fingerprint algorithm:
  1. Strip URLs, paths, hashes, timestamps, request-IDs (replace
     with placeholders)
  2. Strip line/column numbers from tracebacks
  3. Truncate very long messages
  4. Hash the result

`fingerprint("Connection refused at 192.168.1.42:80 at 14:00:01")`
yields the same fingerprint as
`fingerprint("Connection refused at 10.0.0.5:443 at 18:42:11")`.

Use:
  • cluster_recent_failures(hours=24) → list of clusters sorted by
    frequency. Operator triages one root cause at a time.
  • fingerprint_for(message) → just compute the FP for one message.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional


# Normalization patterns — apply in order. Each pattern.match → replacement.
_NORM_PATTERNS = [
    # Timestamps (ISO-like, h:m:s, etc.)
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+\-]\d{2}:?\d{2})?\b"), "<TS>"),
    (re.compile(r"\b\d{1,2}:\d{2}:\d{2}\b"), "<TIME>"),
    # IPv4 / IPv6 / port-suffixed
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?\b"), "<IP>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b"), "<IP6>"),
    # URLs — strip the dynamic parts but keep scheme+host pattern
    (re.compile(r"https?://[^\s,]+"), "<URL>"),
    # File paths (Unix + Windows)
    (re.compile(r"[/\\][\w\-./\\]+\.[a-zA-Z]{2,5}\b"), "<PATH>"),
    # UUIDs
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<UUID>"),
    # Long hex strings (hashes)
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<HASH>"),
    # Large integers (request IDs, sequence numbers)
    (re.compile(r"\b\d{6,}\b"), "<N>"),
    # Python line numbers in tracebacks
    (re.compile(r'File "[^"]+", line \d+'), 'File "<F>", line <L>'),
    # Multiple whitespace
    (re.compile(r"\s+"), " "),
]


def fingerprint(message: str, *, max_length: int = 200) -> dict:
    """Compute a fingerprint for an error message.

    Returns {fingerprint, normalized, original}:
      • fingerprint: short hash for grouping
      • normalized: the normalized string (variable parts replaced)
      • original: input (truncated to max_length)
    """
    if not message:
        return {"fingerprint": "empty", "normalized": "", "original": ""}
    s = str(message)[:max_length * 2]  # trim before normalization
    for pat, repl in _NORM_PATTERNS:
        s = pat.sub(repl, s)
    s = s.strip()[:max_length]
    fp = hashlib.sha1(s.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return {
        "fingerprint": fp,
        "normalized": s,
        "original": message[:max_length],
    }


def cluster_recent_failures(*, hours: int = 24,
                           min_cluster_size: int = 1,
                           limit_messages: int = 5000) -> list:
    """Group recent failed jobs by fingerprint. Returns list of
    {fingerprint, normalized, count, sites, sample_urls, first_seen,
    last_seen}, sorted by count desc."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT message, site_id, url, ts
                                  FROM history
                                  WHERE status = 'failed'
                                    AND ts >= datetime('now', ?)
                                    AND message != ''
                                  ORDER BY id DESC LIMIT ?""",
                              (f"-{int(hours)} hours",
                               int(limit_messages))).fetchall()
    except Exception:
        return []
    clusters: dict = {}
    for r in rows:
        d = dict(r)
        msg = d.get("message") or ""
        fp = fingerprint(msg)
        key = fp["fingerprint"]
        if key not in clusters:
            clusters[key] = {
                "fingerprint": key,
                "normalized": fp["normalized"],
                "count": 0,
                "sites": set(),
                "sample_urls": [],
                "sample_messages": [],
                "first_seen": d.get("ts"),
                "last_seen": d.get("ts"),
            }
        c = clusters[key]
        c["count"] += 1
        c["sites"].add(d.get("site_id") or "")
        if len(c["sample_urls"]) < 3:
            c["sample_urls"].append(d.get("url") or "")
        if len(c["sample_messages"]) < 2:
            c["sample_messages"].append(msg[:200])
        # ts ordering (string compare works for ISO)
        if d.get("ts") and d["ts"] < c["first_seen"]:
            c["first_seen"] = d["ts"]
    # Serialize sets
    out = []
    for c in clusters.values():
        if c["count"] < min_cluster_size:
            continue
        c["sites"] = sorted(c["sites"])
        out.append(c)
    out.sort(key=lambda x: -x["count"])
    return out


def fingerprint_for(message: str) -> str:
    """Convenience: just the FP hash."""
    return fingerprint(message)["fingerprint"]


def explain_top_clusters(*, hours: int = 24, n: int = 5) -> list:
    """Top N clusters with a one-liner explanation per cluster.
    Useful for the operator dashboard 'top errors' widget."""
    clusters = cluster_recent_failures(hours=hours)[:n]
    out = []
    for c in clusters:
        # Heuristic categorization
        cat = "other"
        norm = c["normalized"].lower()
        if "timeout" in norm or "timed out" in norm:
            cat = "timeout"
        elif "401" in norm or "unauthor" in norm or "forbidden" in norm or "403" in norm:
            cat = "auth"
        elif "429" in norm or "rate" in norm or "too many" in norm:
            cat = "rate_limit"
        elif "captcha" in norm or "challenge" in norm or "cloudflare" in norm:
            cat = "captcha"
        elif "ssl" in norm or "certificate" in norm:
            cat = "ssl"
        elif "dns" in norm or "getaddrinfo" in norm:
            cat = "dns"
        elif "no space" in norm or "disk" in norm:
            cat = "disk"
        elif "permission" in norm or "denied" in norm:
            cat = "perms"
        out.append({**c, "category": cat})
    return out
