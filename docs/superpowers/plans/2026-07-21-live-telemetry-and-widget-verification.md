# Live Telemetry and Widget Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make long-running downloads report truthful health, heartbeat, and live throughput; remove safely-fixable test skips; and verify every dashboard widget through automated and live checks.

**Architecture:** Normalize runner status at the health boundary, then derive liveness and throughput from actual byte progress rather than completion-only state. Keep release-only security skips intact, make two environment-independent tests deterministic, and add catalog-wide widget contracts plus rendered browser validation.

**Tech Stack:** Python 3, Flask, pytest-compatible test runner, React 18, TypeScript, Vitest, Playwright/Browser plugin.

## Global Constraints

- Follow red-green-refactor: every production behavior change must have a failing regression test first.
- Do not stop site `026255e0`; its queue must keep downloading toward the 2 TB quota.
- Do not install or enable `bd_dev_inspect` in a release deployment.
- Do not run real-Postgres cutover tests against production; they execute destructive table cleanup.
- Do not weaken or replace missing-corpus integration assertions with synthetic pass-throughs.
- Preserve compatibility with legacy runner status keys while preferring the current `counts` schema.
- A worker is live only when its mapped job's byte count advances; message-only updates must not refresh liveness.
- Live throughput must decay to zero when samples are stale and must not reuse completion EWMA as current speed.
- Browser fixtures must be local/deterministic and must not skip navigation failures after the environment is usable.
- Widget verification covers all 36 catalog widgets and all 5 legacy home widgets.

---

### Task 1: Health status schema adapter

**Files:**
- Modify: `bulk_downloader/app_health.py`
- Modify: `tests/test_d3_u2_v2_endpoints.py`
- Create: `tests/test_live_telemetry.py`

**Interfaces:**
- Produces: `_runner_queue_counts(status: dict) -> tuple[int, int]`, returning `(pending, running)` from `counts` with fallback to top-level `queued`/`active`.

- [ ] Add failing tests proving `/api/health` and `/api/health/v2` aggregate `counts.pending=7` and `counts.running=1`, plus a legacy-key compatibility case.
- [ ] Run the focused tests and confirm current code returns zero for the current-schema fake.
- [ ] Add the shared adapter and use it in both health loops.
- [ ] Run focused endpoint tests and commit `fix: report live runner counts in health endpoints`.

### Task 2: Byte-driven worker liveness and live throughput

**Files:**
- Modify: `bulk_downloader/runner.py`
- Modify: `bulk_downloader/runner_transport.py`
- Modify: `bulk_downloader/app_dashboard.py`
- Modify: `tests/test_live_telemetry.py`
- Modify: `tests/test_v3_43_24_reliability.py`

**Interfaces:**
- Produces: `_worker_current_urls: dict[int, str]` guarded by `_worker_heartbeats_lock`.
- Produces: per-job private progress samples updated only when `file_size` increases.
- Produces: `_current_throughput_bps(now: float | None = None) -> float`, summing fresh running-job samples and returning zero for stale or idle samples.

- [ ] Add failing behavioral tests: byte advance refreshes the mapped worker heartbeat; unchanged bytes do not; worker URL mapping clears in `finally`; start clears stale worker tracking; current throughput is positive after two timed byte advances and zero after freshness expiry.
- [ ] Run focused tests and confirm failures match missing behavior.
- [ ] Initialize the worker URL/progress maps, map the URL immediately around `_process_one`, and clear it in `finally` without lock inversion.
- [ ] Extend `_update_job` so genuine byte advancement records a timestamp/rate and refreshes only the mapped worker heartbeat.
- [ ] Route sequential progress through `_update_job(..., file_size=downloaded)` and ensure direct/multi-connection paths also include advancing `file_size`; retain parallel/JD/qB behavior.
- [ ] Return fresh throughput from `get_status()` and dashboard aggregation while retaining completion EWMA only for tuning/ETA.
- [ ] Run focused and adjacent runner/dashboard suites and commit `fix: report progress during long downloads`.

### Task 3: Convert deterministic environment-independent skips

**Files:**
- Modify: `tests/test_dom_recorder_asi.py`
- Modify: `tests/test_v3_66_68_capture_template.py`

**Interfaces:**
- Produces: a local Playwright route fixture that preserves document navigation/init-script behavior without `data:` navigation.
- Produces: deterministic capture-template input that always infers a `content_id` slot.

- [ ] Reproduce the current Playwright navigation skip and `content_id` fixture skip.
- [ ] Replace the `data:` navigation with a locally fulfilled HTTP route and turn navigation failure into a real failure after browser launch succeeds.
- [ ] Adjust only the synthetic content-ID fixture shape so the drift test reaches its assertions; do not loosen the assertion.
- [ ] Run both files and confirm the two skips become passes; commit `test: make browser and content id fixtures deterministic`.

### Task 4: Validate and install the user-provided capture corpus

**Files:**
- Remote source: `/home/mboyle/templates`, `/home/mboyle/organized`, and other `/home/mboyle` subdirectories
- Remote staging: a new mode-0700 `/home/mboyle/.wacz-stage.*` directory
- Remote test corpus: `/mnt/user-data/uploads` and `/home/claude/corpus/wacz`
- Report: `.superpowers/sdd/wacz-processing-report.md`

