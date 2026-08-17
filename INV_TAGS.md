# INV_TAGS.md — locality index for inline `# INV-<ID>` tags

VERSION: 3.64.4
LAST_VERIFIED: A.1 restore, 2026-05-23
KB_VERSION: 2

This historical locality index cross-references load-bearing source lines that
carry inline `# INV-<ID>` tags. Root `INVARIANTS.json`, current source, and
point-of-use tests are authoritative; this prose index is not.

Regenerate with:
```
grep -rn "# INV-" bulk_downloader/ 2>/dev/null | sort
```

Regression test: `tests/test_inv_tags_not_regressed.py` asserts the
tag count does not decrease between releases. Raise the floor
(`INV_TAG_FLOOR`) when new tags are added; never lower it without a
matching DANGER_MAP/INV_TAGS update in the same commit.

History note: A.1 was originally produced 2026-05-22 but did not
make it into v3.64.1's actual release zip (handoff state never
integrated into the release line). Verified absent across v3.63.10,
v3.64.0, v3.64.1, v3.64.2, and v3.64.3 zips. A.1 was restored
against the v3.64.3 source as of 2026-05-23. Line numbers below
reflect the restored state — they have drifted from the original
2026-05-22 numbers by 1-129 lines per site.

-----

## INV-001 — `session_keeper.pause_site_keepers()` before nested `sync_playwright`

DANGER_MAP entry: "session_keeper — pause before any nested
sync_playwright spawn"

Anti-pattern clause: `resume_site_keepers` does NOT exist and must
never be created. Keepers self-resume on next heartbeat by detecting
`self._pw is None` and relaunching the browser context fresh.

Tagged lines:

- `bulk_downloader/session_keeper.py:886` — `pause_site_keepers`
  definition
- `bulk_downloader/runner.py:2567` — call in `login_async`
- `bulk_downloader/runner.py:2685` — call in `start_manual_login`
- `bulk_downloader/runner.py:2771` — call in
  `start_captcha_solve_session`
- `bulk_downloader/runner.py:3022` — call in `verify_login_after_wizard`
- `bulk_downloader/runner.py:2160` — definition of `_playlist_expand_one`
- `bulk_downloader/runner.py:2207` — definition of `_search_site`

Current dispatch mirror: `bulk_downloader/dev_suite/audit_security.py:146` (dispatch
tracer references INV-001 by ID).

## INV-002 — `runner._process_one` dispatch order

DANGER_MAP entry: "runner.py::_process_one dispatch order"

The 10-step priority chain inside `_process_one` is the load-bearing
structure. Order matters; stash-dedup (step 5) must stay before any
download step. `_DISPATCH_CHAIN` in dev_suite.py mirrors this chain
and is read by a dispatch-tracer test.

Tagged lines:

- `bulk_downloader/runner.py:2905` — `_process_one` definition
- `bulk_downloader/dev_suite/audit_security.py:146` — `_DISPATCH_CHAIN` mirror

Pinning test: `tests/test_dispatch_chain.py`.

## INV-003 — fail-open guards in extractors / hls_downloader

DANGER_MAP entry: "~373 `except Exception: pass` blocks — INV-003
mandates fail-open"

Pattern, not a single line. The file-top INV-003 comment in each
covered module flags the convention; individual `try/except` blocks
inside are not tagged (there are hundreds).

Tagged files (file-top explanatory comment, not inline):

- `bulk_downloader/extractors.py:54` — file-top INV-003 comment
- `bulk_downloader/hls_downloader.py:40` — file-top INV-003 comment

No regression test in this release. A future static check that
warns on conversion of these blocks to hard raises would be the
right shape for enforcement.

