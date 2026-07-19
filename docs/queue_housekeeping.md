# queue_housekeeping (Phase I) — operator guide

`queue_housekeeping` is the first **operational** apply kind on the generic harness (after
`live_site_config` and `staging_json`). It cleans a site's `queue` table, reversibly, under the
same governed review/rollback chain as every other kind.

## What it does

- **I-A — terminal-row GC (live once granted):** deletes queue rows whose status is terminal
  (`done`/`error`/`failed`) and untouched for `BD_QUEUE_HK_GC_AGE_DAYS` (default **7**). The rows are
  finished — zero live-work impact. The deleted rows are snapshotted and re-inserted on rollback.
- **I-B — abandon retry-exhausted/stuck rows (OFF by default):** moves `pending`/`running` rows with
  `retries >= BD_QUEUE_HK_MAX_RETRIES` (default 10) and no update in `BD_QUEUE_HK_STALE_HOURS`
  (default 24) to `failed`. **Disabled** unless `BD_QUEUE_HK_ABANDON=1`. Reversible (restoring the row
  re-queues the job). Enable only after a dry-run week (below).

## Dark by default — it needs a grant

It is an operational kind, so it does **not** use the oracle tier-3 gate. Its gate is an active
per-`(site, queue_housekeeping)` **grant** plus the objective per-row predicate. No grant ⇒ nothing
runs. Grants are human-only via the CLI:

```
cd ~/BulkDownloader
PYTHONPATH=. venv/bin/python tools/autonomy_grant.py grant <SITE_ID> --kind queue_housekeeping --by mboyle --reason "..."
PYTHONPATH=. venv/bin/python tools/autonomy_grant.py list
PYTHONPATH=. venv/bin/python tools/autonomy_grant.py revoke <SITE_ID> --kind queue_housekeeping --by mboyle --reason "..."
```

Granting `(site, queue_housekeeping)` is a **separate decision** from `(site, live_site_config)` —
the two never imply each other.

## Recommended rollout

1. **Dry-run week (no writes):** see exactly what it would do, per site, before granting/activating:
   ```
   PYTHONPATH=. venv/bin/python -c "from tools import autonomy_queue_hk as q; import json; print(json.dumps(q.dry_run('<SITE_ID>'), indent=2))"
   ```
2. **Grant I-A** for a site once the GC plan looks right. GC is the safe tier (terminal rows only).
3. **Run it** — manually or via cron (host job; `reconcile` still only auto-suspends):
   ```
   cd ~/BulkDownloader && PYTHONPATH=. venv/bin/python -c "from tools import autonomy_queue_hk as q; print(q.housekeep_all(by='cron'))"
   ```
   Every change opens a fail-closed review window; silence reverts at the deadline, reject reverts
   immediately, accept blesses it. Review/rollback via the cockpit Authority view.
4. **I-B only later:** after a week of dry-runs confirms the stuck/exhausted predicate doesn't
   false-positive on legitimately slow large downloads, set `BD_QUEUE_HK_ABANDON=1` (in the
   `override.conf` drop-in) and restart.

## Tunables (env / systemd drop-in)

| Var | Default | Meaning |
|-----|---------|---------|
| `BD_QUEUE_HK_GC_AGE_DAYS` | 7 | I-A: terminal rows older than this are GC'd |
| `BD_QUEUE_HK_ABANDON` | off | I-B master switch (`1`/`true` to enable) |
| `BD_QUEUE_HK_MAX_RETRIES` | 10 | I-B: abandon only at/above this retry count |
| `BD_QUEUE_HK_STALE_HOURS` | 24 | I-B: abandon only if not updated in this long |

## Where it shows up

Read-only in the cockpit **Authority** view (it aggregates over all registered kinds) and the
`/api/authority/*` routes. It adds **no new routes and no POST**. All mutations are visible in the
guardrail change/pending/rollback log and the promotion activity timeline (`field=queue_housekeeping`).
