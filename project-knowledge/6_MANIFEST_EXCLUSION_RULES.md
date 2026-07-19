<!-- verified-against: v3.66.276 -->
# #6 — Manifest-exclusion ruleset (authoritative, from source)

The builder (`build_release.py`) and the verifier (`dev_suite.zip_manifest_check`) share **one** exclusion
definition in `bulk_downloader/dev_suite.py` (≈ lines 801–860). If either drifts, the build's manifest gate
fails. This is what a clean release zip is allowed to omit — and therefore what should never count as "missing."

## `_MANIFEST_EXCLUDE_DIRS` (any path segment matching → excluded)
```
__pycache__  .git  venv  .venv  node_modules  screenshots
.pytest_cache  results  profiles  .mypy_cache  state
```
- `state/` is excluded specifically because importing `bulk_downloader.app` during the endpoint-catalog gate
  spins up the heartbeat thread, which writes `state/heartbeat.json` — without the exclusion the zip would ship
  the developer's last heartbeat.

## `_MANIFEST_EXCLUDE_SUFFIXES`
```
.pyc  .pyo  .log  .zip
```
- `.zip` is excluded so the just-written release artifact in the tree isn't flagged "missing from zip."

## `_MANIFEST_EXCLUDE_NAMES` (exact basename)
```
downloader_history.db  downloader_history.db-wal  downloader_history.db-shm
.integrity_check_last  .integrity_last_run  .fts_optimize_last
test_results.json  SUMMARY.txt  sites_config.json  .DS_Store  debug.flag
```

## `_MANIFEST_EXCLUDE_PATHS` (PATH-scoped, added v3.66.263)

A 3rd mechanism alongside DIRS/SUFFIXES/NAMES: exclude by **full relative path**, used when a
basename appears in multiple locations with different ship/no-ship intent. Example: the root
`app_config.json` is PATH-excluded (any app boot in the work tree writes it, and it can carry a
generated secret), while `frontend/app_config.json` (the SPA twin) intentionally still ships. A
basename rule can't express that split; a path rule can.

## Nuances that matter

- **`logs/` is NOT a dir-exclude** — but `.log` files inside it are suffix-excluded, so an empty/`.log`-only
  `logs/` contributes nothing. (A non-`.log` file dropped in `logs/` *would* ship.)
- **`screenshots/` IS a dir-exclude**; `live_recordings/` is not in the dir list but its runtime contents are
  typically excluded by name/suffix.
- The set is **suffix + dir + exact-name**, not globs — match accordingly when scanning.

## Authoritative re-read each release
```
sed -n '801,860p' bulk_downloader/dev_suite.py
```
Use the source, not this copy, if you suspect drift — but this is the 160 state verbatim.
