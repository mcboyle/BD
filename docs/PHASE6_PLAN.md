# Phase 6 — SPA UI/UX, deferred from Slices 4–5 (v3.66.360 baseline)

Carries the items intentionally deferred while finishing Slices 4c/4d/5. All are
**frontend-only, non-guard, no-route** unless noted. Each ships via the normal
overlay; the per-slice gate is unchanged (tsc → vite build → vitest → both-theme
re-render diff vs the Slice-0 baseline; cockpit items add the chromium
`render_check.py` gate + single-file overlay).

## Shipped in 4c/4d/5 (for reference — NOT Phase 6)
- 4c: SecretField sweep (8 raw password inputs → SecretField); shared `ui/FieldCard`
  (labeled field + inline-validation `error`) adopted in AddSiteWizard with advisory
  URL validation; `/queue` PageHeader mobile-wrap.
- 4d: shared `ui/WorkflowSteps` stepper (aria-current) extracted from AddSiteWizard.
- 5 (done): dark muted-text fix (`muted-foreground` → canonical `--ink-3`; 174 usages,
  per-theme); shared `ui/Callout` (Info/Caution/Danger); Settings "Security & access"
  framed as a presentational Danger zone.

## Phase 6 backlog

### P6-1 — Data display (Slice 5 carryover) · effort L
- Density toggle on Queue / History / Library / Activity.
- Sortable lists (column sort on the same list pages).
- Broader loading skeletons — a `Skeleton` primitive already exists (Home uses it);
  adopt on the list pages that still show a bare spinner / blank.

### P6-2 — Feedback (Slice 5 carryover) · effort M
- Richer global status chip: DesktopShell footer "Idle · Nm" → also surface queued
  count + worker usage. NOTE: needs those fields in the footer's health query (today it
  reads only `active_downloads`); either widen that query or reuse `/api/dashboard/v2`.
- Exhaustive per-write mutation feedback: toasts already fire on many writes (sonner is
  wired); audit for any write that still gives no toast/inline confirmation.

### P6-3 — Gated-write banner → Callout convergence · effort M
- The global write-gating / autonomy banner adopts the shared `ui/Callout` look
  (VISUAL_UNIFICATION step 3 — SPA gated-write banner + cockpit Posture banner converge).
- CAUTION: `AttentionBanner` is contract-frozen (`test_d3_u3_attention_banner_uses_resolve_endpoint`,
  `test_d3_u8_polish` role=alert/aria-live). Converge its *visuals* only, or leave it and
  apply Callout to the other banners.

### P6-4 — Remaining Danger/Caution callouts · effort S
- The high-risk clusters NOT under "Security & access" get Caution/Danger callouts
  (presentational, wording-convention-safe — no bypass/unlock/defeat): autonomy
  final-apply, cleartext export, capture/redaction toggles.

### P6-5 — Shared formatter consolidation · effort S
- `lib/format.ts` already centralizes bytes/rate/eta/count/delta. Sweep remaining
  ad-hoc inline formatting onto it (mostly satisfied; this is a tidy-up).

### P6-6 — Eyebrow typography decision (VISUAL_UNIFICATION) · effort M
- The app-wide tiny-uppercase eyebrow language (section eyebrows, KPI labels, stepper,
  status badges) is intentional and consistent. Slice 4c found form *field* labels are
  already sentence-case. Decide keep-vs-sentence-case for the eyebrows as a deliberate
  VISUAL_UNIFICATION typography step — NOT a blind `uppercase`-class sweep (it would diff
  massively against baseline and touch the whole app).

### P6-7 — VISUAL_UNIFICATION cockpit side · effort L · render_check-gated
- Cockpit Posture banner → Callout look; cockpit token reconciliation (Option A: cockpit
  consumes the canonical 62-theme catalog, vs Option B: default-parity only); the
  remaining VISUAL_UNIFICATION element steps (status pills, buttons, cards/grid, form
  controls, PageHeader, empty states, nav) applied cockpit-side.
- Cockpit regime: RED-first structural test where applicable → chromium runtime probe +
  `render_check.py` (mandatory) → single-file overlay `unzip -o <zip> tools/cockpit_console.py`.

### P6-8 — Badges (Settings item 26) · effort M · BACKEND CUT
- Badge non-default settings. Needs a NEW `/api/global_config/defaults` route → this is a
  backend cut (route → in-sync regen: ENDPOINT_CATALOG / DEPENDENCY_GRAPH / gui_parity /
  check_route_counts), not a frontend-only overlay.

## Deferred-honestly (not sandbox-verifiable)
- Live noVNC / Capture wizard steps 2+ (cross-origin iframe; manual/operator flow only).
- Populated-data states (Queue/History/Library with real jobs) — the sandbox instance is
  empty; these need a seeded instance or operator click-through.
