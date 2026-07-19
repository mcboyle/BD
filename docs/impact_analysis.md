# Impact Analysis (Phase G / G5)

*Cockpit area: **Governance → Impact Analysis**. Module:
`tools/autonomy_impact.py` (read-only). Shipped v3.66.130.*

## What it is

A read-only "what would this change cost?" analysis for a **single** proposed change. You
hand it a candidate (a site, optionally a target kind and an action) and it composes the
earlier governance phases into one report:

- **Blast radius** — how many sites the change would touch. A single-site change is radius
  1. A candidate naming more than one site is flagged **family-wide**, which is explicitly
  out of scope — this tool analyses one change at a time and does not promote across a
  family. It also reports the concurrency limit (no other site may have an in-flight
  change).
- **Reversibility** (from G2) — is a reverser registered for the target kind? An
  irreversible change is a hard concern.
- **Pinned target** — does it touch a permanently-ineligible action or credential?
- **Oracle tier + trust + evidence-qualification** (from G1/G3) — the site's standing.

It rolls these into `safe_to_consider`: True only if the change would clear every gate.
**`safe_to_consider` is not authorization.** `participation_eligible` is False for every
candidate, because there is still no Class C apply path and the default is Approve-each.
The tool tells you whether a change *would be* admissible; it never makes one happen.

## Cockpit views (read-only GET)

- `GET /api/impact/status` — summary across sites (how many would clear all gates;
  whether anything is participation-eligible — always no).
- `GET /api/impact/overview` — per-site impact of a benign reversible probe change.
- `GET /api/impact/analyze?site=…&target_kind=…&action=…` — analyse a specific proposed
  change.

No POST — it analyses, it does not apply or promote.

## Reading a report

`concerns[]` lists every blocking factor: irreversible target, pinned action, not
evidence-qualified (with the eligibility decay reasons), family-wide footprint, or an
in-flight change on another site. An empty `concerns` list with `safe_to_consider: true`
means the change would pass the gates — and would *still* sit behind Approve-each with no
apply path.

## In the sandbox

`status`/`overview` enumerate configured sites (empty in the sandbox) and run a benign
reversible probe against each. The per-change `analyze` endpoint works for any site/target
you pass. The tests drive several candidate shapes (reversible, irreversible, pinned,
family-wide) and confirm `participation_eligible` is False in every case.
