from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

from .ai_boot_status import STATE_PATH, load_effective_config, write_status
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
        "endpoint": cfg["endpoint"],
        "keep_alive": KEEP_ALIVE,
        "gpu": gpu or {"available": False, "devices": []},
        "models": {
            "text": {"name": cfg["model_text"], "state": "pending"},
            "vision": {"name": cfg["model_vision"], "state": "pending"},
        },
        "error_code": "",
        "error": "",
    }


def _persist(document, state_path: Path, now, boot_id):
    return write_status(document, state_path, now=now(), boot_id=boot_id)


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
    installed = probe.list_models()
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
    if not gpu.get("available"):
        failure = ProbeFailure("gpu_unavailable", str(gpu.get("error") or "GPU unavailable"))
        failure.partial_status = _base(cfg, attempt, gpu)
        raise failure

    probe.warm_text(cfg["model_text"])
    vision_error = None
    try:
        probe.warm_vision(cfg["model_vision"])
    except ProbeFailure as exc:
        vision_error = ProbeFailure("vision_warm_failed", str(exc))

    entries = probe.resident_models()
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
            "models": {"text": text, "vision": {**vision, "state": "failed" if vision_error else vision["state"]}},
            "error_code": code,
            "error": message[:300],
        })
        failure = ProbeFailure(code, message)
        failure.partial_status = partial
        raise failure

    ready = _base(cfg, attempt, gpu)
    ready.update({"state": "ready", "models": {"text": text, "vision": vision}})
    return ready


def run(config: Mapping[str, Any] | None = None, *, state_path: Path = STATE_PATH,
        probe_factory=OllamaBootProbe, sleep=time.sleep, retry_delays=RETRY_DELAYS,
        now=time.time, boot_id: str | None = None) -> int:
    cfg = load_effective_config() if config is None else dict(config)
    if not cfg["enabled"] or cfg["provider"] != "ollama":
        _persist({**_base(cfg, 0), "state": "not_applicable"}, state_path, now, boot_id)
        return 0
    try:
        _validate_config(cfg)
    except ProbeFailure as exc:
        _persist({**_base(cfg, 0), "state": "degraded", "error_code": exc.code,
                  "error": str(exc)[:300]}, state_path, now, boot_id)
        return 1
    probe = probe_factory(cfg["endpoint"], timeout=REQUEST_TIMEOUT)
    delays = tuple(retry_delays)
    for index in range(len(delays) + 1):
        attempt = index + 1
        try:
            ready = _attempt(cfg, probe, attempt)
            _persist(ready, state_path, now, boot_id)
            return 0
        except ProbeFailure as exc:
            partial = getattr(exc, "partial_status", _base(cfg, attempt))
            final = index == len(delays)
            failed = {
                **partial,
                "state": "degraded" if final else "retrying",
                "attempt": attempt,
                "error_code": exc.code,
                "error": str(exc)[:300],
            }
            _persist(failed, state_path, now, boot_id)
            if final:
                return 1
            sleep(delays[index])
    return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
