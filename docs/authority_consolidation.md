# Authority & the generic Class-C apply harness (v3.66.134)

This release consolidates all Class-C "apply" autonomy behind **one model and one harness**,
and turns on the per-site gate computation (it was a hardcoded `False` stub). Nothing applies
by default — the change is structural plus a **dark-by-default** activation.

## The harness — `tools/autonomy_apply.py`

An apply *kind* is now a registration, not a bespoke module:

```python
aap.register_apply_kind(
    "live_site_config",
    gate=lambda s: el.evaluate_site(s, kind="live_site_config")["participation_eligible"],
    current=_current_live,          # the live state (before)
    proposer=_proposed_block,       # the proposed state (after); None ⇒ skip
    applier=_apply_live_block,      # writes `after`
    reverser=_restore_live_block,   # REQUIRED — no reverser, no apply path
    corroborate=_oracle_corroborates,   # optional
    validator=_post_apply_validation,   # optional; failure ⇒ immediate revert
    backup=_backup_sites_config,         # optional
    unchanged=...,                       # optional idempotency (default: before == after)
    transition_field="live_config",      # default: the kind name
)
```

`apply_for_kind(site, kind, by=…)` runs the **single** orchestration used by every kind:

> gate → propose → corroborate → before/after → idempotency → backup → `record_change` →
> apply → `register_pending` → (validate → revert on miss) → transition.

Fail-closed review is unchanged: silence reverts at the deadline, **reject** reverts now,
**accept** blesses without further change, a failed post-apply validation has already
reverted. These reuse the existing guardrail chain and `/api/review/decide` — **no new POST**.

Registration hooks are **late-binding wrappers** (they resolve module globals at call time),
so the apply behavior is identical to the former bespoke flows and existing tests are
unaffected.

## The authority model (per-(site, kind))

`class_c_site_eligible(site, kind)` is **computed** (D0 = compute the gate now). It is eligible
**iff all** of:

1. an **active** per-(site, kind) grant — granted, not suspended, not expired;
2. **Class C** at `auto_with_guardrails` (not frozen);
3. oracle **tier ≥ 3**.

It is **dark by default**: with no grant, or Class C at the approve-each default, or no tier-3
evidence, it returns `False`. Participation additionally requires evidence-qualified, a
registered apply path (reverser), and blast-radius clearance.

Grants are **per-(site, kind)**. Granting `(site, live_site_config)` does **not** grant
`(site, operational_rows)` or any other kind. Old single-kind grants migrate automatically:
`{site: {granted: …}}` is read as `{site: {live_site_config: …}}` and the nested shape is
persisted on the next write.

### Authority contracts automatically; it never expands automatically

* **Human-only (CLI):** create, revoke, and **un-suspend** a grant.
  ```
  python -m tools.autonomy_grant grant   <site> --kind <kind> --by you --reason "…"
  python -m tools.autonomy_grant revoke  <site> --kind <kind> --by you
  python -m tools.autonomy_grant unsuspend <site> --kind <kind> --by you
  python -m tools.autonomy_grant list
  ```
* **Automatic (host job):** `reconcile_grants` may only **auto-suspend** a grant (trust below
  floor, tier < 3, freeze, or expiry). It never creates a grant and never un-suspends one.

There is **no grant button** in the cockpit. Granting is a deliberate operator decision.

## Cockpit — the Authority view

The **Authority** view replaces the Live Config page: a (site × kind) grant matrix, pending
changes across all kinds, and the registered-kinds table. It is backed by five **read-only**
routes:

```
GET /api/authority/status   GET /api/authority/grants   GET /api/authority/pending
GET /api/authority/kinds     GET /api/authority/change?id=…
```

The legacy live routes remain as **compatibility aliases** (deprecated, not removed),
returning the same data filtered to `live_site_config`:

```
GET /api/live/status   GET /api/live/grants   GET /api/live/pending   GET /api/live/change?id=…
```

Equivalence is by construction — both surfaces read from the same authority functions.

## Adding the next kind (I/J/K)

Each future kind is a registration plus four functions (gate, current, proposer, applier) and
a **reverser**, optionally a validator. It inherits the grant store, CLI, reconcile,
fail-closed window, rollback, audit, and the Authority view for free. Granting
`(site, operational_rows)` is a **separate** human decision from `(site, live_site_config)`.

**Rule:** more autonomy = register more reversers + grant more (site, kind) pairs. Never remove
a gate.
