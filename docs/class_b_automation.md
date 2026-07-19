# Class B Automation — Reversible Housekeeping (Phase B)

*v3.66.121. The second step of the approved AUTONOMY_POLICY_v2 design and the first
real autonomy — deliberately the safest. Default-OFF.*

## What Phase B is

Class B is reversible operational housekeeping: it writes only **regenerable** state,
launches **no external activity**, and touches **nothing correctness-critical** (no
templates, selectors, profiles, corpus, debt, captures, or logins — those are Class
C/D). Phase B builds four such actions plus the two Class B guardrails.

The four actions (`tools/autonomy_housekeeping.py`):

- **reorder_queue** — reorder the operator's PLAN queue by site readiness, then
  priority. The queue is a plan (nothing runs from it), so reordering is reversible.
- **generate_notifications** — derive in-GUI alerts from current state (broken
  templates, high drift, not-ready sites, review backlog). **In-GUI only — no
  external push.**
- **refresh_dashboard_cache** — recompute a dashboard snapshot and cache it
  (regenerable).
- **generate_review_packet** — assemble a read-only packet of pending reviews +
  evidence pointers (regenerable artifact).

Each action: checks the **kill switch first** (frozen ⇒ logged no-op), supports
`mode="suggest"` (compute what would change, apply nothing) and `mode="apply"`
(apply + log + make reversible), is recorded in an **append-only action log**, and is
**reversible** via `reverse_action`.

## Default-OFF — built, not turned on

Phase B builds the two Class B guardrails (`action_logging`, `reversibility`), which
makes Class B **eligible** for Level 3 (auto). It does **not** enable autonomy:

- the policy default for Class B stays **suggest**, so `can_autonomously("B")` is
  False until the operator deliberately runs
  `set_policy_level("B", "auto_with_guardrails", "operator", "reason")`;
- even when opted in, the **kill switch gates every action** and every apply is
  **logged and reversible**;
- **no background scheduler** is installed in this phase — actions are invoked
  explicitly (operator or API). A self-firing loop is a later, deliberately-gated
  step.

This keeps the project's invariant — *nothing autonomous by default* — true even as
the first automation capability lands.

## What it is NOT

Not Class C (no template/selector/profile/corpus/debt mutation), not Class D (no
capture or login execution, no external push). The tests assert the absence of those
constructs. Class C remains blocked: it still needs its six guardrails (oracle,
rollback, blast-radius cap, backlog cap, fail-closed review window, self-throttle),
which are Phases C/E.

## How it runs (functions, not web toggles)

Consistent with Phase A, mutations are audited functions; the cockpit shows read-only
views:

```
run_housekeeping(mode="suggest", by="operator")          # preview all four
run_housekeeping(actions=["reorder_queue"], mode="apply", by="operator")
reverse_action("<action_id>", by="operator")             # undo
```

Cockpit (read-only): a **Governance → Class B Housekeeping** page shows the Class B
level, auto-eligibility, kill-switch state, guardrail status, a suggest-mode preview
of all four actions, and the action log. Endpoints (GET): `/api/housekeeping/status`,
`/preview`, `/log`. Cockpit route count 104; POST surface unchanged.

## Next

Phase C builds the remaining guardrails (full kill switch, rollback engine, backlog
caps, fail-closed review windows, self-throttle), Phase D the review UI, Phase E the
held-out correctness oracle. No Class C auto exists until all of those are built,
tested, and enforced in code — and even then it is rare, per-site, never family-wide,
never for the pinned actions.
