# dev_suite decomposition plan — on v3.66.392

**Target:** `bulk_downloader/dev_suite.py` (9,098 lines · 174 top-level functions · 68 numbered
tool sections · 0 classes) → a `bulk_downloader/dev_suite/` package.

**Type:** pure structural move. **Zero behavior change.** Same public surface, same tests green.
This is *not* RED-first work — there is nothing new to fail; the existing suite is the spec, and
identical-green before/after is the proof.

---

## 0. The one invariant that governs everything

> `from bulk_downloader import dev_suite as _ds` followed by `_ds.<fn>(...)` must keep working
> **byte-for-byte identically** for every caller.

Callers measured on the 392 tree:
- **`app.py` — 125 call sites** (every one is `from . import dev_suite as _ds; _ds.X(...)`).
- **`tools/build_release.py`** — imports dev_suite. **This is a release guard (SHA `e8142436`).** It must
  stay byte-identical, so the shim has to preserve `dev_suite.X` without any edit to build_release.py.
- `tools/verify_release.py`, `tools/check_version_consistency.py` — import the release-lint functions.
- 6 test files: `test_dev_suite_tier0`, `test_u29_config_snapshot`, `test_config_snapshot_diff`,
  `test_u20_pipeline_preview`, `test_v3_66_303_import_preview_redaction`, `test_t40_feature_flags`.

There is **no central dispatch registry** in dev_suite (`route_map()` is a *tool* that lists Flask
routes, not a name→function table). Functions are called directly by name. So preservation is purely:
**re-export every public name from `dev_suite/__init__.py`.** Nothing else to maintain.

---

## 1. Why dev_suite is the correct first decomposition (risk profile)

| Concern | Impact | Why |
|---|---|---|
| FUNCTION_INDEX | **none** | dev_suite is not tracked (only `app.py` + `runner.py` are — confirmed: 0 hits in FUNCTION_INDEX.json). |
| ROUTE_INDEX / ENDPOINT_CATALOG / gui_parity / G12 | **none** | The `/api/dev/*` routes stay in `app.py`. No route's defining file changes. |
| DEPENDENCY_GRAPH | **regen** | New submodule nodes + edges. This is the *only* in-sync artifact that moves. |
| 7 capture/deploy guards | **byte-identical** | dev_suite is not a guard; build_release.py imports it but the shim keeps `dev_suite.X` intact. |
| Deploy | **special** | A `.py`→package conversion can't be overlaid (see §5). |

It's the highest readability win for the lowest ripple in the whole tree.

---

## 2. Target package layout

```
bulk_downloader/dev_suite/
  __init__.py            # re-export shim: explicit `from .sub import (names…)` for every public fn + __all__
  _common.py             # cross-cutting privates (see below)
  introspection.py       # route map, thread inventory, runner state, process info, SSE broker, thread dump
  logs.py                # log tail, log-level toggle, structured log search
  config_tools.py        # effective settings, config dump (redacted), integrity, schema-audit, hot-reload, snapshot/restore/DIFF
  db_tools.py            # DB overview, WAL checkpoint, SQL console, integrity, backup verifier, slow-query/index advisor, queue/FTS
  release_lint.py        # version-consistency, CHANGELOG/.bat/.sh lint, release-zip manifest verifier, systemd-unit  ← guard-imported
  audit_security.py      # invariant self-audit, leak scan, route/auth surface, dispatch-chain tracer, security cluster, CSRF
  perf_metrics.py        # force-GC, latency histogram, slow-endpoint, error-rate, exception ring-buffer
  jobs_runner.py         # stuck-job detector, fleet console/job replay, retry-schedule/worker profiler
  capture_diag.py        # template audit, cookie-jar, login-template dry-run/cred resolver, extractor matrix, ffmpeg, HLS/DASH, dedup
  integrations_diag.py   # rate-limit, session-keeper, Ollama, event/SSE tap, prompt previewer/AI fallback, AI latency, vision, FlareSolverr
  vpn_diag.py            # VPN config renderer/rotation, connectivity probe/egress-IP
  maintenance.py         # dup-site/orphan/stale-ref, temp-dir/lock scanner, disk-usage/folder scanner  [renamed housekeeping — F17]
  test_meta.py           # test-meta cluster, parametrize/flaky detector, fixture-site controller, test-timing
```