**Interfaces:**
- Consumes: SHA-addressed, CRC-valid WACZ inputs and corresponding JSON only.
- Produces: strictly scrubbed, floor-clean, atomically installed test fixtures; sources remain untouched.

- [ ] Re-resolve every candidate by basename and SHA because another workflow is moving files into `/home/mboyle/organized`; reject ambiguous hashes and the three invalid archives.
- [ ] Stage copies with preserved metadata, verify source-before/source-after/staged SHA equality, and process only staged files.
- [ ] Run the project strict capture scrubber, require ZIP CRC success and `scan_floor_secrets(...) == []`, and never print raw URLs, cookies, tokens, or pre-redaction values.
- [ ] Derive `capA.json` only by extracting and strictly scrubbing `archive/capture.json` from the validated `capA.wacz`; do not synthesize or hand-edit evidence.
- [ ] Install validated fixtures by copy-to-temp, SHA verification, and atomic rename; back up any existing target and never delete sources.
- [ ] Run the ten capture-test modules, record which of the 54 skips became passes and which semantic assertions remain honestly blocked, then process candidate WACZ/JSON through read-only draft/preflight stages without auto-promoting reviewed templates.

### Task 5: Activate the real-PostgreSQL integration tests

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: a `postgres-integration` CI job with an isolated PostgreSQL 16 service and `MOD3_PG_TEST_DSN` scoped to that disposable database.

- [ ] Add a PostgreSQL 16 service with a health check and non-production test credentials.
- [ ] Install the repository development requirements plus `psycopg[binary]>=3.1,<4` in the job only.
- [ ] Run exactly `test_v3_66_800_mod3_dual_write.py`, `801_mod3_shadow_read.py`, `803_mod3_migration_rehearsal.py`, and `804_mod3_cutover.py` so all 15 real-PostgreSQL tests execute rather than skip.
- [ ] Validate workflow syntax and run the same four files against a disposable local/container database when available; commit `ci: run real postgres integration tests`.

### Task 6: Verify every dashboard widget

**Files:**
- Create: `frontend/src/lib/widgetCatalog.test.tsx`
- Modify: `frontend/src/routes/Home.onboarding.test.tsx` or create `frontend/src/routes/Home.widgets.test.tsx`
- Modify: `frontend/e2e/d3_smoke.spec.ts`

**Interfaces:**
- Consumes: `WIDGETS`, `WIDGETS_BY_ID`, the five `LEGACY_WIDGET_IDS`, `KPICard`, widget selection/picker hooks, `/api/widgets/data`, and `/api/dashboard/v2`.

- [ ] Add catalog contract tests proving exactly 36 unique widget IDs, valid categories, total spec evaluation for complete/null-heavy data, and successful `KPICard` rendering for every spec.
- [ ] Add a Home test that selects every catalog widget and proves all 36 catalog labels plus the five legacy widgets render without a runtime exception.
- [ ] Add/extend an end-to-end smoke test that opens the widget picker, verifies all catalog entries are discoverable, selects a representative widget, and observes the tile state change.
- [ ] Run Vitest, TypeScript build, and Playwright against the live stash dashboard; commit `test: cover every dashboard widget`.

### Task 7: Resolve or isolate corpus semantic failures

**Files:**
- Modify only if evidence requires: capture-test fixture root helper/tests and strict redaction support
- Remote fixtures: the 16 installed Task 4 targets and private staging evidence

**Interfaces:**
- Produces: zero default-suite failures; an explicit integration-lane result for every supplied fixture; no weakened semantic assertion or privacy floor.

- [ ] Capture sanitized actual-versus-expected diagnostic categories for all 15 failures without retaining capture values.
- [ ] Test whether an alternative supplied floor-clean artifact or privacy-preserving structural transform satisfies each semantic contract; do not synthesize outcomes merely to make tests green.
- [ ] Fix verified processing/source-selection defects with failing tests first.
- [ ] If a contract cannot survive the approved privacy floor, move it behind an explicit opt-in corpus root/lane and preserve the failing integration evidence; the normal release suite must not auto-discover incompatible private fixtures.
- [ ] Re-run the ten capture modules both in the normal release environment and in the explicit corpus lane, reporting passes/failures/skips separately.

### Task 8: Full verification, deployment, and live proof

**Files:**
- Regenerate: `FUNCTION_INDEX.md`
- Update: `.superpowers/sdd/progress.md`

- [ ] Run Python focused tests, the project capture suite, frontend Vitest, frontend build, and widget E2E tests.
- [ ] Confirm the remaining skip count and document why each surviving category is intentionally external or security-gated.
- [ ] Generate a whole-branch review package and obtain spec-compliance and code-quality approval.
- [ ] Deploy the exact committed overlay to stash without replacing the preserved VNC hotpatch, restart the system service, explicitly resume site `026255e0`, and verify the quota watchdog remains active.
- [ ] Prove `/api/health` reports the real queue/activity, `/api/status` reports nonzero fresh throughput with no false hung worker while bytes advance, and the active `.part` continues growing.
- [ ] Use the Browser plugin on desktop and mobile to check page identity, nonblank render, console health, all widget surfaces, picker interaction, and screenshot evidence.
