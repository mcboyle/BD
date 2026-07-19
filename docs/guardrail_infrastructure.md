# Guardrail Infrastructure (Phase C)

*v3.66.122. The third step of the approved AUTONOMY_POLICY_v2 design. Builds the
safety apparatus that Class C autonomy would require — WITHOUT enabling it.*

## The headline property

Building these guardrails does **not** turn on Class C autonomy. After Phase C the
**only** unbuilt guardrail is the correctness oracle (Phase E), so
`set_policy_level("C", "auto_with_guardrails", ...)` is still refused — with
`missing_guardrails == ["correctness_oracle"]` — and Class C stays at **Approve-each**
by default. This phase is the brakes, rollback, caps, fail-closed review windows, and
self-throttle for a capability that remains off.

## What it provides (`tools/autonomy_guardrails.py`)

- **Rollback engine** (§5.3) — records before/after/diff per change and reverses it
  via a registered reverser. Fully functional and tested end-to-end on a **confined
  staging target** (a regenerable file under `governance/guardrails/staging/`); it
  never writes live config or the corpus. A future Class C apply registers its own
  reverser for its real target.
- **Backlog + blast-radius caps** (§5.4) — caps outstanding-unreviewed auto-changes
  (`BACKLOG_CAP`); enforces **one site at a time** (`MAX_INFLIGHT_SITES = 1`); no
  family-wide.
- **Review windows — FAIL-CLOSED for Class C** (§5.5) — an applied Class C auto-change
  is pending-review with a deadline; if not reviewed within the window, the sweep
  **auto-reverts** it. Reversible Class B may stay provisional (fail-open). The sweep
  is invoked explicitly (no scheduler).
- **Self-throttle** (§5.7) — computes auto-apply / rollback / review-expiry rates; on
  a breach it **automatically demotes Class C to Approve-each** and alerts. The
  demotion is **lower-only** (it can never raise a level, so it is always safe) and is
  recorded as a distinct `safety_demote` audit action — separate from the human
  governance edit path, which the pin still guards. Oracle-disagreement-rate is
  reported as null until Phase E builds the oracle.
- **Guardrail-failure branch** (§5.8) — if a guardrail itself fails (a rollback errors,
  a record can't be written), the response is **freeze-and-alert**, never proceed.
  Missing/broken guardrails are treated like the kill switch being on.

## Boundaries (enforced, tested)

- Class C auto stays impossible (only the oracle remains); default C level unchanged.
- Rollback never writes live config / corpus — only the confined staging target.
- Alerts are in-GUI only (no external push).
- `safety_demote` can only lower autonomy, never raise.
- No background scheduler; atomic + utf-8 writes; runtime state never shipped.

## How it runs (functions, not web toggles)

```
record_change("staging_json", ref, before, after, by="operator")
rollback("<change_id>", by="operator")
sweep_review_windows(by="operator")        # fail-closed for Class C
self_throttle_check(by="operator")         # lower-only demote on breach
```

Cockpit (read-only): a **Governance → Guardrails** page shows the guardrail registry,
whether Class C auto is possible (No), the caps config, backlog/in-flight sites, the
self-throttle metrics, the kill switch, plus the rollback ledger and pending-window
views. Endpoints (GET): `/api/guardrails/status`, `/changes`, `/pending`. Cockpit
route count 107; POST surface unchanged.

## Next

Phase D — the human review experience (evidence-chain viewer, before/after diff,
rollback preview, decision audit page). Phase E — the held-out correctness oracle
(descriptors only — never re-download/byte-compare, per posture) + identity matching +
eligibility engine. Only after E exists is Class C auto even *possible to consider* —
and then per-site, deliberate, never family-wide, never for the pinned actions.
