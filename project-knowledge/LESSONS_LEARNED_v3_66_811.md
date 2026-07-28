# Lessons learned -- OPV / render-gap / inventory-drift (through v3.66.811)

ASCII-only. Durable. Written so the next session does not re-derive what this one
paid for. Each item is a trap that read green somewhere and failed somewhere else
-- the CLAUDE.md section 0 shape. Measure at decision time; do not quote this file
as authority (CLAUDE.md section 1).

---

## 1. Interpreter parity is the master trap (3.11 vs 3.12)

`./venv` MUST be Python 3.12 -- the box/CI interpreter. The sandbox default
`python3` is 3.11. A 3.12-only PEP-701 f-string in
`tools/diag_csrf_bootstrap.py` PARSES FINE on 3.12 but drops that file's edges
when an AST tool parses it under 3.11 -- so graph / parity / index artifacts come
out SHORT, read green in the sandbox, and FAIL the box (this was the v3.66.807
box-only failure).

- Check first: `./venv/bin/python --version`. If it is 3.11 and
  `python3.12` exists, the venv is wrong. `cloud-setup.sh` now removes a stale
  3.11 venv and rebuilds on 3.12; if you are in a session where it did not,
  rebuild it: `rm -rf venv && python3.12 -m venv venv && ./venv/bin/pip install
  -r requirements.txt`.
- Run EVERY band, parity regen, and release build with the 3.12 interpreter.
  With `./venv` on 3.12 there is no separate "3.12 venv" to remember --
  `./venv/bin/python` IS the box interpreter. (This session had to use a
  hand-built `/tmp/venv312` only because `./venv` came up 3.11.)
- (That specific file was fixed at v3.66.818 -- see `LESSONS_LEARNED_v3_66_818.md`
  sections 3 and 10. The interpreter rule stands: `./venv` is 3.12 and IS the box
  interpreter; there is no `.venv`, and a command naming one exits 127.)

## 2. reports/gui_parity_inventory.{json,md} is GENERATED + GITIGNORED

It is NOT committed (`.gitignore: reports/*` with three `!` baseline exceptions;
the inventory is not one). It is rebuilt by `tools/gui_parity_inventory.py` and
bundled into the release `.zip`.

- Adding ANY `tools/*.py` (or a route) drifts the inventory item-set.
- `build_release.py` only CHECKS it via the G12 ROUTE-COUNT gate. A new TOOL is
  not a route, so G12 passes -- the stale inventory ships -- and only the
  full-suite `test_v3_66_302_gui_parity_reconcile ::
  test_shipped_inventory_matches_live_regen_itemset` catches it on the box
  (`only-regen=['<newtool>']`). That is exactly how v3.66.811 shipped a `.zip`
  carrying `opv_guide_lint.py` next to a pre-`opv_guide_lint` inventory.
- FIX, every time you add a `tools/*.py`:
  `./venv/bin/python tools/gui_parity_inventory.py` (regenerates json + md).
  Nothing to commit -- the artifact is gitignored, which is exactly why no deploy
  ever refreshes it.
- Diagnose by inspecting the actual artifact, not the summary: read the item-set
  out of the deployed tree's `reports/gui_parity_inventory.json`. (In 2026-07 this
  meant `unzip -j <zip> '*gui_parity_inventory.json' -d /tmp/x` against the release
  zip; the box now deploys by `git fetch origin main && git reset --hard
  origin/main`, operator-confirmed 2026-07-27, so read the file on disk instead.
  The trap is unchanged either way: `git reset --hard` does not regenerate a
  gitignored artifact and `git clean -fd` does not remove one -- that needs `-x`.)

## 3. The render-gap: "full exposure" that renders nothing

`config_surface_inventory` marks a GLOBAL config key `gui_exposure="full"` when
the key STRING appears in any `frontend/src/*.ts*` file. `settingsSchema.ts`
satisfies that alone -- but `Settings.tsx` renders global controls from EXPLICIT
hand-written JSX. So a key can read "full" with ZERO rendered control.

- Verify `refs_in_Settings.tsx >= 1` (the stash report emits this per key).
- Add explicit JSX in `Settings.tsx` (mirror `captcha_takeover_mode`) PLUS a
  RED-first test that asserts a control renders, not just that the string exists.
- PER-SITE keys are different: they render via the schema-driven
  `SiteSettings.tsx`, so a schema entry IS the control there.

## 4. Phantom env var trap

`config_surface_inventory` scans ALL files for `BD_[A-Z0-9_]+`. A LITERAL such
token in a `.sh`/`.py` (even inside a regex or comment) is ledgered as a config
env var -> `open_runtime_tunable` rises -> parity gates fail. Never write a bare
`BD_<NAME>` token in a tool/script unless it is a real, intended config var.
(`opv_guide_lint.py` once carried `BD_BASE` inside a curl regex and opened five
parity failures.)

## 5. Sandbox service OOM is per-bash-call, not per-service

The Flask service OOM-dies BETWEEN separate bash calls but runs fine WITHIN a
single call. To live-test an endpoint: start the service, poll readiness with a
`curl` loop (never `sleep` as an event), probe, and tear down -- ALL in one bash
invocation. Launch it wired:
`from bulk_downloader.db import db_init; db_init(); from bulk_downloader.app
import app, _load_app_config; _load_app_config(); app.run(port=<p>,
threaded=True, use_reloader=False)` (route registration + `_load_app_config()`
happen at import).

## 6. Analyzer captures: host derivation + the durable index

`/api/analyzer/pin` needs a capture whose NAME encodes a host
(`{host.with.a.dot}_{siteid}_{YYYYMMDD}.wacz`). A redacted / hash-named capture
reports `host=""` and pin 400s `host required`. The picker serves from a durable
SQLite index (`db_captures_all`) that reconciles-from-disk only when empty -- to
force a fresh rescan that surfaces a newly-seeded capture, move
`downloader_history.db` aside so `db_init` starts empty, then restore it.
`DRAFTS_DIR = templates/drafts`; a pin writes `<host>.template-draft.json` with
`status=draft_review_required`, `enabled=False`.

## 7. Guard files and the merged-PR workflow

- 7 SHA-pinned guards must stay byte-identical (CLAUDE.md section 2).
  `build_release.py` IS a guard; `cloud-setup.sh` and `gui_parity_inventory.py`
  are NOT.
- A merged PR is finished. Restart the branch from `origin/main`
  (`git checkout -B <branch> origin/main`), commit follow-ups there -> a NEW PR.
  `--force-with-lease` is fine when the old branch is fully merged. Never stack
  new commits on merged history.

## 8. The Unverified-commit stop-hook

GitHub's squash-merge commit is authored by `GitHub <noreply@github.com>` and
will always read Unverified. That is NOT a local commit to rewrite -- never
`amend`/`rebase` merged `main`. The real fix is preventive: set the git identity
(`user.email=noreply@anthropic.com`, `user.name=Claude`) so YOUR future commits
are attributable. `cloud-setup.sh` now does this at session start.

## 9. Verify-then-act, always

The inventory failure was diagnosed by opening the actual shipped `.zip` and
counting its item-set -- not by trusting a register or a summary. Re-derive
status from source; run the tool; paste the real output (CLAUDE.md sections 1,
10).
