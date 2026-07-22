# AI GPU Boot Readiness Design

## Goal

Make the local Ollama integration ready soon after host boot without making
BulkDownloader availability depend on Ollama, the GPU, or either configured
model. The boot path must verify actual GPU use, warm the configured text and
vision models, retry transient failures, and surface a durable degraded state
to operators.

The approved operating policy is:

- BulkDownloader starts normally and never waits for AI readiness.
- Text and vision models are warmed sequentially after boot.
- Successful warm requests use Ollama's existing `10m` keep-alive.
- If vision cannot be made ready, text readiness is restored and retained as a
  usable fallback.

## Current Behavior and Gap

`OllamaProvider.warmup()` currently sends a one-token request for only the text
model. `aiassist.warm_once()` invokes that path on the first inference attempt,
marks the attempt complete before making the request, and intentionally does
not retry. This protects request handling from retry storms, but it means a
transient Ollama or GPU failure can leave the first real AI request cold or
failed until the BulkDownloader process restarts.

`bulk_downloader.llm_readiness` already provides fixed, benign text and vision
probe payloads plus model-presence checks. It does not run automatically after
boot, inspect Ollama residency/VRAM information, retry, or persist readiness.

The generated `bulkdownloader.service` orders itself after `ollama.service`,
but ordering does not mean Ollama is responsive, the models are loaded, or the
runner is using the GPU. The main service therefore remains correctly
available when AI is unavailable, but operators have no durable boot-readiness
result.

## Chosen Architecture

Add a companion command and systemd service rather than putting boot work in
the Flask process or `ExecStartPost`.

The command will be exposed as:

```text
python -m bulk_downloader.ai_boot_readiness
```

The generated `bulkdownloader-ai-ready.service` will run the command as the
same non-root user and from the same working directory as BulkDownloader. It
will use the same optional `.env` file and the persisted `app_config.json`.
The unit is independently enabled at boot and ordered after the network and
Ollama, but `bulkdownloader.service` will not `Require`, `Wants`, or wait for
it.

The companion unit will be `Type=simple` with `Restart=on-failure`. One command
invocation performs a bounded retry cycle and exits successfully only for a
terminal ready or not-applicable result. An exhausted transient or degraded
result exits nonzero, allowing systemd to retry after a fixed cooldown without
holding the main application open or restarting it. `StartLimitIntervalSec=0`
keeps recovery possible after a long Ollama or GPU outage.

This isolation was selected over two alternatives:

1. An application background thread would share failure and resource behavior
   with Flask and would duplicate work across development/reloader processes.
2. `ExecStartPost` would make AI loading part of the main unit's startup result
   and could delay or fail an otherwise healthy downloader.

## Applicability and Configuration

The command reads AI settings from `app_config.json` using a small loader that
does not import and initialize the Flask application. Missing keys use the
same defaults as the application kernel.

The command is applicable only when AI is enabled and the selected provider is
local Ollama. Disabled AI and cloud providers write a `not_applicable` status
and exit zero. The command never pulls a model, changes application settings,
or sends operator/site content.

The first implementation will use conservative constants rather than adding
new UI settings:

- Ollama reachability/model-load timeout: 120 seconds per request.
- Retry delays inside one invocation: 1, 2, 4, 8, and 16 seconds.
- Systemd restart cooldown after an exhausted invocation: 60 seconds.
- Model keep-alive: the provider's existing `10m` value.

These values remain module-level constants so tests can replace them and a
later change can make them configurable without changing the state contract.

## Readiness Sequence

Each invocation performs the following sequence:

1. Load the effective AI provider, endpoint, text model, and vision model.
2. If the feature is not applicable, persist `not_applicable` and exit zero.
3. Confirm the Ollama tags endpoint responds and both configured model names
   are installed. Model-name comparison reuses the existing `:latest`
   normalization in `llm_readiness.model_present`.
4. Check that `nvidia-smi` succeeds for the service user and reports at least
   one NVIDIA device. This is an early diagnostic, not proof of model offload.
5. Warm the text model with the existing fixed benign text probe and
   `keep_alive="10m"`.
6. Warm the vision model with the existing 1x1 content-free PNG and fixed
   benign vision probe, also using `keep_alive="10m"`.
7. Query Ollama `/api/ps` and match resident entries to the configured model
   names. A model is GPU-backed only when its resident entry reports
   `size_vram > 0`. Record `size`, `size_vram`, and their ratio when available;
   do not infer GPU readiness from `nvidia-smi` alone.
8. If both models are resident and GPU-backed, persist `ready` and exit zero.
9. If vision warming or residency fails, warm the text model once more and
   re-check `/api/ps`. Persist `degraded` with text ready and vision failed when
   text remains GPU-backed, then exit nonzero so background recovery continues.
10. For endpoint, model, GPU, or text failures, persist `retrying` during the
    bounded cycle. On exhaustion, persist `degraded` and exit nonzero.

Text is deliberately re-warmed last on the fallback path because Ollama may
evict an earlier resident model under memory pressure. This guarantees the
documented text-first fallback instead of merely assuming both models remain
loaded.

## Failure Classification and Retry

Failures are recorded with stable machine-readable codes and a bounded,
sanitized message. Initial codes are:

- `ollama_unreachable`
- `model_missing`
- `gpu_unavailable`
- `text_warm_failed`
- `vision_warm_failed`
- `text_not_gpu_backed`
- `vision_not_gpu_backed`
- `residency_probe_failed`
- `invalid_config`

All applicable failures are recoverable from the main application's point of
view. The companion exits nonzero after recording the best available state;
systemd retries it in the background. No failure stops, restarts, or marks
`bulkdownloader.service` failed.

