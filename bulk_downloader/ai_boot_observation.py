"""Bounded, fail-closed capture evidence for the AI boot companion."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .ai_boot_status import (
    STALE_AFTER_SECONDS,
    get_boot_id,
    load_effective_config,
    read_status,
    sanitize_endpoint,
)

UNIT = "bulkdownloader-ai-ready"
UNIT_PROPERTIES = (
    "LoadState", "ActiveState", "SubState", "Result", "ExecMainStatus",
    "NRestarts", "InvocationID",
)
READY_PHASES = ["list", "gpu", "warm_text", "warm_vision", "ps"]
DEFAULT_TIMEOUT = 300.0
DEFAULT_INTERVAL = 5.0
DEFAULT_RESTART_LIMIT = 3
MIN_INTERVAL = 0.25
DEFAULT_MAX_SAMPLES = 121
MAX_TIMEOUT_SECONDS = 300.0
MAX_INTERVAL_SECONDS = 30.0
MAX_FUTURE_CLOCK_SKEW_SECONDS = 5


def _hold(reason: str, *, unit: Mapping[str, Any] | None = None,
          status: Mapping[str, Any] | None = None, restart_limit: int = 0,
          verdict: str = "HOLD") -> dict[str, Any]:
    restarts = unit.get("NRestarts") if isinstance(unit, Mapping) else None
    return {
        "exit_code": 2,
        "verdict": verdict,
        "reason": reason,
        "restart_count": restarts,
        "restart_limit": restart_limit,
        "unit": dict(unit) if isinstance(unit, Mapping) else None,
        "status": dict(status) if isinstance(status, Mapping) else None,
    }


def _runtime_ready(model: object, expected_name: str) -> bool:
    if not isinstance(model, Mapping):
        return False
    ratio = model.get("gpu_ratio")
    return (
        model.get("name") == expected_name
        and model.get("state") == "ready"
        and model.get("resident") is True
        and isinstance(model.get("size_vram"), int)
        and not isinstance(model.get("size_vram"), bool)
        and model["size_vram"] > 0
        and isinstance(ratio, (int, float))
        and not isinstance(ratio, bool)
        and ratio > 0
    )


def classify(*, config: Mapping[str, Any] | None, unit: Mapping[str, Any] | None,
             status: Mapping[str, Any] | None, now: float, boot_id: str,
             restart_limit: int = DEFAULT_RESTART_LIMIT) -> dict[str, Any]:
    """Classify one complete observation without performing I/O or sleeping."""
    if not isinstance(config, Mapping) or config.get("observed") is not True:
        return _hold("config evidence missing or malformed", restart_limit=restart_limit)
    if not isinstance(unit, Mapping):
        return _hold("unit evidence missing or malformed", restart_limit=restart_limit)
    if not isinstance(status, Mapping):
        return _hold("status evidence missing or malformed", unit=unit,
                     restart_limit=restart_limit)
    if (not isinstance(boot_id, str) or not boot_id.strip()
            or boot_id.strip().lower() == "unknown"):
        return _hold("current boot identity is unproved", unit=unit, status=status,
                     restart_limit=restart_limit)
    status_boot_id = status.get("boot_id")
    if (not isinstance(status_boot_id, str) or not status_boot_id.strip()
            or status_boot_id.strip().lower() == "unknown"):
        return _hold("status boot identity is unproved", unit=unit, status=status,
                     restart_limit=restart_limit)
    schema = status.get("schema_version")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema != 1:
        return _hold("status schema is missing or unsupported", unit=unit,
                     status=status, restart_limit=restart_limit)
    restarts = unit.get("NRestarts")
    if not isinstance(restarts, int) or isinstance(restarts, bool) or restarts < 0:
        return _hold("restart count missing or malformed", unit=unit, status=status,
                     restart_limit=restart_limit)
    if unit.get("LoadState") != "loaded":
        return _hold("companion unit is not loaded", unit=unit, status=status,
                     restart_limit=restart_limit)
    invocation_id = unit.get("InvocationID")
    if not isinstance(invocation_id, str) or not invocation_id.strip():
        return _hold("systemd invocation identity is missing", unit=unit,
                     status=status, restart_limit=restart_limit)
    if status.get("boot_id") != boot_id:
        return _hold("status belongs to a previous boot", unit=unit, status=status,
                     restart_limit=restart_limit)
    updated = status.get("updated_epoch")
    if updated is None:
        try:
            updated = datetime.fromisoformat(
                str(status.get("updated_at")).replace("Z", "+00:00")
            ).timestamp()
        except (TypeError, ValueError):
            updated = None
    if (not isinstance(updated, (int, float)) or isinstance(updated, bool)
            or not math.isfinite(float(updated))
            or now - float(updated) > STALE_AFTER_SECONDS):
        return _hold("status is stale or has a malformed timestamp", unit=unit,
                     status=status, restart_limit=restart_limit)
    if float(updated) - now > MAX_FUTURE_CLOCK_SKEW_SECONDS:
        return _hold("status timestamp is too far in the future", unit=unit,
                     status=status, restart_limit=restart_limit)
    state = status.get("state")
    if state in {"unknown", "stale"}:
        return _hold(f"status is {state}", unit=unit, status=status,
                     restart_limit=restart_limit)
    if state == "retrying" or status.get("final") is False:
        reason = "restart contention exceeded limit" if restarts > restart_limit else "companion is retrying"
        verdict = "HOLD" if restarts > restart_limit else "RETRYING"
        return _hold(reason, unit=unit, status=status, restart_limit=restart_limit,
                     verdict=verdict)
    if status.get("final") is not True:
        return _hold("status has no terminal finality", unit=unit, status=status,
                     restart_limit=restart_limit)

    applicable = bool(config.get("enabled")) and config.get("provider") == "ollama"
    unit_success = (
        unit.get("ActiveState") == "inactive"
        and unit.get("SubState") == "dead"
        and unit.get("Result") == "success"
        and unit.get("ExecMainStatus") == 0
    )
    if not applicable:
        if state != "not_applicable" or not unit_success:
            return _hold("not-applicable status or unit result is inconsistent",
                         unit=unit, status=status, restart_limit=restart_limit)
        return {
            "exit_code": 0, "verdict": "NOT_APPLICABLE", "reason": "",
            "restart_count": restarts, "restart_limit": restart_limit,
            "observed": {"config": True, "unit": True, "status": True,
                         "text_runtime": False, "vision_runtime": False,
                         "gpu_runtime": False},
            "phases": list(status.get("phases") or []),
        }

    if state != "ready":
        return _hold(f"terminal status is {state}", unit=unit, status=status,
                     restart_limit=restart_limit)
    if not unit_success:
        return _hold("ready status lacks a successful completed unit",
                     unit=unit, status=status, restart_limit=restart_limit)
    phases = status.get("phases")
    if phases != READY_PHASES:
        return _hold("phase evidence is incomplete or out of order",
                     unit=unit, status=status, restart_limit=restart_limit)
    gpu = status.get("gpu")
    gpu_ready = isinstance(gpu, Mapping) and gpu.get("available") is True
    models = status.get("models")
    text_ready = isinstance(models, Mapping) and _runtime_ready(
        models.get("text"), str(config.get("model_text") or "")
    )
    vision_ready = isinstance(models, Mapping) and _runtime_ready(
        models.get("vision"), str(config.get("model_vision") or "")
    )
    if not text_ready:
        return _hold("text model lacks positive GPU runtime proof", unit=unit,
                     status=status, restart_limit=restart_limit)
    if not vision_ready:
        return _hold("vision model lacks positive GPU runtime proof", unit=unit,
                     status=status, restart_limit=restart_limit)
    if not gpu_ready:
        return _hold("GPU runtime evidence is unavailable", unit=unit,
                     status=status, restart_limit=restart_limit)
    return {
        "exit_code": 0, "verdict": "READY", "reason": "",
        "restart_count": restarts, "restart_limit": restart_limit,
        "observed": {"config": True, "unit": True, "status": True,
                     "text_runtime": True, "vision_runtime": True,
                     "gpu_runtime": True},
        "phases": list(phases),
    }


def _unit_snapshot(run=subprocess.run) -> dict[str, Any] | None:
    command = ["systemctl", "show", UNIT]
    for name in UNIT_PROPERTIES:
        command.extend(["--property", name])
    result = run(command, capture_output=True, text=True, timeout=10, check=False)
    if result.returncode != 0:
        return None
    values: dict[str, Any] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in UNIT_PROPERTIES:
            values[key] = value
    for key in ("ExecMainStatus", "NRestarts"):
        try:
            values[key] = int(values[key])
        except (KeyError, TypeError, ValueError):
            values[key] = None
    return values


def _config_snapshot(path: Path = Path("app_config.json")) -> dict[str, Any]:
    """Read the effective AI tuple with explicit on-disk provenance."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {**load_effective_config({}), "observed": True,
                "source": "absent_defaults"}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("app_config.json is absent or malformed") from exc
    if not isinstance(raw, dict):
        raise ValueError("app_config.json is not an object")
    return {**load_effective_config(raw), "observed": True, "source": "file"}


