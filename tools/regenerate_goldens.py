#!/usr/bin/env python3
"""regenerate_goldens.py — T51 / D-75 follow-on.

The T42 dev tool (/api/dev/golden_files) is read-only: it reports
drift between each tests/fixtures/golden/<name>.golden baseline
and its sibling <name>.current file, but never writes. Regeneration
was deliberately kept off the network — it mutates test inputs.

This CLI is the operator's regenerate path. By default it is a
dry-run that lists what would change; --apply actually overwrites
the .golden files from their .current siblings.

Layout convention (same as T42):

    tests/fixtures/golden/
        <name>.golden        — the committed baseline (overwritten by --apply)
        <name>.current       — the value the test currently produces
        <name>.golden.meta   — optional JSON metadata (touched on --apply
                                to record last_regenerated_ts + reason)

Usage:

    # Dry-run, the default. Lists drift, exits 0. No writes.
    python tools/regenerate_goldens.py

    # Actually regenerate. --reason is mandatory; it goes into the
    # append-only log so a regeneration is auditable.
    python tools/regenerate_goldens.py --apply \\
        --reason "selector schema v2 rolled out 2026-05-21"

    # Limit to a single golden by stem (no .golden suffix).
    python tools/regenerate_goldens.py --apply \\
        --reason "..." --only login_form_html

Safety:

  • --apply WITHOUT --reason refuses. The reason is recorded in
    tools/regenerate_goldens.log (append-only) with timestamp and
    git rev when available.
  • If the working tree has uncommitted changes to any FILE inside
    the goldens dir already, the script refuses (operator must
    commit or stash first). This stops regeneration from burying
    a real diff under a "drift" sweep. Pass --force to override
    when you genuinely know the goldens dir has unrelated edits.
  • Atomic writes — `.tmp` sibling then os.replace().
  • Refuses to act on a .current file that's empty (0 bytes), as
    a guard against regenerating from a half-written test output.

Exit codes:
  0  — success (dry-run with or without drift; apply that wrote)
  1  — usage error (missing --reason on --apply)
  2  — refused by safety check (dirty tree, empty .current, etc.)
  3  — I/O error during regeneration

This script is importable: tests exercise the dry-run and the
guard paths in-process; main() is only called when run as a
script.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib as _hashlib
import json as _json
import os
import subprocess
import sys
from pathlib import Path


_DEFAULT_GOLDENS_REL = ("tests", "fixtures", "golden")
_LOG_REL = ("tools", "regenerate_goldens.log")


# ── Discovery ───────────────────────────────────────────────────────

def find_repo_root(start=None):
    """Walk up from `start` (or this file's parent) looking for a
    directory that contains both CHANGELOG.md and a bulk_downloader/
    package. Falls back to the parent of this file."""
    here = Path(start).resolve() if start else Path(__file__).resolve().parent
    for d in [here] + list(here.parents):
        if (d / "CHANGELOG.md").is_file() and (d / "bulk_downloader").is_dir():
            return d
    # Fallback: parent of this file's directory (tools/ → repo root).
    return Path(__file__).resolve().parents[1]


def goldens_dir(repo_root=None):
    """The conventional goldens directory under the given repo root."""
    root = Path(repo_root) if repo_root else find_repo_root()
    return root.joinpath(*_DEFAULT_GOLDENS_REL)


# ── Git working-tree check ──────────────────────────────────────────

def git_dirty_paths(repo_root, subdir):
    """Return the list of paths under `subdir` (relative to
    repo_root) that have uncommitted changes. Empty list if clean
    OR if git is unavailable / repo_root is not a git checkout.

    Best-effort: if `git status` fails for any reason we return
    an empty list and the caller decides whether to refuse on its
    own grounds. We do NOT raise — a non-git deployment must still
    be able to regenerate.
    """
    try:
        cp = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "--",
             str(subdir)],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if cp.returncode != 0:
        return []
    dirty = []
    for line in cp.stdout.splitlines():
        # Porcelain format: "XY path" where X and Y are status codes.
        if len(line) < 4:
            continue
        path = line[3:].strip()
        # Strip the optional " -> " arrow form (renames).
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        dirty.append(path)
    return dirty


def git_rev(repo_root):
    """Short HEAD rev, or 'unknown' if git is unavailable / no repo."""
    try:
        cp = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "unknown"
    if cp.returncode != 0:
        return "unknown"
    return cp.stdout.strip() or "unknown"


# ── Drift scan ──────────────────────────────────────────────────────

def scan_goldens(gdir, only=None):
    """Yield drift records {stem, golden_path, current_path,
    golden_bytes, current_bytes, drift, error} for every .golden
    that has a sibling .current. Records with `drift=False` are
    still included (so a dry-run can print "no drift"). `only`,
    if provided, restricts to stems matching that exact name.
    """
    gdir = Path(gdir)
    out = []
    if not gdir.is_dir():
        return out
    for p in sorted(gdir.iterdir()):
        if not p.is_file() or not p.name.endswith(".golden"):
            continue
        stem = p.name[: -len(".golden")]
        if only and stem != only:
            continue
        current = gdir / f"{stem}.current"
        rec = {
            "stem": stem,
            "golden_path": str(p),
            "current_path": str(current),
            "has_current": current.is_file(),
            "drift": False,
            "error": None,
        }
        if not current.is_file():
            rec["error"] = "no .current sibling"
            out.append(rec)
            continue
        try:
            gb = p.read_bytes()
            cb = current.read_bytes()
        except OSError as e:
            rec["error"] = f"read failed: {e}"
            out.append(rec)
            continue
        rec["golden_bytes"] = len(gb)
        rec["current_bytes"] = len(cb)
        rec["golden_sha256"] = _hashlib.sha256(gb).hexdigest()
        rec["current_sha256"] = _hashlib.sha256(cb).hexdigest()
        if rec["golden_sha256"] != rec["current_sha256"]:
            rec["drift"] = True
        out.append(rec)
    return out


# ── Regenerate ──────────────────────────────────────────────────────

def _atomic_write_bytes(target, data):
    """Atomic-write `data` to `target` via a `.tmp` sibling +
    os.replace. Raises on I/O failure."""
    target = Path(target)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, target)


def _touch_meta(gdir, stem, reason, rev):
    """Append/update <stem>.golden.meta with the regen audit fields.
    Best-effort — a meta write failure does NOT roll back the golden
    write, but is reported to the caller."""
    meta_path = Path(gdir) / f"{stem}.golden.meta"
    try:
        existing = (_json.loads(meta_path.read_text(encoding="utf-8"))
                    if meta_path.is_file() else {})
    except Exception:
        existing = {}
    existing["last_regenerated_ts"] = _dt.datetime.now(
        _dt.timezone.utc).isoformat(timespec="seconds")
    existing["last_regenerated_reason"] = reason
    existing["last_regenerated_rev"] = rev
    try:
        _atomic_write_bytes(
            meta_path,
            _json.dumps(existing, indent=2, sort_keys=True).encode("utf-8"))
        return None
    except OSError as e:
        return f"meta write failed: {e}"


def regenerate(gdir, records, reason, rev):
    """Apply: copy each drifting .current onto its .golden sibling.
    Returns {applied, skipped, errors} lists of stems. Non-drifting
    records are skipped silently."""
    applied = []
    skipped = []
    errors = []
    for r in records:
        stem = r["stem"]
        if r.get("error"):
            skipped.append((stem, r["error"]))
            continue
        if not r["drift"]:
            skipped.append((stem, "no drift"))
            continue
        if r.get("current_bytes", 0) == 0:
            errors.append((stem, "empty .current — refusing"))
            continue
        try:
            data = Path(r["current_path"]).read_bytes()
            _atomic_write_bytes(r["golden_path"], data)
        except OSError as e:
            errors.append((stem, f"write failed: {e}"))
            continue
        meta_err = _touch_meta(gdir, stem, reason, rev)
        if meta_err:
            errors.append((stem, meta_err))
        applied.append(stem)
    return {"applied": applied, "skipped": skipped, "errors": errors}


# ── Audit log ───────────────────────────────────────────────────────

def append_log(repo_root, reason, rev, result):
    """Append-only audit log. One line per regeneration run."""
    log = Path(repo_root).joinpath(*_LOG_REL)
    log.parent.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now(
        _dt.timezone.utc).isoformat(timespec="seconds")
    applied = ",".join(result["applied"]) or "-"
    errors = (";".join(f"{s}={e}" for s, e in result["errors"])
              or "-")
    line = (f"{ts}\trev={rev}\tapplied={applied}\t"
            f"errors={errors}\treason={reason}\n")
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(line)
    return str(log)


# ── Entry point ─────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Regenerate golden test baselines from their "
                    "sibling .current files. Dry-run by default.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually overwrite .golden files. Requires --reason.")
    parser.add_argument(
        "--reason", type=str, default="",
        help="Free-text reason recorded in the audit log "
             "(mandatory with --apply).")
    parser.add_argument(
        "--only", type=str, default=None,
        help="Only act on the given golden stem (no .golden suffix).")
    parser.add_argument(
        "--force", action="store_true",
        help="Skip the dirty-tree safety check.")
    parser.add_argument(
        "--repo-root", type=str, default=None,
        help="Override the auto-detected repo root.")
    args = parser.parse_args(argv)

    if args.apply and not args.reason.strip():
        print("ERROR: --apply requires --reason \"...\"",
              file=sys.stderr)
        return 1

    repo_root = (Path(args.repo_root).resolve()
                 if args.repo_root else find_repo_root())
    gdir = goldens_dir(repo_root)

    if not gdir.is_dir():
        print(f"no goldens dir at {gdir}; nothing to do")
        return 0

    # Dirty-tree guard: only matters under --apply. Under dry-run
    # we never write, so dirty paths are irrelevant.
    if args.apply and not args.force:
        try:
            subdir_rel = gdir.relative_to(repo_root)
        except ValueError:
            subdir_rel = gdir
        dirty = git_dirty_paths(repo_root, subdir_rel)
        if dirty:
            print("REFUSED: uncommitted changes inside the goldens "
                  "directory:", file=sys.stderr)
            for p in dirty:
                print(f"  {p}", file=sys.stderr)
            print("Commit or stash these first, OR pass --force "
                  "if you really mean it.", file=sys.stderr)
            return 2

    records = scan_goldens(gdir, only=args.only)
    if not records:
        if args.only:
            print(f"no golden found matching --only={args.only}")
        else:
            print(f"no .golden files in {gdir}")
        return 0

    drift = [r for r in records if r["drift"]]
    if not args.apply:
        # Dry-run output.
        print(f"goldens_dir: {gdir}")
        print(f"total: {len(records)} | drift: {len(drift)}")
        for r in records:
            if r["drift"]:
                print(f"  DRIFT  {r['stem']}  "
                      f"({r['golden_bytes']}B -> "
                      f"{r['current_bytes']}B)")
            elif r.get("error"):
                print(f"  SKIP   {r['stem']}  ({r['error']})")
            else:
                print(f"  ok     {r['stem']}")
        if drift:
            print()
            print("Re-run with --apply --reason \"...\" to "
                  "overwrite the .golden files above.")
        return 0

    # --apply path.
    rev = git_rev(repo_root)
    result = regenerate(gdir, records, args.reason, rev)
    log_path = append_log(repo_root, args.reason, rev, result)
    print(f"goldens_dir: {gdir}")
    print(f"rev: {rev}")
    print(f"applied: {len(result['applied'])}")
    for s in result["applied"]:
        print(f"  + {s}")
    if result["errors"]:
        print(f"errors: {len(result['errors'])}", file=sys.stderr)
        for s, e in result["errors"]:
            print(f"  ! {s}: {e}", file=sys.stderr)
    print(f"audit log: {log_path}")
    return 3 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
