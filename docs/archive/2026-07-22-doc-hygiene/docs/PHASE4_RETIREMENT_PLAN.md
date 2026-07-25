# Phase 4 — Legacy retirement plan

**Derived from source at v3.66.217; §C re-derived + reconciled against the live v3.66.276 tree on 2026-06-17.** No code changed; this is a design doc. Scope: retire the legacy `/legacy` shell, drop the legacy assets, migrate the tests that pin them, and clear the final `legacy_only` ratchet entry `1 → 0`.

> **276 reconciliation (read first).** §A deletion set, §B redirect map, and §D `{x}` proof were re-verified at 276 and HOLD (the `{x}` sole producer is still `static/app.js:2660`; every legacy `*.js` still loads only from index.html/mobile.html). **§A.3 line numbers drifted ~130 (serve_legacy 2873→3006, _INDEX_HTML_CACHE 2926→3057, manifest 2984→3109) — symbols valid, re-derive numbers at cut time.** §C below REPLACES the 217 test-migration map, which was both stale (its 13-file family + themes_catalog already migrated off index.html) and incomplete (4 real @276 pins it never listed).
doc. Scope: retire the legacy `/legacy` shell, drop the legacy assets, migrate the
tests that pin them, and clear the final `legacy_only` ratchet entry `1 → 0`.

> **This is a guard-adjacent, route-changing cut.** It is NOT authorized by the
> session that produced this doc. Executing it requires: explicit cut approval, the
> in-sync regen (ENDPOINT_CATALOG/DEPENDENCY_GRAPH/gui_parity), and a full on-stash
> suite. None of the 7 release-guard files are touched by Phase 4 (verified: the
> deletion set below is all `app.py` routes + `static/` + `templates/` + tests).

---

## A. Exact deletion set

### A.1 Files deleted in full

| Path | What it is | Why it goes |
|---|---|---|
| `bulk_downloader/templates/index.html` | The legacy shell HTML (134 KB, ~8.8k lines of inline JS) | The only `/legacy` body; SPA at `/` replaces it |
| `bulk_downloader/templates/mobile.html` | Pre-D4 mobile shell, kept for rollback | `/m` already 302s to `/`; rollback window closed |
| `bulk_downloader/templates/m_ops.html` | Retired PT8 mobile-ops page, kept for rollback | `/m/ops` already 302s to `/` |
| `bulk_downloader/static/app.js` | Legacy main JS (740 KB) — **sole producer of `/api/{x}`** | See §D |
| `bulk_downloader/static/app.css` | Legacy shell stylesheet | shell-only |
| `bulk_downloader/static/approval_ui.js` | shell module (referenced **only** by `index.html`) | shell-only |
| `bulk_downloader/static/captcha_relay.js` + `captcha_relay.css` | shell module | shell-only* |
| `bulk_downloader/static/components.js` | shell module | shell-only |
| `bulk_downloader/static/library.js` | shell module | shell-only |
| `bulk_downloader/static/live_recorder.js` + `live_recorder.css` | shell module | shell-only* |
| `bulk_downloader/static/pwa.js` | shell PWA glue (no other referrer) | shell-only |
| `bulk_downloader/static/queue_ux.js` | shell module | shell-only |
| `bulk_downloader/static/ux.js` | shell module | shell-only |
| `bulk_downloader/static/vpn_ui.js` + `vpn_ui.css` | shell module | shell-only |
| `bulk_downloader/static/widgets.js` + `widgets.css` | shell widgets UI | shell-only** |

Reference check (grep at 217): every `static/*.js` above resolves **only** to
`templates/index.html` (and the retired `mobile.html`) as a referrer. The
non-template hits for `app.js` / `widgets.js` / `app.css` are all **comments or
docstrings** (`openapi_spec.py:233` "from grep of app.js", `dev_suite.py:3343`,
`app.py:531/5270/17190`, `widgets_config.py:56`, `app_widgets_api.py:149`) — not
runtime loads. `widgets_config.py` / `widgets.json` is a **different artifact**
(the widget-config persistence file), not `widgets.js`.

\* **`captcha_relay.*` and `live_recorder.*`** are shell-only at 217, but the
capture/relay *runtime* lives in Python (`tools/capture_session.py`,
`bulk_downloader/dom_overlay.py`, the noVNC flow). Confirm once more at cut time
that no cockpit page (`app_*center.py`, `app_report_center.py`) adds a
`<script src>` to them before deleting — the grep is clean at 217 but this is the
one pair worth re-grepping the day of the cut.

\*\* **`widgets.js`** drives the legacy dashboard widgets; the SPA has its own
widget rendering. The backend widget API (`app_widgets_api.py`,
`/api/widgets/<scope>`) **stays** — only the legacy JS goes.

