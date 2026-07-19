# Template Autopilot

*v3.66.116 (Phase 7). Operator-guided, NOT automation. Read-only orchestration that
ends at the human decision. See `template_review_workbench.md`.*

## What it is

One cockpit page — **Template Autopilot** — where the operator enters a URL or site
id and runs the guided chain:

```
URL/site → detect site → load login template → login health check →
load video template → download analysis → drift check →
generate suggested updates → review queue → human decision
```

It sequences the read-only checks from Phases 1–6 and stops at the Template Review
Workbench. Nothing is automated and nothing is applied.

## Key boundary: detection is recognition, not fetching

"Detect site" matches the URL/site against the stored `sites_config` (by id/name or
host) — **the URL is never fetched**. An unrecognised target returns
`not_recognized` and says so explicitly; the operator adds the site to proceed. The
live login and live download still run only via the existing approved paths — the
download-analysis step here uses the pure scorer on sample candidates (flagged
`is_sample`) unless live candidates are supplied.

## The run

Each step reports its status and result: detect (with inferred family), login
template presence + health, video template presence + download analysis, drift
signals, data-only suggested updates (login + video), and the review-queue items for
the site. It ends pointing at the review workbench, where approve/reject is recorded
through the existing inert decision — never auto-applied.

## Boundaries (enforced, tested)

- recognition-only: detection never fetches the URL; no live page, no model, no
  replay, no `do_login`/fill/click, no `web_fetch`
- read-only: no writes (no `write_text`, no `json.dump(`, no `_store_save`)
- suggestions are data-only; nothing is auto-applied or promoted
- ends at the human decision (the review workbench); the live login/download run via
  the existing approved paths

## Endpoint (v3.66.116)

`GET /cockpit/api/template/autopilot?target=…` — GET, read-only. Cockpit route count
94; POST surface unchanged.
