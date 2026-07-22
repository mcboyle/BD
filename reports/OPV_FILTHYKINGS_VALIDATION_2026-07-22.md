# FilthyKings OPV validation — 2026-07-22

## Result

PASS. BulkDownloader exercised the authorized site-provided download flow through
the configured CloakBrowser runtime, sampled exactly 262,144 bytes, aborted the
transfer, saved no media file, cleared the temporary draft override, and restored
the production queue/config/database/profile state.

No live scene identifier, row href, signed query, cookie, token, or credential is
recorded in this report or in the reviewed template.

## Live selector evidence

- The authenticated member scene exposed a site-provided Download control.
- Opening it produced one download modal with exactly eight anchor rows.
- Visible row order was `160p, 240p, 360p, 480p, 540p, 720p, 1080p, 2160p`.
- The stable modal-scoped selector matched exactly those eight rows:
  `.VideoJSPlayer-Modal .VideoJSPlayer-DownloadOption-Link`.
- Row paths normalized to the site-provided
  `/movieaction/download/:id/:resolution/mp4` shape. Raw hrefs and queries were
  intentionally not persisted.

## BulkDownloader probe evidence

The run used the normal BulkDownloader application and its real
`POST /api/template/test_extract` workflow. Production state was removed from the
live runtime while stopped and replaced with one empty temporary site/database.
The existing 962-job queue therefore could not start.

Runtime evidence:

- `resolved_backend: cloakbrowser` (CloakBrowser 0.4.10).
- Worker journal: CloakBrowser persistent profile; no Playwright fallback.
- `persist: false`, `probe: true`, `force_download: true`.
- One isolated queue row only.
- Terminal status `done`.
- Sampled bytes: exactly `262144`.
- Completion note confirmed the stream was aborted and no file was saved.
- Output directory remained empty.
- Draft override was explicitly cleared.

Rollback evidence:

- Original site/config/database were restored from stopped-state SHA-256 verified
  snapshots.
- Original queue restored to `962 pending + 6 done = 968` rows.
- Active downloads returned to zero and both original sites were loaded.
- The original runtime cookie database was unchanged.
- Temporary database, profile, output, config, and unfiltered journal copies were
  removed after verification.

Sanitized evidence is retained on stash under:

- `.opv-rollback/20260722T183723Z-mbX7VA` (fail-closed cap finding + rollback)
- `.opv-rollback/20260722T184231Z-TH50fZ` (passing exact-cap run + rollback)

## General fixes that made the run pass

1. Exact global probe cap:
   - Send `Range: bytes=0-262143`.
   - Slice each received chunk to the remaining byte budget before sniffing or
     counting it.
   - Regression-test an oversized first chunk and require exactly 262,144 bytes.
2. Reusable modal recognition:
   - Treat stable class/id tokens ending in `-modal` or `_modal` as modal scope.
   - This lets future normalizations keep safe selectors such as
     `.VideoJSPlayer-Modal ...` instead of dropping them.
3. Reviewed FilthyKings template:
   - Add the stable modal-scoped row selector.
   - Correct the observed resolution list (add 160p, remove unsupported 576p).
   - Enable only after the real capped CloakBrowser probe passed.
   - Keep the observed API host/path as review evidence only; no response schema
     or signed URL is persisted.

## Verification

- Probe-cap regression followed RED/GREEN TDD: the oversized-chunk test first
  failed at 266,240 bytes, then passed at exactly 262,144 bytes.
- Related transport/VPN/runtime tests: 49 passed locally.
- Deployed probe/VPN tests: 15 passed on stash.
- FilthyKings template, normalizer, and probe tests: 30 passed.
- Template validator: `VERDICT: ok`.
- `git diff --check`: clean.
- Final stash certification with `DISPLAY=:99` and 60 workers:
  - Unit: `12628 passed / 0 failed / 73 skipped`.
  - Live: `35 passed / 0 warned / 0 failed`.
  - `CAPTURE VERDICT: PASS`.

The repository-wide raw `pytest` invocation remains unsuitable as a release
gate in this environment because collection stops on the pre-existing
`bd_module_wipe` mark-normalization issue. The project orchestrator completed
the supported full suite and live certification successfully instead.