### A.2 Files that SURVIVE (do **not** delete)

| Path | Why it stays |
|---|---|
| `bulk_downloader/static/sw.js` | Service worker — the live SPA push (`hooks/usePush.ts`, T9b) reuses the **root `/sw.js`** to preserve existing subscriptions. Served by an `app.py` route. |
| `bulk_downloader/static/manifest.json` | PWA manifest, served at `/manifest.json` (`app.py:2984` `send_from_directory(static_folder, "manifest.json")`). SPA PWA uses it. |
| `bulk_downloader/static/icons/` | PWA icons (manifest references). |
| `bulk_downloader/app_widgets_api.py`, `widgets_config.py` | Backend widget API + config — SPA-facing. |

### A.3 `app.py` deletions (legacy shell handlers)

| Lines (217) | Symbol | Action |
|---|---|---|
| `2873–2924` | `@app.route("/legacy/")` / `@app.route("/legacy")` `serve_legacy()` | Replace body with `return redirect("/", code=302)` (see §B). The inline CSRF-mint / session-bootstrap block is dropped — it was shell-specific. |
| `2926–2953` | `_INDEX_HTML_CACHE` global + `_load_index_html()` | Delete — only caller was `serve_legacy()`. |
| (cache-clear list) | wherever `_INDEX_HTML_CACHE` is reset by the hot-reload/cache-clear helper (pinned by `test_u28_config_cache`) | Remove `_INDEX_HTML_CACHE` from the cleared-targets set. |

### A.4 Optional housekeeping (dead since the root flip; delete *or* keep)

The opt-in cookie machinery `_m2_opt_state`, `_m2_apply_opt_cookie`,
`_M2_COOKIE_NAME`, `_M2_COOKIE_TTL` (`app.py` ~802–940 region) is **dead** post
root-flip — the `/m2` shim no longer consumes it (its own docstring says so). It is
kept alive **only** by `test_d3_u9_opt_in.py`. **Recommendation:** delete it in
Phase 4 and retire `test_d3_u9_opt_in.py` (§C). This is independent of the `{x}`
clearance — fold it in only if you want the cut to also sweep the dead cookie code.

---

## B. `/legacy → 302 /` redirect map

The other shell paths already redirect (verified at 217); Phase 4 only changes
`/legacy`:

| Path | At 217 | After Phase 4 |
|---|---|---|
| `/legacy`, `/legacy/` | 200 legacy shell (`serve_legacy`) | **302 → `/`** |
| `/legacy/<anything-else>` | 404 (reserved namespace, not SPA HTML) | **unchanged — stays 404** (the redirect matches only the two exact paths) |
| `/m`, `/m/` | 302 → `/` (`serve_mobile_view`) | unchanged |
| `/m/ops`, `/m/ops/` | 302 → `/` (`serve_mobile_ops_view`) | unchanged |
| `/m2`, `/m2/`, `/m2/<path>` | 302 → `/` + subpath, query preserved (`serve_m2_spa`) | unchanged (deep-link preserver kept) |
| `/` and SPA client routes | SPA (`serve_spa_root`) | unchanged |

New `serve_legacy()` body:

```python
@app.route("/legacy/")
@app.route("/legacy")
def serve_legacy():
    # Phase 4: the legacy shell is retired; bounce old bookmarks to the SPA.
    from flask import redirect
    return redirect("/", code=302)
```

