#!/usr/bin/env python3
"""rollback.py — PT14. Restore a previous BulkDownloader release.

Two modes:

  rollback.py --list                 — show what's in the archive
  rollback.py 3.63.6                 — restore that version
  rollback.py --archive 3.63.7       — copy the *current* release zip
                                       into the archive (one-time
                                       per release; safe to repeat)

Archive layout: a single directory holding release zips, named
`BulkDownloader_v<X_Y_Z>.zip` (matching the format the operator
already uses). Default location is
`~/.bulkdownloader/releases/`; override with `--archive-dir`
or env `BD_RELEASE_ARCHIVE`.

The script never DELETES from the archive — you only ever add zips,
or rollback from one. The archive grows monotonically; prune
manually if it gets unwieldy.

The script DOES overwrite files in the app directory during a
rollback. That's the point. Before doing it, it:
  1. Prints what's being restored (version + zip path).
  2. Stops the bulkdownloader service if running.
  3. Backs up the current `bulk_downloader/__init__.py` so the
     pre-rollback version is recoverable if the rollback target
     is also broken. Backup goes to `<app_dir>/.rollback_backup/`.
  4. Extracts the chosen zip over the app dir.
  5. Restarts the service.

If anything fails mid-way, the operator is told exactly what state
they're in. There is no auto-recovery — rollback is a manual,
attentive operation, not a black box.

Why a Python tool, not a shell script: the version-pattern parsing,
the zip listing, and the systemctl interaction are all friendlier in
Python; this avoids the kind of escaping/quoting issue PT11's
ExecStartPre hit.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

DEFAULT_ARCHIVE = Path(os.environ.get(
    "BD_RELEASE_ARCHIVE",
    str(Path.home() / ".bulkdownloader" / "releases"),
))
SERVICE_NAME = "bulkdownloader"
ZIP_PATTERN = re.compile(r"^BulkDownloader_v(\d+)_(\d+)_(\d+)\.zip$")


def _app_dir() -> Path:
    """The repo this script lives in. Script is at <app_dir>/tools/."""
    return Path(__file__).resolve().parent.parent


def _list_archive(archive_dir: Path) -> list[tuple[str, Path]]:
    """Return [(version_str, path), ...] sorted by version ascending."""
    if not archive_dir.is_dir():
        return []
    entries: list[tuple[tuple[int, int, int], str, Path]] = []
    for p in archive_dir.iterdir():
        m = ZIP_PATTERN.match(p.name)
        if m:
            major, minor, patch = (int(g) for g in m.groups())
            v = f"{major}.{minor}.{patch}"
            entries.append(((major, minor, patch), v, p))
    entries.sort()
    return [(v, p) for _, v, p in entries]


def _current_version(app_dir: Path) -> str:
    init_py = app_dir / "bulk_downloader" / "__init__.py"
    if not init_py.is_file():
        return "unknown"
    try:
        for line in init_py.read_text(
                encoding="utf-8", errors="replace").splitlines():
            m = re.match(
                r"""^__version__\s*=\s*['"]([^'"]+)['"]""", line)
            if m:
                return m.group(1)
    except OSError:
        pass
    return "unknown"


def cmd_list(archive_dir: Path) -> int:
    items = _list_archive(archive_dir)
    if not items:
        print(f"Archive {archive_dir} is empty (or does not exist).")
        print()
        print("To populate it for the first time, build the current")
        print("release archive zip and copy it in by hand:")
        print(f"    mkdir -p {archive_dir}")
        print(f"    cp BulkDownloader_v<X_Y_Z>.zip {archive_dir}/")
        print()
        print("Or use this script's --archive mode on the next release.")
        return 0
    print(f"Archive: {archive_dir}")
    print(f"{len(items)} release zip(s) (oldest first):")
    print()
    for v, p in items:
        size_mb = p.stat().st_size / (1024 * 1024)
        mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime(
            "%Y-%m-%d")
        print(f"  v{v:<10}  {size_mb:>6.1f} MB  {mtime}  {p.name}")
    print()
    return 0


def cmd_archive(archive_dir: Path, version: str,
                src_zip: Path | None) -> int:
    """Copy a release zip into the archive."""
    if src_zip is None:
        # Look in the app's parent dir (where the operator usually
        # uploads release zips) for a matching file.
        app_parent = _app_dir().parent
        candidate_name = (
            f"BulkDownloader_v{version.replace('.', '_')}.zip")
        for d in [Path.cwd(), app_parent, Path.home()]:
            cand = d / candidate_name
            if cand.is_file():
                src_zip = cand
                break
        if src_zip is None:
            print(f"FAIL: could not find {candidate_name} in cwd, "
                  f"{app_parent}, or ~/. Specify with --from PATH.",
                  file=sys.stderr)
            return 2
    if not src_zip.is_file():
        print(f"FAIL: {src_zip} is not a file.", file=sys.stderr)
        return 2

    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / f"BulkDownloader_v{version.replace('.', '_')}.zip"

    if dest.exists():
        # Re-archiving same version — check it's the same content; if
        # different, refuse so the operator decides.
        if dest.stat().st_size == src_zip.stat().st_size:
            print(f"OK: v{version} already in archive (same size, "
                  f"assumed identical).")
            return 0
        print(f"FAIL: {dest} exists but differs from {src_zip} in size",
              file=sys.stderr)
        print(f"  Delete the existing archive entry manually if you "
              f"want to replace it.", file=sys.stderr)
        return 2

    shutil.copy2(src_zip, dest)
    print(f"OK: archived {src_zip} -> {dest}")
    return 0


def _systemctl(action: str) -> tuple[int, str]:
    """Run systemctl. Returns (rc, output)."""
    try:
        r = subprocess.run(
            ["sudo", "systemctl", action, SERVICE_NAME],
            capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, f"{type(e).__name__}: {e}"


def cmd_rollback(archive_dir: Path, version: str,
                 skip_service: bool, skip_confirm: bool) -> int:
    items = dict(_list_archive(archive_dir))
    if version not in items:
        print(f"FAIL: v{version} not in archive.", file=sys.stderr)
        print(f"  Available: {', '.join(items) or '(none)'}",
              file=sys.stderr)
        return 1
    zip_path = items[version]
    app_dir = _app_dir()
    current = _current_version(app_dir)

    print(f"Rollback plan:")
    print(f"  app dir         : {app_dir}")
    print(f"  current version : {current}")
    print(f"  rollback target : {version}")
    print(f"  zip             : {zip_path}")
    print(f"  service         : {SERVICE_NAME}"
          f"{' (skipped)' if skip_service else ''}")
    print()

    if not skip_confirm:
        try:
            ans = input(
                "Proceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            print("aborted")
            return 1
        if ans not in ("y", "yes"):
            print("aborted")
            return 1

    # 1) backup current __init__.py
    init_py = app_dir / "bulk_downloader" / "__init__.py"
    backup_dir = app_dir / ".rollback_backup"
    backup_dir.mkdir(exist_ok=True)
    if init_py.is_file():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"__init__.py.{current}.{ts}"
        shutil.copy2(init_py, backup_path)
        print(f"  backed up current __init__.py -> {backup_path}")

    # 2) stop the service
    if not skip_service:
        rc, out = _systemctl("stop")
        if rc != 0:
            print(f"  WARNING: systemctl stop returned {rc}: {out}")
            print(f"  Continuing — the extract will still work, but a "
                  f"running service may hold file locks.")

    # 3) extract over the app dir
    try:
        with zipfile.ZipFile(zip_path) as zf:
            # Detect whether the zip has a top-level prefix directory.
            # Look for the anchor file `bulk_downloader/__init__.py` —
            # if it's at the zip root we don't strip; if it's at
            # `<prefix>/bulk_downloader/__init__.py` we strip that
            # prefix. This is safer than the "all files share one top
            # component" heuristic, which false-positives on flat zips
            # where every file happens to live under one subdirectory.
            ANCHOR = "bulk_downloader/__init__.py"
            strip_prefix = None
            for n in zf.namelist():
                norm = n.replace("\\", "/")
                if norm.endswith("/" + ANCHOR):
                    strip_prefix = norm[:-len(ANCHOR)]  # incl trailing /
                    break
                if norm == ANCHOR:
                    strip_prefix = ""
                    break
            if strip_prefix is None:
                print(f"FAIL: zip does not contain {ANCHOR}; refusing "
                      f"to extract — not a BulkDownloader release zip.",
                      file=sys.stderr)
                return 2

            for member in zf.infolist():
                if member.is_dir():
                    continue
                target_rel = member.filename.replace("\\", "/")
                if strip_prefix:
                    if not target_rel.startswith(strip_prefix):
                        # File outside the expected prefix — skip
                        # silently rather than spill it into the app
                        # dir root.
                        continue
                    target_rel = target_rel[len(strip_prefix):]
                target = app_dir / target_rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        print(f"  extracted {zip_path} -> {app_dir}"
              f"{f' (stripped prefix {strip_prefix!r})' if strip_prefix else ''}")
    except (zipfile.BadZipFile, OSError) as e:
        print(f"FAIL: extract error: {e}", file=sys.stderr)
        print(f"  App dir may be in a partial state. The pre-rollback "
              f"__init__.py is at {backup_dir}.", file=sys.stderr)
        return 2

    # 4) verify the rolled-back version
    new = _current_version(app_dir)
    if new == version:
        print(f"  verified: __init__.py now reads {new}")
    else:
        print(f"  WARNING: __init__.py reads {new}, expected {version}")

    # 5) restart the service
    if not skip_service:
        rc, out = _systemctl("start")
        if rc != 0:
            print(f"  WARNING: systemctl start returned {rc}: {out}")
            print(f"  Check: journalctl -u {SERVICE_NAME} -n 50")
        else:
            print(f"  service started")

    print()
    print(f"Rollback complete: v{current} -> v{new}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Restore a previous BulkDownloader release.",
        epilog="Set BD_RELEASE_ARCHIVE to override the default "
               "archive directory.")
    ap.add_argument("version", nargs="?",
                    help="version to roll back to, e.g. 3.63.6")
    ap.add_argument("--list", action="store_true",
                    help="list what's in the archive and exit")
    ap.add_argument("--archive", metavar="VERSION",
                    help="archive a release zip rather than rolling "
                         "back. The zip is found in the current dir "
                         "or ~/, or specify with --from.")
    ap.add_argument("--from", dest="src_zip", type=Path,
                    help="path to the zip to archive (with --archive)")
    ap.add_argument("--archive-dir", type=Path,
                    default=DEFAULT_ARCHIVE,
                    help=f"archive directory (default {DEFAULT_ARCHIVE})")
    ap.add_argument("--no-service", action="store_true",
                    help="skip systemctl stop/start (for testing or "
                         "non-systemd hosts)")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="skip the confirmation prompt")
    args = ap.parse_args()

    if args.list:
        return cmd_list(args.archive_dir)

    if args.archive:
        return cmd_archive(args.archive_dir, args.archive, args.src_zip)

    if args.version:
        return cmd_rollback(args.archive_dir, args.version,
                            args.no_service, args.yes)

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
