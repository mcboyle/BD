#!/usr/bin/env python3
"""reap_orphan_tempdirs -- reap stale BD-owned temp dirs/files under the system
temp dir.

BD creates temp dirs with known prefixes (bdback_, bdrestore_, bd_plugin_upload_,
bd-diag-, bd-restore-test-, .selftest_, .cross_site_). Most are cleaned on the
happy path, but an error path can leave one behind -- the /tmp-flood class the
operator hit. This reaps ONLY those known BD prefixes, and ONLY when older than
--max-age-hours, so it can never touch another process's temp files.

Dry-run by default; pass --apply to actually delete.

  reap_orphan_tempdirs                 # list what WOULD be reaped (>24h)
  reap_orphan_tempdirs --apply         # delete them
  reap_orphan_tempdirs --max-age-hours 6 --apply
"""
import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.safe_temp_remove import rename_verify_destroy

# Known BD runtime temp prefixes (grep of bulk_downloader/*.py mkdtemp/
# NamedTemporaryFile prefix=). Conservative allow-list -- nothing else is touched.
_TEMP_PREFIXES = (
    "bdback_", "bdback_preview_", "bdrestore_", "bd-restore-test-",
    "bd_plugin_upload_", "bd-diag-", ".selftest_", ".cross_site_",
)


def find_orphans(root=None, max_age_h=24.0, prefixes=_TEMP_PREFIXES):
    """Return paths of BD-prefixed entries under `root` older than max_age_h."""
    root = root or tempfile.gettempdir()
    cutoff = time.time() - max_age_h * 3600.0
    out = []
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in names:
        if not name.startswith(tuple(prefixes)):
            continue
        p = os.path.join(root, name)
        try:
            if os.path.getmtime(p) < cutoff:
                out.append(p)
        except OSError:
            continue
    return out


def reap(paths, apply=False):
    """Delete (when apply) each path; return (done, failed), never swallow."""
    done, failed = [], []
    for p in paths:
        if not apply:
            done.append(p)
            continue
        removed, reason = rename_verify_destroy(p)
        if removed:
            done.append(p)
        else:
            failed.append((p, reason or "unverified removal failure"))
    return done, failed


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Reap stale BD-owned temp dirs/files.")
    ap.add_argument("--root", default=None,
                    help="temp root (default: system temp dir)")
    ap.add_argument("--max-age-hours", type=float, default=24.0,
                    help="only reap entries older than this (default 24)")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default: dry-run)")
    a = ap.parse_args(argv)
    root = a.root or tempfile.gettempdir()
    orphans = find_orphans(root, a.max_age_hours)
    done, failed = reap(orphans, apply=a.apply)
    verb = "reaped" if a.apply else "would reap"
    print(f"{verb} {len(done)} stale BD temp entry(ies) "
          f"(>{a.max_age_hours}h) under {root}")
    for p in done:
        print("  ", os.path.basename(p))
    for p, reason in failed:
        print("  FAILED", os.path.basename(p), reason, file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
