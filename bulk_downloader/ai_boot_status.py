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
# @874. How long an IN-FLIGHT document stays believable without a refresh.
# Applies to final=False documents ONLY -- expiring terminal verdicts at this
# interval would silently halve STALE_AFTER_SECONDS and turn an actionable
# `degraded: gpu_unavailable` into `stale`, which is the same information loss
# as the defect this closes, pointed the other way.
#
# Measured run: ~108s over six attempts (RETRY_DELAYS sums to 31s of sleeps), so
# roughly 18s between writes. 300 tolerates a slow attempt without calling a
# live run abandoned. It does NOT cover a pathological attempt that times out on
# all five sequential calls at REQUEST_TIMEOUT=120 -- that run would be graded
# abandoned at 300s. Fail-safe, and stated here rather than discovered on the
# box. Deliberately NOT derived by importing REQUEST_TIMEOUT: that is a reverse
# import edge and a cycle. Deliberately NOT BD_-prefixed: CLAUDE.md section 4
# bands test_gui_parity on any BD_ name.
IN_FLIGHT_TTL_SECONDS = 300
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
                 now: float | None = None, boot_id: str | None = None,
                 final: bool = True) -> dict[str, Any]:
    """Persist the readiness document. `final=False` marks a run IN FLIGHT.

    The marker is stamped HERE, into the same block as schema_version, rather
    than derived at read time -- tests/test_ai_boot_status.py:40 asserts
    `loaded == written`, so a field invented by read_status would break it.

    The True default is safe only because _persist in ai_boot_readiness makes
    the argument REQUIRED, so no run() path can inherit it by accident; the
    default exists purely so the existing direct callers keep working.
    """
    epoch = time.time() if now is None else float(now)
    payload = dict(document)
    payload.update({
        "schema_version": SCHEMA_VERSION,
        "boot_id": get_boot_id() if boot_id is None else boot_id,
        "updated_at": _iso_timestamp(epoch),
        "final": bool(final),
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
    # @874 -- FINALITY, checked after boot_id and before expiry.
    #
    # (a) A document with no `final` key cannot answer "did this run finish?".
    #     That is UNKNOWN, and unknown is a third state. Defaulting it to True
    #     would grade every pre-fix document -- every document in a rolling
    #     deploy -- as a terminal verdict, which is this exact defect
    #     reintroduced by its own fix, invisibly. Self-heals on the next write.
    if "final" not in payload:
        return {**payload, "state": "unknown", "reason": "no_finality_marker"}
    # (b) An in-flight marker whose writer stopped refreshing it. Without this
    #     the marker rots into a lie: today's bare `retrying` token stays
    #     readable for the full 600s after a SIGKILL, so the reader is misled
    #     in the opposite direction. An unparseable timestamp takes this branch
    #     too -- fail-safe.
    if payload.get("final") is False:
        if updated is None or epoch - updated > IN_FLIGHT_TTL_SECONDS:
            return {**payload, "state": "stale", "stale_reason": "abandoned"}
        # (c) in flight and fresh: the answer the reader actually wanted.
        return payload
    if updated is None or epoch - updated > STALE_AFTER_SECONDS:
        return {**payload, "state": "stale", "stale_reason": "expired"}
    return payload
