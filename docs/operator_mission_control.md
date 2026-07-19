# Operator Mission Control

*v3.66.119 (Phase 10 — the capstone). Read-only. The single operator screen, rolling
up the whole template-intelligence stack (Phases 1–9) + ops state. See
`template_health_cockpit.md`.*

## What it is

One cockpit page — **Operator Mission Control** (first in the Templates nav) — that
answers "what should I look at?" across all sites in four zones. Pure read-only
roll-up: no live fetch, no model, no replay, no writes — nothing here acts.

## The four zones

- **Needs Attention** — broken login templates, broken video templates (missing or
  stale), high-drift sites, not-ready sites (Phase 9), open template reviews, and
  open correction/validation debt.
- **Healthy** — ready sites (Phase 9), trusted templates (Phase 3 maturity), and
  sites with fresh evidence (≤30 days).
- **Active Work** — captures currently running and the running tasks (from the
  existing ops `mission_control`), the review queue size, and recent drift (last 7
  days + recent corpus drift verdicts).
- **Recommended Actions** — a prioritised, de-duplicated list of **suggestions**
  tied to a site: `review_template`, `investigate_drift`, `run_capture`,
  `refresh_evidence`, each with a reason and a priority. These are suggestions only —
  the operator decides and acts via the existing paths (capture, the review
  workbench, etc.).

## Composition, not new capability

Mission Control composes existing read-only signals — `site_readiness`,
`login/video_template_health`, `drift_frequency`, `template_review_queue`,
`template_maturity_score`, and the existing ops `cockpit_core.mission_control`
(active captures, debt, recent drift). It adds no new data source and no new action.

## Boundaries (enforced, tested)

- read-only: no writes (no `write_text`, no `json.dump(`, no `_store_save`),
  no live fetch, no replay, no `do_login`, no apply
- nothing acts: recommended actions are suggestions tied to a site; the status line
  states it plainly
- composes the existing ops mission_control rather than reimplementing it

## Endpoint (v3.66.119)

`GET /cockpit/api/template/mission-control` — GET, read-only. Cockpit route count 97;
POST surface unchanged.