~13 submodules + `_common`, ~500–900 lines each. **The grouping is tunable** — what's load-bearing is
the mechanics in §3. Keep `release_lint.py` intact as one unit (the guard-imported surface).

---

## 3. The mechanics that keep it safe

### 3a. `_common.py` — truly cross-cutting privates only
Move these: `_dev_mode`, `_SECRET_KEY_HINTS`, `_redact`, `_repo_root`, `_read_version`, `_percentile`,
`_proc_uptime_seconds`, `_BD_ENV_VARS`. Section-specific privates move WITH their section.

### 3b. The ONE systematic body transform — the 97 lazy sibling imports
dev_suite has **97 `from . import X`** lazy in-function imports. After a function moves into
`bulk_downloader/dev_suite/<sub>.py`, `.` resolves to `bulk_downloader.dev_suite` — wrong. Every one →
`from bulk_downloader import X` (absolute, kept LAZY/in-fn). A miss fails loudly on first call; the
surface-smoke test catches all 97.

### 3c. The re-export shim — `__init__.py`
For each submodule emit `from .submod import (a, b, c, …)` then a flat `__all__`. Explicit names, no
`import *`. Importing `dev_suite` imports every submodule — same net surface incl. lazy-app-import.

---

## 4. In-sync + guard handling (precise)

- **DEPENDENCY_GRAPH** — regen; run `test_dependency_graph_in_sync` from the extracted zip.
- **FUNCTION_INDEX** — unchanged; run `test_function_index_in_sync` to prove it didn't move.
- **ROUTE_INDEX / ENDPOINT_CATALOG / gui_parity / G12** — unchanged (routes stay in app.py).
- **Guards** — confirm all 7 SHAs byte-identical, esp. `tools/build_release.py` (`e8142436`).

---

## 5. Deploy — the overlay-can't-delete trap

```bash
cd ~/BulkDownloader
rm -f bulk_downloader/dev_suite.py            # MANDATORY — remove the shadowing module
unzip -o <BulkDownloader_v3_66_NNN.zip>       # lands bulk_downloader/dev_suite/ package
find . -name '__pycache__' -type d -prune -exec rm -rf {} +
find . -name '*.pyc' -delete
sudo systemctl restart bulkdownloader
curl -s localhost:5555/api/health             # confirm version flipped
```

---

## 6. Validation — characterization, not RED-first

1. **Surface-lock test** (`tests/test_dev_suite_surface_lock.py`, lands WITH the cut): assert the public
   surface is exactly preserved + smoke-import every submodule. Capture EXPECTED_PUBLIC from the pre-move tree.
2. **Re-run the 6 dev_suite-touching tests + `test_dev_suite_tier0`** unchanged — identical green is the proof.
3. All from the extracted zip.

---

## 7. Landing — one consolidated cut

**Band (`--suites`):** `test_dev_suite_surface_lock` · `test_dev_suite_tier0` · `test_u29_config_snapshot` ·
`test_config_snapshot_diff` · `test_u20_pipeline_preview` · `test_v3_66_303_import_preview_redaction` ·
`test_t40_feature_flags` · `test_dependency_graph_in_sync` · `test_function_index_in_sync` · `test_contracts`.

---

## 8. Execution checklist

1. Snapshot the surface from the 392 tree → freeze EXPECTED_PUBLIC into the surface-lock.
2. Create `dev_suite/_common.py`; move the cross-cutting privates.
3. Move each section into its submodule, applying the §3b import transform as you move.
4. Generate `dev_suite/__init__.py`.
5. `rm bulk_downloader/dev_suite.py`.
6. `python3 -c "import bulk_downloader.dev_suite"` + import every submodule.
7. Run the band locally; fix any ImportError.
8. Regen DEPENDENCY_GRAPH; confirm both in-sync tests green.
9. Confirm 7 guard SHAs byte-identical — build_release.py must read `e8142436`.
10. `bd-cut`.
11. Author the deploy note with the §5 `rm` step front-and-center.

> NOTE (max-verify correction, v3.66.392): dev_suite carries **5** `# INV-` tags (001,002,004,005,006),
> not 11; the tree-wide floor is 6 distinct / 23 occurrences. Section count is 82 (section-map) vs 84
> (pass-log) — a doc inconsistency; sections are a planning abstraction, not a source marker. The
> load-bearing 174 fns / 125 public is source-verified. See DECOMPOSITION_PROGRAM_ROADMAP §1.
