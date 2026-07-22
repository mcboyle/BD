# AI GPU Boot Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Warm and verify BulkDownloader's configured local Ollama text and vision models on the GPU after boot, with durable background retries that never block BulkDownloader startup.

**Architecture:** Three focused Python modules separate durable status/config loading, low-level Ollama/GPU probes, and retry orchestration. A companion systemd service runs the orchestrator independently from Flask; the existing AI status API and Integrations widget read the durable result without controlling the service.

**Tech Stack:** Python 3.12 standard library, Ollama HTTP API, NVIDIA `nvidia-smi`, Flask, React 18, TypeScript, TanStack Query, Vitest/Testing Library, pytest, Bash/systemd, PuTTY/Plink deployment to `stash`.

## Global Constraints

- `bulkdownloader.service` starts normally and never waits for AI readiness.
- Warm text first and vision second; both requests use `keep_alive="10m"`.
- A vision failure re-warms text last and records a text-ready fallback.
- Verify model GPU use from Ollama `/api/ps` `size_vram > 0`; `nvidia-smi` visibility alone is not readiness proof.
- Retry delays inside one invocation are exactly 1, 2, 4, 8, and 16 seconds.
- The companion systemd restart cooldown is exactly 60 seconds and remains retryable indefinitely.
- Request timeout is 120 seconds per warm request; readiness state becomes stale after 600 seconds.
- Disabled AI and non-Ollama providers are `not_applicable` and exit successfully.
- Never pull, delete, replace, or select a model automatically.
- Never restart Ollama or BulkDownloader from the readiness command.
- Probes use only the fixed benign text prompt and content-free 1x1 PNG already defined by `llm_readiness`.
- Status/log output contains no API keys, credentials, site content, or URL user information.
- Preserve all existing `/api/ai/status` top-level fields; `boot_readiness` is additive.
- Follow red-green TDD and commit after every independently testable task.
- Preserve unrelated user changes and stage only files named by each task.

---

### Task 1: Durable boot-readiness status and effective config

**Files:**
- Create: `bulk_downloader/ai_boot_status.py`
- Create: `tests/test_ai_boot_status.py`

**Interfaces:**
- Consumes: `global_config.get_config()`, cwd-relative `app_config.json`, Linux boot ID, and `state/`.
- Produces: `load_effective_config(raw: Mapping[str, Any] | None = None) -> dict[str, Any]`.
- Produces: `write_status(document: Mapping[str, Any], path: Path = STATE_PATH, *, now: float | None = None, boot_id: str | None = None) -> dict[str, Any]`.
- Produces: `read_status(path: Path = STATE_PATH, *, now: float | None = None, boot_id: str | None = None) -> dict[str, Any]`.
- Produces: `sanitize_endpoint(endpoint: str) -> str` and `get_boot_id() -> str`.

- [ ] **Step 1: Write the failing status/config tests**

Create `tests/test_ai_boot_status.py` with:

```python
import json
from pathlib import Path

from bulk_downloader import ai_boot_status as status


def test_load_effective_config_uses_app_keys_and_defaults():
    cfg = status.load_effective_config({
        "ai_enabled": True,
        "ai_provider": "OLLAMA",
        "ai_endpoint": "http://user:secret@127.0.0.1:11434/api/",
        "ai_model_text": "text-model",
        "ai_model_vision": "vision-model",
    })
    assert cfg == {
        "enabled": True,
        "provider": "ollama",
        "endpoint": "http://127.0.0.1:11434/api",
        "model_text": "text-model",
        "model_vision": "vision-model",
    }
    assert status.load_effective_config({}) == {
        "enabled": False,
        "provider": "ollama",
        "endpoint": "http://localhost:11434",
        "model_text": "qwen2.5:7b",
        "model_vision": "qwen2.5vl:7b",
    }


def test_write_then_read_current_status(tmp_path):
    path = tmp_path / "state" / "ai_boot_readiness.json"
    written = status.write_status(
        {"state": "ready", "models": {}},
        path,
        now=1_000.0,
        boot_id="boot-a",
    )
    loaded = status.read_status(path, now=1_100.0, boot_id="boot-a")
    assert loaded == written
    assert loaded["schema_version"] == 1
    assert loaded["updated_at"] == "1970-01-01T00:16:40Z"


def test_status_from_prior_boot_or_after_keepalive_is_stale(tmp_path):
    path = tmp_path / "state.json"
    status.write_status({"state": "ready"}, path, now=1_000.0, boot_id="boot-a")
    prior = status.read_status(path, now=1_001.0, boot_id="boot-b")
    expired = status.read_status(path, now=1_601.0, boot_id="boot-a")
    assert prior["state"] == "stale"
    assert prior["stale_reason"] == "previous_boot"
    assert expired["state"] == "stale"
    assert expired["stale_reason"] == "expired"


def test_missing_or_malformed_status_is_safe_unknown(tmp_path):
    missing = status.read_status(tmp_path / "missing.json", boot_id="boot-a")
    bad = tmp_path / "bad.json"
    bad.write_text("{not-json", encoding="utf-8")
    malformed = status.read_status(bad, boot_id="boot-a")
    assert missing == {"schema_version": 1, "state": "unknown", "reason": "missing"}
    assert malformed == {"schema_version": 1, "state": "unknown", "reason": "malformed"}


def test_write_is_atomic_and_never_persists_url_credentials(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    replacements = []
    real_replace = status.os.replace

    def capture_replace(source, target):
        replacements.append((Path(source), Path(target)))
        return real_replace(source, target)

    monkeypatch.setattr(status.os, "replace", capture_replace)
    status.write_status(
        {
            "state": "degraded",
            "endpoint": status.sanitize_endpoint(
                "http://name:password@localhost:11434/private"
            ),
        },
        path,
        now=1_000.0,
        boot_id="boot-a",
    )
    raw = path.read_text(encoding="utf-8")
    assert replacements and replacements[0][0].name.endswith(".tmp")
    assert replacements[0][1] == path
    assert "name" not in raw and "password" not in raw
    assert json.loads(raw)["endpoint"] == "http://localhost:11434/private"
```

