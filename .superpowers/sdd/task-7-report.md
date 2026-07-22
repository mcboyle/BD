# Task 7 report: Full regression, stash deployment, and recovery proof

Date: 2026-07-22 (America/New_York)

Status: **BLOCKED at Step 3 release artifact gate. No archive was produced, uploaded, or deployed. No remote service state was changed.**

## Source identity

- Worktree: `C:\Users\Administrator\Documents\BulkDownloader\.worktrees\ai-boot-readiness`
- Branch: `codex/ai-boot-readiness`
- HEAD/deployed candidate commit: `f9d855104e50078aaf4b6f6fc4d9198adee1d2a5`
- Candidate version: `3.66.815`
- Linked-worktree proof:
  - Git dir: `C:/Users/Administrator/Documents/BulkDownloader/.git/worktrees/ai-boot-readiness`
  - Common dir: `C:/Users/Administrator/Documents/BulkDownloader/.git`

## Step 1: Focused regression

Command:

```powershell
python -m pytest -q tests/test_ai_boot_status.py tests/test_ollama_boot_probe.py tests/test_ai_boot_readiness.py tests/test_ai_boot_service_install.py tests/test_ai_boot_status_api.py tests/test_phase9_6_readiness.py tests/test_ollama_keepalive_warmup.py tests/test_v3_66_656_ideaharden_closers.py tests/test_api.py tests/test_t5_t6_wired.py tests/test_u31_deploy_lint.py tests/test_v3_62_2_guards.py
```

Result: exit `0`; `100 passed in 16.05s`; no focused skips.

Command:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' -n install_service.sh uninstall_service.sh
```

Result: exit `0`; no syntax errors.

Command (Node installation directory prepended to this process's `PATH` because Node/npm are not on the default shell `PATH`):

```powershell
$env:Path='C:\Program Files\nodejs;'+$env:Path
npm test -- --run src/components/ui/AiBootReadinessStatus.test.tsx
```

Result: exit `0`; `1` test file passed; `7` tests passed.

Command:

```powershell
$env:Path='C:\Program Files\nodejs;'+$env:Path
npm run build
```

Result: exit `0`; TypeScript and Vite build succeeded; 2,780 modules transformed. Vite emitted its pre-existing large-chunk advisory.

## Step 2: Committed-diff review

The shell's default `PATH` does not include Git, so these commands used `C:\Program Files\Git\cmd\git.exe` explicitly.

```powershell
git diff origin/main...HEAD --check
git status --short --branch
git log --oneline origin/main..HEAD
```

Result at the gate: exit `0`; diff check emitted no errors; status was clean and `codex/ai-boot-readiness...origin/main [ahead 11]`.

Commits present:

```text
f9d8551 chore: prepare v3.66.815
4e09a82 feat: show AI GPU boot readiness
9d3595c fix: report systemd cleanup in dry run
6e78f17 feat: install AI readiness companion service
05ebdc5 fix: preserve safe AI boot fallback state
632524c feat: warm AI models after boot with retry
e23586f fix: keep probe failures content-free
3320a06 feat: probe Ollama model GPU residency
b2937eb feat: add durable AI boot readiness state
d7d1d83 docs: plan AI GPU boot readiness
0d065df docs: design AI GPU boot readiness
```

## Step 3: Release artifact gate — failed

The required dedicated directory did not exist and was created:

`C:\Users\Administrator\Downloads\bd-ai-boot-readiness-v3_66_815`

Build command (Node installation directory prepended to this process's `PATH` for the SPA prebuild):

```powershell
$releaseDir = 'C:\Users\Administrator\Downloads\bd-ai-boot-readiness-v3_66_815'
if (Test-Path -LiteralPath $releaseDir) { throw "Refusing to reuse existing release directory: $releaseDir" }
New-Item -ItemType Directory -Path $releaseDir | Out-Null
$env:Path='C:\Program Files\nodejs;'+$env:Path
python tools/build_release.py --out $releaseDir --prebuild-spa
```

Result: exit `1`. Release construction stopped at dependency-graph validation:

```text
FAIL: DEPENDENCY_GRAPH.json drift detected.
FAIL: DEPENDENCY_GRAPH.md drift detected.
Run `python tools/dependency_graph.py` to fix.
FAIL: DEPENDENCY_GRAPH.* is stale. Run `python tools/dependency_graph.py` and recommit before retrying the release build.
```

The reported drift included:

- package edge count `1358` -> `1365`
- new `bulk_downloader/ai_boot_status.py` dependency edges
- new `bulk_downloader/ai_boot_readiness.py` imports
- AI provider count `2` -> `3`
- global-config reader count `68` -> `69`

No `BulkDownloader_v3_66_815.zip` exists and therefore no release-verification result or SHA-256 exists.

The failed build also left `downloader_history.db.premigration.bak` untracked in the worktree. To restore a clean worktree without deletion or overwrite, it was moved to the failed build directory as `failed-build-downloader_history.db.premigration.bak` (168 bytes). No destructive cleanup was performed.

## Steps 4-7: Not run

Per the task brief's instruction not to improvise past a failed artifact gate:

- no upload or deployment occurred;
- neither systemd unit was installed/restarted by this run;
- the authorized Ollama stop/start recovery proof was not started;
- no new readiness JSON, `/api/ai/status`, `/api/health`, `nvidia-smi`, or `/api/ps` evidence was collected;
- the OPV gate was not run.

## Required resolution before retry

Regenerate and commit the dependency graph in the isolated branch, review the resulting diff, restore a clean worktree, and choose a new never-before-used dedicated release directory because the required directory above now exists. Then restart Task 7 at Step 1 so all evidence is fresh.

## Blocker resolution: dependency graph regenerated

RED command:

```powershell
python tools/dependency_graph.py --check
```

Result: exit `1`; both graph artifacts were stale. The expected AI boot-readiness import additions changed the internal edge count from `1358` to `1365`.

Regeneration command:

```powershell
python tools/dependency_graph.py
```

Result: exit `0`; regenerated `DEPENDENCY_GRAPH.json` and `DEPENDENCY_GRAPH.md` without hand edits. Review confirmed the diff was limited to those two files and reflected the new `ai_boot_readiness`, `ai_boot_status`, and `ollama_boot_probe` nodes/edges plus derived counts.

GREEN commands:

```powershell
python tools/dependency_graph.py --check
python -m pytest -q tests/test_dependency_graph_in_sync.py
git diff --check
```

Results: all exit `0`; graph check reported `OK: dependency graph in sync (edges=1365)`, the focused test suite passed `10 passed`, and the whitespace check reported no errors.

The regenerated graph artifacts were committed as `505dc88 docs: refresh AI readiness dependency graph`. No build, upload, deployment, or other file modification was performed by this blocker-resolution step.
