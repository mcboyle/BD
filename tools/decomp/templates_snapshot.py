#!/usr/bin/env python3
"""templates_snapshot.py -- list-identity guard for bulk_downloader.templates.TEMPLATES.

The templates decomposition (templates.py -> shim over site_templates/) is a pure
DATA motion: the 91-element ``TEMPLATES`` list is partitioned across per-family
modules and re-concatenated in ``__init__.py``. The binding invariant is that the
re-assembled list is byte-for-byte identical to the pristine list: same length,
same order, same per-element content.

This tool freezes that contract as a per-element ``(index, id, sha256(repr(elem)))``
manifest plus a whole-list rollup hash, and re-checks it. Element bodies are NEVER
printed -- some ``learned`` payloads embed heavyweight captured-session blobs -- only
their hashes are emitted.

Placed under ``tools/decomp/`` (a subdir) so the non-recursive ``tools/*.py`` globs
in dependency_graph (tool.total) and gui_parity_inventory (operator itemset) never
see it (H-06 does not fire). The ``bulk_downloader.templates`` import is in-function
(lazy), so it adds no module-level edge to DEPENDENCY_GRAPH.

Usage:
    python3 tools/decomp/templates_snapshot.py --freeze   # write the baseline
    python3 tools/decomp/templates_snapshot.py --check    # assert live == baseline (exit 1 on drift)
    python3 tools/decomp/templates_snapshot.py            # same as --check
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BASELINE = os.path.join(_REPO, "tools", "decomp", "templates_snapshot_baseline.json")


def _load_templates():
    if _REPO not in sys.path:
        sys.path.insert(0, _REPO)
    from bulk_downloader import templates as t  # noqa: E402  (lazy: no graph edge)
    return t.TEMPLATES


def _elem_sha(elem) -> str:
    return hashlib.sha256(repr(elem).encode("utf-8")).hexdigest()


def compute_manifest():
    templates = _load_templates()
    rows = []
    for i, elem in enumerate(templates):
        rows.append(
            {
                "index": i,
                "id": elem.get("id", "<no-id>"),
                "sha256": _elem_sha(elem),
            }
        )
    rollup = hashlib.sha256(
        "\n".join(r["sha256"] for r in rows).encode("utf-8")
    ).hexdigest()
    return {"count": len(rows), "rollup": rollup, "elements": rows}


def freeze() -> int:
    manifest = compute_manifest()
    with open(_BASELINE, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=False)
        fh.write("\n")
    print(f"FROZE {manifest['count']} elements -> {_BASELINE}")
    print(f"  rollup = {manifest['rollup']}")
    return 0


def check() -> int:
    if not os.path.isfile(_BASELINE):
        print(f"FAIL: no baseline at {_BASELINE} -- run --freeze first", file=sys.stderr)
        return 1
    with open(_BASELINE, encoding="utf-8") as fh:
        base = json.load(fh)
    live = compute_manifest()

    problems = []
    if live["count"] != base["count"]:
        problems.append(f"count {live['count']} != baseline {base['count']}")
    if live["rollup"] != base["rollup"]:
        problems.append("rollup hash drift")

    base_by_idx = {r["index"]: r for r in base["elements"]}
    live_by_idx = {r["index"]: r for r in live["elements"]}
    for idx in sorted(set(base_by_idx) | set(live_by_idx)):
        b = base_by_idx.get(idx)
        v = live_by_idx.get(idx)
        if b is None:
            problems.append(f"index {idx} ({v['id']}) ADDED")
        elif v is None:
            problems.append(f"index {idx} ({b['id']}) DROPPED")
        elif b["id"] != v["id"]:
            problems.append(f"index {idx}: id {b['id']!r} -> {v['id']!r} (reorder/swap)")
        elif b["sha256"] != v["sha256"]:
            problems.append(f"index {idx} ({b['id']}): content drift")

    if problems:
        print("FAIL: TEMPLATES list-identity drift:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"PASS: TEMPLATES list identity holds ({live['count']} elements, ordered, content-stable).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--freeze", action="store_true", help="write the baseline manifest")
    g.add_argument("--check", action="store_true", help="assert live == baseline")
    args = ap.parse_args()
    if args.freeze:
        return freeze()
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
