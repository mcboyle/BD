# library_reconcile (Phase J) — operator guide

`library_reconcile` is the second operational apply kind (after `queue_housekeeping`). It removes
stale **library index rows** — rows for media files that have gone missing — reversibly, under the
same governed review/rollback chain as every other kind. **It never touches the media files
themselves.**

## What it does
Removes `library` rows where `file_exists=0` AND the file was last seen present more than
`BD_LIB_RECONCILE_MISSING_DAYS` (default **30**) ago. The age is measured from `last_scanned`, which
the scanner only refreshes when it actually sees the file — so a temporary mount/NAS blip (file
reappears on the next scan) never counts toward the age. Only genuinely-gone files are reconciled.

## It never deletes files
Removal is always `also_delete_file=False`. The file on disk is your data; this kind only cleans the
index row that points at an already-absent file. And it's reversible — the row, its tags, and the
history link are snapshotted and restored on rollback.

## Dark by default — it needs a grant
Operational kind: no oracle tier-3. Gate = active per-`(site, library_reconcile)` **grant** + the
aged-missing predicate. No grant ⇒ nothing runs. Grants are human-only via the CLI:

```
cd ~/BulkDownloader
PYTHONPATH=. venv/bin/python tools/autonomy_grant.py grant <SITE_ID> --kind library_reconcile --by mboyle --reason "..."
PYTHONPATH=. venv/bin/python tools/autonomy_grant.py list
PYTHONPATH=. venv/bin/python tools/autonomy_grant.py revoke <SITE_ID> --kind library_reconcile --by mboyle --reason "..."
```

Granting `(site, library_reconcile)` is separate from any other kind's grant.

## Recommended rollout
1. **Observation week (no writes):** see exactly what would be removed, per site:
   ```
   PYTHONPATH=. venv/bin/python -c "from tools import autonomy_library_reconcile as l; import json; print(json.dumps(l.dry_run('<SITE_ID>'), indent=2))"
   ```
2. **Grant** the site once the list looks right (all genuinely-gone files).
3. **Run** it — manually or via host cron:
   ```
   cd ~/BulkDownloader && PYTHONPATH=. venv/bin/python -c "from tools import autonomy_library_reconcile as l; print(l.reconcile_all(by='cron'))"
   ```
   Each change opens a fail-closed review window (silence reverts at the deadline, reject reverts
   immediately, accept blesses). Review/rollback via the cockpit Authority view.

## Tunables
| Var | Default | Meaning |
|-----|---------|---------|
| `BD_LIB_RECONCILE_MISSING_DAYS` | 30 | only remove rows missing (last-seen) longer than this |

## Scope (what it does NOT do)
- **No orphan import** (adding rows for on-disk files the app doesn't know about) — those rows are
  unattributed to a site and await the global grant scope. Use the manual library scan for now.
- **No file moves** — `storage_tier` handles relocation (opt-in, with dry-run/symlink).
- **No metadata/NFO writing.**

## Where it shows up
Read-only in the cockpit **Authority** view and `/api/authority/*`. Adds **no new routes, no POST**.
All removals/restores are visible in the guardrail change/pending/rollback log and the promotion
timeline (`field=library_reconcile`).