- [ ] **Step 2: Run the tests to verify RED**

Run:

```powershell
python -m pytest -q tests/test_ai_boot_status.py
```

Expected: collection fails with `ImportError: cannot import name 'ai_boot_status'`.

- [ ] **Step 3: Implement the status/config module**

Create `bulk_downloader/ai_boot_status.py` with these definitions and behavior:

```python
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
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest -q tests/test_ai_boot_status.py
python -m py_compile bulk_downloader/ai_boot_status.py
```

Expected: `5 passed` and compilation exits `0`.

- [ ] **Step 5: Commit the durable status layer**

```powershell
git add bulk_downloader/ai_boot_status.py tests/test_ai_boot_status.py
git diff --cached --check
git commit -m "feat: add durable AI boot readiness state"
```

Expected: one commit containing only the status module and its tests.

---

### Task 2: Low-level Ollama and NVIDIA probes

**Files:**
- Create: `bulk_downloader/ollama_boot_probe.py`
- Create: `tests/test_ollama_boot_probe.py`
- Modify: `bulk_downloader/llm_readiness.py:18-22`

**Interfaces:**
- Consumes: `llm_readiness.PROBE_PROMPT`, `VISION_PROBE_PROMPT`, `TINY_PNG`, and `model_present()`.
- Produces: `ProbeFailure(code: str, message: str)` with public `.code`.
- Produces: `OllamaBootProbe(endpoint: str, timeout: float = 120.0, request_json=_request_json, run=subprocess.run, which=shutil.which)`.
- Produces methods: `list_models() -> list[str]`, `warm_text(model: str) -> None`, `warm_vision(model: str) -> None`, `resident_models() -> list[dict[str, Any]]`, `gpu() -> dict[str, Any]`, and `resident_for(model: str, entries: list[dict]) -> dict | None`.

- [ ] **Step 1: Write failing probe-contract tests**

Create `tests/test_ollama_boot_probe.py` with:

```python
from types import SimpleNamespace

from bulk_downloader import llm_readiness
from bulk_downloader.ollama_boot_probe import OllamaBootProbe


class FakeHttp:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, payload, timeout):
        self.calls.append((method, url, payload, timeout))
        if url.endswith("/api/tags"):
            return {"models": [{"name": "text:latest"}, {"name": "vision:latest"}]}
        if url.endswith("/api/ps"):
            return {"models": [
                {"name": "text:latest", "size": 100, "size_vram": 100},
                {"name": "vision:latest", "size": 200, "size_vram": 180},
            ]}
        if url.endswith("/api/generate"):
            return {"response": "ok"}
        raise AssertionError(url)


def test_model_listing_warms_text_then_vision_with_fixed_payloads():
    http = FakeHttp()
    probe = OllamaBootProbe("http://localhost:11434/", request_json=http)
    assert probe.list_models() == ["text:latest", "vision:latest"]
    probe.warm_text("text")
    probe.warm_vision("vision")
    generates = [call[2] for call in http.calls if call[1].endswith("/api/generate")]
    assert generates[0] == {
        "model": "text",
        "prompt": llm_readiness.PROBE_PROMPT,
        "stream": False,
        "keep_alive": "10m",
        "options": {"num_predict": 1, "temperature": 0},
    }
    assert generates[1]["model"] == "vision"
    assert generates[1]["prompt"] == llm_readiness.VISION_PROBE_PROMPT
    assert generates[1]["images"] == [llm_readiness.TINY_PNG]


def test_residency_matches_latest_and_reports_vram():
    probe = OllamaBootProbe("http://localhost:11434", request_json=FakeHttp())
    entries = probe.resident_models()
    text = probe.resident_for("text", entries)
    vision = probe.resident_for("vision:latest", entries)
    assert text == {"name": "text:latest", "size": 100, "size_vram": 100}
    assert vision["size_vram"] == 180
    assert probe.resident_for("missing", entries) is None


def test_gpu_probe_uses_service_user_visible_nvidia_smi():
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="Tesla T4\n", stderr="")

    probe = OllamaBootProbe(
        "http://localhost:11434",
        request_json=FakeHttp(),
        which=lambda name: "/usr/bin/nvidia-smi",
        run=run,
    )
    assert probe.gpu() == {"available": True, "devices": ["Tesla T4"]}
    assert calls[0][0] == [
        "/usr/bin/nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits"
    ]


def test_gpu_probe_fails_open_to_structured_unavailable():
    probe = OllamaBootProbe(
        "http://localhost:11434",
        request_json=FakeHttp(),
        which=lambda name: None,
    )
    assert probe.gpu() == {"available": False, "devices": [], "error": "nvidia-smi not found"}


def test_operation_failures_use_stable_codes():
    def broken(method, url, payload, timeout):
        raise TimeoutError("not ready")

    probe = OllamaBootProbe("http://localhost:11434", request_json=broken)
    operations = (
        (probe.list_models, (), "ollama_unreachable"),
        (probe.warm_text, ("text",), "text_warm_failed"),
        (probe.warm_vision, ("vision",), "vision_warm_failed"),
        (probe.resident_models, (), "residency_probe_failed"),
    )
    for operation, args, expected in operations:
        try:
            operation(*args)
        except Exception as exc:
            assert exc.code == expected
        else:
            raise AssertionError(f"{operation.__name__} did not fail")
```

