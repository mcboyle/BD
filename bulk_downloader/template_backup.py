"""bulk_downloader.template_backup -- A0 KEYSTONE: generational gold-backup
with guaranteed restore (v3.66.471).

The original ``template_keystone.snapshot_gold`` keeps exactly ONE gold per host
(``<host>.template.json.bak``, "first snapshot wins"): a sequence of writes
loses every known-good state but the first. A0 generalizes ``profile_sync``'s
timestamped move-aside pattern into a *generational* backup:

    templates/.gold_backups/<host>/<ts>/
        <host>.template.json     # the backed-up bytes (copy2)
        manifest.json            # {host, ts, sha256, version, reason, source}

Every write to an enabled/gold template can first snapshot to a fresh ``<ts>/``,
so the full history is restorable -- and ``restore_template`` is a one call that
copies a chosen generation back over live and VERIFIES the sha matches the
manifest (byte-identical guarantee). A backup failure is reported ``ok=False``
(never swallowed), so a caller can ABORT the write rather than overwrite gold
with no recovery point. This is the safety primitive every autonomous write in
Phase B/C is gated on.

Pure stdlib; reviewed-dir relative; no network, no Flask. ``BD_ROOT`` overrides
the project root for relocation (the custom test runner chdirs).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_SUFFIX = ".template.json"
_BACKUPS_DIRNAME = ".gold_backups"
_MANIFEST = "manifest.json"


def _project_root() -> Path:
    env = os.environ.get("BD_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1]


def _reviewed_dir(reviewed_dir=None) -> Path:
    if reviewed_dir is not None:
        return Path(reviewed_dir)
    return _project_root() / "templates" / "reviewed"


def _safe_host(host: str) -> Optional[str]:
    """Reject anything that could escape the backups tree or isn't a bare host."""
    if not host or not isinstance(host, str):
        return None
    if "/" in host or "\\" in host or ".." in host or host.startswith("."):
        return None
    return host


def _backups_root(reviewed_dir=None) -> Path:
    # .gold_backups sits under templates/ (the parent of reviewed/), mirroring
    # the spec path templates/.gold_backups/<host>/<ts>/.
    return _reviewed_dir(reviewed_dir).parent / _BACKUPS_DIRNAME


def _host_backups(host: str, reviewed_dir=None) -> Path:
    return _backups_root(reviewed_dir) / host


def _live_path(host: str, reviewed_dir=None) -> Path:
    return _reviewed_dir(reviewed_dir) / f"{host}{_SUFFIX}"


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _new_ts() -> str:
    """A timestamp unique even within the same wall-clock second: second
    granularity (human-readable, sorts lexically) + a monotonic-ns suffix so two
    backups in the same second never collide on the same directory."""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + format(time.monotonic_ns() & 0xFFFFFF, "06x")


# ── primitives ───────────────────────────────────────────────────────────────

def backup_template(host: str, *, reviewed_dir=None, reason: str = "") -> Dict[str, Any]:
    """Snapshot the current live ``<host>.template.json`` into a fresh
    generational backup dir with a manifest. Returns ``ok=False`` on ANY failure
    so the caller can abort the pending write.

    No live template -> ``{ok: True, backed_up: False}`` (nothing to protect;
    the first write of a host has no gold to lose).
    """
    h = _safe_host(host)
    if not h:
        return {"ok": False, "error": "invalid host"}
    live = _live_path(h, reviewed_dir)
    if not live.is_file():
        return {"ok": True, "backed_up": False, "reason": "no live template"}
    try:
        raw = live.read_bytes()
        sha = _sha256_bytes(raw)
        try:
            from . import __version__ as _bd_version
        except Exception:
            _bd_version = None
        # Prefer the template's own version stamp; fall back to the package one.
        tpl_version = None
        try:
            tpl_version = (json.loads(raw.decode("utf-8")) or {}).get("version")
        except Exception:
            tpl_version = None
        ts = _new_ts()
        dest_dir = _host_backups(h, reviewed_dir) / ts
        dest_dir.mkdir(parents=True, exist_ok=False)
        (dest_dir / f"{h}{_SUFFIX}").write_bytes(raw)
        manifest = {
            "host": h,
            "ts": ts,
            "sha256": sha,
            "version": tpl_version or _bd_version,
            "reason": (reason or "")[:200],
            "source": str(live),
            "created": int(time.time()),
        }
        (dest_dir / _MANIFEST).write_text(json.dumps(manifest, indent=2), "utf-8")
        return {"ok": True, "backed_up": True, "ts": ts,
                "dir": str(dest_dir), "sha256": sha}
    except Exception as e:  # surfaced, never swallowed -> caller ABORTS
        return {"ok": False, "error": f"backup failed: {e}"[:160]}


def list_backups(host: str, *, reviewed_dir=None) -> List[str]:
    """Timestamps of retained generations for ``host``, oldest-first."""
    h = _safe_host(host)
    if not h:
        return []
    base = _host_backups(h, reviewed_dir)
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir()
                  if d.is_dir() and (d / _MANIFEST).is_file())


def latest_backup(host: str, *, reviewed_dir=None) -> Optional[str]:
    gens = list_backups(host, reviewed_dir=reviewed_dir)
    return gens[-1] if gens else None


def read_manifest(host: str, ts: str, *, reviewed_dir=None) -> Optional[Dict[str, Any]]:
    h = _safe_host(host)
    if not h:
        return None
    man = _host_backups(h, reviewed_dir) / ts / _MANIFEST
    if not man.is_file():
        return None
    try:
        return json.loads(man.read_text("utf-8"))
    except Exception:
        return None


def restore_template(host: str, *, ts: Optional[str] = None, reviewed_dir=None) -> Dict[str, Any]:
    """Copy a backed-up generation back over live and verify it is byte-identical
    to the manifest's sha. ``ts=None`` restores the latest generation."""
    h = _safe_host(host)
    if not h:
        return {"ok": False, "error": "invalid host"}
    if ts is None:
        ts = latest_backup(h, reviewed_dir=reviewed_dir)
    if not ts:
        return {"ok": False, "error": "no backup to restore"}
    gen_dir = _host_backups(h, reviewed_dir) / ts
    src = gen_dir / f"{h}{_SUFFIX}"
    man = read_manifest(h, ts, reviewed_dir=reviewed_dir)
    if not src.is_file() or man is None:
        return {"ok": False, "error": "backup generation missing or corrupt"}
    try:
        raw = src.read_bytes()
        if _sha256_bytes(raw) != man.get("sha256"):
            return {"ok": False, "error": "backup integrity check failed (sha mismatch)"}
        live = _live_path(h, reviewed_dir)
        live.parent.mkdir(parents=True, exist_ok=True)
        # Write via a temp + atomic replace so a crash mid-restore can't tear live.
        tmp = live.with_name(live.name + ".restore.tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, live)
        return {"ok": True, "restored": str(live), "from": str(src), "ts": ts}
    except Exception as e:
        return {"ok": False, "error": f"restore failed: {e}"[:160]}
