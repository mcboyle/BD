# LEGACY_MIGRATION_PLAN — full migration of the legacy shell into the D3 SPA

> **LIVE STATUS lives in TASK_TRACKER, not here.** This doc is a v3.66.201 *schedule*, not a tracker; phases/tranches it lists as future may already be LIVE (e.g. T1–T11 shipped by v3.66.264). Re-derive status from canonical `TASK_TRACKER_DATA.json` (with `TASK_TRACKER.md`/`.xlsx` as generated views) + `tools/legacy_parity.py`. Anchor any code reference on a SYMBOL, never a line number (they drift). Remaining live items: T12, P4, EXIT-1/3/4.

> **Current status (re-derived 2026-07-21):**
> `python tools/legacy_parity.py --json` reports `legacy_total=0`,
> `spa_total=358`, `legacy_only_count=0`, and `family_count=0`. That live tool
> output is authoritative for endpoint parity; it does not waive the separate
> operator soak/deletion exit criteria. Use the canonical tracker for those
> remaining disposition steps.

Status: PLAN, cut at v3.66.201 alongside the ratchet gate. **Every tranche below is per-task
authorized** — this document schedules nothing; it sequences. Source is ground truth; re-derive
the numbers with `python3 tools/legacy_parity.py` before starting any tranche.

## Goal / non-goals / exit criteria

**Goal:** the D3 SPA is the only frontend. The legacy shell
(`bulk_downloader/templates/index.html`, 2,036 lines + `bulk_downloader/static/*.js`,
21,716 lines — `app.js` alone is 16,365) is deleted with **zero functional loss**.

**Non-goals:** feature triage (this plan ports "full functionality" as asked — cruft included),
visual redesign, backend/API changes (the API surface is the contract; we move callers only),
and anything touching the 7 release guards.