- [ ] **Step 2: Run the tests to verify RED**

Run:

```powershell
python -m pytest -q tests/test_ollama_boot_probe.py
```

Expected: collection fails because `bulk_downloader.ollama_boot_probe` does not exist.

- [ ] **Step 3: Expose the benign image and implement the probe**

In `bulk_downloader/llm_readiness.py`, replace the private image declaration with a public name while retaining the old alias:

```python
TINY_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQ"
            "DwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
_TINY_PNG = TINY_PNG
```

Create `bulk_downloader/ollama_boot_probe.py`:

```python
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
        except Exception as exc:
            raise ProbeFailure(error_code, f"{type(exc).__name__}: {exc}"[:300]) from exc

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
        except Exception as exc:
            return {"available": False, "devices": [], "error": f"{type(exc).__name__}: {exc}"[:300]}
        devices = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if result.returncode != 0 or not devices:
            return {"available": False, "devices": [], "error": (result.stderr.strip() or "no NVIDIA devices")[:300]}
        return {"available": True, "devices": devices}
```

- [ ] **Step 4: Run focused and existing readiness tests**

Run:

```powershell
python -m pytest -q tests/test_ollama_boot_probe.py tests/test_phase9_6_readiness.py tests/test_ollama_keepalive_warmup.py
python -m py_compile bulk_downloader/ollama_boot_probe.py bulk_downloader/llm_readiness.py
```

Expected: all tests pass and compilation exits `0`.

- [ ] **Step 5: Commit the low-level probe**

```powershell
git add bulk_downloader/ollama_boot_probe.py bulk_downloader/llm_readiness.py tests/test_ollama_boot_probe.py
git diff --cached --check
git commit -m "feat: probe Ollama model GPU residency"
```

---

### Task 3: Boot warming, fallback, and bounded retry orchestration

**Files:**
- Create: `bulk_downloader/ai_boot_readiness.py`
- Create: `tests/test_ai_boot_readiness.py`

**Interfaces:**
- Consumes: Task 1 `load_effective_config()`/`write_status()` and Task 2 `OllamaBootProbe`/`ProbeFailure`.
- Produces: `run(config: Mapping[str, Any] | None = None, *, state_path=STATE_PATH, probe_factory=OllamaBootProbe, sleep=time.sleep, retry_delays=RETRY_DELAYS, now=time.time, boot_id=None) -> int`.
- Produces: `main() -> int`; module execution uses `raise SystemExit(main())`.
- Exit `0`: `ready` or `not_applicable`. Exit `1`: exhausted applicable failure, including text-ready/vision-degraded fallback.

- [ ] **Step 1: Write failing orchestration tests**

Create `tests/test_ai_boot_readiness.py` with scripted probes that assert ordering and recovery:

