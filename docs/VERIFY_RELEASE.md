# VERIFY_RELEASE — release verification tooling (#15)

A small, read-only toolset that answers "is this release sound?" without
promoting, enabling, swapping, building, or bumping anything. It composes the
checks that already exist in the tree (`bulk_downloader/dev_suite.py`,
`tools/promote_template.py`, `run_tests.py`) rather than re-implementing them, so
there is one definition of each rule.

All four tools are stdlib-only and run with plain `python3` from the repo root
(on stash, run them inside the loaded env — e.g. `bd python3 tools/...`).

| Tool | What it answers | Exit |
|------|-----------------|------|
| `tools/verify_release.py` | The umbrella: version + docs + templates + (opt) zip manifest + (opt) tests | 0 pass / 1 fail / 2 error |
| `tools/check_version_consistency.py` | Do source banners + CHANGELOG agree with `__version__`? | 0 / 1 / 2 |
| `tools/check_doc_drift.py` | Are required docs present? What's archival? | 0 / 1 / 2 |
| `tools/template_inventory.py` | What templates exist, are they sane, are they gate-ready? | 0 (report) / 1 (`--strict`) |

## Quick start

```bash
# Structural check of the current tree (fast — no tests run):
python3 tools/verify_release.py

# Also compare a built zip's manifest against the tree:
python3 tools/verify_release.py --zip /path/BulkDownloader_v3_66_<n>.zip

# Add the release gate suites (one fresh BD_HOME per file):
bd python3 tools/verify_release.py --tests          # 'gate' (contract + endpoint + current-release suites)
bd python3 tools/verify_release.py --tests full     # the whole suite (slow)

# JSON for tooling:
python3 tools/verify_release.py --json
```

### Verifying an *extracted* release

To verify an extracted release zip, **cd into the extracted directory and run its
own `tools/verify_release.py`.** The composed checks (`dev_suite`,
`check_version_consistency`, `check_doc_drift`, `template_inventory`) operate on
the tree they live in — `dev_suite` in particular derives the repo root from its
own location, so `--root` pointed at a *different* tree will not redirect the
version scan. The `--zip` flag is the exception: it compares a zip's manifest
against the current tree via `dev_suite.zip_manifest_check()`.

## What each gate means

- **version_consistency** — `dev_suite.version_consistency()` scans
  `*.py/*.sh/*.bat/*.spec` for `Bulk Downloader vX.Y.Z` banners and
  `VERSION = "X.Y.Z"` assignments that disagree with
  `bulk_downloader/__init__.py::__version__`; `dev_suite.changelog_lint()`
  confirms the **topmost** CHANGELOG entry is the current version. `/api/health`
  emits the version straight from `__init__`, so it is consistent by
  construction. The frontend (`frontend/package.json`) is **independently
  versioned** and intentionally not aligned — reported as informational only.
- **required_docs** — README, CHANGELOG, SANDBOX, SETUP, ENDPOINT_CATALOG,
  FUNCTION_INDEX present, and `docs/` non-empty. Whether the generated indices
  are *in sync* is enforced by `tests/test_endpoint_catalog_in_sync.py`, not
  re-derived here. Historical per-release handoffs are listed as **archival
  candidates** (informational). The canonical KB set (charter / active index /
  automation policy / operating instructions / schemas / handoff / runbook)
  lives in **project knowledge**, not the tree — reported so the split is
  visible.
- **template_sanity** — inventories `templates/{reviewed,enabled,drafts,review_candidates}`.
  Hard rules: nothing outside `reviewed/`+`enabled/` may be `enabled`, and
  templates in `reviewed/`+`enabled/` **must** be `enabled` (the registry loads
  only `enabled`). Completeness score and `promotion_ready` mirror the real
  `promote_template.py` gate (`(trigger|row_selectors|button)` + non-empty
  `resolutions` + no blocked terms). Incomplete drafts are *not* a failure — they
  are expected to be incomplete until reviewed.
- **zip_manifest** (with `--zip`) — `dev_suite.zip_manifest_check()`; flags files
  in the tree but missing from the zip (or vice-versa).
- **tests** (with `--tests`) — see below.

## The test runner — encoded lessons

These are the hard-won rules from the full-suite work; the runner bakes them in
so a verifier never re-learns them the hard way.

1. **One fresh `BD_HOME` per file — never shared.** `verify_release` runs each
   test file in its own subprocess with `BD_HOME=$(mktemp -d)`. Sharing a
   `BD_HOME` across files lets "fresh-DB"/global-state tests see rows an earlier
   file wrote, producing *false* failures. (A full 18-batch run that shared one
   `BD_HOME` per batch produced 22 such false-fails; all passed one-file-per-`BD_HOME`.)

2. **GTK + Xvfb (a real `DISPLAY`).** `tests/test_v3_43_80_modules.py::test_all_modules_import`
   imports `tray_app`, which initialises GTK. Without the GTK typelib/lib paths
   (`GI_TYPELIB_PATH`, `LD_LIBRARY_PATH`, `XDG_DATA_DIRS`) **and** a live
   `DISPLAY` (Xvfb), it fails — first `Namespace Gtk not available`, then with the
   typelib set, `Bad display name`. With both, it passes 49/49. `verify_release`
   **inherits the ambient environment** (it does not hardcode sandbox paths), so
   run it inside the loaded env: on stash via `bd`, in the sandbox with the
   SANDBOX.md §6 env vars exported and `Xvfb :99` up.

3. **`test_perf_lab.py` is isolated with a bounded timeout.** The documented
   whole-dir hang is an *interaction*, not the file: run alone it passes (17/17)
   well under a 60 s bound. `verify_release --tests full` runs it isolated with
   that bound; it never stalls the run.

4. **Failures are classified — harness/env vs. real regression.** A failure whose
   output carries a GTK/`DISPLAY` signature (`Namespace Gtk not available`, `Bad
   display name`, `Can't connect to display`, `libgtk`, headed-browser-without-XServer)
   is reported as a **HARNESS/ENV** artifact, separate from a real regression.
   The exit code (and the `RESULT`) gate only on **real** regressions.

5. **Expected network/display skips.** With the network off, browser/noVNC/live
   suites skip; that is expected, not failure. The skip *count* is gated
   separately by `tools/check_skip_baseline.py` against
   `tests/SKIP_BASELINE.json`. It compares exact collected test identities and
   reasons, so a mass-skip regression or changed justification cannot hide
   behind an unchanged aggregate count. Run the sanctioned real-pytest suite
   with `--junitxml=<path>` and then run
   `python3 tools/check_skip_baseline.py --junit <path>`.

## Release-checklist mapping

verify_release covers, in one command, several manual checklist items:

- "Bump `__init__.py` line 26" → version_consistency confirms nothing else drifted.
- "CHANGELOG entry with matching version, topmost" → changelog_lint.
- "Verify from the extracted/built zip" → run the extracted tree's own
  `verify_release.py`, plus `--zip` for the manifest.
- "Run the release suites in small batches" → `--tests` (one `BD_HOME` per file,
  perf_lab isolated, harness-vs-regression classified).

It does **not** replace the human steps: gold-backup, candidate promotion, swap,
and the live download all stay manual/operator-gated.