**Exit criteria (all required):**
1. `tools/legacy_parity.py --check` green with an **empty baseline** (`legacy_only == 0`).
2. `tools/nav_reachability.py --check` green with the crawl **re-rooted at the SPA**.
3. Full on-stash suite green post-cutover + an operator **soak period** (recommend ≥ 2 weeks
   on Phase 1's `/legacy` escape hatch before deletion).
4. Legacy deletion cut: remove template + static JS + `/legacy` route (302 → `/`), final
   in-sync regen, the 4 legacy-pinning test files migrated or retired **with their contracts
   re-expressed against the SPA** (never silently dropped).

## Measured baseline (v3.66.201 tree)

Literal scan (`tools/legacy_parity.py`, param-collapsed): legacy calls **165** endpoints, the
SPA calls **110**, **legacy-only = 119 across 59 URL families**. Committed as
`reports/legacy_parity_baseline.json`; human snapshot in `reports/legacy_parity.md`.

## The ratchet gate (already live as of this cut)

`tools/legacy_parity.py --check` + `tests/test_legacy_parity.py` (band test, same pattern as
`nav_reachability` — deliberately NOT inside `build_release.py`, which is a guard):

- **Fails on growth**: any legacy-only endpoint not in the committed baseline aborts the band —
  from today, nobody can add a legacy-only feature by accident.
- **Passes on shrink**: migrated endpoints report as ratchet-available progress.
- **Per-tranche close-out**: `--write-baseline` (refuses to grow without `--allow-grow`, which
  is an explicit operator decision), commit the shrunken baseline in the tranche's cut.
- Scanner limit (documented in the tool): literal-based; concatenated bases are invisible. SPA
  convention already mandates full `/api/…` literals, so the SPA side is reliable; legacy
  over-count only makes the gate stricter.

## Phase 0 — prerequisites (1 cut + decisions, BEFORE any porting)

**STATUS: CLOSED at v3.66.202** — P0.1 implemented + RED-proven; P0.2/P0.3 operator-decided
2026-06-12. Phase 1 (root flip) is unblocked, attended, per-task authorized.

- **P0.1 CSRF bootstrap extraction. ✅ DONE (v3.66.202).** The token bootstrap lived in the
  legacy `index()` handler (and a `/`-path-gated after_request hook); `/api/csrf` *refused to
  mint* without an existing session. Fixed: `/api/csrf` is now the app-level bootstrap — no
  valid session → mint the same anonymous `source="csrf_bootstrap"` session a cookie-less
  `GET /` receives and set the cookie on its own response. Legacy paths untouched (additive).
  Blocking proof shipped: `tests/test_p01_csrf_bootstrap.py` (4 tests, proven RED on pristine
  201) — a client that never loads the legacy shell completes a CSRF-protected POST. Old
  refusal pin in `test_security.py` re-expressed, not dropped.
- **P0.2 SW/PWA scope decision. ✅ DECIDED: vite-built SW** (operator, 2026-06-12). The SW
  joins the one build pipeline; Flask serves the built artifact at root scope (`/sw.js`) so
  installed PWAs and push subscriptions survive. Lands in T9; bundle layout may assume a
  vite SW entry from T1 onward.
- **P0.3 IA decision. ✅ DECIDED: extend the 200 pattern** (operator, 2026-06-12). Settings
  sections + command palette as the index, dedicated routes per ported page — the convention
  the 200 nav-consolidation cut proved, held honest by `nav_reachability`. The tab bar stays
  contract-frozen at 5 (`test_d3_u3`). A top-level "Ops" hub remains a possible later
  *refinement* if T4–T5 operational surfaces feel cramped; it is not the foundation.


## Phase 1 — root flip (Plan 3 core; 1–2 cuts, attended)

**STATUS: SHIPPED at v3.66.203.** `/` serves the SPA (vite base + router basename re-rooted
to "/"; `serve_spa_root` catch-all with reserved-namespace 404 discipline + the not-built 503
surface); legacy moved to `/legacy` FULLY FUNCTIONAL (`serve_legacy` = pre-flip `index()`
verbatim; inline CSRF mint intact; `_bootstrap_session` hook covers both shells; token
allowlist extended). `/m`, `/m/ops`, `/m2` are 302 shims to root (`/m2` deep-link- and
query-preserving). `nav_reachability` re-rooted: crawl roots `{/, /legacy}`, `/m2` joins the
redirect shims. Full in-sync regen done. Pre-flip contracts re-expressed (never dropped) in
test_v3_43_55_csrf_bootstrap / test_d3_u1_scaffold / test_d3_u9_opt_in / test_d3_u8_polish /
test_fresh_install_gui_smoke; flip contract pinned in tests/test_phase1_root_flip.py (13
tests). **Operator click-through post-deploy still required before T1 starts.**

`/` serves the SPA; legacy moves to `/legacy` **fully functional** (escape hatch for the whole
program). Route change → full in-sync regen (endpoint catalog, parity inventory, dependency
graph, G12, pin sweep). Re-root `nav_reachability` at the SPA with `/legacy` allowlisted.
Operator click-through required. Highest single-step risk in the plan; it ships alone.

## Phase 2 — porting tranches (T1–T10)

Ordered cheap/read-only → stateful → integration → operationally hot.

**Pacing AMENDED (operator, 2026-06-12), T1/T2 already landed solo:** remaining tranches
batch as **5 cuts** — **T3+T4** → **T5+T6** → **T7 solo** (secrets / `(R)` redaction
pairing deserves isolation) → **T8+T10** (both attended-review surfaces in one attended
cut; T10's dispatcher literal-ization lands before T9) → **T9 solo, last** (HOT/attended;
push-subscription migration gets maximum soak). Per-tranche rules below apply per batch:
one bump, one zip, both families' pins + ratchet shrink in the same cut. Each cut:
wire SPA pages/sections per P0.3 · per-family **confirm+audit gating** on writes (GUI-parity
contract: secrets never displayed, never one-click, `(R)` writes only alongside redaction) ·
tests · ratchet baseline · band incl. `test_legacy_parity.py` + `test_nav_reachability.py`.

| T | Families (legacy-only count) | Notes |
|---|---|---|
| T1 | dashboard 1 · stats 3 · hourly_stats 1 · capacity 1 · status 1 · session_status 1 · health 1 · widgets 1 · weather 1 · changelog 1 · route_urls 1 — **13** | Pure read-only dashboard. `widgets.js` (790 ln) is a mini-framework — port as one SPA dashboard route, not 1:1 widgets. |
| T2 | history 2 · session_history 1 · events_all 1 · ui_events 2 · logs 2 · search 1 · saved_searches 3 — **12** | Read-heavy + small writes (log clear, vacuum, saved-search CRUD) → confirm-gated. |
| T3 | library 4 · tags 6 · scene_score 1 · storage_rebalance 1 — **12** | Extends existing SPA `/library` + `/rebalance` routes. `library.js` (479 ln) retires here. |
| T4 | sites 3 (bulk CSV/XLSX) · runners 2 · concurrent 1 · rate_limit 1 · retry_policy 1 · crash_recovery 2 · file 1 — **11** | Operational controls. `pause_all`/`resume_all` and crash-recovery actions are dangerous-selection class → confirm+audit. |
| T5 | retention 3 · rights 3 · scheduled_exports 4 · diagnostics_bundle 2 — **12** | Destructive-capable (retention apply, blocklist) → preview-first UX ports verbatim. |
| T6 | plex_advanced 6 · tpdb 2 · subtitles 1 · thumbnail_sheets 1 · marketplace 1 · jsonapi 1 · ai 2 — **14** | External integrations; read-mostly. Low risk, high count. |
| T7 | notify 3 · tg 3 · alerts 1 — **7** | Notification settings carry secrets (tokens/URLs) → secret-input rules: masked, write-only, capture-body redaction in the same tranche. |
| T8 | fed 4 · edge_deploy 2 · pair 2 — **8** | Federation/pairing; `pair/redeem` is an auth surface → attended review. |
| T9 | live 4 · stream 1 · push 4 (+ SW/PWA per P0.2) — **9** | **HOT, attended.** `live_recorder.js` (349 ln) + stream tokens + push re-subscription migration (existing subscriptions must survive). |
| T10 | template 3 · templates 1 · macros 3 · dev 4 · plugins 2 · synthetic_tests 2 · i18n 1 · `/api/{x}` dispatcher 1 · csrf 1 (verify P0.1 closed it) — **18** | Template sandbox/extract/refine + the generic action dispatcher: replace dynamic `/api/${action}` with explicit literals so the scanner (and the parity inventory) finally sees the truth. |

Running total: 13+12+12+11+12+14+7+8+9+18 = **116**; the residual 3 are the one
unassigned **vpn** family (3 endpoints), not singletons — 116 + 3 = **119** (baseline).
Re-derive per tranche — the baseline is the truth, this table is the map.

## Phase 3 — guardrail surfaces (attended, LAST, one cut each)

- **T11 approval_ui** (`approval_ui.js`, 233 ln) — the **fail-open-into-review** workflow.
  Port with contract tests proving the review path still interposes; a regression here is a
  safety-surface regression, not a UI bug.
- **T12 captcha_relay** (`captcha_relay.js`, 343 ln + css) — the **manual challenge handoff**
  surface, an explicit manual-only boundary in AUTOMATION_POLICY. Port 1:1, no "improvement":
  challenge detection/logging → operator handoff → fail open into review. On-stash validation
  against a real challenge before its baseline ratchet.

## Phase 4 — retirement (1 cut + soak)

Exit criteria 1–3 green → delete `templates/index.html` + `static/*.js` (~23.7k lines),
`/legacy` → 302 `/`, final in-sync regen, migrate/retire the 4 legacy-pinning tests
(`test_v3_43_60_captcha_relay` re-targets the SPA relay; the others re-express or retire with
rationale in the cut's CHANGELOG). `legacy_parity` stays in the band forever with the empty
baseline as the tombstone.

## Risk register

| Risk | Mitigation |
|---|---|
| CSRF bootstrap coupled to legacy render | P0.1 blocking, test-proven before Phase 1 |
| Root-scope SW / push subscription loss | P0.2 decision + T9 attended; keep SW root-scoped |
| Test-weakening during rewrites | Per-tranche rule: every retired legacy test names its replacement in the cut; contracts re-expressed, never dropped |
| Interim dual-UI surface (worse before better) | `/legacy` escape hatch + ratchet means the gap only shrinks; soak before deletion |
| Frozen 5-tab contract vs 59 families | P0.3 IA decision once; `nav_reachability` enforces homes |
| SSE multiplexing (legacy is stream-driven everywhere) | SPA already speaks `/api/stream`; per-tranche the ported page proves live updates before ratchet |
| Bundle growth (chunk-size warning pre-exists) | Route-level code-splitting from T1; revisit at T6 |
| Stash drift (operator live-edits) | Same discipline as `cockpit_console.py`: report divergence, merge-not-overlay where flagged |
| Scanner blind spots (concatenated bases) | T10 literal-izes the `/api/${action}` dispatcher; SPA literal convention enforced by review |

## Effort (honest)

Phase 0: 1 cut + 2 decisions. Phase 1: 1–2 cuts, attended. Phase 2: **7 cuts** under the
amended pacing (T1, T2 landed solo; then T3+T4 · T5+T6 · T7 · T8+T10 · T9 — batched cuts
are wave-sized ~23–26 families; T9 attended). Phase 3: 2 attended cuts. Phase 4: 1 cut +
soak. **≈ 15–17 cuts total**, multi-session — comparable to the GUI-parity program, which is
the honest answer to "how hard." Nothing here touches a guard file; every cut keeps 7/7
byte-identical.
