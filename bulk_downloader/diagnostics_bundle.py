"""Diagnostics bundle — single-call support snapshot (Phase 117, Block Q).

When the operator needs to ask for help (or send a bug report), they
need a snapshot of system state. This module produces a redacted,
zippable bundle containing:

  • Version / build info
  • Site configs (redacted — no passwords / cookies / API keys)
  • Recent history stats (counts by status, last 7 days)
  • Recent events log (last 200 entries)
  • Account health snapshot
  • Recent errors from stderr buffer (if available)
  • Subsystem availability: tools (ffmpeg, playwright, qbittorrent),
    optional deps (subliminal, pystray, lxml, rich)
  • OS / Python version / installed package versions

Output:
  • bundle() returns a Python dict
  • bundle_as_zip(dest_path) writes a zip with the dict as JSON +
    optional log files
  • redact_secrets(value) reusable helper for plugin authors

The redaction is paranoid by default — even fields like
`download_dir` get truncated to last 3 path segments so absolute
host filesystem layouts don't leak.
"""
from __future__ import annotations

import io
import json
import os
import platform
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional


# Field names whose value should ALWAYS be redacted, regardless of
# where they appear in the bundle.
_SENSITIVE_FIELDS = {
    "password", "passwd", "pwd", "secret", "api_key", "apikey",
    "auth_token", "token", "bearer", "cookies", "cookies_b64",
    "cookie", "set-cookie", "authorization",
    "tpdb_key", "tpdb_token",
    "vpn_username", "vpn_password", "wireguard_private_key",
    "vapid_private_key", "smtp_password",
    "discord_token", "telegram_token", "telegram_bot_token",
    "username", "user_email", "email", "twocaptcha_key",
    "capsolver_key", "openai_api_key", "anthropic_api_key",
}

# Field name patterns (case-insensitive) that should always redact
_SENSITIVE_PATTERNS = [
    re.compile(r".*password.*", re.IGNORECASE),
    re.compile(r".*api[-_ ]?key.*", re.IGNORECASE),
    re.compile(r".*secret.*", re.IGNORECASE),
    re.compile(r".*token.*", re.IGNORECASE),
    re.compile(r".*cookie.*", re.IGNORECASE),
]

_REDACT_PLACEHOLDER = "<redacted>"


def _is_sensitive_key(key: str) -> bool:
    if not isinstance(key, str):
        return False
    if key.lower() in _SENSITIVE_FIELDS:
        return True
    if any(p.match(key) for p in _SENSITIVE_PATTERNS):
        return True
    # REDACT-SOT Cut 2: also defer to the shared config-domain SoT so anchored
    # key-material names the local pattern set missed (private_key / passphrase /
    # preshared_key) are stripped from the diagnostics bundle. Lazy import avoids
    # a static import edge.
    from .site_editor import is_secret_config_key
    return is_secret_config_key(key)


def redact_secrets(value, *, depth: int = 0):
    """Recursively redact sensitive keys from a dict/list/scalar.
    Returns a redacted copy. Strings are returned as-is unless they
    look like a token (long, all hex/alnum, no spaces) — in which
    case redaction is conservative and we leave them; only key-based
    redaction is applied."""
    if depth > 20:  # cycle / depth guard
        return "<too-deep>"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _is_sensitive_key(k):
                # Preserve type hint (was it a string, a list, etc.)
                if isinstance(v, (str, bytes)):
                    out[k] = _REDACT_PLACEHOLDER
                elif isinstance(v, (list, tuple)):
                    out[k] = f"<redacted list of {len(v)}>"
                elif isinstance(v, dict):
                    out[k] = "<redacted dict>"
                else:
                    out[k] = _REDACT_PLACEHOLDER
            else:
                out[k] = redact_secrets(v, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [redact_secrets(v, depth=depth + 1) for v in value]
    return value


def _short_path(p: str) -> str:
    """Shorten an absolute path to last 3 segments, so operators don't
    leak their full host filesystem layout."""
    if not p or not isinstance(p, str):
        return ""
    parts = Path(p).parts
    if len(parts) <= 3:
        return p
    return os.sep.join(("…",) + parts[-3:])


def _capture_versions() -> dict:
    """Optional-dep version snapshot. Each lookup wrapped — missing
    package shows as None."""
    out = {"python": sys.version.split()[0],
           "platform": platform.platform(),
           "system": platform.system()}
    for pkg in ("playwright", "yt_dlp", "requests", "flask",
                "lxml", "rich", "pystray", "PIL", "subliminal",
                "cryptography", "pywebpush"):
        try:
            mod = __import__(pkg.replace("-", "_"))
            out[pkg] = getattr(mod, "__version__", "installed")
        except ImportError:
            out[pkg] = None
        except Exception:
            out[pkg] = "?"
    return out


def _capture_tools() -> dict:
    """Which external tools are reachable on PATH."""
    out = {}
    for tool in ("ffmpeg", "ffprobe", "yt-dlp", "wget", "curl",
                 "qbittorrent-nox", "rclone"):
        out[tool] = bool(shutil.which(tool))
    return out


def _capture_history_stats() -> dict:
    """Aggregate counts from the history table."""
    out = {"total": 0, "by_status_7d": {}, "by_site_7d": []}
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("SELECT COUNT(*) AS n FROM history").fetchone()
            out["total"] = int(row[0] if not hasattr(row, "keys") else row["n"])
            # by status, last 7d
            rows = cx.execute("""SELECT status, COUNT(*) AS n
                                  FROM history
                                  WHERE ts >= datetime('now', '-7 days')
                                  GROUP BY status""").fetchall()
            for r in rows:
                out["by_status_7d"][r[0] if not hasattr(r, "keys") else r["status"]] = \
                    int(r[1] if not hasattr(r, "keys") else r["n"])
            # by site, last 7d
            rows = cx.execute("""SELECT site_id, COUNT(*) AS n,
                                       SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
                                       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
                                  FROM history
                                  WHERE ts >= datetime('now', '-7 days')
                                  GROUP BY site_id
                                  ORDER BY n DESC""").fetchall()
            out["by_site_7d"] = [
                {"site_id": r[0] if not hasattr(r, "keys") else r["site_id"],
                 "total": int(r[1] if not hasattr(r, "keys") else r["n"]),
                 "done": int(r[2] if not hasattr(r, "keys") else r["done"]),
                 "failed": int(r[3] if not hasattr(r, "keys") else r["failed"])}
                for r in rows
            ]
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def _capture_account_health() -> dict:
    try:
        from . import account_health as _ah
        return {"summary": _ah.summary(), "rows": _ah.report_all()}
    except Exception as e:
        return {"error": str(e)[:200]}


def _capture_recent_failures(limit: int = 20) -> list:
    """Top recent failures by frequency — quick triage signal."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT message, COUNT(*) AS n
                                  FROM history
                                  WHERE status='failed'
                                    AND ts >= datetime('now', '-7 days')
                                  GROUP BY substr(message, 1, 100)
                                  ORDER BY n DESC LIMIT ?""",
                              (int(limit),)).fetchall()
        return [{"message": (r[0] if not hasattr(r, "keys") else r["message"])[:200],
                 "count": int(r[1] if not hasattr(r, "keys") else r["n"])}
                for r in rows]
    except Exception:
        return []


