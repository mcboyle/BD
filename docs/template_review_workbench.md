# Template Review Workbench

*v3.66.113 (Phase 4). The human review layer. Read-only data; the only write is the
operator's own inert accept/reject/defer decision. See `template_health_cockpit.md`.*

## What it is

One cockpit page — **Template Review Workbench** (`/cockpit/api/template/review-queue`)
— that turns the login + video template suggestions from Phases 2–3 into reviewable
items the operator can approve, reject, or defer.

For each suggestion it shows:

- **Side-by-side before/after** — the current template selectors vs. the suggested
  ones, per selector group, with added (+) / removed (−) / unchanged marked.
- **Confidence explanation** — why it's suggested (the flagged reasons), the
  selector-confidence signal, and recent success rate where relevant.
- **Change history** — the site's recorded drift events (what changed over time).
- **Capture evidence** — pointers to the site's corpus/capture evidence (subject +
  date + category, query-stripped; captures are binary and never echoed).
- **Decision** — any recorded accept/reject/defer, plus approve / reject / defer
  buttons.

## The boundary: recording ≠ applying

This is the first review surface that records a decision, so the boundary is
explicit and enforced:

- Approve / reject / defer is recorded through the **existing inert decision store**
  (`/api/review/decide` → `cockpit_core.review_decide`), which states plainly that
  *recording a decision never applies it*.
- The workbench adds **no new POST endpoint** — it reuses that existing inert
  endpoint, so the cockpit's POST surface is unchanged.
- The cockpit **does not rewrite `sites_config`**. Applying an approved template to
  the live config stays the **existing approved path** — the workbench surfaces the
  exact before/after so the operator can apply it deliberately.
- Every item is `applies_automatically: false`. Nothing is auto-promoted, no debt is
  auto-retired.

## Boundaries (enforced, tested)

- read-only data; the only write is the operator's inert decision note (no
  `_store_save`, no `write_text`, no `json.dump(`, no `_apply_detected_selectors`)
- no new POST surface; approve/reject reuses the existing inert decision endpoint
- the cockpit never rewrites `sites_config`; apply via the existing path
- recognition-only: no live fetch, no model/network call, no replay
- suggestions are data-only; approve records a human decision, it does not apply it

## Endpoint (v3.66.113)

`GET /cockpit/api/template/review-queue` — read-only. Approve/reject posts to the
existing `POST /cockpit/api/review/decide`. Cockpit route count 89; POST surface
unchanged (21).
