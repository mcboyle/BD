from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

SCHEMA_VERSION = 1
STALE_AFTER_SECONDS = 600
STATE_PATH = Path("state") / "ai_boot_readiness.json"
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")

DEFAULTS = {
    "enabled": False,
    "provider": "ollama",
    "endpoint": "http://localhost:11434",
    "model_text": "qwen2.5:7b",
    "model_vision": "qwen2.5vl:7b",
}


def sanitize_endpoint(endpoint: str) -> str:
    value = str(endpoint or DEFAULTS["endpoint"]).strip()
    parsed = urlsplit(value)
    host = parsed.hostname or "localhost"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parsed.scheme or "http", netloc, parsed.path.rstrip("/"), "", ""))


def load_effective_config(raw: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if raw is None:
        from . import global_config
        raw = global_config.get_config()
    return {
        "enabled": bool(raw.get("ai_enabled", DEFAULTS["enabled"])),
        "provider": str(raw.get("ai_provider") or DEFAULTS["provider"]).strip().lower(),
        "endpoint": sanitize_endpoint(str(raw.get("ai_endpoint") or DEFAULTS["endpoint"])),
        "model_text": str(raw.get("ai_model_text") or DEFAULTS["model_text"]).strip(),
        "model_vision": str(raw.get("ai_model_vision") or DEFAULTS["model_vision"]).strip(),
    }


def get_boot_id(path: Path = BOOT_ID_PATH) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError:
        return "unknown"


def _iso_timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: object) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def write_status(document: Mapping[str, Any], path: Path = STATE_PATH, *,
                 now: float | None = None, boot_id: str | None = None) -> dict[str, Any]:
    epoch = time.time() if now is None else float(now)
    payload = dict(document)
    payload.update({
        "schema_version": SCHEMA_VERSION,
        "boot_id": get_boot_id() if boot_id is None else boot_id,
        "updated_at": _iso_timestamp(epoch),
    })
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    return payload


def read_status(path: Path = STATE_PATH, *, now: float | None = None,
                boot_id: str | None = None) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "state": "unknown", "reason": "missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("status is not an object")
    except (OSError, ValueError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "state": "unknown", "reason": "malformed"}
    current_boot = get_boot_id() if boot_id is None else boot_id
    if payload.get("boot_id") != current_boot:
        return {**payload, "state": "stale", "stale_reason": "previous_boot"}
    updated = _parse_timestamp(payload.get("updated_at"))
    epoch = time.time() if now is None else float(now)
    if updated is None or epoch - updated > STALE_AFTER_SECONDS:
        return {**payload, "state": "stale", "stale_reason": "expired"}
    return payload
