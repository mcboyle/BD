# Rollback Center (Phase G / G2)

*Cockpit area: **Governance → Rollback Center**. Module:
`tools/autonomy_rollback.py` (read-only). Shipped v3.66.127.*

## What it is

A read-only window onto the guardrail **rollback engine** — the safety machinery that
lets a Class C correctness change be undone. It shows what can be reverted, what has been
reverted, which review windows are about to expire, and the registry of change kinds that
are reversible at all.

It is **read-only**. Reverting a change is an audited guardrail function
(`autonomy_guardrails.rollback`, the reject path of `mark_reviewed`, or
`sweep_review_windows`), invoked by the host-scheduled autonomy cycle or by the operator.
It is never a button in the cockpit, and this module never executes it.

## The engine's guarantees (unchanged; re-anchored here)

These are properties of the guardrail engine, not new behavior:

- **A revert restores before-state and is idempotent.** Rolling back a change returns the
  target to its recorded "before"; rolling back again is a no-op.
- **Review rejection triggers an immediate revert.** Rejecting a pending Class C change
  rolls it back at once.
- **Expired-unreviewed Class C changes auto-revert (fail-closed).** If a Class C change's
  review window lapses unreviewed, the next host-invoked sweep reverts it.
- **A reverser error freezes all automation.** If a revert itself errors, the
  guardrail-failure branch freezes everything rather than leaving state half-reverted —
  absence of a working guardrail is treated like the kill switch being on.

## The reverser registry — and why it gates eligibility

A change can only be reverted if a **reverser** is registered for its *target kind*. In
this build only the confined `staging_json` target is registered; a real Class C apply
path (a later phase) would register its own reverser at apply time.

G2 wires this into eligibility (G1): a candidate change whose target kind has **no
registered reverser is irreversible**, and an irreversible change is never
evidence-qualified. So "can this be rolled back?" is now a precondition for "could this
site ever participate?" — you cannot qualify a change you cannot undo. The eligibility
verdict carries `rollback_target_kind` and `rollback_capable` to make this explicit.

## Cockpit views (all read-only GET)

- `GET /api/rollback/center` — dashboard: engine operational?, reverser count, changes
  recorded vs reverted, pending/expiring windows, rollback + review-expiry rates, freeze
  state.
- `GET /api/rollback/history` — recorded changes and which were reverted.
- `GET /api/rollback/reversibility` — per change: reversible right now? (reverser
  registered AND not already rolled back), with the irreversible-pending list.
- `GET /api/rollback/reversers` — the registered-reverser registry (the change kinds that
  are reversible, and thus eligible).

No POST is added — the center shows the rollback state; it never reverts.

## In the sandbox

With no Class C apply path, nothing produces real auto-changes, so the history and
reversibility views render against the changes the guardrail tests/operators record (or
empty). `staging_json` is the only registered reverser, so any other target kind reads as
irreversible — which is exactly why no real Class C target is eligible yet.
