#!/usr/bin/env python3
"""legacy_pin_scan — surface every test that reads a Phase-4 deletion-set asset.

The Phase-4 retirement cut deletes the legacy shell (templates/index.html +
mobile.html + m_ops.html) and the legacy static JS/CSS. Any test that reads one
of those files by path (`read_text`, `Path("…/static/x.js")`, or asserts a
`<script src="/static/x.js">` literal) will break the moment the asset is
deleted. The retirement plan's §C pin list MUST be derived from this scan at cut
time, never hand-maintained — that is exactly how the 217-era §C went stale
(13 already-migrated entries) AND incomplete (4 live pins it never listed).

Stdlib only. Usage:
  python3 tools/legacy_pin_scan.py                 # human table
  python3 tools/legacy_pin_scan.py --json          # machine-readable
  python3 tools/legacy_pin_scan.py --tests-dir D   # scan a different tree

SURVIVORS (never reported): static/sw.js, static/manifest.json, static/icons/* —
they are retained by the SPA (push/PWA) per the retirement plan §A.2.
"""
import argparse
import glob
import json
import os
import re

SURVIVORS = {"sw.js", "manifest.json"}  # + icons/ (dir) handled below


def deletion_set(root):
    """The assets Phase-4 deletes: legacy templates + all static JS/CSS except
    the survivors. Derived from the tree so it can't drift from reality."""
    paths = []
    for tpl in ("index.html", "mobile.html", "m_ops.html"):
        p = os.path.join("bulk_downloader", "templates", tpl)
        if os.path.isfile(os.path.join(root, p)):
            paths.append(p)
    sdir = os.path.join(root, "bulk_downloader", "static")
    for f in sorted(os.listdir(sdir)) if os.path.isdir(sdir) else []:
        if f in SURVIVORS or f == "icons" or f.endswith(".json"):
            continue
        if f.endswith(".js") or f.endswith(".css"):
            paths.append(os.path.join("bulk_downloader", "static", f))
    return paths


def _classify(line):
    """Best-effort: is this a real file-read/assert, or a comment/fixture?"""
    s = line.strip()
    if s.startswith("#") or s.startswith('"""') or s.startswith("*"):
        return "comment"
    # a fixture URL like cdn.x/static/app.js or http(s)://…/static/app.js
    if re.search(r"https?://[^\s\"']*static/", line):
        return "fixture-url"
    return "read"


def scan(root, tests_dir):
    assets = deletion_set(root)
    # Match (a) a bare contiguous path (read_text / Path("…")), (b) a
    # <script src="/static/x"> literal, or (c) a COMPOSED path built by pathlib
    # '/' chaining or os.path.join — '"<parent>" / "<base>"' /
    # '"<parent>", "<base>"' — where the full contiguous path never appears on
    # one line. (c) is anchored on the immediate parent dir ("templates" /
    # "static") so a bare unrelated mention of the basename can't false-pin, and
    # survivors are excluded by deletion_set so no pattern is built for them.
    pats = {}
    for a in assets:
        base = os.path.basename(a)
        parent = os.path.basename(os.path.dirname(a))
        pats[a] = re.compile(
            re.escape(a)
            + r"|/static/" + re.escape(base)
            + r"|" + re.escape(parent) + r"[\"']?\s*[/,]\s*[\"']?" + re.escape(base)
        )
    hits = {}
    for tf in sorted(glob.glob(os.path.join(tests_dir, "test_*.py"))):
        name = os.path.basename(tf)
        with open(tf, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                for a, rx in pats.items():
                    if rx.search(ln):
                        kind = _classify(ln)
                        hits.setdefault(name, {}).setdefault(a, kind)
    # a test is a REAL pin if any of its hits classifies as a read
    pins, soft = {}, {}
    for name, amap in hits.items():
        if any(k == "read" for k in amap.values()):
            pins[name] = amap
        else:
            soft[name] = amap
    return {"deletion_set": assets, "pins": pins, "non_pins": soft}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--tests-dir", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    td = a.tests_dir or os.path.join(a.root, "tests")
    res = scan(a.root, td)
    if a.json:
        print(json.dumps(res, indent=2))
        return 0
    print(f"Phase-4 deletion set: {len(res['deletion_set'])} assets")
    print(f"\nLIVE PINS (must migrate in the deletion cut): {len(res['pins'])}")
    for name in sorted(res["pins"]):
        for asset, kind in sorted(res["pins"][name].items()):
            if kind == "read":
                print(f"  {name:42s} -> {asset}")
    print(f"\nNON-PINS (comment/fixture only — confirm, no action): {len(res['non_pins'])}")
    for name in sorted(res["non_pins"]):
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