Pre-existing dev_suite.py reference: none (dispatch tracer doesn't
mirror INV-003 because it's a pattern, not a line).

## INV-004 — `db.py::db_conn` WAL isolation-level hack

DANGER_MAP entry: "db.py::db_conn — the WAL isolation-level hack
is load-bearing"

Three lines must stay together: isolation None set → PRAGMA call →
isolation restore. Removing or moving any one of them breaks WAL
silently — `journal_mode` stays at `delete` and concurrent writes
deadlock.

Tagged lines:

- `bulk_downloader/db.py:206` — `cx.isolation_level = None`
- `bulk_downloader/db.py:207` — `PRAGMA journal_mode=WAL`
- `bulk_downloader/db.py:211` — `cx.isolation_level = ""`

Current mirror reference: `bulk_downloader/dev_suite/audit_security.py:146`.

## INV-005 — `RESOLUTION_TIERS` / `res_label` parallel tables

DANGER_MAP entry: "Two parallel resolution tables must stay synced"

Three sites encode the same resolution data via separate paths.
Changing one without the others causes silent quality-selection
bugs (a tier appears in one path but not the other; or a label
changes while the score stays the same).

Tagged lines:

- `bulk_downloader/heuristic_scoring.py:122` — `RESOLUTION_TIERS`
  constant
- `bulk_downloader/detect.py:57` — `_RES_LABEL_PATTERNS` constant
- `bulk_downloader/detect.py:138` — `res_label` function

Current mirror reference: `bulk_downloader/dev_suite/audit_security.py:146`.

## INV-006 — credentials route through vault abstraction

DANGER_MAP entry: "Credentials must route through the vault
abstraction"

Never read `cfg["password"]` directly. Login code calls
`secrets_store.resolve_password()`; VPN code calls
`vpn_config.resolve_secrets()`. Direct reads leak `@cred:<key>`
literals into login forms and tunnel configs.

Tagged lines:

- `bulk_downloader/secrets_store.py:702` — `resolve_password`
  function
- `bulk_downloader/vpn_config.py:302` — `resolve_secrets` function

Current mirror reference: `bulk_downloader/dev_suite/audit_security.py:146`.

-----

## Inventory summary

- **19 inline `# INV-XXX` tags** across 8 source files
  (session_keeper.py, runner.py, dev_suite.py, db.py,
  heuristic_scoring.py, detect.py, secrets_store.py, vpn_config.py).
- **8 file-top marker comments**
  (`# Load-bearing invariants tagged inline as # INV-<ID>; see DANGER_MAP.md.`).
- **2 file-top INV-003 explanatory comments**
  (extractors.py:54, hls_downloader.py:40).
- **4 pre-existing dev_suite.py tags** (lines 272/280/293/301 —
  dispatch tracer references; predate A.1).

**Total `# INV-` lines counted by grep: 33** (matches the floor in
`tests/test_inv_tags_not_regressed.py`).

## Invariants NOT tagged in A.1 (need ID assignment first)

Per the A.1 "Don't" rule: invariants without numbered IDs were
skipped to avoid tagging unanchored constraints. The following
DANGER_MAP entries are candidates for future INV-007+ assignment:

- Phase B login fallback's `allow_manual` gate placement inside
  `login_async`.
- Dispatch tracer mirrors `_process_one` (folded under INV-002 for
  now).
- `learned.login` is the teach-skip trigger.
- Database backups must include WAL sidecars.
- bw_chart time-series UTC requirement.
- Module-level startup env-gated (`BD_DISABLE_KEEPALIVE`).
- Dev-tools modules import-clean rule.
- `_PINNED_TOGETHER` in `run_tests.py` (v1 plan example called for
  tagging this but it lacks a numbered ID; deferred).
- Sound-preference dual-source (per-device default + opt-in sync
  via `useSyncedSoundPref`; landed v3.64.3, documented in the
  DANGER_NOTES handoff).
- `_M2_DIST_ROOT` module-relative (landed v3.64.3; tested via
  monkeypatch pattern).
- Release-zip exclusion list pins to `db.py`'s sentinel constants
  (landed v3.64.3; tested via
  `test_v3_64_3_release_zip_excludes_runtime_sentinels.py`).

Plus most entries from "Looks like a bug, is intentional" and
"Platform / environment gotchas" sections of DANGER_MAP. These each
merit an INV-007+ assignment in a dedicated pass — out of scope for
A.1 restore.
