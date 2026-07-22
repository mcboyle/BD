# BulkDownloader AI GPU Boot-Readiness Validation Report

**Validation date:** 2026-07-22

**Overall result:** COMPLETE / PASS

**Deployment target:** `stash` - `/home/mboyle/BulkDownloader`

**Validated runtime commit:** `62df20ab2c59e7a738d9f6bbe1873101c1b2c632`

**Validated version:** `3.66.815`

**Validated build stamp:** `912df53ac064`

**Release archive SHA-256:** `6635b5e27ec113f62f95c8657ce768513a794220c8f0619e46c505c58105ad60`

## Executive result

The AI GPU boot-readiness companion is installed and enabled. It does not
block the main BulkDownloader service when Ollama is unavailable, retries
automatically, warms both configured models after recovery, and proves actual
Tesla T4 residency through positive `size_vram` values. The final stash OPV
capture exited zero with no unit failures, live failures, or live warnings.

## Release and deployment gates

| Gate | Result |
|---|---:|
| Release inventory / source-to-ZIP verification | **3,317 / 3,317 files; PASS** |
| Raw-capture posture gate | **PASS** |
| Critical frontend artifact gate | **PASS** |
| SPA production build | **PASS** |
| Deploy-script tests | **11 passed** |
| Guarded SHA / health / backend deployment | **PASS** |
| Relative `--dir BulkDownloader` live deployment | **PASS** |
| Independent final code review | **0 Critical / 0 Important / 0 Minor** |

The user explicitly waived the extracted-ZIP test-suite gate on 2026-07-22.
The release was therefore built with the release tool's documented
`--skip-tests` option. All other release gates above and the full on-stash OPV
suite remained required and passed.

## Failure and recovery proof

Ollama was deliberately stopped and the companion restarted. During the
outage:

- `bulkdownloader.service` remained `active`.
- `/api/health` remained `ok=true`, `db_ok=true`, version `3.66.815`.
- Readiness persisted `state=retrying` with
  `error_code=ollama_unreachable`.

After a normal `systemctl start ollama.service`, the existing companion process
recovered without manual model calls. Readiness became `ready`; the text and
vision models both became `ready` and fully GPU-backed. The final post-OPV
status repeated the proof on attempt 1.

## GPU and model-residency proof

- GPU: `Tesla T4`, 15,360 MiB total VRAM.
- Text model: `qwen2.5:7b`, `size_vram=4748056984`, `gpu_ratio=1.0`.
- Vision model: `qwen2.5vl:7b`, `size_vram=5487093349`, `gpu_ratio=1.0`.
- `/api/ai/status`: `ok=true`, boot readiness `state=ready`.
- Companion unit: enabled; last execution `Result=success`,
  `ExecMainStatus=0`.

The companion is a successful one-shot unit, so `inactive/dead` after a
successful run is expected; it is not a failed service state.

## Final OPV evidence

The final command ran with `DISPLAY=:99`, the host's existing Xvfb/noVNC
display, so the headed-browser check exercised the real visible browser path.

```text
CAPTURE VERDICT: PASS - unit 12626 pass/0 fail/73 skip; live 35 pass/0 warn/0 fail
```

Additional live evidence:

- L2 headed Chromium launch passed on Xvfb `:99`.
- L19 AI text roundtrip passed in 2,020 ms.
- L28 preserved 200 sampled queue URLs across a service restart.
- L30 found one consistent VPN tunnel inventory.
- HTTP smoke, dev routes, CSRF diagnostic, service install/start, and T51
  regenerate-goldens dry-run completed successfully.

Evidence bundle:

- Remote: `/tmp/bd_capture.tar.gz`
- Local:
  `C:\Users\Administrator\Downloads\bd-ai-boot-readiness-v3_66_815-retry8\bd_capture-62df20a.tar.gz`
- SHA-256:
  `fa312ce7e5137a35a5bd78961073bd67ede6baf52fbc8eb8ff0a43df38723121`

## Fixes required to reach green

1. Made versionless health responses remain inside the deploy retry loop.
2. Ran the backend probe from the selected install directory and preserved its
   failure status without exposing raw stderr.
3. Canonicalized relative deploy and venv paths before the backend probe.
4. Refreshed generated dependency, route, PIN, and import-graph inventories.
5. Declared the seven intended AI-readiness import edges in the frozen import
   baseline.
6. Ran the final live lane with the host's configured Xvfb display so headed
   browser validation produced a pass rather than an environment warning.

## Final disposition

**AI GPU boot readiness: COMPLETE.**

**Stash deployment: HEALTHY.**

**Final OPV capture: PASS.**