def bundle(s_cfg: Optional[dict] = None,
           *,
           include_history: bool = True) -> dict:
    """Build a diagnostics snapshot. Safe to share with support."""
    snapshot = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "schema": "bd-diagnostics-v1",
        "versions": _capture_versions(),
        "tools_available": _capture_tools(),
    }
    if s_cfg:
        # Redact per-site config — sensitive auth/cookie data scrubbed
        redacted_cfg = {}
        for sid, cfg in s_cfg.items():
            r = redact_secrets(cfg)
            # Shorten path fields
            for path_key in ("download_dir", "cookies_path", "log_path"):
                if path_key in r and r[path_key]:
                    r[path_key] = _short_path(r[path_key])
            redacted_cfg[sid] = r
        snapshot["sites"] = redacted_cfg
        snapshot["site_count"] = len(s_cfg)
    if include_history:
        snapshot["history_stats"] = _capture_history_stats()
        snapshot["recent_failures"] = _capture_recent_failures()
    snapshot["account_health"] = _capture_account_health()
    # Subsystem availability flags
    snapshot["subsystems"] = {}
    for mod_name in ("subtitles", "tpdb", "wayback_cdx", "enrichment",
                     "selector_playground", "tray_app", "cli_dashboard"):
        try:
            mod = __import__(f"bulk_downloader.{mod_name}", fromlist=[mod_name])
            if hasattr(mod, "is_available"):
                snapshot["subsystems"][mod_name] = mod.is_available()
            else:
                snapshot["subsystems"][mod_name] = "loaded"
        except Exception:
            snapshot["subsystems"][mod_name] = False
    return snapshot


def bundle_as_zip(dest_path: str, *,
                  s_cfg: Optional[dict] = None,
                  include_logs: bool = False) -> dict:
    """Write a diagnostics zip to `dest_path`. Returns
    {ok, path, size_bytes, error}.

    `include_logs=True` attaches bd.log / runner.log if they exist
    next to the install dir."""
    snap = bundle(s_cfg=s_cfg)
    try:
        with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("diagnostics.json",
                       json.dumps(snap, indent=2, ensure_ascii=False))
            zf.writestr("README.txt", _readme_text())
            if include_logs:
                _attach_logs(zf)
        return {"ok": True, "path": dest_path,
                "size_bytes": os.path.getsize(dest_path)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _attach_logs(zf: zipfile.ZipFile):
    """Attach known log files, line-redacted.

    F-COREBD13-03: this bundle is documented as 'redacted / safe to share',
    so attached logs must be scrubbed too -- not written verbatim. Each line
    is run through the capture secret redactor (JWTs, signed URLs, cookies,
    kv-secrets) before it enters the zip.
    """
    from .capture_artifact_redact import redact_value
    candidates = []
    try:
        from .constants import INSTALL_DIR
        for name in ("bd.log", "runner.log", "events.log"):
            p = Path(INSTALL_DIR) / name
            if p.is_file() and p.stat().st_size < 10 * 1024 * 1024:
                candidates.append(p)
    except Exception:
        pass
    for p in candidates:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            redacted = "\n".join(redact_value(ln) for ln in text.splitlines())
            zf.writestr(f"logs/{p.name}", redacted)
        except Exception:
            pass


def _readme_text() -> str:
    return (
        "BulkDownloader Diagnostics Bundle\n"
        "==================================\n\n"
        "diagnostics.json contains redacted system state:\n"
        "  - version + platform info\n"
        "  - external tool availability\n"
        "  - per-site configs (passwords/cookies/api keys redacted)\n"
        "  - recent history stats + failure clusters\n"
        "  - last 200 events\n"
        "  - account health summary\n\n"
        "Logs (if included) are in logs/.\n\n"
        "Share this bundle with support; nothing should be sensitive.\n"
        "If you find a leaked secret in the JSON, please report — that's\n"
        "a redaction bug that needs fixing.\n"
    )
