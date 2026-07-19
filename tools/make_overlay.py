#!/usr/bin/env python3
"""make_overlay — write a deploy overlay zip from the release diff.

The overlay (changed+added files for a quick `unzip -o` deploy) was hand-listed,
which silently under-deploys if a file is missed. This derives it from
diff_release_zips' own added+changed set against the baseline, so it can't miss
one. Stdlib only (reuses tools/diff_release_zips.py).

  python3 tools/make_overlay.py --baseline <prev.zip> --new <release.zip> \
      --out <overlay.zip>

Refuses to include forbidden artifacts (pyc/db/secrets) — if the new zip is
clean (it is, build_release gates that) this never triggers.
"""
import argparse
import importlib.util
import os
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _diff_mod():
    spec = importlib.util.spec_from_file_location(
        "diff_release_zips", REPO / "tools" / "diff_release_zips.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def build_overlay(baseline, new, out):
    dz = _diff_mod()
    d = dz.diff(baseline, new)            # diff() takes zip PATHS
    payload = sorted(set(d.get("added", [])) | set(d.get("changed", [])))
    forbidden = set(d.get("forbidden_new", []))
    payload = [p for p in payload if p not in forbidden]
    with zipfile.ZipFile(new) as zn, zipfile.ZipFile(out, "w",
                                                     zipfile.ZIP_DEFLATED) as zo:
        for p in payload:
            zo.writestr(p, zn.read(p))
    return payload, sorted(forbidden)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    payload, forbidden = build_overlay(a.baseline, a.new, a.out)
    print(f"overlay {os.path.basename(a.out)}: {len(payload)} files "
          f"(from diff vs {os.path.basename(a.baseline)})")
    for p in payload:
        print(f"  {p}")
    if forbidden:
        print(f"  (excluded {len(forbidden)} forbidden artifacts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
