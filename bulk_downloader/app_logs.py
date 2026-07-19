"""logs API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/logs views moved onto a Flask Blueprint.
Endpoint labels gain a "logs." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from pathlib import Path

logs_bp = Blueprint("logs", __name__)


@logs_bp.route("/api/logs/tail")
def api_logs_tail():
    """Return the last N lines of the log file. Useful for remote debug
    via the web UI without SSH access to the server. Default 200 lines,
    capped at 5000.

    v3.47.7: rotation is now 5GB×6 files (was 5MB×3), so we always
    read from the END via a 1MB windowed seek rather than risking a
    full-file read on a multi-gigabyte log."""
    try:
        n = max(1, min(5000, int(request.args.get("lines", 200))))
    except Exception:
        n = 200
    log_path = Path("logs/bulk_downloader.log")
    if not log_path.exists():
        return jsonify({"ok": True, "lines": [], "note": "log file not yet created"})
    try:
        # Always windowed-read to handle multi-GB files cleanly. 1MB
        # tail comfortably holds 5000 typical log lines.
        size = log_path.stat().st_size
        with log_path.open("rb") as f:
            if size > 1_000_000:
                f.seek(size - 1_000_000)
                data = f.read()
                # Drop the first line which is probably partial
                nl = data.find(b"\n")
                if nl != -1: data = data[nl+1:]
            else:
                data = f.read()
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        from . import log as _log
        return jsonify({
            "ok": True,
            "lines": lines[-n:],
            "total_lines_returned": min(len(lines), n),
            "file_size": size,
            "current_level": _log.get_level(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@logs_bp.route("/api/logs/clear", methods=["POST"])
def api_logs_clear():
    """v3.47.7: truncate the active log file and remove rotated archives.

    Called by the Clear button in the Log tab. We truncate in-place
    rather than unlinking because the RotatingFileHandler holds an
    open file handle — unlinking on Windows would fail (file in use)
    and on POSIX would silently keep writing to the deleted inode
    (the visible file stays empty after the next rotation). Truncate
    avoids both modes.

    Archived rotated files (.log.1 ... .log.5) are deleted outright;
    they're not held open by any handler.
    """
    try:
        log_path = Path("logs/bulk_downloader.log")
        truncated_bytes = 0
        if log_path.exists():
            truncated_bytes = log_path.stat().st_size
            with log_path.open("r+b") as f:
                f.truncate(0)
        # Sweep rotated archives. RotatingFileHandler names them
        # bulk_downloader.log.1 ... .log.<backupCount>.
        archives_removed = 0
        for archive in Path("logs").glob("bulk_downloader.log.*"):
            try:
                archive.unlink()
                archives_removed += 1
            except OSError:
                # In-use on Windows or permission denied — skip and
                # report partial success; the active file is already
                # truncated so the user gets the visible result.
                pass
        # Best-effort: emit a marker line so the next tail shows the
        # clear happened (not just an empty file).
        try:
            import logging as _logging
            _logging.getLogger("bulk_downloader").info(
                "log cleared by user via /api/logs/clear "
                "(freed %d bytes, removed %d archives)",
                truncated_bytes, archives_removed)
        except Exception:
            pass
        return jsonify({"ok": True,
                        "freed_bytes": truncated_bytes,
                        "archives_removed": archives_removed})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

def register_routes(app) -> int:
    app.register_blueprint(logs_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("logs."))

