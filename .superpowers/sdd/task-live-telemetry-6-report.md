# Task 6 report: verify every dashboard widget

## Result

- Catalog contract and renderer coverage added for all 36 widgets.
- Home coverage renders all 36 selected catalog widgets plus all five legacy widgets (41 distinct layout tiles).
- Picker E2E discovers exactly 36 catalog actions, toggles a representative widget, checks the dashboard tile, and restores the selection.
- Two product defects were characterized with failing tests and fixed minimally:
  1. Catalog `throughput` collided with the legacy chart layout ID and was filtered from Home/SiteDetail. Colliding KPI layout keys now use `kpi-throughput` while selection/API persistence retains canonical ID `throughput`.
  2. Home and WidgetPicker mounted separate global selection-hook instances; native `storage` events do not fire in the same document, so picker changes did not update Home. Global selection changes now publish a same-tab event in addition to retaining cross-tab storage synchronization.

## Inventory

| Category | Count | Widget IDs |
|---|---:|---|
| Activity | 4 | `done_today`, `done_hour`, `bytes_today`, `files_hour` |
| Performance | 5 | `throughput`, `success_rate`, `avg_speed`, `avg_size`, `avg_quality` |
| Capacity | 4 | `queue_depth`, `workers`, `disk_free`, `bandwidth` |
| Health | 4 | `action_req`, `stuck`, `failures`, `retries` |
| System | 5 | `cpu_ram`, `gpu`, `eta_clear`, `cookies`, `sites_active` |
| Collection | 8 | `lib_total`, `lib_size`, `lib_watched`, `lib_unrated`, `lib_missing`, `lib_recent`, `lib_top_studio`, `audit_recent` |
| Diagnostics | 6 | `success_rate_24h`, `error_top_cluster`, `captcha_24h`, `cookie_warnings`, `sites_need_attention`, `active_alerts` |
| **Catalog total** | **36** | 36 unique IDs |

Legacy Home widgets (5): `attention`, `today`, `throughput`, `now-running`, `by-site`.

## Changed files

- `frontend/src/lib/widgetCatalog.test.tsx`
- `frontend/src/routes/Home.widgets.test.tsx`
- `frontend/e2e/d3_smoke.spec.ts`
- `frontend/src/hooks/useDashboardLayout.ts`
- `frontend/src/hooks/useWidgetSelection.ts`
- `frontend/src/routes/Home.tsx`
- `frontend/src/routes/SiteDetail.tsx`

## Characterization and fix evidence

1. Initial all-selection Home test failed because catalog label `Throughput` was absent while all other catalog labels rendered. Root cause: the catalog ID matched the legacy chart layout ID and Home/SiteDetail explicitly discarded any catalog ID in `LEGACY_WIDGET_IDS`.
2. After the layout-key alias, focused tests rendered 36 catalog labels and 41 tile handles.
3. Live browser interaction then showed `Top studio` selected in the picker (`aria-pressed=true`) but absent from Home after closing. A new Home picker-interaction test reproduced the failure locally.
4. After same-tab selection synchronization, the regression test renders the `Top studio` tile and `Drag to reorder lib_top_studio tile` handle.

No catalog count was weakened and no unavailable widget was skipped.

## Commands and outputs

The shell did not expose `npm`; commands used the bundled Node runtime directly.

### Focused Vitest

```powershell
node.exe .\node_modules\vitest\vitest.mjs run src/lib/widgetCatalog.test.tsx src/routes/Home.widgets.test.tsx
```

Result: PASS — 2 files, 7 tests.

### Full frontend Vitest

```powershell
node.exe .\node_modules\vitest\vitest.mjs run
```

Result: PASS — 119 files, 490 tests. Existing suite stderr includes React Router v7 future-flag warnings and several pre-existing TanStack Query “data cannot be undefined” messages in unrelated route tests.

### TypeScript and Vite production build

```powershell
node.exe .\node_modules\typescript\bin\tsc -b
node.exe .\node_modules\vite\bin\vite.js build
```

Result: PASS — 2,760 modules transformed; production bundle emitted. Vite reported the existing chunk-size warning for chunks over 500 kB (`index` about 656 kB and `vendor` about 867 kB minified).

### Live Playwright E2E

```powershell
$env:BD_E2E_BASE='http://10.0.70.20:5555'
node.exe .\node_modules\@playwright\test\cli.js test e2e/d3_smoke.spec.ts --grep "widget picker discovers"
```

Result against currently deployed stash v3.66.811: FAIL — 2/2 projects (Desktop Chrome and Pixel 5). Both reached the picker, discovered all 36 actions, and selected `Top studio`, then timed out because the tile did not appear after closing. This is the same same-tab synchronization defect fixed in the local branch. The live host has not been deployed from this branch, so a passing post-fix live run is deployment-bound.

## Browser QA

URL: `http://10.0.70.20:5555/`

| Check | Desktop/default viewport | Mobile 393x851 |
|---|---|---|
| Page identity | PASS: URL exact, title `BulkDownloader` | PASS |
| Meaningful content / blank page | PASS: Home dashboard and live site data rendered | PASS |
| Framework error overlay | PASS: none observed | PASS |
| Picker discoverability | PASS: `All 36`, 36 catalog action buttons | PASS: 36 catalog actions, category tabs wrapped and usable |
| Representative selection | PARTIAL on deployed build: picker changed to 6/36 and `Top studio` pressed | Same deployed-code limitation |
| Dashboard tile state | FAIL on deployed build: selected tile absent after dialog close | Repo Playwright reproduced the same failure |
| Console | Initial load: no warnings/errors | Dialog interaction: one Radix warning, below |

The temporary `Top studio` selection was removed, the picker was closed, edit mode was exited, and the viewport override was reset. No remote data or destructive controls were touched.

## Console errors and concerns

- Live dialog interaction logged: `Warning: Missing Description or aria-describedby={undefined} for DialogContent.` This is an accessibility warning in the deployed WidgetPicker dialog and is outside the requested widget inventory/rendering fix.
- At 393x851 while edit mode was active, the live dashboard screenshot showed a horizontal scrollbar and partial clipping of the second KPI column. Because the viewport was changed on an already-mounted desktop tab, confirm this once more in a fresh mobile context after deployment before treating it as a layout regression.
- The live E2E cannot pass until this commit is deployed to stash. Do not mark the live tile-change gate green based only on local unit/build results.