```python
import json

from bulk_downloader import ai_boot_readiness as readiness


CFG = {
    "enabled": True,
    "provider": "ollama",
    "endpoint": "http://localhost:11434",
    "model_text": "text",
    "model_vision": "vision",
}


class ScriptedProbe:
    def __init__(self, *, fail_lists=0, fail_vision=False, cpu_vision=False,
                 gpu_available=True):
        self.fail_lists = fail_lists
        self.fail_vision = fail_vision
        self.cpu_vision = cpu_vision
        self.gpu_available = gpu_available
        self.events = []

    def list_models(self):
        self.events.append("list")
        if self.fail_lists:
            self.fail_lists -= 1
            raise readiness.ProbeFailure("ollama_unreachable", "booting")
        return ["text:latest", "vision:latest"]

    def gpu(self):
        self.events.append("gpu")
        if not self.gpu_available:
            return {"available": False, "devices": [], "error": "driver unavailable"}
        return {"available": True, "devices": ["Tesla T4"]}

    def warm_text(self, model):
        self.events.append("warm_text")

    def warm_vision(self, model):
        self.events.append("warm_vision")
        if self.fail_vision:
            raise readiness.ProbeFailure("vision_warm_failed", "vision failed")

    def resident_models(self):
        self.events.append("ps")
        return [
            {"name": "text:latest", "size": 100, "size_vram": 100},
            {"name": "vision:latest", "size": 200,
             "size_vram": 0 if self.cpu_vision else 200},
        ]

    def resident_for(self, model, entries):
        bare = model.removesuffix(":latest")
        return next((e for e in entries if e["name"].removesuffix(":latest") == bare), None)


def _factory(probe):
    return lambda endpoint, timeout=120.0: probe


def test_disabled_and_cloud_providers_are_not_applicable(tmp_path):
    for cfg in ({**CFG, "enabled": False}, {**CFG, "provider": "openai"}):
        path = tmp_path / (cfg["provider"] + ".json")
        code = readiness.run(cfg, state_path=path, probe_factory=lambda *a, **k: None,
                             retry_delays=(), now=lambda: 1_000.0, boot_id="boot-a")
        assert code == 0
        assert json.loads(path.read_text())["state"] == "not_applicable"


def test_success_warms_text_then_vision_and_requires_gpu_residency(tmp_path):
    probe = ScriptedProbe()
    path = tmp_path / "ready.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(), now=lambda: 1_000.0, boot_id="boot-a")
    body = json.loads(path.read_text())
    assert code == 0
    assert probe.events == ["list", "gpu", "warm_text", "warm_vision", "ps"]
    assert body["state"] == "ready"
    assert body["models"]["text"]["gpu_ratio"] == 1.0
    assert body["models"]["vision"]["size_vram"] == 200


def test_transient_startup_retries_then_recovers(tmp_path):
    probe = ScriptedProbe(fail_lists=1)
    sleeps = []
    path = tmp_path / "retry.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(1,), sleep=sleeps.append,
                         now=lambda: 1_000.0, boot_id="boot-a")
    assert code == 0
    assert sleeps == [1]
    assert json.loads(path.read_text())["attempt"] == 2


def test_retry_exhaustion_persists_degraded(tmp_path):
    probe = ScriptedProbe(fail_lists=2)
    path = tmp_path / "exhausted.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(1,), sleep=lambda seconds: None,
                         now=lambda: 1_000.0, boot_id="boot-a")
    body = json.loads(path.read_text())
    assert code == 1
    assert body["state"] == "degraded"
    assert body["attempt"] == 2
    assert body["error_code"] == "ollama_unreachable"


def test_missing_vision_model_is_marked_missing(tmp_path):
    probe = ScriptedProbe()
    probe.list_models = lambda: ["text:latest"]
    path = tmp_path / "missing.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(), now=lambda: 1_000.0, boot_id="boot-a")
    body = json.loads(path.read_text())
    assert code == 1
    assert body["error_code"] == "model_missing"
    assert body["models"]["vision"]["state"] == "missing"


def test_gpu_absence_is_degraded_before_warming(tmp_path):
    probe = ScriptedProbe(gpu_available=False)
    path = tmp_path / "gpu.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(), now=lambda: 1_000.0, boot_id="boot-a")
    body = json.loads(path.read_text())
    assert code == 1
    assert body["error_code"] == "gpu_unavailable"
    assert "warm_text" not in probe.events and "warm_vision" not in probe.events


def test_vision_failure_rewarms_text_last_and_exits_degraded(tmp_path):
    probe = ScriptedProbe(fail_vision=True)
    path = tmp_path / "degraded.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(), now=lambda: 1_000.0, boot_id="boot-a")
    body = json.loads(path.read_text())
    assert code == 1
    assert probe.events[-2:] == ["warm_text", "ps"]
    assert body["state"] == "degraded"
    assert body["error_code"] == "vision_warm_failed"
    assert body["models"]["text"]["state"] == "ready"
    assert body["models"]["vision"]["state"] == "failed"


def test_cpu_only_vision_is_degraded_even_with_nvidia_smi(tmp_path):
    probe = ScriptedProbe(cpu_vision=True)
    path = tmp_path / "cpu.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(), now=lambda: 1_000.0, boot_id="boot-a")
    body = json.loads(path.read_text())
    assert code == 1
    assert body["error_code"] == "vision_not_gpu_backed"
    assert body["models"]["vision"]["state"] == "cpu_only"


def test_later_invocation_replaces_degraded_with_ready(tmp_path):
    path = tmp_path / "recover.json"
    bad = ScriptedProbe(fail_vision=True)
    good = ScriptedProbe()
    assert readiness.run(CFG, state_path=path, probe_factory=_factory(bad),
                         retry_delays=(), now=lambda: 1_000.0, boot_id="boot-a") == 1
    assert readiness.run(CFG, state_path=path, probe_factory=_factory(good),
                         retry_delays=(), now=lambda: 1_001.0, boot_id="boot-a") == 0
    assert json.loads(path.read_text())["state"] == "ready"


def test_invalid_config_is_persisted_without_constructing_probe(tmp_path):
    path = tmp_path / "invalid.json"
    code = readiness.run(
        {**CFG, "endpoint": "file:///tmp/ollama.sock"},
        state_path=path,
        probe_factory=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("probe must not be constructed")
        ),
        retry_delays=(),
        now=lambda: 1_000.0,
        boot_id="boot-a",
    )
    assert code == 1
    assert json.loads(path.read_text())["error_code"] == "invalid_config"
```

- [ ] **Step 2: Run the orchestration tests to verify RED**

```powershell
python -m pytest -q tests/test_ai_boot_readiness.py
```

Expected: collection fails because `bulk_downloader.ai_boot_readiness` does not exist.

- [ ] **Step 3: Implement orchestration and the module CLI**

Create `bulk_downloader/ai_boot_readiness.py`. Use these constants and helpers exactly:

```python
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
```

Implement one attempt with explicit text-last fallback:

```python
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
```

Implement bounded retry and CLI:

```python
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
```

During GREEN, preserve the partial text-ready model document when retries exhaust; do not replace it with an empty `_base` document.

- [ ] **Step 4: Run orchestration and combined backend tests**

```powershell
python -m pytest -q tests/test_ai_boot_readiness.py tests/test_ai_boot_status.py tests/test_ollama_boot_probe.py tests/test_phase9_6_readiness.py tests/test_ollama_keepalive_warmup.py tests/test_v3_66_656_ideaharden_closers.py
python -m py_compile bulk_downloader/ai_boot_readiness.py
```