def _sanitize_config(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "observed": config.get("observed") is True,
        "enabled": bool(config.get("enabled")),
        "provider": str(config.get("provider") or ""),
        "endpoint": sanitize_endpoint(str(config.get("endpoint") or "")),
        "model_text": str(config.get("model_text") or ""),
        "model_vision": str(config.get("model_vision") or ""),
        "source": str(config.get("source") or "injected"),
    }


def _bounded_number(value: float, *, default: float, minimum: float,
                    maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return min(maximum, max(minimum, number))


def observe(*, timeout: float = DEFAULT_TIMEOUT, interval: float = DEFAULT_INTERVAL,
            max_samples: int = DEFAULT_MAX_SAMPLES,
            restart_limit: int = DEFAULT_RESTART_LIMIT, now=time.time,
            sleep=time.sleep, config_reader=_config_snapshot,
            unit_reader=_unit_snapshot, status_reader=read_status,
            boot_id_reader=get_boot_id) -> dict[str, Any]:
    started = now()
    samples: list[dict[str, Any]] = []
    timeout = _bounded_number(timeout, default=0.0, minimum=0.0,
                              maximum=MAX_TIMEOUT_SECONDS)
    interval = _bounded_number(interval, default=MIN_INTERVAL,
                               minimum=MIN_INTERVAL, maximum=MAX_INTERVAL_SECONDS)
    max_samples = max(1, int(max_samples))
    timed_out = False
    config_evidence: dict[str, Any] | None = None
    while True:
        acquisition_errors = []
        try:
            raw_config = config_reader()
            cfg = _sanitize_config(raw_config) if isinstance(raw_config, Mapping) else None
        except Exception:
            cfg = None
            acquisition_errors.append("config_reader")
        if cfg is not None:
            config_evidence = cfg
        try:
            unit = unit_reader()
        except Exception:
            unit = None
            acquisition_errors.append("unit_reader")
        try:
            current_boot = boot_id_reader()
        except Exception:
            current_boot = "unknown"
            acquisition_errors.append("boot_id_reader")
        try:
            status = status_reader(now=now(), boot_id=current_boot)
        except Exception:
            status = None
            acquisition_errors.append("status_reader")
        result = classify(config=cfg, unit=unit, status=status, now=now(),
                          boot_id=current_boot, restart_limit=restart_limit)
        starting = isinstance(unit, Mapping) and (
            unit.get("ActiveState") in {"active", "activating"}
            or unit.get("SubState") in {"start", "auto-restart"}
        )
        retryable = result["verdict"] == "RETRYING" or (
            starting and cfg is not None
            and isinstance(unit.get("NRestarts"), int)
            and not isinstance(unit.get("NRestarts"), bool)
            and unit.get("NRestarts") <= restart_limit
        )
        samples.append({"epoch": now(), "unit": unit, "status": status,
                        "verdict": "RETRYING" if retryable else result["verdict"],
                        "acquisition_errors": acquisition_errors})
        if result["exit_code"] == 0 or not retryable:
            break
        if now() - started >= timeout or len(samples) >= max_samples:
            timed_out = True
            boundary = "sample limit" if len(samples) >= max_samples else "deadline"
            result = _hold(f"bounded observation {boundary} reached during retry contention",
                           unit=unit, status=status, restart_limit=restart_limit)
            break
        sleep(min(interval, max(0.0, timeout - (now() - started))))
    result["samples"] = samples
    result["sample_count"] = len(samples)
    result["elapsed_seconds"] = max(0.0, now() - started)
    restart_counts = [sample["unit"].get("NRestarts") for sample in samples
                      if isinstance(sample.get("unit"), Mapping)
                      and isinstance(sample["unit"].get("NRestarts"), int)
                      and not isinstance(sample["unit"].get("NRestarts"), bool)]
    result["first_restart_count"] = restart_counts[0] if restart_counts else None
    result["last_restart_count"] = restart_counts[-1] if restart_counts else None
    result["restart_delta"] = (
        restart_counts[-1] - restart_counts[0] if restart_counts else None
    )
    result["timed_out"] = timed_out
    result["config"] = config_evidence
    result["acquisition_errors"] = sorted({error for sample in samples
                                           for error in sample["acquisition_errors"]})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--max-samples", type=int, default=DEFAULT_MAX_SAMPLES)
    parser.add_argument("--restart-limit", type=int, default=DEFAULT_RESTART_LIMIT)
    args = parser.parse_args(argv)
    try:
        result = observe(timeout=args.timeout, interval=args.interval,
                         max_samples=max(1, args.max_samples),
                         restart_limit=args.restart_limit)
    except Exception:
        result = {
            "exit_code": 2,
            "verdict": "HOLD",
            "reason": "observer raised before classification",
            "samples": [],
            "sample_count": 0,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, args.output)
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
