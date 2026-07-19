# Human Review Experience (Phase D)

*v3.66.123. The fourth step of the approved AUTONOMY_POLICY_v2 design. Makes human
approval of correctness-critical changes BETTER-INFORMED — the opposite of autonomy.*

## What Phase D is

A read-only composition over what Phases A–C already record, giving the operator the
context to approve or reject a Class C change well. It enables no autonomy (Class C
auto stays impossible until the Phase E oracle) and mutates nothing — the review
**decision** itself is committed via the existing audited path
(`autonomy_guardrails.mark_reviewed` / the inert `/api/review/decide`). Phase D only
informs that deliberate commit.

Four surfaces (`tools/autonomy_review.py`):

- **Evidence chain** — for a change: its decision snapshot (policy version + hash +
  the scores/thresholds/inputs used), the before/after/diff, the pending-review status
  and fail-closed deadline, and the site's evidence base. The falsifiable, later-
  reconstructable "why."
- **Before/after diff** — the change's before/after with a structured diff rendered as
  added / removed / changed.
- **Rollback preview** — exactly what reverting would restore (the before-state) and
  what's currently applied (the after-state), **without executing anything**.
- **Decision audit** — one timeline across the policy audit (Phase A), the Class B
  housekeeping log (Phase B), and the guardrail alerts + change ledger + review
  decisions (Phase C).

Plus a **review dashboard**: outstanding changes awaiting review, each with evidence-
chain / diff / rollback-preview pointers, ordered soonest fail-closed deadline first.

## Boundaries (enforced, tested)

- read-only: the module defines no apply / rollback / decide / freeze / policy-edit
  function; it only reads. (The decision flows through the existing audited path.)
- rollback preview never executes (the change stays not-rolled-back after a preview).
- no live fetch, no external push, no scheduler.
- Class C auto still impossible (only the correctness oracle remains, Phase E).

## Cockpit (read-only)

A **Governance → Review** page lists outstanding reviews; per-change sub-views show the
evidence chain, before/after diff, and rollback preview; a decision-audit page shows the
unified timeline. Endpoints (GET): `/api/review/dashboard`, `/evidence`, `/diff`,
`/rollback-preview`, `/audit`. Cockpit route count 112; POST surface unchanged.

(Phase D also added a delegated handler so in-page `data-p` links route correctly —
this fixed the earlier governance sub-view back-links too.)

## Next

Phase E — the held-out correctness oracle (descriptors only — never re-download or
byte-compare, per posture), identity matching, and the eligibility engine. It builds
the last guardrail and makes Class C auto *possible to consider* — and even then,
per-site, deliberate, never family-wide, never for the pinned actions.
