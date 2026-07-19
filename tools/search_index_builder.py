#!/usr/bin/env python3
"""search_index_builder.py — search/discovery GROUNDWORK only (K). Read-only
except the index files. Builds a unified metadata index over templates, reports,
and documented endpoints. NO runtime integration — emits JSON under --outdir for
future consumers. CLI: --root, --outdir reports
"""
import os as _os_rc, sys as _sys_rc
_sys_rc.path.insert(0, _os_rc.path.dirname(_os_rc.path.abspath(__file__)))
import report_core as _RC  # shared write/render helpers

import argparse, glob, json, os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import template_inventory as TI  # type: ignore

_EP = re.compile(r"^(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)", re.M)


def build(root="."):
    entries = []
    # templates
    scan = TI.scan(root)
    for name, items in scan["dirs"].items():
        for a in items:
            entries.append({"type": "template", "id": a["source"], "title": a["host"],
                            "keywords": [name, a["status"]] + a["selector_groups"]})
    # reports
    for p in glob.glob(os.path.join(root, "reports", "*")):
        entries.append({"type": "report", "id": os.path.relpath(p, root),
                        "title": os.path.basename(p), "keywords": ["report"]})
    # endpoints (from ENDPOINT_CATALOG.md if present)
    cat = os.path.join(root, "ENDPOINT_CATALOG.md")
    if os.path.isfile(cat):
        text = open(cat, encoding="utf-8", errors="replace").read()
        for method, path in _EP.findall(text):
            entries.append({"type": "endpoint", "id": f"{method} {path}",
                            "title": path, "keywords": [method.lower(),
                                                        path.strip("/").split("/")[0] or "root"]})
    return {"version": 1, "entry_count": len(entries), "entries": entries,
            "by_type": {t: sum(1 for e in entries if e["type"] == t)
                        for t in ("template", "report", "endpoint")}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="."); ap.add_argument("--outdir", default="reports")
    a = ap.parse_args(argv)
    idx = build(a.root)
    p = _RC.write_json(os.path.join(a.outdir, "search_index.json"), idx)
    print(f"wrote {p}: {idx['entry_count']} entries {idx['by_type']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