Expected: all focused tests pass; the previous `warm_once()` one-shot behavior remains unchanged.

- [ ] **Step 5: Commit the orchestrator**

```powershell
git add bulk_downloader/ai_boot_readiness.py tests/test_ai_boot_readiness.py
git diff --cached --check
git commit -m "feat: warm AI models after boot with retry"
```

---

### Task 4: Install and remove the independent systemd companion

**Files:**
- Modify: `install_service.sh:80-311`
- Modify: `uninstall_service.sh:53-204`
- Create: `tests/test_ai_boot_service_install.py`

**Interfaces:**
- Consumes: `python -m bulk_downloader.ai_boot_readiness` from Task 3.
- Produces: `/etc/systemd/system/bulkdownloader-ai-ready.service`.
- Preserves: no dependency from `bulkdownloader.service` to the companion unit.

- [ ] **Step 1: Write failing service lifecycle/source tests**

Create `tests/test_ai_boot_service_install.py`:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = (ROOT / "install_service.sh").read_text(encoding="utf-8")
UNINSTALL = (ROOT / "uninstall_service.sh").read_text(encoding="utf-8")


def test_companion_unit_is_non_blocking_and_retries_forever():
    assert 'AI_SERVICE_NAME="bulkdownloader-ai-ready"' in INSTALL
    assert "ExecStart=${PYEXE} -m bulk_downloader.ai_boot_readiness" in INSTALL
    assert "Restart=on-failure" in INSTALL
    assert "RestartSec=60" in INSTALL
    assert "StartLimitIntervalSec=0" in INSTALL
    main_unit = INSTALL.split("Description=BulkDownloader (Flask", 1)[1].split("UNIT", 1)[0]
    assert "bulkdownloader-ai-ready" not in main_unit
    assert "ollama.service" not in main_unit


def test_companion_uses_same_user_directory_python_and_env():
    assert "User=${RUN_USER}" in INSTALL
    assert "WorkingDirectory=${APP_DIR}" in INSTALL
    assert "EnvironmentFile=-${APP_DIR}/.env" in INSTALL
    assert "ExecStart=${PYEXE} -m bulk_downloader.ai_boot_readiness" in INSTALL


def test_installer_enables_companion_but_start_failure_is_warning():
    assert 'systemctl enable "${AI_SERVICE_NAME}"' in INSTALL
    assert 'systemctl restart "${AI_SERVICE_NAME}"' in INSTALL
    assert "AI readiness will retry after boot" in INSTALL


def test_uninstaller_stops_disables_removes_and_resets_companion():
    for expected in (
        'AI_SERVICE_NAME="bulkdownloader-ai-ready"',
        'systemctl stop "$AI_SERVICE_NAME"',
        'systemctl disable "$AI_SERVICE_NAME"',
        'rm -f "$AI_UNIT_PATH"',
        'systemctl reset-failed "$AI_SERVICE_NAME"',
    ):
        assert expected in UNINSTALL
```

- [ ] **Step 2: Run service tests and shell syntax to verify RED**

```powershell
python -m pytest -q tests/test_ai_boot_service_install.py
& 'C:\Program Files\Git\bin\bash.exe' -n install_service.sh uninstall_service.sh
```

Expected: pytest fails on the missing companion constants; both existing scripts still parse.

- [ ] **Step 3: Add the companion unit and lifecycle operations**

Near the existing installer service constants, add:

```bash
AI_SERVICE_NAME="bulkdownloader-ai-ready"
AI_UNIT_PATH="/etc/systemd/system/${AI_SERVICE_NAME}.service"
```

In the main `bulkdownloader.service` heredoc, change the unit ordering to remove
Ollama from the main application's boot path:

```ini
[Unit]
Description=BulkDownloader (Flask + Playwright video downloader)
After=network-online.target
Wants=network-online.target
```

Update the adjacent comment to state that only the companion is ordered after
Ollama. The main unit must not contain `ollama.service` or the companion name.

After verifying the main unit file and before `daemon-reload`, write and verify:

```bash
if ! sudo tee "$AI_UNIT_PATH" >/dev/null <<UNIT
[Unit]
Description=BulkDownloader AI GPU boot readiness
After=network-online.target ollama.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=-${APP_DIR}/.env
ExecStart=${PYEXE} -m bulk_downloader.ai_boot_readiness
Restart=on-failure
RestartSec=60
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
UNIT
then
    echo "  ERROR: failed to write $AI_UNIT_PATH"
    exit 1
fi
if [ ! -s "$AI_UNIT_PATH" ]; then
    echo "  ERROR: $AI_UNIT_PATH is missing or empty after sudo tee."
    exit 1
fi
```

After the main unit enables successfully, enable the companion as a required install action:

```bash
if ! sudo systemctl enable "${AI_SERVICE_NAME}"; then
    echo "  ERROR: systemctl enable ${AI_SERVICE_NAME} failed"
    exit 1
fi
```

After the main service is restarted, start the companion best-effort:

```bash
echo "  Starting ${AI_SERVICE_NAME} in the background..."
if ! sudo systemctl restart "${AI_SERVICE_NAME}"; then
    echo "  WARNING: ${AI_SERVICE_NAME} did not become ready."
    echo "  AI readiness will retry after boot; BulkDownloader remains available."
