# SPA UI/UX Improvement Plan — v3.66.356 baseline

Reference baseline: full render sweep this session — 21 nav tabs + 7 drill-in routes +
in-page filter tabs + full 7,631px Settings, in **both** light and dark. Harnesses:
`spa_render.sh` / `spa_tabs.py` / `subtabs_cap.py` / `subtabs_click.py` (theme-aware via
`BD_THEME`). All renders against the live backend on `127.0.0.1:5599`, empty instance.

> **Current status (2026-07-21): HISTORICAL BASELINE.** Later Phase 6 work
> shipped across v3.66.361-363 and supersedes this document's START HERE and
> future-tense status labels. Keep the design/validation rationale for history;
> use `CHANGELOG.md`, `docs/PHASE6_PLAN.md`, and the canonical tracker for current
> status.

**Release-risk (whole plan):** non-guard, non-route, **frontend-only** (`frontend/src/**`
+ `index.css`). Ships via the normal overlay — no in-sync doc regen, none of the 7 release
guards touched, no `@app.route` change. `/api/dashboard/v2` is shared (Home + Dashboard) and
stays. Settings re-architecture is the one higher-coupling exception (save handlers + a
version-pinned test in its test file).

**Validation gate per slice (non-negotiable):** `tsc` → `vite build` → `vitest` →
re-render the affected routes in chromium (both themes) and diff against the Slice-0 baseline.
Never claim a slice green without the re-render.

---

## Slice 0 — Regression baseline (DONE this session; formalize as the gate)
Pulled to the front from the original "item 51." We already have the both-theme render
sweep; this slice just *locks it as the pre-slice diff target*.
- Baseline PNGs: `$BD_OUT/tabs{,_dark}/`, `$BD_OUT/subtabs{,_dark}/`, Settings slices.
- Add a `vitest` smoke that mounts Home / Settings / a form page and asserts no thrown render.
- **Deferred-honestly:** live browser/noVNC routes (Capture wizard steps 2+) and populated
  data states (Queue/History/Library with jobs) are not sandbox-renderable.

## Slice 1a — Shared foundation (START HERE)
Lowest-risk, highest-coherence; **everything else builds on it.**
- Design tokens in `index.css`: card radius/border/shadow/surface, spacing scale, gap scale —
  light + `.dark` pairs. Lock the look so it can't drift.
- Harden the existing `PageHeader` component (title + subtitle + actions slot); adopt on the
  routes that lack a consistent header.
- Max content width + consistent horizontal padding (kill page sprawl + the ~10px overflow).
- Visible focus ring, dark-tuned (light/accent ring on dark bg).
- **Effort:** M · **Impact:** H (touches every page's chrome).

## Slice 1b — Home / Dashboard grid + the Dashboard decision
- Real CSS grid, equal row heights, equal-width KPI row, tightened gaps, widget min-height.
- Constrain grid to the content column (fixes Throughput clipping).
- Sane empty-instance default layout — **must not clobber an operator's saved/customized
  layout** (the Edit/WidgetPicker flow persists state; add a migration/guard).
- Throughput sparkline → flat baseline + caption, not an empty card.
- **Decision (locked):** keep both, differentiate. **Home = next-action operator cockpit.
  Dashboard → rename "System Overview"** (distinct widgets: Health checklist, Capacity,
  Weather, Route lookup justify keeping it).
- **Effort:** M · **Impact:** H.

## Slice 1c — Dark-mode form-control fixes
- Shared dark form-control tokens/classes for `textarea`/`input`/`select`/date/JSON/code/
  path/token fields — **prefer a shared class over one broad risky selector.** (White
  textareas confirmed on Batch ops, Imports, Import & Saved Views.)
- Full dark audit of every control type; lift low-contrast helper text; verify gated-write
  banner + status pills contrast on the dark header; chart/sparkline dark variants.
- **Effort:** S · **Impact:** M (correctness bug).

## Slice 2 — Settings Center (its own slice — highest coupling)
Re-architect the 7,631px scroll without dropping a control or breaking a save handler.
- Tabs / section sub-nav; sticky section index; "filter settings…" search (by label, helper
  text, env/config key); collapsible groups (General, Downloads, Browser, Network, Capture,
  Security & Access, AI Assist, Imports/Exports, Notifications, Advanced, System, Tools/Ops),
  dangerous/advanced collapsed by default; sticky Save; badge non-default settings.
- **Risk note:** mind the version-pinned test in the Settings test file; re-band it.
- **Effort:** L · **Impact:** H.

## Slice 3 — Empty states + first-run
- Standard empty-state pattern (marker + one line + explanation + primary action) everywhere;
  per-tab copy for Activity/History/Saved views; replace grey placeholder blocks (Imports,
  Rebalance) with real empty cards/skeletons; Home first-run "Add your first site →".
- **Effort:** M · **Impact:** M.

## Slice 4 — Navigation + mobile + form-heavy/workflow pages
- Mobile: bottom-bar overlap fix (safe-area inset + scroll padding); "More" overflow for
  grouped nav. Persist sidebar group state; highlight active parent; ⌘K affordance; collapsed
  icon-only + tooltips; safe count badges (Queue/Needs-review/Sites-issues/Failed).
- Form pages: sentence-case labels (drop ALL-CAPS micro-labels), labeled field cards, inline
  validation, consistent `SecretField` for all token/key/password inputs.
- Workflow pattern (title/purpose/step indicator/current panel/guidance/gated actions) for
  Capture, Template Manager, AI repair, Rebalance, VPN, Cluster, Secrets, Import plans.
- **Effort:** L · **Impact:** M.

## Slice 5 — Data display + feedback + danger treatment
- Density toggle (Queue/History/Library/Activity); sortable lists; loading skeletons; one
  shared formatter (bytes/durations/counts/percentages).
- Gated-write banner → compact, per-session-dismissible callout (keep a reopen/details path).
- Mutation feedback (toast/inline) on every write; richer global status chip (replace
  "Idle · 2s"); hover states on clickable cards/rows.
- **Danger Zone — presentational only.** Group existing high-risk controls (tokens, cockpit
  shell, dev/test runner, autonomy final-apply, filesystem roots, cleartext export, capture/
  redaction toggles) under Info/Caution/Danger callouts. **No behavior change, no rewording
  toward bypass/unlock/defeat** (operating-instructions wording conventions).
- **Effort:** L · **Impact:** M.

---

## Final report after each slice
1. What was already present. 2. What changed. 3. Files changed. 4. Commands/tests run + results.
5. Routes manually re-rendered (both themes). 6. Deferred items + why. 7. No protected
runtime/capture/extraction files modified. 8. Live/build/deploy values shown only from existing
safe status data.
