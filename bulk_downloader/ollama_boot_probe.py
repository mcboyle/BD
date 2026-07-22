from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Callable
from urllib.request import Request, urlopen

from . import llm_readiness

KEEP_ALIVE = "10m"


class ProbeFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _request_json(method: str, url: str, payload: dict | None, timeout: float) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if body is None else {"Content-Type": "application/json"}
    request = Request(url, data=body, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        parsed = json.loads(response.read().decode("utf-8", "replace"))
    if not isinstance(parsed, dict):
        raise ValueError("Ollama returned non-object JSON")
    return parsed


class OllamaBootProbe:
    def __init__(self, endpoint: str, timeout: float = 120.0, *,
                 request_json: Callable = _request_json,
                 run: Callable = subprocess.run,
                 which: Callable = shutil.which):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self._request_json = request_json
        self._run = run
        self._which = which

    def _request(self, method: str, path: str, payload: dict | None = None,
                 error_code: str = "ollama_unreachable") -> dict:
        try:
            return self._request_json(method, self.endpoint + path, payload, self.timeout)
        except Exception:
            raise ProbeFailure(error_code, "Ollama request failed") from None

    def list_models(self) -> list[str]:
        data = self._request("GET", "/api/tags")
        return [str(item.get("name") or "") for item in data.get("models", []) if item.get("name")]

    def _warm(self, model: str, *, vision: bool) -> None:
        payload = {
            "model": model,
            "prompt": llm_readiness.VISION_PROBE_PROMPT if vision else llm_readiness.PROBE_PROMPT,
            "stream": False,
            "keep_alive": KEEP_ALIVE,
            "options": {"num_predict": 1, "temperature": 0},
        }
        if vision:
            payload["images"] = [llm_readiness.TINY_PNG]
        code = "vision_warm_failed" if vision else "text_warm_failed"
        self._request("POST", "/api/generate", payload, error_code=code)

    def warm_text(self, model: str) -> None:
        self._warm(model, vision=False)

    def warm_vision(self, model: str) -> None:
        self._warm(model, vision=True)

    def resident_models(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/api/ps", error_code="residency_probe_failed")
        return [dict(item) for item in data.get("models", []) if isinstance(item, dict)]

    @staticmethod
    def resident_for(model: str, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
        names = [str(item.get("name") or item.get("model") or "") for item in entries]
        for item, name in zip(entries, names):
            if llm_readiness.model_present(model, [name]):
                return item
        return None

    def gpu(self) -> dict[str, Any]:
        executable = self._which("nvidia-smi")
        if not executable:
            return {"available": False, "devices": [], "error": "nvidia-smi not found"}
        try:
            result = self._run(
                [executable, "--query-gpu=name", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except Exception:
            return {"available": False, "devices": [], "error": "nvidia-smi execution failed"}
        devices = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if result.returncode != 0 or not devices:
            return {"available": False, "devices": [], "error": "no NVIDIA devices"}
        return {"available": True, "devices": devices}
