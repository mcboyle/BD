# Tiered Correctness Oracle & Eligibility Engine (Phase E)

*v3.66.124. The fifth and final step of the approved AUTONOMY_POLICY_v2 design.
Determines ELIGIBILITY ONLY — it performs no automation.*

## What Phase E is

A tiered descriptor oracle (Tier 0–3) plus an eligibility engine. It completes the
guardrail set (`correctness_oracle` is now built), but **completing it does not enable
Class C auto.** Three independent facts keep automation off:

1. Class C defaults to **Approve-each** (a fresh policy never has C at auto).
2. The per-site eligibility gate (`class_c_site_eligible`) is **empty by design** — no
   tier authorizes automation, and there is no per-site grant mechanism (issuing one is
   a separate governance decision with no code in this build).
3. There is **no Class C apply path** anywhere in the codebase.

The oracle assesses; it never acts.

## The tiers (no tier authorizes automation)

- **Tier 0 — no oracle** — no independent held-out capture, or only training evidence.
  Suggestions + standard review only; ineligible for Class C auto consideration.
- **Tier 1 — weak descriptor oracle** — held-out exists, partial descriptors. Enhanced
  review support only.
- **Tier 2 — standard descriptor oracle** — held-out exists; media identity + rendition
  descriptors; stable template shape; descriptor consistency; signing markers by name
  only. Oracle-qualified — eligible for *future governance review* (not automation).
- **Tier 3 — strong descriptor oracle** — multiple held-out captures with descriptor
  agreement (identity / rendition / structure stable). Highest tier; a *future
  candidate* for limited per-site autonomy evaluation — still requires separate
  governance approval AND a separate policy decision.

Every verdict carries `automation_eligible: False`.

## Posture (descriptors only)

The oracle works purely from posture-safe descriptors (identity, rendition,
template-shape, signing-marker NAMES). It NEVER reconstructs signed URLs, reuses
signing values, replays requests, drives a browser, re-downloads media, byte-compares
media, or fetches network resources. Any raw signing value is a hard failure.

## Hard-failure conditions (override to ineligible)

No held-out evidence; held-out overlaps training evidence; a raw signing value appears;
or the candidate affects credentials / corpus / debt / governance state. (Network /
browser / download / byte-compare are structurally absent — the tests assert no such
constructs exist.)

## Permanently ineligible (regardless of tier)

corpus writes; validation/correction debt retirement; finding confirmation/
falsification; release approval; posture-policy changes; automation-policy changes;
credential creation/modification; login credential handling.

## Outputs

`oracle_reports()` returns the assembled bundle (read-only). `generate_oracle_reports(by)`
explicitly writes six artifacts — `oracle_verdict.json`, `eligibility_matrix.{json,md}`,
`oracle_report.md`, `held_out_evidence_report.md`, `ineligible_sites_report.md` — to a
runtime reports dir. Generation is operator-invoked, never automatic.

## Cockpit (read-only)

A **Governance → Oracle & Eligibility** page shows oracle status (guardrails complete;
Class C not enabled; 0 automation-eligible sites), the eligibility matrix (per-site
tiers), per-site verdicts, held-out evidence, ineligible sites, and the reports bundle.
Endpoints (GET): `/api/oracle/status`, `/eligibility`, `/verdict`, `/held-out`,
`/ineligible`, `/reports`. Cockpit route count 118; POST surface unchanged.

## After Phase E

The autonomy A–E build is complete: governance foundation, Class B automation,
guardrail infrastructure, human review experience, and the tiered oracle. Class C auto
is **not** on — enabling any per-site autonomy is a separate governance decision,
made deliberately, per-site, gated on the oracle's independent eligibility signal,
never family-wide, never for the pinned actions, with a per-site grant mechanism that a
future phase would build under its own governance.
