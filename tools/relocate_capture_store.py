#!/usr/bin/env python3
"""relocate_capture_store.py -- Phase 1 Cut 1.3 migrator.

Move the capture-OUTPUT dirs (captures/, offline_out/, offline_captures/) from
one root to another, so the raw capture artifacts can live off the repo/root. The
template review dirs (templates/drafts, templates/review_candidates) are NEVER
moved -- they belong to the template lifecycle and stay under PROJECT_ROOT.

After moving, set the `capture_store_root` app-config key to the new root so
dom_analyzer's capture-output resolution (picker / scan / drift-repair provider)
and the selftest disk check point at the relocated store. This tool does NOT edit
the config itself -- it prints the exact key/value to set -- so the operator owns
that flip.

Usage:
    python3 tools/relocate_capture_store.py --from <OLD_ROOT> --to <NEW_ROOT> [--dry-run]

Or programmatically:
    from relocate_capture_store import relocate
    relocate(old_root, new_root, dry_run=True)
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# The capture-output dirs (must match dom_analyzer._CAPTURE_OUTPUT_DIRS). Template
# dirs are intentionally excluded -- they are not part of the capture store.
CAPTURE_OUTPUT_DIRS = ("captures", "offline_out", "offline_captures")


def relocate(old_root: str, new_root: str, *, dry_run: bool = True) -> dict:
    """Move each capture-output dir from old_root to new_root. Returns a plan/result
    dict: {moved: [...], skipped: [...], dry_run, from, to}. Idempotent-ish: a dir
    absent under old_root is skipped; merging into an existing target dir moves the
    individual entries so a partially-migrated store completes cleanly."""
    old = Path(old_root)
    new = Path(new_root)
    result = {"from": str(old), "to": str(new), "dry_run": bool(dry_run),
              "moved": [], "skipped": [], "errors": []}
    if old.resolve() == new.resolve():
        result["errors"].append("old and new roots are identical")
        return result
    for d in CAPTURE_OUTPUT_DIRS:
        src = old / d
        dst = new / d
        if not src.is_dir():
            result["skipped"].append(f"{d} (absent under old root)")
            continue
        if dry_run:
            n = sum(1 for _ in src.rglob("*") if _.is_file())
            result["moved"].append(f"{d} ({n} file(s)) -> {dst}")
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                # merge: move each top-level entry into the existing target
                for entry in list(src.iterdir()):
                    target = dst / entry.name
                    if target.exists():
                        result["errors"].append(f"{d}/{entry.name} already exists at target; left in place")
                        continue
                    shutil.move(str(entry), str(target))
                # remove the now-empty source dir
                try:
                    src.rmdir()
                except OSError:
                    pass
            else:
                shutil.move(str(src), str(dst))
            result["moved"].append(f"{d} -> {dst}")
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"{d}: {str(e)[:200]}")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Relocate the BD capture-output store.")
    ap.add_argument("--from", dest="old_root", required=True, help="current root (PROJECT_ROOT)")
    ap.add_argument("--to", dest="new_root", required=True, help="new capture store root (absolute)")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, move nothing")
    args = ap.parse_args(argv)

    new = Path(args.new_root)
    if not new.is_absolute():
        print(f"ERROR: --to must be an absolute path (got {args.new_root!r})", file=sys.stderr)
        return 2
    new.mkdir(parents=True, exist_ok=True)

    res = relocate(args.old_root, args.new_root, dry_run=args.dry_run)
    print(f"{'DRY-RUN ' if res['dry_run'] else ''}relocate capture store: {res['from']} -> {res['to']}")
    for m in res["moved"]:
        print(f"  move   {m}")
    for s in res["skipped"]:
        print(f"  skip   {s}")
    for e in res["errors"]:
        print(f"  ERROR  {e}", file=sys.stderr)
    if not res["dry_run"] and not res["errors"]:
        print()
        print("Next: set the app-config key so BD resolves captures at the new root:")
        print(f'  capture_store_root = "{res["to"]}"')
        print("(Settings -> Global, or edit app_config.json; then restart the service.)")
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
