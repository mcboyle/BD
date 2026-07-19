<!-- verified-against: v3.66.754 -->
# #8 — `build_release.py` cheat sheet (from source)

The canonical, deterministic release builder (`tools/build_release.py`, ~958 lines). "Deterministic" = same
source tree → byte-identical zip (modulo uncontrollable zip metadata). **Files-only output** (no directory
entries — see #5).

## What it does (in order)
1. Reads `__version__` from `bulk_downloader/__init__.py` and **confirms `CHANGELOG.md`'s top `## vX.Y.Z`
   matches** (`_verify_changelog`, exit 2 on mismatch).
2. **Endpoint-catalog gate** — runs `tools/build_endpoint_catalog.py --check` as a subprocess; FAIL → fix with
   `python tools/build_endpoint_catalog.py` and rebuild.
3. **Function-index gate** — runs `tools/build_function_index.py --check`; FAIL → `python
   tools/build_function_index.py` and rebuild.
4. Builds the zip using the **same exclusion list** as `dev_suite._manifest_excluded()` (see #6), then runs
   `dev_suite.zip_manifest_check()` against the built zip — **drift fails the build**.
5. Extracts to a scratch dir and runs the **test suite from the extracted copy**; emits the final zip **only if
   `Failed: 0`**. ⚠️ no-arg `run_tests.py` can hit the perf_lab hang (see #2).

## Flags
| Flag | Effect |
|------|--------|
| `--out DIR` | output dir (default cwd) |
| `--skip-tests` | zip + verify gates only (recommended; then run the band manually — see #2) |
| `--quick` | tests become an exhortation, not a gate (between releases only) |
| `--prebuild-spa` | rebuilds the SPA first; requires `frontend/`, `package.json`, and `npm` on PATH |

## Exit codes
`0` all gates passed · `1` built but a gate failed (verifier drift / test failures) · `2` couldn't build
(missing source, version mismatch, IO).

## Regen invocations (needed when a route/function changed)
```
PYTHONPATH=/tmp/prestaged_site_packages BD_DISABLE_KEEPALIVE=1 python3 tools/build_endpoint_catalog.py   # needs Flask
python3 tools/build_function_index.py        # tracks only app.py + runner.py line numbers (blueprint page funcs are OUT of scope)
PYTHONPATH=/tmp/prestaged_site_packages BD_DISABLE_KEEPALIVE=1 python3 tools/dependency_graph.py          # needs Flask; regens DEPENDENCY_GRAPH.json + .md
PYTHONPATH=/tmp/prestaged_site_packages BD_DISABLE_KEEPALIVE=1 python3 tools/gui_parity_inventory.py       # needs Flask; regens reports/gui_parity_inventory.{json,md}
```
Verify each with its `--check` (the first three) and re-run `tools/check_route_counts.py` (the G12 route-count
gate cross-checks source-decorators == inventory == test-pin). A GUI-parity write cut touches **all four**:
endpoint_catalog (+1 cockpit route), dependency_graph (edges/blueprint), gui_parity_inventory (the endpoint
flips `spa_wired`), and the route-count gate (actions_center N→N+1). FUNCTION_INDEX usually does **not** change
for a new cockpit blueprint page — those funcs are out of its scope; confirm it stays in-sync rather than
assuming it regenerated.
(160 needed both regens: endpoint catalog for the 8 Settings Center routes, function index for app.py
line-shift.)

### SPA parity gotcha (has cost a re-cut before)
`gui_parity_inventory` marks an endpoint `spa_wired` by scanning the React SPA for **literal `/api/…` strings**
(it tolerates whole `${…}` template blocks). Build call paths as FULL literals —
``apiPost(`/api/sites/${encodeURIComponent(siteId)}/foo`)`` — **not** via a concatenated `base` const
(``const base = `/api/sites/${id}``; ``apiPost(`${base}/foo`)``): the scanner can't see `${base}/foo` as an
`/api/` path, the endpoint stays `spa_unwired`, and the parity delta won't move even though the wiring works.

## After it emits (not automated)
- `tools/verify_release.py --zip <path>` — confirm `RESULT: PASS` (banner/version_consistency gate; see #2). Exits 1 on FAIL / 0 on PASS (gate-safe on `$?`; `--json` for machine-parse). Known-benign: reptyle-draft status + `frontend/package.json 0.1.0` (independent versioning).
- `python tools/rollback.py --archive X.Y.Z --from <path>` — archive bookkeeping is a separate manual step.
