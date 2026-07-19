# Eligibility Governance (Phase G / G1)

*Cockpit area: **Governance → Eligibility Governance**. Module:
`tools/autonomy_eligibility.py` (read-only). Shipped v3.66.126.*

## What it is

A per-site **participation-eligibility** layer that sits *above* the correctness
oracle. The oracle (Phase E) answers "how good is the held-out descriptor evidence for
this site?" as a tier (0–3). This layer answers a different question: "is this site
*evidence-qualified* to even be **considered** for human-approved Class C
experimentation — and is it still qualified *today*?"

It is **evaluation only**. It applies nothing, approves nothing, issues no grant, and
creates no Class C apply path. It reads governance state and returns verdicts.

## Why building it does not enable autonomy

`participation_eligible` is **0 for every site** in this build, for three independent
reasons — the same three the oracle documents:

1. **Class C defaults to Approve-each.** `autonomy_policy.can_autonomously("C")` is not
   allowed by default.
2. **The per-site auto-grant store is empty by design** — there is no writer, so
   `autonomy_oracle.class_c_site_eligible(...)` is False for every site.
3. **No Class C apply path exists anywhere in the codebase.** `apply_path_exists()`
   returns `False`, and participation is gated on it. `can_participate(site)` — the
   single chokepoint any *future* apply path must consult — therefore returns `False`
   for every site.

Completing eligibility *infrastructure* is not the same as granting *authority*. Any
increase in authority is a separate governance decision made after the infrastructure
proves itself.

## The two dimensions of a verdict

`evaluate_site(site)` returns both:

- **`evidence_qualified`** — oracle Tier ≥ 3 **and** held-out evidence ≤ 30 days old
  **and** not frozen **and** no oracle hard-failure **and** the candidate (if any) is not
  a permanently-ineligible target. This is the dimension that **decays**.
- **`participation_eligible`** — `evidence_qualified` **and** the per-site Class C gate
  is open **and** an apply path exists **and** no other site has an unreviewed change in
  flight (blast-radius). Always `False` here.

## Decay — the load-bearing safety property

Trust may only ever **decrease** automatically. A site that was qualified yesterday loses
qualification on its own when:

- its held-out evidence ages past the **30-day** freshness window (or its freshness is
  unknown — treated as stale, fail-safe);
- automation is **frozen** (the kill switch / a guardrail failure);
- the oracle drops below **Tier 3** or raises a hard failure;
- a candidate change targets a **permanently-ineligible** action.

There is no mechanism in this layer that raises eligibility on its own — only the
operator designating fresh, disjoint held-out evidence can lift the oracle tier, and even
then participation stays gated by the absent grant and apply path.

## Hard limits this layer enforces

- **Permanently ineligible, any tier/trust:** corpus writes; validation & correction
  debt retirement; finding confirmation/falsification; release approval; posture-policy
  changes; automation-policy changes; credential handling; plus capture/login execution.
  A candidate targeting any of these is blocked regardless of evidence.
- **Small set only:** the evidence-qualified "considered" set is capped at **3** sites
  ("evaluate only a small number of Tier-3-eligible sites"); the rest are reported as
  over-cap and excluded.
- **Posture:** descriptors by **name** only (inherited from the oracle). No network
  fetch, media re-download, browser interaction, byte comparison, signed-URL
  reconstruction, or capture/login execution. The module performs no file writes and no
  module-level I/O.

## Cockpit views (all read-only GET)

- `GET /api/eligibility/status` — summary: Class C level, thresholds (Tier ≥ 3, ≤ 30d,
  cap 3), evidence-qualified + considered counts, 0 participation-eligible, frozen,
  apply-path-exists.
- `GET /api/eligibility/overview` — per-site rows (tier, evidence-fresh, qualified,
  participation-eligible, decay reasons) + the capped considered set.
- `GET /api/eligibility/site?site=<id>` — per-site detail: tier, evidence age, why
  qualification would decay, why participation is blocked, and the permanently-ineligible
  list.

No POST is added — the cockpit shows eligibility; it never grants it.

## In the sandbox

With no live `sites_config` and no designated capture provenance, every site is Tier 0
with unknown freshness, so nothing is evidence-qualified and the views render against
empty/default stores. Real qualification only appears once the tool runs on `stash` with
designated, fresh, disjoint held-out evidence per site.
