"""Row 1016 -- rerunnable "lock-free" measurement (bd-worker-F1-B, W27).

Measures what a COMPETING WRITER sees while queue_bulk_upsert() runs: how many times its
BEGIN IMMEDIATE hits SQLITE_BUSY (busy_timeout=0) and the longest contiguous busy span in ms,
i.e. the writer lock-hold as observed from outside. Same fixture on any tree:

    PYTHONPATH=<tree> venv/bin/python tests/perf_row1016_lock_hold_probe.py --rows 5000 --repeat 5

Prints one JSON line. Run it in the BASE tree and in the CUT tree; the pair is the evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import tempfile
import threading
import time


def _probe(path: str, stop: threading.Event, out: dict) -> None:
    cx = sqlite3.connect(path, timeout=0.0, isolation_level=None)
    cx.execute("PRAGMA busy_timeout=0")
    busy = 0
    ok = 0
    span_start = None
    max_span = 0.0
    while not stop.is_set():
        try:
            cx.execute("BEGIN IMMEDIATE")
            cx.execute("ROLLBACK")
            ok += 1
            if span_start is not None:
                max_span = max(max_span, (time.perf_counter() - span_start) * 1000.0)
                span_start = None
        except sqlite3.OperationalError:
            busy += 1
            if span_start is None:
                span_start = time.perf_counter()
        time.sleep(0.0005)
    if span_start is not None:
        max_span = max(max_span, (time.perf_counter() - span_start) * 1000.0)
    cx.close()
    out.update({"busy": busy, "ok": ok, "max_busy_span_ms": round(max_span, 2)})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=5000)
    ap.add_argument("--repeat", type=int, default=5)
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="row1016-probe-")
    path = os.path.join(tmp, "downloader_history.db")
    from bulk_downloader import db

    db.DB_PATH = path
    db._resolve_db_path = lambda: path
    db.db_init()

    site = "row1016-probe"
    runs = []
    for k in range(args.repeat):
        urls = [f"https://example.invalid/{k}/{i}" for i in range(args.rows)]
        stop = threading.Event()
        seen: dict = {}
        t = threading.Thread(target=_probe, args=(path, stop, seen), daemon=True)
        t.start()
        time.sleep(0.05)
        t0 = time.perf_counter()
        db.queue_bulk_upsert(site, urls, ord_start=0, listing_titles={u: "t" for u in urls})
        wall = (time.perf_counter() - t0) * 1000.0
        time.sleep(0.05)
        stop.set()
        t.join()
        with db.db_conn() as cx:
            count = cx.execute("SELECT count(*) FROM queue WHERE site_id=?", (site,)).fetchone()[0]
        runs.append({"call_ms": round(wall, 1), "rows_in_queue": count, **seen})

    result = {
        "tree": os.path.dirname(os.path.dirname(os.path.abspath(db.__file__))),
        "rows": args.rows,
        "repeat": args.repeat,
        "call_ms_median": round(statistics.median(r["call_ms"] for r in runs), 1),
        "busy_median": statistics.median(r["busy"] for r in runs),
        "max_busy_span_ms_median": round(statistics.median(r["max_busy_span_ms"] for r in runs), 2),
        "max_busy_span_ms_max": max(r["max_busy_span_ms"] for r in runs),
        "runs": runs,
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