fi
```

In `uninstall_service.sh`, add the companion constants and mirror the existing main-unit lifecycle using exact quoted variables:

```bash
AI_SERVICE_NAME="bulkdownloader-ai-ready"
AI_UNIT_PATH="/etc/systemd/system/${AI_SERVICE_NAME}.service"
```

Add companion stop/disable operations before file removal:

```bash
if systemctl is-active --quiet "$AI_SERVICE_NAME" 2>/dev/null; then
    run_ok sudo systemctl stop "$AI_SERVICE_NAME"
fi
if systemctl is-enabled --quiet "$AI_SERVICE_NAME" 2>/dev/null; then
    run_ok sudo systemctl disable "$AI_SERVICE_NAME"
fi
if [ -f "$AI_UNIT_PATH" ]; then
    run sudo rm -f "$AI_UNIT_PATH"
fi
```

After daemon reload, reset both unit names:

```bash
run_ok sudo systemctl reset-failed "$SERVICE_NAME"
run_ok sudo systemctl reset-failed "$AI_SERVICE_NAME"
```

Also add companion operations to the dry-run action summary so `--dry-run` accurately reports every mutation.

- [ ] **Step 4: Run service lifecycle, syntax, and deploy-lint tests**

```powershell
python -m pytest -q tests/test_ai_boot_service_install.py tests/test_u31_deploy_lint.py
& 'C:\Program Files\Git\bin\bash.exe' -n install_service.sh uninstall_service.sh
```

Expected: all tests pass and both scripts parse with exit `0`.

- [ ] **Step 5: Commit systemd integration**

```powershell
git add install_service.sh uninstall_service.sh tests/test_ai_boot_service_install.py
git diff --cached --check
git commit -m "feat: install AI readiness companion service"
```

---

### Task 5: Surface boot readiness through the API and Integrations widget

**Files:**
- Modify: `bulk_downloader/app_ai.py:14-20`
- Create: `tests/test_ai_boot_status_api.py`
- Modify: `frontend/src/hooks/useIntegrations.ts:159-164`
- Create: `frontend/src/components/ui/AiBootReadinessStatus.tsx`
- Create: `frontend/src/components/ui/AiBootReadinessStatus.test.tsx`
- Modify: `frontend/src/routes/Integrations.tsx:20-30,363-373`

**Interfaces:**
- Consumes: Task 1 `read_status()` result.
- Produces: additive `/api/ai/status.boot_readiness`.
- Produces: `AiBootReadinessStatus({ value }: { value?: AiBootReadiness })`.

- [ ] **Step 1: Write failing API and component tests**

Create `tests/test_ai_boot_status_api.py`:

```python
def test_ai_status_adds_boot_readiness_without_removing_existing_fields(fresh_app, monkeypatch):
    from bulk_downloader import ai_boot_status, aiassist

    monkeypatch.setattr(aiassist, "ai_status", lambda: {
        "ok": True, "enabled": True, "provider": "ollama",
        "configured_models": ["vision", "text"],
    })
    monkeypatch.setattr(ai_boot_status, "read_status", lambda: {
        "schema_version": 1, "state": "ready",
    })
    body = fresh_app.get("/api/ai/status").get_json()
    assert body["ok"] is True
    assert body["configured_models"] == ["vision", "text"]
    assert body["boot_readiness"] == {"schema_version": 1, "state": "ready"}
```

Create `frontend/src/components/ui/AiBootReadinessStatus.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AiBootReadinessStatus } from "./AiBootReadinessStatus";

describe("AiBootReadinessStatus", () => {
  it.each([
    [{ state: "ready" }, "AI ready (GPU)"],
    [{ state: "retrying" }, "AI warming"],
    [{ state: "degraded", models: { text: { state: "ready" }, vision: { state: "failed" } } }, "Text ready; vision retrying"],
    [{ state: "degraded", error_code: "gpu_unavailable" }, "AI degraded: gpu_unavailable"],
    [{ state: "not_applicable" }, "AI boot warm not applicable"],
    [{ state: "stale" }, "AI readiness stale"],
    [undefined, "AI readiness unknown"],
  ])("renders %j as %s", (value, label) => {
    render(<AiBootReadinessStatus value={value} />);
    expect(screen.getByRole("status")).toHaveTextContent(label);
  });
});
```

- [ ] **Step 2: Run both tests to verify RED**

```powershell
python -m pytest -q tests/test_ai_boot_status_api.py
Push-Location frontend
npm test -- --run src/components/ui/AiBootReadinessStatus.test.tsx
Pop-Location
```

Expected: API assertion fails because `boot_readiness` is absent; Vitest fails because the component file is absent.

- [ ] **Step 3: Add the API field, typed hook, and status component**

Change `api_ai_status()` in `bulk_downloader/app_ai.py` to:

```python
@ai_bp.route("/api/ai/status")
def api_ai_status():
    from . import ai_boot_status, aiassist
    payload = dict(aiassist.ai_status())
    payload["boot_readiness"] = ai_boot_status.read_status()
    return jsonify(payload)
```

In `frontend/src/hooks/useIntegrations.ts`, add:

```ts
export interface AiBootModelStatus {
  name?: string;
  state?: string;
  resident?: boolean;
  size?: number;
  size_vram?: number;
  gpu_ratio?: number;
}

export interface AiBootReadiness {
  state?: string;
  error_code?: string;
  models?: {
    text?: AiBootModelStatus;
    vision?: AiBootModelStatus;
  };
}

