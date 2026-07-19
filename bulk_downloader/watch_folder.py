"""v3.43.30: watch folder for .txt URL imports.

Per-site watched directory: drop a .txt file with URLs (one per line)
and the site's worker picks them up automatically. Useful for the
"I have a list of URLs from a scraper / browser bookmarks export and
want them in the queue" workflow without opening the UI.

## Behavior

  - Per-site daemon thread polls `watch_folder` every N seconds
    (default 30s, configurable)
  - Each .txt file in the folder is parsed: one URL per line, blanks
    and `#` comments ignored
  - URLs are deduped against the runner's existing queue and against
    each other within the same file
  - After successful import, the file is MOVED to a `.processed/`
    subfolder with a timestamp prefix so it doesn't get re-imported
  - On parse error or zero usable URLs, the file is MOVED to `.failed/`
    with the error in a sibling `.log` file
  - Events surface as `watch_import` in the per-site event log

## Why move vs delete?

A "scanned-and-applied" file is safer to keep around than to delete:
the user's source-of-truth scraper might re-emit the same URLs, and
deleting silently loses information. The processed/ subfolders give
the user an audit trail.

## Why not inotify / FileSystemWatcher?

Cross-platform reliability. inotify works on Linux only; the Windows
equivalents (ReadDirectoryChangesW) require platform-specific code
and have edge cases on network shares. A 30s polling interval is
slower but works everywhere identically. For the actual use case
(drop a .txt file from a scraper), 30s lag is fine.

## Configuration

Per-site config keys:
  watch_enabled (bool)         — default False
  watch_folder (str)           — absolute path, default empty
  watch_poll_seconds (int)     — default 30, min 5
  watch_url_priority (str)     — "normal" | "high"; default "normal"
"""
from __future__ import annotations

import re
import shutil
import threading
import time
from pathlib import Path
from typing import Callable


# Comment / blank line patterns. URLs may have leading whitespace
# (user's scraper indented them); we strip per-line.
_COMMENT_RE = re.compile(r"^\s*#")
_URL_RE = re.compile(r"^(https?://|magnet:\?)", re.IGNORECASE)


def parse_url_file(path: Path) -> tuple[list[str], list[str]]:
    """Read a .txt file and return (urls, errors).

    Format:
      - One URL per line
      - Blank lines ignored
      - Lines starting with `#` ignored (comments)
      - Other lines that don't look like URLs (http://, https://,
        magnet:?) are reported as errors but don't abort parsing

    Returns:
      urls: deduped list preserving first-seen order
      errors: list of human-readable per-line problems
    """
    urls = []
    errors = []
    seen = set()
    try:
        # latin-1 fallback for files with weird encodings — better
        # to extract URLs than to bail on a single bad byte
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1", errors="replace")
    except OSError as e:
        return [], [f"read failed: {type(e).__name__}: {e}"]

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or _COMMENT_RE.match(line):
            continue
        if not _URL_RE.match(line):
            errors.append(f"line {lineno}: not a URL: {line[:80]}")
            continue
        if line in seen:
            continue
        seen.add(line)
        urls.append(line)
    return urls, errors


