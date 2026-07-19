# Autonomy Governance (Phase A)

*v3.66.120. The governance foundation from `AUTONOMY_POLICY_v2.md` — the brakes and
the dashboard, built before any engine. Read-only / record-only; nothing here
executes autonomously.*

## What Phase A is

The first implementation step of the approved 2-D autonomy policy. It builds the
*model* and the *safety apparatus* — and deliberately no automation:

- **Policy model** — action classes by **write-target** (A advisory / B reversible
  housekeeping / C correctness-critical mutation = live config or corpus / D
  footprint-affecting = third party, irreversible) × involvement levels (0 Observe /
  1 Suggest / 2 Approve-each / 3 Auto-with-guardrails). Classes move independently.
- **Storage + versioning + hash** — the policy is a versioned JSON document; every
  edit bumps the version and writes an audit entry; a stable `policy_hash` over the
  load-bearing state (levels + thresholds + model) lets any decision be reproduced
  against the rules as they were.
- **Decision-snapshot recorder** — `record_decision_snapshot()` writes an immutable
  record (inputs, captures/scores/thresholds used, **plus the policy version + hash
  in effect**). Nothing produces decisions yet; the recorder is built and ready.
- **Independent kill switch** — a global freeze flag in a **separate file** from the
  policy it freezes, so a bad policy edit can never disable the brakes. Fails safe
  (unreadable freeze file ⇒ treated as frozen). `is_frozen()` is the contract every
  future automation must check.
- **Guardrail registry + enforcement primitive** — tracks which Level-3 guardrails
  exist (Phase A ships `kill_switch` + `decision_snapshot`; the rest are later
  phases). `can_autonomously(class)` is the gate future phases call — **False for
  every class in Phase A.**

## Safe by construction

- Default posture is the inherited one: A=observe, B=suggest, C=approve-each,
  D=approve-each. Nothing is autonomous.
- `set_policy_level()` is the **code-level hard gate**: it refuses Level 3 when a
  class's guardrails are absent, refuses it for read-only Class A, and refuses it for
  irreversible Class D (auto is *never* available for D — it requires Approve-each
  with periodic re-authorization).
- Several Class-C actions are **permanently pinned at Approve-each** and can never
  advance to auto: corpus writes, validation/correction debt retirement, finding
  confirmation/falsification, login template changes, release approval, posture-policy
  changes, and **automation-policy changes** (the policy's own edit path is
  human-only).

## Why mutations aren't web toggles

Policy edits and the kill switch are deliberate governance actions performed via
audited functions, not casual cockpit buttons (per the design doc's §7). They are
invoked at the function/CLI level:

```
freeze("operator", "reason")            # emergency global freeze
unfreeze("operator", "reason")
set_policy_level("B", "approve_each", "operator", "reason")
```

The cockpit exposes only **read-only** views of this state. This is a stronger gate
than a web button (it also works if the web app itself is the thing misbehaving) and
keeps the cockpit POST surface unchanged.

## Cockpit (read-only)

A **Governance → Autonomy Policy** page shows the matrix (configured level, ceiling,
Level-3 availability + why, guardrails required vs present, pinned actions), the kill-
switch state, the guardrail registry, and links to the audit log and decision
snapshots.

Endpoints (all GET, read-only): `/cockpit/api/policy/matrix`, `/status`, `/audit`,
`/snapshots`. Cockpit route count 101; POST surface unchanged.

## What comes next (and stays off until then)

Phase B (Class B automation), Phase C (rollback, backlog caps, fail-closed review
windows, self-throttle), Phase D (review UI), Phase E (held-out correctness oracle +
eligibility). No Class C auto exists until every guardrail is built, tested, and
enforced in code — and even then it is rare, per-site, never family-wide, and never
for the pinned actions.