export interface AiStatus extends Record<string, unknown> {
  enabled?: boolean;
  boot_readiness?: AiBootReadiness;
}
```

Change the hook return type to `useQuery<AiStatus, Error>` and `apiGet<AiStatus>`.

Create `frontend/src/components/ui/AiBootReadinessStatus.tsx`:

```tsx
import type { AiBootReadiness } from "@/hooks/useIntegrations";

export function AiBootReadinessStatus({ value }: { value?: AiBootReadiness }) {
  let label = "AI readiness unknown";
  const state = value?.state;
  if (state === "ready") label = "AI ready (GPU)";
  else if (state === "retrying") label = "AI warming";
  else if (
    state === "degraded" &&
    value?.models?.text?.state === "ready" &&
    value?.models?.vision?.state !== "ready"
  ) label = "Text ready; vision retrying";
  else if (state === "degraded") label = `AI degraded${value?.error_code ? `: ${value.error_code}` : ""}`;
  else if (state === "not_applicable") label = "AI boot warm not applicable";
  else if (state === "stale") label = "AI readiness stale";

  return <p className="text-sm text-ink-3" role="status">{label}</p>;
}
```

Import the component in `frontend/src/routes/Integrations.tsx` and replace the raw status paragraph with:

```tsx
<AiBootReadinessStatus value={aiStatus.data?.boot_readiness} />
```

Keep the existing loading skeleton and model-list button unchanged.

- [ ] **Step 4: Run API, component, frontend build, and route guards**

```powershell
python -m pytest -q tests/test_ai_boot_status_api.py tests/test_api.py tests/test_t5_t6_wired.py tests/test_v3_62_2_guards.py
Push-Location frontend
npm test -- --run src/components/ui/AiBootReadinessStatus.test.tsx
npm run build
Pop-Location
```

Expected: all Python and Vitest tests pass; TypeScript/Vite build exits `0`. No route catalog regeneration is required because the route and methods are unchanged.

- [ ] **Step 5: Commit API and widget visibility**

```powershell
git add bulk_downloader/app_ai.py tests/test_ai_boot_status_api.py frontend/src/hooks/useIntegrations.ts frontend/src/components/ui/AiBootReadinessStatus.tsx frontend/src/components/ui/AiBootReadinessStatus.test.tsx frontend/src/routes/Integrations.tsx
git diff --cached --check
git commit -m "feat: show AI GPU boot readiness"
```

---

### Task 6: Release identity for v3.66.815

**Files:**
- Modify: `tests/test_settings_center_slice4.py:199-201`
- Modify: `bulk_downloader/__init__.py:33`
- Modify: `CHANGELOG.md:7`

**Interfaces:**
- Consumes: completed Tasks 1-5.
- Produces: application version `3.66.815` and matching release notes for the artifact/deployment gate.

- [ ] **Step 1: Advance the pinned version test first**

In `tests/test_settings_center_slice4.py`, change only the expected version:

```python
    from bulk_downloader import __version__
    assert __version__ == "3.66.815", __version__
```

- [ ] **Step 2: Run the version test to verify RED**

```powershell
python -m pytest -q tests/test_settings_center_slice4.py::test_containment_routes_and_version
```

Expected: FAIL showing actual `3.66.814` versus expected `3.66.815`.

- [ ] **Step 3: Bump the runtime version and add exact release notes**

In `bulk_downloader/__init__.py`, set:

```python
__version__ = "3.66.815"
```

Insert this section before v3.66.814 in `CHANGELOG.md`:

```markdown
## v3.66.815 - AI GPU boot readiness

- Add an independent systemd companion that warms the configured Ollama text
  and vision models after boot without blocking BulkDownloader startup.
- Verify live model GPU residency through Ollama, retry transient failures,
  and retain text-only readiness when vision warming fails.
- Persist boot-scoped readiness and surface ready, warming, degraded, and stale
  states through the AI status API and Integrations widget.
