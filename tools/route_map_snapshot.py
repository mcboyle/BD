#!/usr/bin/env python3
"""route_map_snapshot.py -- the F5.1 decomposition invariant.

Emits a canonical, noise-free snapshot of the live url_map: one sorted line per
rule as "<rule>\t<sorted methods>\t<bare endpoint>". The endpoint's blueprint
prefix is STRIPPED (Flask names a blueprint view "<bp>.<func>"), so moving a view
verbatim into a blueprint diffs EMPTY -- while a rename / added / removed route or a
changed method set diffs non-empty. No timestamp, no version: a before/after diff is
purely behavioral.

    python3 tools/route_map_snapshot.py > /tmp/before.txt
    # ... perform exactly one extraction cut ...
    python3 tools/route_map_snapshot.py > /tmp/after.txt
    diff /tmp/before.txt /tmp/after.txt && echo INVARIANT_HELD

Pair with tests/test_route_map_invariant.py, which freezes the F5.1-OPEN baseline
once (the baseline IS the contract) and asserts equality every cut. Because the
registration block is fail-open, a silently-failed register_routes() drops routes
from the url_map -> this snapshot catches it.

Read-only. Imports the app exactly as build_endpoint_catalog.py / gui_parity do.
"""
from __future__ import annotations
import os, sys, importlib

os.environ.setdefault("BD_HOME", os.environ.get("BD_HOME", "/tmp/bd_snap_home"))
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")


def snapshot() -> str:
    app = importlib.import_module("bulk_downloader.app").app
    lines = []
    for r in app.url_map.iter_rules():
        methods = ",".join(sorted(m for m in r.methods if m not in ("HEAD", "OPTIONS")))
        bare = r.endpoint.rsplit(".", 1)[-1]   # strip the blueprint prefix
        lines.append(f"{r.rule}\t{methods}\t{bare}")
    lines.sort()
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.stdout.write(snapshot())