Logs and state must not contain API keys, request payloads beyond the names of
the fixed probes, or credentials embedded in URLs. Endpoint values are
sanitized to scheme, host, port, and path before persistence.

## Durable Status Contract

The companion writes `state/ai_boot_readiness.json` relative to the application
working directory. The `state` directory is already runtime-only and writable
by the service user. Writes use a sibling temporary file, `fsync`, and
`os.replace` so the Flask process never reads a partial document.

The JSON document has a versioned, additive schema:

```json
{
  "schema_version": 1,
  "state": "ready",
  "boot_id": "linux-boot-id",
  "updated_at": "2026-07-22T12:00:00Z",
  "attempt": 1,
  "provider": "ollama",
  "endpoint": "http://localhost:11434",
  "keep_alive": "10m",
  "gpu": {
    "available": true,
    "devices": ["Tesla T4"]
  },
  "models": {
    "text": {
      "name": "qwen2.5:7b",
      "state": "ready",
      "resident": true,
      "size": 4680000000,
      "size_vram": 4680000000,
      "gpu_ratio": 1.0
    },
    "vision": {
      "name": "qwen2.5vl:7b",
      "state": "ready",
      "resident": true,
      "size": 5000000000,
      "size_vram": 5000000000,
      "gpu_ratio": 1.0
    }
  },
  "error_code": "",
  "error": ""
}
```

`state` is one of `not_applicable`, `retrying`, `ready`, or `degraded`.
Per-model state is one of `pending`, `ready`, `missing`, `failed`,
`not_resident`, or `cpu_only`. Unknown fields are ignored by readers.

If the file is absent, malformed, from another boot, or older than 10 minutes,
the API reports the boot readiness as `unknown` or `stale`; it does not treat a
previous boot's success as current proof. Reading status never raises into an
HTTP request.

## API and Widget Behavior

`/api/ai/status` retains every existing top-level field and gains a
`boot_readiness` object loaded from the durable status file. This is additive
so existing live tests and clients remain compatible.

The existing AI status widget will show:

- `AI ready (GPU)` when both models are current and ready.
- `Text ready; vision retrying` for the fallback state.
- `AI warming` while the companion is retrying.
- `AI degraded` with the sanitized error code when readiness is unavailable.
- Its existing disabled/cloud-provider presentation for `not_applicable`.

The widget remains informational. It does not disable downloads, block the
application, or trigger model pulls. A refresh of the existing status request
is sufficient to observe recovery; no new websocket or polling channel is
introduced.

## Installation and Removal

`install_service.sh` will generate and enable both
`bulkdownloader.service` and `bulkdownloader-ai-ready.service`. The companion
unit uses the selected Python executable, run user, application directory, and
optional environment file already resolved by the installer. Installation
starts the main service independently, then starts the companion best-effort.
A companion start failure is printed as a warning and does not make the main
installation fail.

`uninstall_service.sh` will stop, disable, remove, reset, and daemon-reload the
companion unit along with the main unit. It will not delete the readiness JSON,
which is harmless diagnostic history and is already covered by normal state
backup/cleanup policy.

## Testing Strategy

Implementation follows red-green test-driven development. Network, process,
clock, sleep, boot ID, and filesystem boundaries are injectable so unit tests
do not require Ollama, systemd, or an NVIDIA GPU.

Focused tests will cover:

- Disabled AI and non-Ollama providers return `not_applicable` without probes.
- Delayed Ollama startup succeeds within bounded retries.
- Missing text or vision models are classified without attempting a pull.
- Text and vision are warmed in order using only fixed benign probes.
- `/api/ps` with positive VRAM marks each resident model GPU-backed.
- CPU-only or absent residency is degraded even when `nvidia-smi` succeeds.
- Vision failure re-warms text last and records the text-ready fallback.
- Retry exhaustion returns nonzero and leaves an atomic degraded status.
- A later invocation replaces degraded state with ready state.
- Malformed, stale, or prior-boot status reads return a safe unknown/stale
  result.
- `/api/ai/status` preserves its existing contract and adds boot readiness.
- The AI widget renders ready, warming, text-only, degraded, and
  not-applicable states.
- Installer and uninstaller lint tests assert companion unit lifecycle,
  non-blocking dependency direction, restart policy, and environment parity.

Existing `llm_readiness`, Ollama keep-alive, API, route-map, deploy-lint, and
widget suites remain in the focused regression set.

## Deployment Verification on `stash`

After automated tests are green:

1. Install the updated units and daemon-reload systemd.
2. Reboot or manually start the companion while leaving BulkDownloader
   running.
3. Confirm `bulkdownloader.service` becomes active without waiting for the
   companion.
4. Confirm the health endpoint remains healthy during an intentional companion
   failure/retry.
5. Confirm the service user can run `nvidia-smi` and the readiness state names
   the Tesla T4.
6. Confirm the text and vision warm probes succeed and `/api/ps` reports
   positive `size_vram` for both configured models.
7. Confirm `/api/ai/status` and the AI widget show `AI ready (GPU)`.
8. Stop Ollama, confirm the companion becomes degraded while BulkDownloader
   stays healthy, restart Ollama, and confirm automatic recovery without
   restarting BulkDownloader.
9. Run the focused AI/service tests, then the normal OPV capture gate before a
   release is declared complete.

## Non-Goals

- Blocking BulkDownloader startup on AI, Ollama, model, or GPU readiness.
- Pulling, deleting, or replacing models automatically.
- Restarting Ollama or BulkDownloader automatically.
- Treating `nvidia-smi` visibility alone as proof of model offload.
- Replacing the existing first-use `warm_once()` guard; it remains a safe
  per-process fallback.
- Adding GPU management for cloud AI providers.
- Keeping models resident indefinitely beyond Ollama's existing 10-minute
  keep-alive.
