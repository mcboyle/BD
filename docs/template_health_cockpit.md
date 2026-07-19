# Template Health — cockpit area

*v3.66.110. The Template Intelligence area in the operator cockpit. Read-only,
recognition-only.*

This is a first-class cockpit area (not buried in generic reports) that tells the
operator why login/download detection works or fails, what changed, and what to
review next.

## Pages (v3.66.110 — priority 1 shipped)

- **Video Templates** — per-site video/download template health + selector drift.
- **Download Decision Explorer** — why a download candidate is chosen (pure scorer,
  narrated). Shows candidates, scores, reasons, resolution tiers, chosen vs.
  rejected.

Reached from the cockpit nav under **Templates**.

## Planned (subsequent priorities)

- Login Templates (health), Template Health (combined), Template Drift, Template
  Review Queue, two-step flow model, and the Template Autopilot guided flow. These
  follow the same read-only / recognition-only / suggest-don't-apply contract.

## Contract (every surface in this area)

- read-only: no writes to `sites_config.json`, no corpus writes, no debt retirement
- recognition-only: no live page fetch, no model/network call, no request replay,
  no captured-token reuse, no generated browser replay scripts
- credential and signing **values** are never shown — only field names, selectors,
  and query-stripped paths
- suggested template updates are **data only** — the operator approves/promotes
  manually; nothing is auto-applied
- a safe login dry-run (planned) identifies fields and reports confidence and does
  **not** submit credentials except via the existing approved login path, and never
  exposes credential values

## Endpoints (v3.66.110)

- `GET /cockpit/api/template/video-health`
- `GET /cockpit/api/template/download-explain`

Both GET, both read-only; the cockpit route count is 83 and the POST surface is
unchanged.
