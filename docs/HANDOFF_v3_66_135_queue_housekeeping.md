# HANDOFF v3.66.135 — Phase I: `queue_housekeeping` (first operational kind on the harness)

## What shipped
`tools/autonomy_queue_hk.py` registers kind **`queue_housekeeping`** (per-site) on the generic
apply harness — the first `register_apply_kind(...)` after consolidation, and the first operational
target. One import line added to `tools/cockpit_console.py` (registration trigger, alongside
live/staging). New test `tests/test_v3_66_135_queue_housekeeping.py` (14/14). No other code changed.

## Design (two operations, two risk tiers, one kind)
- **I-A — terminal-row GC (live once granted):** delete queue rows with terminal status
  (done/error/failed) older than `BD_QUEUE_HK_GC_AGE_DAYS` (default 7). Zero live-work impact;
  fully reversible (row snapshot → re-`queue_upsert` on rollback).
- **I-B — abandon retry-exhausted/stuck rows (OFF by default):** pending/running with
  `retries >= BD_QUEUE_HK_MAX_RETRIES` and stale > `BD_QUEUE_HK_STALE_HOURS` → `failed`. Behind
  `BD_QUEUE_HK_ABANDON` (default off); enable only after a dry-run week. Reversible (re-queues).

## Architecture decisions (carry forward to J/K)
- **Operational kinds do NOT use the oracle tier-3 gate.** `queue_housekeeping`'s gate is an active
  per-`(site, queue_housekeeping)` grant + an objective per-row predicate. Dark by default. The
  harness `gate` hook makes this per-kind — confirmed the pattern for all future operational kinds.
- **Per-site reuse:** `queue` has `site_id`, so the kind reuses `(site,kind)` grants + reconcile +
  auto-suspend unchanged. Global-scope operational data (webhooks, etc.) still needs a non-site
  grant scope before it can be a kind — not built here.
- **DB access via injectable wrappers** (`_q_load`/`_q_delete`/`_q_upsert`/`_q_mark`, lazy-importing
  `bulk_downloader.db`) so tests run with a fake in-memory queue and no real database. This is the
  template for any future kind that mutates SQLite.
- **Reverser = exact row restore** keyed by the `(site_id, url)` PK. `target_ref = queue::<site>`.
- Reuses the harness chain unchanged: record_change → register_pending (fail-closed review window)
  → validator (lenient on read error; revert on positive miss) → rollback. Transition logged as
  `field=queue_housekeeping`.

## Verification
- New test 14/14; regression (consolidation + H + v1 + cockpit) 96/96; contract+drift 24/24;
  function index 1057 (queue_hk not indexed); endpoint catalog 890 (no route change).
- Cockpit **155 paths / 21 POST** unchanged; Authority view now lists 3 kinds
  (live_site_config, queue_housekeeping, staging_json).
- Full suite from the built zip, 10-phase split: **53 failures — exactly the pre-existing baseline,
  zero regression** (canary + session/db + library/db + SSRF + keyring/honeypot/resolve). test_135
  passes in phase 07.

## Activation path (operator)
Dark until `(site, queue_housekeeping)` is granted (CLI, human-only). Recommended: dry-run week
(`autonomy_queue_hk.dry_run(site)`, no writes) → grant I-A → run via host cron → enable I-B
(`BD_QUEUE_HK_ABANDON=1`) only after the predicate is confirmed safe. See `docs/queue_housekeeping.md`.

## Roadmap position
Phase I done. Per the H→L reassessment: J (library_layout / orphan reconcile) is the next per-site
peer and a thin registration on this same pattern; global kinds (webhook prune) wait on the non-site
grant scope; K (held-out designation assist) is the highest-risk, assist-only, last code phase;
L is the policy decision to raise Class C to auto — not code.
