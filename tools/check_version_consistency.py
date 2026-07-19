#!/usr/bin/env python3
"""
check_version_consistency.py — release-time version-alignment gate.

Composes the canonical checks already in bulk_downloader/dev_suite.py (single
source of truth — this CLI does not re-implement them):

  * dev_suite.version_consistency()  — scans *.py/*.sh/*.bat/*.spec for stale
    'Bulk Downloader vX.Y.Z' banners and `VERSION = "X.Y.Z"` assignments that
    disagree with bulk_downloader/__init__.py::__version__.
  * dev_suite.changelog_lint()       — confirms CHANGELOG.md's TOPMOST entry is
    '## v<current>'.

Note on the runtime: /api/health emits `version` straight from
bulk_downloader.__init__.__version__, so health is consistent by construction —
no separate check needed. The frontend (frontend/package.json) is INDEPENDENTLY
versioned (its own semver, currently 0.1.0) and is intentionally NOT aligned to
the release version; it is reported as informational only.

stdlib-only; run with plain `python3` from the repo root.

Exit 0 = all aligned. Exit 1 = a stale banner or a changelog mismatch. Exit 2 =
could not import dev_suite (run from the repo root).
"""
import argparse
import json
import os
import sys


def _load_dev_suite(root):
    sys.path.insert(0, root)
    try:
        from bulk_downloader import dev_suite  # type: ignore
        return dev_suite
    except Exception as e:  # noqa: BLE001
        print(f"error: cannot import bulk_downloader.dev_suite from {root!r}: {e}",
              file=sys.stderr)
        return None


def _frontend_version(root):
    p = os.path.join(root, "frontend", "package.json")
    try:
        with open(p) as fh:
            return json.load(fh).get("version")
    except (OSError, ValueError):
        return None


def run(root="."):
    ds = _load_dev_suite(root)
    if ds is None:
        return 2, None
    vc = ds.version_consistency()
    cl = ds.changelog_lint()
    fe = _frontend_version(root)
    ok = (not vc["mismatches"]) and cl["ok"]
    return (0 if ok else 1), {"version_consistency": vc, "changelog_lint": cl,
                              "frontend_version": fe}


def _print(report):
    vc = report["version_consistency"]
    cl = report["changelog_lint"]
    print("=" * 70)
    print(f"  Version consistency — release version {vc['version']}")
    print("=" * 70)
    print(f"\n-- source banners ({vc['scanned_count']} files scanned) --")
    if vc["mismatches"]:
        for m in vc["mismatches"]:
            print(f"  ! {m['file']}:{m['line']}  found {m['found']}  | {m['context']}")
    else:
        print("  OK — no stale version banners")
    print("\n-- CHANGELOG.md --")
    print(f"  {'OK' if cl['ok'] else '!'} {cl['verdict']}")
    print(f"\n-- frontend (independent versioning, informational) --")
    print(f"  frontend/package.json version = {report['frontend_version']!r} "
          "(NOT aligned to the release version by design)")
    print("\n" + "=" * 70)
    print("  VERDICT: " + ("ALIGNED" if (not vc['mismatches'] and cl['ok'])
                           else "MISALIGNED — see flagged lines above"))
    print("=" * 70)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Version-alignment gate (composes dev_suite).")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    code, report = run(args.root)
    if report is None:
        return code
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
