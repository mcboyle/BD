#!/usr/bin/env python3
"""
report_core.py — shared markdown/JSON rendering + write helpers (consolidation).

Collapses the per-tool boilerplate (makedirs + open/write, dual json+md writes) and
the small repeated renderers (bullet lists, key/value lines, tables, ✓/· cells)
into one place. Adopting the *write* helpers is pure plumbing — it does not change
any report's content. The *render* helpers are adopted only where output is
identical; remaining bespoke renderers are intentionally left as-is to avoid format
drift (documented in CONSOLIDATION_SUMMARY.md).

Read-only; the write helpers are the only side-effecting functions and they only
write the file path the caller already intended to write.
"""
import json
import os


# ── write helpers (pure plumbing; content unchanged) ───────────────
def write_md(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    return path


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2, default=str)
    return path


def write_report(outdir, name, text):
    """makedirs(outdir) + write `name` with `text`; returns the full path."""
    os.makedirs(outdir, exist_ok=True)
    return write_md(os.path.join(outdir, name), text)


# ── render helpers (adopt only where output is identical) ──────────
def yn(b):
    return "✓" if b else "·"


def h1(title):
    return [f"# {title}", ""]


def bullets(items):
    return [f"- {it}" for it in items]


def kv_lines(d):
    return [f"{k}: {v}" for k, v in d.items()]


def table(headers, rows, aligns=None):
    sep = "|" + "|".join(aligns[i] if aligns else "---" for i in range(len(headers))) + "|"
    out = ["| " + " | ".join(headers) + " |", sep]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return out
