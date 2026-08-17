from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

from .ai_boot_status import (
    STATE_PATH,
    load_effective_config,
    sanitize_endpoint,
    write_status,
)
from .aiassist import validate_endpoint
from .llm_readiness import model_present
from .ollama_boot_probe import KEEP_ALIVE, OllamaBootProbe, ProbeFailure

REQUEST_TIMEOUT = 120.0
RETRY_DELAYS = (1, 2, 4, 8, 16)


def _model_state(name: str, entry: dict | None) -> dict[str, Any]:
    if entry is None:
        return {"name": name, "state": "not_resident", "resident": False,
                "size": 0, "size_vram": 0, "gpu_ratio": 0.0}
    size = int(entry.get("size") or 0)
    size_vram = int(entry.get("size_vram") or 0)
    return {
        "name": name,
        "state": "ready" if size_vram > 0 else "cpu_only",
        "resident": True,
        "size": size,
        "size_vram": size_vram,
        "gpu_ratio": round(size_vram / size, 4) if size > 0 else 0.0,
    }


def _base(cfg: Mapping[str, Any], attempt: int, gpu: dict | None = None) -> dict:
    return {
        "attempt": attempt,
        "provider": cfg["provider"],
        "endpoint": sanitize_endpoint(str(cfg["endpoint"])),
        "keep_alive": KEEP_ALIVE,
        "gpu": gpu or {"available": False, "devices": []},
        "models": {
            "text": {"name": cfg["model_text"], "state": "pending"},
            "vision": {"name": cfg["model_vision"], "state": "pending"},
        },
        "error_code": "",
        "error": "",
        "phases": [],
    }


def _persist(document, state_path: Path, now, boot_id, *, final: bool):
    """`final` is REQUIRED and deliberately has no default.

    Omitting it at any call site is an authoring-time TypeError rather than a
    silent True -- and a silent True on an in-flight write is precisely the
    defect this closes.
    """
    return write_status(document, state_path, now=now(), boot_id=boot_id, final=final)


def _validate_config(cfg: Mapping[str, Any]) -> None:
    endpoint_ok, endpoint_message = validate_endpoint(
        str(cfg.get("endpoint") or ""), provider="ollama"
    )
    if not endpoint_ok:
        raise ProbeFailure("invalid_config", endpoint_message)
    if not str(cfg.get("model_text") or "").strip():
        raise ProbeFailure("invalid_config", "text model is empty")
    if not str(cfg.get("model_vision") or "").strip():
        raise ProbeFailure("invalid_config", "vision model is empty")


def _attempt(cfg, probe, attempt: int) -> dict[str, Any]:
    phases = []
    installed = probe.list_models()
    phases.append("list")
    missing = [name for name in (cfg["model_text"], cfg["model_vision"])
               if not model_present(name, installed)]
    if missing:
        failure = ProbeFailure("model_missing", "configured model missing: " + ", ".join(missing))
        partial = _base(cfg, attempt)
        for role in ("text", "vision"):
            if cfg[f"model_{role}"] in missing:
                partial["models"][role]["state"] = "missing"
        failure.partial_status = partial
        raise failure
    gpu = probe.gpu()
    phases.append("gpu")
    if not gpu.get("available"):
        failure = ProbeFailure("gpu_unavailable", str(gpu.get("error") or "GPU unavailable"))
        failure.partial_status = _base(cfg, attempt, gpu)
        raise failure

    probe.warm_text(cfg["model_text"])
    phases.append("warm_text")
    vision_error = None
    try:
        probe.warm_vision(cfg["model_vision"])
        phases.append("warm_vision")
    except ProbeFailure as exc:
        vision_error = ProbeFailure("vision_warm_failed", str(exc))

    entries = probe.resident_models()
    phases.append("ps")
    text = _model_state(cfg["model_text"], probe.resident_for(cfg["model_text"], entries))
    vision = _model_state(cfg["model_vision"], probe.resident_for(cfg["model_vision"], entries))
    if text["state"] != "ready":
        code = "text_not_gpu_backed" if text["state"] == "cpu_only" else "text_warm_failed"
        raise ProbeFailure(code, "text model is not GPU resident")

    if vision_error is not None or vision["state"] != "ready":
        code = vision_error.code if vision_error else (
            "vision_not_gpu_backed" if vision["state"] == "cpu_only" else "vision_warm_failed"
        )
        message = str(vision_error or "vision model is not GPU resident")
        probe.warm_text(cfg["model_text"])
        fallback_entries = probe.resident_models()
        text = _model_state(
            cfg["model_text"], probe.resident_for(cfg["model_text"], fallback_entries)
        )
        partial = _base(cfg, attempt, gpu)
        partial.update({
            "state": "degraded",
            "phases": phases,
            "models": {"text": text, "vision": {**vision, "state": "failed" if vision_error else vision["state"]}},
            "error_code": code,
            "error": message[:300],
        })
        failure = ProbeFailure(code, message)
        failure.partial_status = partial
        raise failure

    ready = _base(cfg, attempt, gpu)
    ready.update({"state": "ready", "models": {"text": text, "vision": vision},
                  "phases": phases})
    return ready