Keep the route registered (don't delete the decorator) so an old `/legacy`
bookmark resolves cleanly instead of 404ing.

---

## C. Legacy-pinning tests to migrate — RE-DERIVED @276

GENERATED by `tools/legacy_pin_scan.py` (run it at cut time; do NOT hand-maintain this list -- that is how the 217 §C went stale). The tool errs toward over-reporting (a docstring/fixture mention may surface as a candidate) so it CANNOT miss a real pin; confirm each. Symbol/behavioral pins (test_u28_config_cache `_INDEX_HTML_CACHE`, test_legacy_parity ratchet, test_phase1_root_flip routes) are NOT path-based and are listed separately below. Re-scanned the whole `tests/` tree at 276 for reads of any asset §A deletes (`templates/index.html`, `templates/mobile.html`, `static/*.js`). The set is materially different from the 217 map: most of the old family already migrated, and four files that DO still pin a to-be-deleted asset were never listed. Authoritative @276 set below.

### C.1 Still-live pins — MUST migrate in the deletion cut

| Test file | What it pins @276 | New assertion after Phase 4 |
|---|---|---|
| `test_v3_43_60_captcha_relay` | `assert 'src="/static/captcha_relay.js"' in _index_html()` | **Re-express against the SPA captcha relay** (T12 surface); drop the index.html `<script src>` assert. Pairs with T12 — sequence after T12 lands. |
| `test_v3_43_60_vpn_ui` | asserts `src="/static/vpn_ui.js"` AND `src="/static/widgets.js"` in index.html | Drop both src asserts; lean on the SPA `Vpn` route + the surviving widget API (`/api/widgets/<scope>`). |
| `test_v3_43_60_widgets` | reads `Path("bulk_downloader/static/widgets.js")` directly | Re-point to the SPA widget components / widget API, or retire with rationale (legacy widgets.js is superseded by SPA rendering). |
| `test_v3_51_phase4` | reads `Path("bulk_downloader/static/widgets.js")` | Re-point or retire with rationale in the cut CHANGELOG. |
| `test_u28_config_cache` | save/restore/assert `bd_app._INDEX_HTML_CACHE` (11 refs — symbol, not a path) | Remove the `_INDEX_HTML_CACHE` save/restore/assert lines; keep the other cache-clear targets. Pairs with removing `_INDEX_HTML_CACHE` from the clear-list (§A.3). |
| `test_js_smoke` (whole file) | `tpl.replace('<script src="/static/app.js">…')` — lints index.html + app.js | **Delete the file.** Guarantees move to `tsc --noEmit` + `vite build` (JS parses) and `legacy_parity`/`gui_parity` (fetch paths have routes). |
| `test_legacy_parity` | the ratchet itself (behavioral, via `measure()`) | **Repurpose as terminal guard:** assert the shell is gone (`not index.html.exists()`, `not app.js.exists()`), `measure()['legacy_only_count']==0`, `legacy_total==0`; flip the baseline to `{"legacy_only":[],"legacy_only_count":0}`. |
| `test_phase1_root_flip` | `GET /legacy[/]` → 200 + CSRF meta + `bd_session` (behavioral) | `GET /legacy[/]` → **302**, `Location=="/"`; reserved-subpath 404 unchanged; the template-lint case deletes (no template to read). |

### C.2 Already migrated since 217 — NO action (do not re-investigate)

Re-verified absent from index.html/app.js at 276 (the 217 doc's §C work, already done): the entire `index.html`-reading family — `test_v3_43_20_fixes`, `_28_stash_deep`, `_29_plex_deep`, `_30_watch_folder`, `_33_ai_login`, `_35_account_pool`, `_37_jellyfin_deep`, `_37_retry_policy`, `_38_dashboard_widgets`, `_41_download_window`, `_42_storage_tier`, `_45_template_extractor`, `_48_audit` — plus **`test_themes_catalog`** (already re-pointed to `frontend/src/lib/themes.ts`) and **`test_v3_43_55_csrf_bootstrap`** (no longer reads the shell file; confirm at cut whether any `/legacy` 200-mint assertion remains, else it is already a no-op).

### C.3 Conditional

`test_d3_u9_opt_in` — delete ONLY if §A.4 (the dead `_m2_opt_*` cookie sweep) is taken in this cut; otherwise leave untouched.

### C.4 Confirmed NON-pins @276 (grep false-positives — no change)

`test_v3_66_219_share_target` (reads `/static/manifest.json`, which SURVIVES) · `test_v3_66_254_promote_gate` (`static/app.js` only as a fixture URL string) · `test_t11_approval_wired` (the `static/approval_ui.js` mention is a docstring) · `test_contracts` / `test_d3_u1_scaffold` / `test_d3_u2_v2_endpoints` / `test_d3_u8_polish` / `test_fresh_install_gui_smoke` (about `/m`,`/m2` shims, which survive).

## D. `{x}` dynamic-dispatch clearance proof (`legacy_only 1 → 0`)

**Claim:** deleting `static/app.js` drops `legacy_only` from 1 to 0, with zero
functional loss.

1. **The scanner.** `tools/legacy_parity.py` scans `bulk_downloader/static/*.js`
   and `templates/index.html` for `/api/...` literals (`_EP_RE`), collapses any
   `${...}` interpolation to `{x}` (`_collapse`), and reports
   `legacy_only = legacy − spa`. The committed baseline is exactly `["/api/{x}"]`
   (count 1).

2. **The sole producer.** The only literal in the entire legacy surface that
   collapses to `/api/{x}` is **`bulk_downloader/static/app.js:2660`**:

   ```js
   const r = await fetch(`/api/${action}_all`, {method:'POST', …});  // bulkSiteAction(action)
   ```

   `_EP_RE` captures `/api/${action`; `_collapse` rewrites `${action…}` → `{x}` →
   `/api/{x}`. A grep of both legacy files for `'/api/${'` returns **this one line**.

3. **Why the SPA never produces it.** `bulkSiteAction(action)` is the legacy
   "apply to every site" pill. The backend defines exactly three concrete targets
   (`app.py:16082-16086`): `/api/pause_all`, `/api/resume_all`, `/api/start_all`.
   The SPA wires all three as **full literals** — `Queue.tsx` (`pause_all`,
   `resume_all`, `start_all`) and `CommandPalette.tsx` (`pause_all`, `resume_all`).
   Full literals are scanned as their concrete selves and **never collapse** to
   `/api/{x}` (project rule: SPA wiring must use full `/api/…` literals — the exact
   anti-pattern the migration removes). So `/api/{x}` is, by construction, present
   in the legacy set and absent from the SPA set — permanently legacy-only until the
   producing file is deleted.

4. **The clearance.** Phase 4 deletes `static/app.js` (and the rest of the shell).
   `_legacy_files()` then yields no file containing `/api/${…}`, so the scanned
   legacy set no longer contains `/api/{x}`. `legacy_only = legacy − spa` →
   `[]` → **count 0**. The ratchet floor of 1 (a P4 tombstone by definition) is
   reached and the ratchet retires (§C.1, `test_legacy_parity` repurpose).

**Functional-loss check:** none. Every endpoint the legacy dispatcher could reach
(`start_all` / `pause_all` / `resume_all`) is already SPA-wired and backend-tested.
The `{x}` entry was a scanner artifact of the interpolation pattern, not a missing
SPA feature.

---

## E. Soak + rollback

### E.1 Pre-cut gates (in-sandbox, before building)
- `tsc --noEmit` + `vite build` green (the new "JS parses" floor replacing `test_js_smoke`).
- Targeted band from the extracted zip: the migrated tests (`test_legacy_parity`,
  `test_phase1_root_flip`, `test_u28_config_cache`, `test_themes_catalog`, the 13
  family files) green — run in small batches (never the whole `tests/` dir).
- `tools/legacy_parity.py --json` → `legacy_only_count == 0`.
- In-sync regen: `ENDPOINT_CATALOG` (route removed from `/legacy` body change is
  cosmetic, but `/legacy` still registered), `DEPENDENCY_GRAPH`, `gui_parity`
  (no SPA route change expected → inventory unchanged), then `check_route_counts` (G12).
- 7 release guards byte-identical (Phase 4 touches none — re-confirm SHAs).
- `verify_release.py --zip` → `RESULT: PASS` on true `$?`.

### E.2 Stash soak (post-deploy)
- Confirm `/api/health` reports the new version (clear `__pycache__`/`.pyc` first).
- `curl -sI localhost:5555/legacy` → **302**, `Location: /`. Same for `/legacy/`.
- `curl -s localhost:5555/static/app.js` → **404** (asset gone).
- `curl -s localhost:5555/` → SPA HTML (`<div id="root">`); `/sw.js` and
  `/manifest.json` still **200** (push + PWA intact).
- Full on-stash suite green (the binding gate) — expect the total to drop by the
  deleted whole-file tests and to stay 0-failed.
- Soak window: one operator day. Watch for any client still hard-loading `/legacy`
  assets (server logs: 404s on `/static/app.js` etc. from a stale bookmark are
  expected and harmless — they 302/404 cleanly, no 500s).

### E.3 Rollback
- Standard archive rollback: `python tools/rollback.py --archive <prev> --from <zip>`,
  then clear caches + restart + confirm `/api/health`.
- The deleted files return wholesale on rollback (they're in the prior zip), so
  `/legacy` is fully functional again immediately — no data migration, no DB change,
  nothing irreversible in Phase 4. This is a low-blast-radius cut: pure
  route-body + file-deletion + test-migration, no runtime/guard/datastore change.
- Trigger to roll back: any 5xx on `/`, `/sw.js`, or `/manifest.json`, or a
  full-suite failure that traces to a missed `index.html`/`app.js` referrer.

---

## F. Acceptance self-check

A reviewer can execute Phase 4 from this doc without further source spelunking,
**except** two explicitly-flagged day-of-cut confirmations: (1) re-grep
`captcha_relay.*` / `live_recorder.*` referrers (§A.1\*), and (2) the `THEMES`
export shape in `frontend/src/lib/themes.ts` for the themes-catalog test re-point
(§C.1). Everything else — deletion set, redirect map, per-test new assertions, the
`{x}` proof, soak/rollback — is pinned to specific 217 source locations above.
> [!WARNING]
> **Historical archive - do not execute.** This file is a point-in-time record. Its commands, procedures, paths, versions, and acceptance criteria may be obsolete; use active documentation and current release gates instead.
