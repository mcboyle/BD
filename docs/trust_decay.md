# Trust Decay (Phase G / G3)

*Cockpit area: **Governance → Trust Decay**. Module: `tools/autonomy_trust.py`.
Shipped v3.66.128.*

## The one rule

**Trust may only ever decrease automatically.** This is the invariant Phase H is built
on. Adverse signals lower a site's trust; favourable signals never raise it on their own.
The only thing that raises trust is an explicit **human restore** — a governance action,
with no automatic caller anywhere in the code. Authority never expands on its own; it only
contracts as risk rises.

## How a trust value is produced

Two pieces:

- **Signal** (`signal_trust`, a pure 0–1 score) reflects the *current* real signals — the
  oracle tier, whether the held-out evidence is fresh (≤ 30 days), the global rollback and
  review-expiry rates, oracle hard-failures, and the freeze state. A frozen system caps
  every signal near zero.
- **Stored / effective trust** (`effective_trust`) is the value that actually counts. An
  unseen site starts at the baseline (1.0). Decay ratchets the stored value *down* toward
  the signal; it never ratchets up.

So the stored value is a one-way ratchet: `decay_trust` sets `stored := min(stored,
signal)`. If the signal improves, the stored floor is left where it was. The only way back
up is `reset_trust`, which requires a human identity and is logged.

This phase deliberately does **not** invent an "oracle disagreement" signal: that would
need real applied-change-vs-oracle outcomes, which don't exist without a Class C apply
path. Trust uses only the signals that are real today.

## How trust gates eligibility

The eligibility layer (G1) reads `effective_trust(site)`. A site whose trust has decayed
below the minimum (0.5) is **not eligible** — and stays ineligible even if its evidence
later looks good — until a human restores trust. Past misbehaviour does not auto-forgive.
The eligibility verdict carries a `trust` field so this is visible.

## Who applies decay (and who restores)

- **Decay** (`decay_trust` / `decay_all_trust`) is host-scheduled — a cron job or operator
  CLI runs it. It is never a cockpit button. It can only lower trust.
- **Restore** (`reset_trust`) is an operator action requiring a human identity. There is no
  automatic caller. It is the single path that raises trust.

The cockpit Trust Decay screens are **read-only**: they show each site's stored trust,
current signal, what the next decay would lower it to, eligibility, and the last human
restore. No POST is added.

## Cockpit views (read-only GET)

- `GET /api/trust/status` — thresholds (min/baseline/fresh-days), below-minimum count,
  freeze state.
- `GET /api/trust/overview` — per-site stored trust + current signal + eligibility, and
  the below-minimum set.
- `GET /api/trust/site?site=…` — per-site detail incl. `would_decay_to` and the last human
  restore.

## In the sandbox

With no configured sites and no decay job running, the overview renders empty and every
site reads at the baseline. The behaviour is exercised by the tests, which decay a site
and confirm (a) a later good signal does not raise it, (b) it loses eligibility, and (c)
only a human `reset_trust` brings it back.
