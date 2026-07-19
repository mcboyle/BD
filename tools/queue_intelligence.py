#!/usr/bin/env python3
"""
queue_intelligence.py — read-only queue diagnostics + health (#22 / P8).

Composes the existing read helpers in bulk_downloader/db.py (queue_count_by_status,
queue_search, db_queue_recovery_summary) — it adds no new SQL writers. It reports:

  * counts by status + totals (done / in-flight / failures) + failure rate
  * failure categorization — buckets the `message` of error/failed rows
    (timeout, auth, not_found, forbidden, rate_limit, challenge, network, parse,
     disk_io, cancelled, unknown)
  * retry reason reporting — top failure messages + retry-count distribution
  * stuck URL reporting — in-flight rows (pending/running) that are over the retry
    threshold OR have not advanced for longer than the age threshold
  * per-site health (from the recovery summary)

The only DB call that is not a pure SELECT is `db.db_init()`, which runs
`CREATE TABLE IF NOT EXISTS` — a no-op on a live DB (tables already exist) and
never writes or alters data. Every read is wrapped so a missing table degrades to
empty rather than raising.

SQLite stamps ts_updated in UTC (`strftime('now')`), so ages are computed against
UTC.

CLI:
    python3 tools/queue_intelligence.py [--site SID] [--stuck-retries N]
        [--stuck-age-hours H] [--json] [--md OUT]
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bulk_downloader.db as DB  # type: ignore  # noqa: E402

_FAILURE_RULES = [
    (re.compile(r"time?d?\s*out|timeout", re.I), "timeout"),
    (re.compile(r"\b429\b|rate.?limit|too many requests", re.I), "rate_limit"),
    (re.compile(r"\b401\b|\b403\b|forbidden|unauthor|login|credential|sign[\s-]?in",
                re.I), "auth_or_forbidden"),
    (re.compile(r"\b404\b|\b410\b|not found|gone|missing", re.I), "not_found"),
    (re.compile(r"captcha|challenge|cloudflare|turnstile|hcaptcha|recaptcha", re.I),
     "challenge"),
    (re.compile(r"connection|connect|dns|ssl|tls|network|reset|refused|unreachable",
                re.I), "network"),
    (re.compile(r"parse|selector|extract|no\s+match|element|json|decode", re.I),
     "parse_or_extract"),
    (re.compile(r"disk|space|no space|i/?o error|permission|read-only file", re.I),
     "disk_io"),
    (re.compile(r"cancel", re.I), "cancelled"),
]


def categorize(message):
    msg = (message or "").strip()
    if not msg:
        return "none"
    for rx, cat in _FAILURE_RULES:
        if rx.search(msg):
            return cat
    return "unknown"


def _age_hours(ts_updated, now=None):
    if not ts_updated:
        return None
    try:
        t = datetime.strptime(str(ts_updated)[:19], "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return None
    now = now or datetime.utcnow()
    return round((now - t).total_seconds() / 3600.0, 2)


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def _collect(status, site_id, cap):
    """Page queue_search to gather all rows of a status (read-only), capped."""
    rows, cursor = [], None
    while len(rows) < cap:
        try:
            batch, cursor = DB.queue_search(site_id=site_id, status=status,
                                            after_ord=cursor, limit=200)
        except Exception:  # noqa: BLE001
            break
        rows.extend(batch)
        if not cursor:
            break
    return rows[:cap]


def analyze(site_id=None, stuck_retries=3, stuck_age_hours=24, cap=5000):
    _safe(DB.db_init, None)  # idempotent CREATE TABLE IF NOT EXISTS; never writes data
    counts = _safe(lambda: DB.queue_count_by_status(site_id), {}) or {}
    total = sum(counts.values())
    failures = counts.get("error", 0) + counts.get("failed", 0)
    in_flight = counts.get("pending", 0) + counts.get("running", 0)
    done = counts.get("done", 0)

    err_rows = _collect("error", site_id, cap) + _collect("failed", site_id, cap)
    fail_categories = Counter(categorize(r.get("message")) for r in err_rows)
    retry_reasons = Counter(
        (r.get("message") or "").strip()[:120] for r in err_rows
        if (r.get("message") or "").strip())
    retries_dist = Counter(int(r.get("retries") or 0) for r in err_rows)
    over_retry = [r for r in err_rows if int(r.get("retries") or 0) >= stuck_retries]

    inflight_rows = _collect("pending", site_id, cap) + _collect("running", site_id, cap)
    stuck = []
    for r in inflight_rows:
        rt = int(r.get("retries") or 0)
        age = _age_hours(r.get("ts_updated"))
        if rt >= stuck_retries or (age is not None and age >= stuck_age_hours):
            stuck.append({"url": r.get("url"), "status": r.get("status"),
                          "retries": rt, "age_hours": age,
                          "message": (r.get("message") or "")[:120]})

    recovery = _safe(DB.db_queue_recovery_summary, {}) or {}
    return {
        "site_id": site_id,
        "counts_by_status": counts,
        "totals": {"total": total, "done": done, "in_flight": in_flight,
                   "failures": failures},
        "failure_rate": round(failures / total, 3) if total else None,
        "failure_categories": dict(fail_categories.most_common()),
        "retry": {
            "distribution": dict(sorted(retries_dist.items())),
            "max": max((int(r.get("retries") or 0) for r in err_rows), default=0),
            "over_threshold": len(over_retry),
            "threshold": stuck_retries,
            "top_reasons": dict(retry_reasons.most_common(10)),
        },
        "stuck": {"threshold_retries": stuck_retries,
                  "threshold_age_hours": stuck_age_hours,
                  "count": len(stuck), "items": stuck[:100]},
        "per_site": recovery.get("per_site", {}),
    }


def _md(a):
    t = a["totals"]
    L = ["# Queue intelligence", "",
         f"- scope: {a['site_id'] or 'all sites'}",
         f"- total: **{t['total']}** (done {t['done']}, in-flight {t['in_flight']}, "
         f"failures {t['failures']})",
         f"- failure rate: {a['failure_rate']}", "",
         "## Status counts", ""]
    for s, n in (a["counts_by_status"] or {}).items():
        L.append(f"- {s}: {n}")
    L += ["", "## Failure categories", ""]
    if a["failure_categories"]:
        for c, n in a["failure_categories"].items():
            L.append(f"- {c}: {n}")
    else:
        L.append("- none")
    L += ["", "## Retry", "",
          f"- max retries: {a['retry']['max']}; over threshold "
          f"(>={a['retry']['threshold']}): {a['retry']['over_threshold']}",
          f"- distribution: {a['retry']['distribution']}", "",
          "### Top failure messages", ""]
    if a["retry"]["top_reasons"]:
        for msg, n in a["retry"]["top_reasons"].items():
            L.append(f"- ({n}) {msg}")
    else:
        L.append("- none")
    L += ["", f"## Stuck URLs ({a['stuck']['count']})",
          f"(in-flight, retries>={a['stuck']['threshold_retries']} "
          f"or idle>={a['stuck']['threshold_age_hours']}h)", ""]
    for s in a["stuck"]["items"]:
        L.append(f"- {s['url']} [{s['status']}] retries={s['retries']} "
                 f"age_h={s['age_hours']} {s['message']}")
    if not a["stuck"]["items"]:
        L.append("- none")
    if a["per_site"]:
        L += ["", "## Per-site", ""]
        for site, by in a["per_site"].items():
            L.append(f"- {site}: {by}")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Queue intelligence (read-only).")
    ap.add_argument("--site")
    ap.add_argument("--stuck-retries", type=int, default=3)
    ap.add_argument("--stuck-age-hours", type=float, default=24)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", metavar="OUT")
    args = ap.parse_args(argv)
    a = analyze(args.site, args.stuck_retries, args.stuck_age_hours)
    if args.md:
        with open(args.md, "w") as fh:
            fh.write(_md(a))
        print(f"wrote {args.md}")
    if args.json:
        print(json.dumps(a, indent=2, default=str))
    elif not args.md:
        sys.stdout.write(_md(a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