def run(config: Mapping[str, Any] | None = None, *, state_path: Path = STATE_PATH,
        probe_factory=OllamaBootProbe, sleep=time.sleep, retry_delays=RETRY_DELAYS,
        now=time.time, boot_id: str | None = None) -> int:
    cfg = load_effective_config() if config is None else dict(config)
    if not cfg["enabled"] or cfg["provider"] != "ollama":
        _persist({**_base(cfg, 0), "state": "not_applicable"}, state_path, now, boot_id,
                 final=True)
        return 0
    try:
        _validate_config(cfg)
    except ProbeFailure as exc:
        _persist({**_base(cfg, 0), "state": "degraded", "error_code": exc.code,
                  "error": str(exc)[:300]}, state_path, now, boot_id, final=True)
        return 1
    # @874 -- CLOSE THE PRE-ATTEMPT-1 WINDOW. Nothing was written between
    # process start and the END of attempt 1, and attempt 1 can issue five
    # sequential calls each bounded by REQUEST_TIMEOUT. On the box, where
    # systemd restarts this unit on failure, a reader sampling that window sees
    # the PREVIOUS run's terminal `degraded` document verbatim and cannot tell
    # it from a finished failure. That produced a wrong "Audit #3 reproduced"
    # verdict. Written BEFORE the factory call so a slow factory is covered too.
    _persist({**_base(cfg, 0), "state": "retrying", "error_code": "", "error": ""},
             state_path, now, boot_id, final=False)
    probe = probe_factory(cfg["endpoint"], timeout=REQUEST_TIMEOUT)
    delays = tuple(retry_delays)
    last_text_ready = None
    for index in range(len(delays) + 1):
        attempt = index + 1
        # The heartbeat that keeps IN_FLIGHT_TTL_SECONDS honest. Ships WITH the
        # TTL or neither ships: TTL without a heartbeat grades a slow live run
        # abandoned; heartbeat without a TTL lets a dead run read live forever.
        _persist({**_base(cfg, attempt), "state": "retrying", "error_code": "",
                  "error": ""}, state_path, now, boot_id, final=False)
        try:
            ready = _attempt(cfg, probe, attempt)
            _persist(ready, state_path, now, boot_id, final=True)
            return 0
        except ProbeFailure as exc:
            final = index == len(delays)
            partial = getattr(exc, "partial_status", None)
            if (partial is not None
                    and partial.get("models", {}).get("text", {}).get("state") == "ready"):
                last_text_ready = partial
            elif final and last_text_ready is not None:
                partial = last_text_ready
            if partial is None:
                partial = _base(cfg, attempt)
            failed = {
                **partial,
                "state": "degraded" if final else "retrying",
                "attempt": attempt,
                "error_code": exc.code,
                "error": str(exc)[:300],
            }
            _persist(failed, state_path, now, boot_id, final=final)
            if final:
                return 1
            sleep(delays[index])
    return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
