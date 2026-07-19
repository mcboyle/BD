# HANDOFF v3.66.136 — Phase J: `library_reconcile` (governed file-index cleanup)

## What shipped
`tools/autonomy_library_reconcile.py` registers kind **`library_reconcile`** (per-site) on the
generic harness — the second operational kind after `queue_housekeeping`. Two helpers added to
`bulk_downloader/library.py` (`library_snapshot`, `library_restore`, `library_missing_for_site`).
One import line in `tools/cockpit_console.py` (registration trigger). New test
`tests/test_v3_66_136_library_reconcile.py` (13/13). No other code changed.

## What it does
Removes `library` rows whose file is gone (`file_exists=0`) and was last seen present more than
`BD_LIB_RECONCILE_MISSING_DAYS` ago (default 30). The media file is **never** touched
(`also_delete_file=False`, always) — only the stale index row. Fully reversible.

## How the spec changed during the build (grounded)
- **No migration / no scanner edit.** The spec feared J-B needed a `missing_since` column + a
  scanner hook. Grep showed the mark-missing pass (`UPDATE library SET file_exists=0`) does NOT
  touch `last_scanned`, so `last_scanned` already records "last seen present." A mount blip
  reappears on the next scan and refreshes it, so only genuinely-gone files accumulate missing-age.
  Debounce is `last_scanned < now - N days`. The riskiest piece of J evaporated.
- **Orphan import deferred on principle, not difficulty.** Imported orphans are unattributed
  (`site_id=''`) and don't fit the per-`(site,kind)` grant — they belong to the not-yet-built global
  scope (same as webhooks). So the per-site J is the missing-row reconcile only; orphan import is
  out, documented.
- File MOVES stay with `storage_tier` (already auto-moves, opt-in). NFO/sidecar regen unverified, out.

## The one hard part: exact reversal of a child-FK row (carry to K)
A `library` row's autoincrement `id` is an FK target — `library_tags ON DELETE CASCADE` and
`history.library_id` back-ref. So `library_delete` cascades the tags and nulls the back-ref. The
reverser therefore can't just re-insert (a new id would orphan the tags). `library_snapshot(id)`
captures `{row (all columns), tags}`; `library_restore(snapshot)` re-inserts the row with the
**original id** (INTEGER PRIMARY KEY = explicit rowid insert), re-inserts the tag junction rows, and
re-points `history.library_id`. Both helpers are generic over columns, so they survive schema
additions. This is the template for any future kind that removes a row with children.

## Architecture (consistent with I)
- **Operational gate, no oracle tier-3.** Gate = active `(site, library_reconcile)` grant + the
  aged-missing predicate. Dark by default. Four safety layers on a removal: grant required, 30-day
  debounce, file never touched, exact-reversible.
- DB access via injectable `_lib_*` wrappers (lazy-import `bulk_downloader.library`) → DB-free tests.
- `last_scanned` is REAL epoch, so J uses epoch math (no UTC-string mixing).
- Reuses the harness chain unchanged: record_change → register_pending (fail-closed review) →
  validator (lenient on read error) → rollback. Transition logged `field=library_reconcile`.
- `dry_run(site)` reports the would-remove set with no writes.

## Verification
- New test 13/13; regression (I + consolidation + H + v1 + cockpit) 110/110; contract+drift 24/24;
  function index 1057 (library.py + tools not indexed); endpoint catalog 890 (no route change).
- Cockpit **155 paths / 21 POST** unchanged; Authority now lists 4 kinds.
- Full suite from the built zip, 10-phase split: **53 failures — exactly the baseline, zero
  regression** (the library area, phase 5, stayed at 15 — the added helpers broke nothing). test_136
  passes in phase 07.

## Activation path (operator)
Dark until `(site, library_reconcile)` is granted (CLI, human-only, `--kind library_reconcile`).
Recommended: observation week via `autonomy_library_reconcile.dry_run(site)` (no writes) → grant →
run via host cron. Conservative 30-day default; tune with `BD_LIB_RECONCILE_MISSING_DAYS`. Never
`also_delete_file=True`. See `docs/library_reconcile.md`.

## Roadmap position
Phases through J done (J = this). Remaining: K (held-out evidence designation assist — feeds H's
tier-3 gate; highest-risk, assist-only, last code phase); L is not code (the policy decision to
raise Class C to auto). The global grant scope is still the prerequisite for orphan-import and any
global/webhook kind.
