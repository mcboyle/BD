# Promotion Activity (Phase G / G6)

*Cockpit area: **Governance → Promotion Activity**. Module:
`tools/autonomy_promotion.py` (read-only views over an append-only log). Shipped
v3.66.131.*

## What it is

The audit trail that ties G1–G5 together. As a site moves through the governance pipeline
— gaining or losing evidence-qualification (G1), its trust crossing the floor (G3), its
evidence going overdue for re-validation (G4) — each **transition** is appended to a
permanent log.

The name is deliberate but narrow: "Promotion Activity" is the activity of the
*governance* pipeline, **not** an applied change. The log records that a site's standing
moved; it never moves it, and it never applies or promotes anything. Because there is no
Class C apply path, `participation_eligible` never transitions to True — every entry shows
evidence/trust/validation movement with participation pinned at False.

## How transitions get recorded

A host-scheduled scan (`scan_and_record`, run from cron/CLI alongside `decay_all_trust`)
computes each site's current governance state, compares it to the last snapshot, and
appends a row for every field that changed. The first scan is a baseline — it records
nothing. There is **no button** for this in the cockpit; the cockpit only reads the log.

Tracked fields: `evidence_qualified`, `participation_eligible` (always False),
`trust_eligible`, `oracle_tier`, `validation_status`.

## Append-only, atomic

The activity log (`governance/promotion/activity.jsonl`) is append-only — it only grows,
prior rows are never rewritten, and the module has no truncate/delete path (it matches the
existing guardrail alerts log). The state snapshot (`snapshot.json`) is written atomically
(`.tmp` + replace).

## Cockpit views (read-only GET)

- `GET /api/promotion/status` — total transitions + the tracked-field list.
- `GET /api/promotion/activity` — the recent transition log (newest first in the page).
- `GET /api/promotion/site?site=…` — one site's transition history.

No POST — the scan is host-scheduled; the cockpit reads.

## In the sandbox

`status`/`activity` read whatever the log contains (empty until a scan runs). The tests
drive a real transition by decaying a site's trust below the floor and confirming the next
scan records the `trust_eligible` True -> False movement, that the log only grows, and that
no entry ever shows participation becoming eligible.

## Where this sits

G6 is the last Phase-G deliverable. With G1–G6 in place the governance *infrastructure* is
complete, and Class C is still not enabled (Approve-each, no apply path,
`participation_eligible` False everywhere). The recommended next step is a telemetry pause:
deploy to `stash`, wire `decay_all_trust` and `scan_and_record` into cron, and collect
about a week of real signal before leaning on the trust and transition data.