```

- [ ] **Step 4: Verify version consistency and release hygiene**

```powershell
python -m pytest -q tests/test_settings_center_slice4.py::test_containment_routes_and_version
python tools/scan_version_pins.py --expect 3.66.815
python tools/precut_check.py
```

Expected: all commands exit `0`; the version-pin scan reports no disagreement.

- [ ] **Step 5: Commit release identity**

```powershell
git add bulk_downloader/__init__.py tests/test_settings_center_slice4.py CHANGELOG.md
git diff --cached --check
git commit -m "chore: prepare v3.66.815"
```

---

### Task 7: Full regression, `stash` deployment, and recovery proof

**Files:**
- Validate: all files committed in Tasks 1-5.
- Deploy: committed branch artifact only; do not copy files from the dirty main checkout.
- Evidence: `/home/mboyle/BulkDownloader/state/ai_boot_readiness.json`, systemd status/journal, `/api/ai/status`, and OPV capture output.

**Interfaces:**
- Consumes: completed Tasks 1-6.
- Produces: fresh proof that BulkDownloader stays healthy while the companion fails/retries and both configured models become GPU-resident after Ollama recovers.

- [ ] **Step 1: Run the focused backend and frontend regression set**

```powershell
python -m pytest -q tests/test_ai_boot_status.py tests/test_ollama_boot_probe.py tests/test_ai_boot_readiness.py tests/test_ai_boot_service_install.py tests/test_ai_boot_status_api.py tests/test_phase9_6_readiness.py tests/test_ollama_keepalive_warmup.py tests/test_v3_66_656_ideaharden_closers.py tests/test_api.py tests/test_t5_t6_wired.py tests/test_u31_deploy_lint.py tests/test_v3_62_2_guards.py
& 'C:\Program Files\Git\bin\bash.exe' -n install_service.sh uninstall_service.sh
Push-Location frontend
npm test -- --run src/components/ui/AiBootReadinessStatus.test.tsx
npm run build
Pop-Location
```

Expected: every command exits `0`, with no skipped focused test.

- [ ] **Step 2: Review the committed diff and run repository checks**

```powershell
git diff origin/main...HEAD --check
git status --short --branch
git log --oneline origin/main..HEAD
```

Expected: clean worktree, the design/plan and six implementation commits are present, and no unrelated file is changed.

- [ ] **Step 3: Build the normal release artifact and verify it**

Build into a dedicated directory that must not already exist, then select the one expected archive:

```powershell
$releaseDir = 'C:\Users\Administrator\Downloads\bd-ai-boot-readiness-v3_66_815'
if (Test-Path -LiteralPath $releaseDir) { throw "Refusing to reuse existing release directory: $releaseDir" }
New-Item -ItemType Directory -Path $releaseDir | Out-Null
python tools/build_release.py --out $releaseDir --prebuild-spa
$archives = @(Get-ChildItem -LiteralPath $releaseDir -Filter 'BulkDownloader_v3_66_815.zip' -File)
if ($archives.Count -ne 1) { throw "Expected exactly one v3.66.815 archive, found $($archives.Count)" }
python tools/verify_release.py --zip $archives[0].FullName
Get-FileHash -Algorithm SHA256 $archives[0].FullName
```

Expected: build and verification exit `0`; record one archive path and one SHA-256 and use that same pair for upload and deployment.

- [ ] **Step 4: Deploy with the established release workflow and install both units**

Upload the verified archive, re-check its digest, deploy through the repository's guarded overlay script, and install both units:

```powershell
$archive = 'C:\Users\Administrator\Downloads\bd-ai-boot-readiness-v3_66_815\BulkDownloader_v3_66_815.zip'
$sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
& 'C:\Program Files\PuTTY\pscp.exe' -batch -load stash $archive 'mboyle@10.0.70.20:/home/mboyle/BulkDownloader_v3_66_815.zip'
& 'C:\Program Files\PuTTY\plink.exe' -batch -load stash "cd /home/mboyle/BulkDownloader && ./scripts/deploy.sh --zip /home/mboyle/BulkDownloader_v3_66_815.zip --expect 3.66.815 --sha $sha --dir /home/mboyle/BulkDownloader && ./install_service.sh"
```

Expected: installer reports `bulkdownloader` running; companion startup may initially be active, activating, or retrying without making installation fail.

- [ ] **Step 5: Prove non-blocking failure and automatic recovery**

First capture healthy main-service state, stop Ollama, and restart only the companion:

```powershell
& 'C:\Program Files\PuTTY\plink.exe' -batch -load stash "sudo -n systemctl stop ollama.service; sudo -n systemctl restart bulkdownloader-ai-ready.service || true; sleep 3; systemctl is-active bulkdownloader.service; curl -fsS http://127.0.0.1:5555/api/health; cat /home/mboyle/BulkDownloader/state/ai_boot_readiness.json"
```

Expected: BulkDownloader is `active`, health contains `"ok":true`, and readiness is `retrying` or `degraded` with `ollama_unreachable`.

Start Ollama and wait up to five minutes for the companion to recover through its own restart policy:

```powershell
$recoveryCheck = @'
sudo -n systemctl start ollama.service
for i in $(seq 1 60); do
    state=$(python3 -c 'import json; print(json.load(open("/home/mboyle/BulkDownloader/state/ai_boot_readiness.json")).get("state", ""))' 2>/dev/null || true)
    [ "$state" = ready ] && break
    sleep 5
done
systemctl is-active bulkdownloader.service
cat /home/mboyle/BulkDownloader/state/ai_boot_readiness.json
curl -fsS http://127.0.0.1:5555/api/ai/status
curl -fsS http://127.0.0.1:5555/api/health
'@
& 'C:\Program Files\PuTTY\plink.exe' -batch -load stash $recoveryCheck
```

Expected: readiness becomes `ready`, both model states are `ready`, the main service remains `active`, and both HTTP endpoints succeed.

- [ ] **Step 6: Verify actual Tesla T4 residency as the service user**

```powershell
& 'C:\Program Files\PuTTY\plink.exe' -batch -load stash "sudo -n -u mboyle nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader,nounits; curl -fsS http://127.0.0.1:11434/api/ps"
```

Expected: `Tesla T4` is listed and `/api/ps` contains both configured model names with positive `size_vram`. Do not accept `nvidia-smi` alone as model-offload proof.

- [ ] **Step 7: Run the normal OPV gate and preserve the report**

```powershell
& 'C:\Program Files\PuTTY\plink.exe' -batch -load stash "cd /home/mboyle/BulkDownloader && ./capture.sh --workers=600 --summary"
```

Expected: exit `0`, zero failed suites/live tests, and only the four previously approved intentional skips unless the test policy has changed. Save the capture summary with the exact deployed commit, archive SHA-256, main service state, companion readiness state, and `/api/ps` evidence.