def _safe_move(src: Path, dest_dir: Path) -> Path:
    """Move src into dest_dir, prefixing with a timestamp so multiple
    runs don't collide. Creates dest_dir if missing. Returns the
    final path; raises OSError on disk failure."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest = dest_dir / f"{ts}_{src.name}"
    # Collision is theoretically possible if two files happen the
    # same second — append the source mtime nanoseconds to disambiguate
    if dest.exists():
        nano = int(src.stat().st_mtime_ns) % 1000000
        dest = dest_dir / f"{ts}_{nano:06d}_{src.name}"
    shutil.move(str(src), str(dest))
    return dest


def scan_once(watch_folder: str | Path) -> list[Path]:
    """Return a list of .txt files in watch_folder, sorted by mtime
    (oldest first so they're processed in arrival order). Skips the
    .processed/ and .failed/ subfolders so we don't reprocess history.
    """
    p = Path(watch_folder)
    if not p.is_dir():
        return []
    candidates = []
    for entry in p.iterdir():
        # Skip subdirs (specifically .processed/, .failed/) and
        # non-.txt files
        if not entry.is_file():
            continue
        if entry.suffix.lower() != ".txt":
            continue
        if entry.name.startswith("."):  # hidden files, lock files
            continue
        candidates.append(entry)
    candidates.sort(key=lambda f: f.stat().st_mtime)
    return candidates


def process_file(file_path: Path, runner,
                  priority: str = "normal") -> dict:
    """Parse one .txt file and call runner.load_urls() with the
    extracted URLs. Moves the file to .processed/ on success,
    .failed/ on parse error or zero URLs.

    Args:
      file_path: the .txt file
      runner: SiteRunner instance
      priority: "normal" or "high" for the imported URLs

    Returns:
      {
        "ok": bool,
        "urls_imported": int,
        "errors": [str, ...],
        "moved_to": str (absolute path of post-move location)
      }
    """
    urls, errors = parse_url_file(file_path)
    folder = file_path.parent
    if not urls:
        # No usable URLs — failed
        failed_dir = folder / ".failed"
        moved = _safe_move(file_path, failed_dir)
        # Write a sibling .log file explaining why
        try:
            log_path = moved.with_suffix(moved.suffix + ".log")
            reason = "No usable URLs found"
            if errors:
                reason += "\n\nErrors:\n" + "\n".join(errors[:50])
            log_path.write_text(reason, encoding="utf-8")
        except OSError:
            pass
        return {
            "ok": False, "urls_imported": 0,
            "errors": errors or ["empty file"],
            "moved_to": str(moved),
        }

    # Load the URLs, then honor the watch-folder priority by tagging the
    # queued rows via the runner's bulk_priority (updates the in-memory job
    # map, the live queue ordering, AND the db queue rows). THC (v3.66.529):
    # the previous `load_urls(urls, url_priorities=...)` call passed a kwarg
    # load_urls never had (its signature is load_urls(urls, dedupe, folder_scan)),
    # so it ALWAYS raised TypeError -> the except fell back to load_urls(urls)
    # WITHOUT priority -> the watch-folder priority was silently dropped on
    # every imported file. bulk_priority is a method on the passed runner, so
    # no new import edge is introduced.
    try:
        runner.load_urls(urls)
        if priority and priority != "normal":
            # mirror load_urls' own per-URL normalization (strip a tab-separated
            # header suffix + surrounding whitespace) so the queued job keys match
            norm_urls = [u.split("\t", 1)[0].strip() for u in urls]
            runner.bulk_priority(norm_urls, priority)
    except Exception as e:
        # Loading failed (DB error, etc.) — treat as failed
        failed_dir = folder / ".failed"
        moved = _safe_move(file_path, failed_dir)
        try:
            log_path = moved.with_suffix(moved.suffix + ".log")
            log_path.write_text(
                f"load_urls failed: {type(e).__name__}: {e}",
                encoding="utf-8")
        except OSError:
            pass
        return {
            "ok": False, "urls_imported": 0,
            "errors": [f"load_urls failed: {e}"],
            "moved_to": str(moved),
        }

    # Success — move to .processed/
    try:
        moved = _safe_move(file_path, folder / ".processed")
    except OSError as e:
        # The URLs WERE imported, but we couldn't move the file. Log
        # it but don't fail — better to leave the file in-place and
        # potentially reprocess than to lose the import work. The
        # second pass will be a no-op anyway since load_urls dedups.
        return {
            "ok": True, "urls_imported": len(urls),
            "errors": [f"move failed (file left in place): {e}"],
            "moved_to": str(file_path),
        }
    return {
        "ok": True, "urls_imported": len(urls),
        "errors": errors,  # non-fatal per-line errors carried forward
        "moved_to": str(moved),
    }


# ── Watch loop ───────────────────────────────────────────────────────

def watch_loop_for_site(runner, stop_event: threading.Event,
                          poll_fn: Callable = scan_once,
                          sleep_fn: Callable = time.sleep):
    """Per-site daemon loop. Polls the configured watch_folder every
    N seconds and processes any new .txt files.

    Args:
      runner: SiteRunner instance
      stop_event: signals shutdown
      poll_fn: scan_once or test stub
      sleep_fn: time.sleep or test stub (so tests can avoid sleeping)
    """
    while not stop_event.is_set():
        cfg = runner.config
        if not cfg.get("watch_enabled"):
            # Disabled by config edit. Poll less aggressively (we still
            # need to notice when the user RE-enables it). 5s baseline.
            if stop_event.wait(5):
                return
            continue
        folder = (cfg.get("watch_folder") or "").strip()
        poll_s = max(5, int(cfg.get("watch_poll_seconds") or 30))
        priority = (cfg.get("watch_url_priority") or "normal").strip().lower()
        if not folder:
            if stop_event.wait(poll_s):
                return
            continue
        try:
            files = poll_fn(folder)
        except Exception as e:
            runner.log_event("watch_import",
                f"Folder scan failed: {type(e).__name__}: {e}")
            files = []
        for f in files:
            if stop_event.is_set():
                return
            try:
                result = process_file(f, runner, priority=priority)
                if result["ok"]:
                    runner.log_event("watch_import",
                        f"Imported {result['urls_imported']} URL(s) "
                        f"from {f.name}"
                        + (f" (with {len(result['errors'])} per-line warnings)"
                           if result["errors"] else ""))
                else:
                    runner.log_event("watch_import",
                        f"Failed to import {f.name}: "
                        + ", ".join(result["errors"][:3])
                        + f" — moved to {Path(result['moved_to']).parent.name}/")
            except Exception as e:
                runner.log_event("watch_import",
                    f"Unexpected error processing {f.name}: "
                    f"{type(e).__name__}: {e}")
        # Wait for the next poll cycle, but respond to stop quickly
        if stop_event.wait(poll_s):
            return
