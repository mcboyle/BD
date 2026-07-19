# Validation Operations (Phase G / G4)

*Cockpit area: **Governance → Validation Ops**. Module:
`tools/autonomy_validation.py` (read-only, advisory). Shipped v3.66.129.*

## What it is

A re-validation **schedule** for each site's held-out evidence. Held-out captures age; once
they pass the 30-day freshness floor, the site is no longer evidence-qualified (the G1
freshness decay). This view gives you lead time: it flags a site **due soon** at 21 days —
before the floor — so you can refresh evidence in time, and **overdue** once evidence is
already stale.

It is purely **advisory and read-only**. It tells you *when* to re-validate; it never does
it. Re-capturing held-out evidence, logging into a site, and re-running the oracle are
operator/host actions. This module does not capture, does not log in, does not drive a
browser, and does not touch the network. It also adds no eligibility gate of its own —
freshness is already enforced by eligibility; this is the operational heads-up layered on
top.

## Status buckets

For each site, from when its held-out evidence was designated:

- **current** — within the 21-day interval; nothing to do.
- **due_soon** — past 21 days but still within the 30-day floor; re-validate soon to avoid
  losing eligibility.
- **overdue** — past the 30-day floor; evidence is stale and the site has *already* lost
  eligibility (confirm against the Eligibility Governance view).
- **never** — no held-out evidence has been designated; re-validation can't be scheduled
  until a human designates held-out captures.

`overdue` deliberately uses the same floor eligibility uses (`FRESH_FLOOR_DAYS` mirrors
`eligibility.EVIDENCE_FRESH_DAYS`), so the two views never disagree about staleness.

## Cockpit views (read-only GET)

- `GET /api/validation/status` — interval/floor and due-soon/overdue/never counts.
- `GET /api/validation/overview` — per-site schedule + status counts.
- `GET /api/validation/site?site=…` — per-site detail: evidence age, status, recommended
  re-validation date.

No POST — the view schedules nothing and executes nothing.

## In the sandbox

`validation_status`/`validation_overview` enumerate configured sites via the sites config,
which is empty in the sandbox, so they render empty there. Per-site
`validation_schedule(site)` works against whatever oracle provenance exists. The tests seed
provenance at several ages to exercise all four buckets.
